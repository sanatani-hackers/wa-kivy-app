"""groqchat — High-speed Groq-backed AI chat plugin for WA Core.
Powered by Groq's LPU inference engine (Meta Llama 3.3, Llama 3.1, DeepSeek R1).

Commands (prefix !ai or !groq):
  !ai <question>        Ask the AI (per-chat conversation memory, 30 min idle TTL)
  !ai reset             Forget conversation memory for this chat
  !ai help              Show command help
  !ai models            List recommended Groq models
  !ai status            View plugin configuration and health status
Owner only:
  !ai setkey <KEY>      Save Groq API key (gsk_...) - auto-registers first user as owner
  !ai model [id]        Show or change the active model (default: llama-3.3-70b-versatile)
  !ai persona <prompt>  Customize the system persona (!ai persona reset -> default)
  !ai on / !ai off      Toggle automatic AI replies for THIS chat
  !ai owners            List registered bot owners
  !ai addowner <num>    Add an authorized phone number as owner
  !ai delowner <num>    Remove an authorized owner
"""
import asyncio
import json
import logging
import os
import re
import time
from collections import OrderedDict

NAME = "groqchat"
log = logging.getLogger("plugin.groqchat")

PREFIXES = ("!ai", "!groq")
API_URL = "https://api.groq.com/openai/v1/chat/completions"
DEFAULT_MODEL = "llama-3.3-70b-versatile"

POPULAR_MODELS = [
    ("llama-3.3-70b-versatile", "Flagship, highest quality (Default)"),
    ("llama-3.1-8b-instant", "Ultra-fast, lowest latency"),
    ("deepseek-r1-distill-llama-70b", "Deep reasoning & problem solving"),
    ("gemma2-9b-it", "Google Gemma 2 9B"),
    ("mixtral-8x7b-32768", "Mixtral 8x7B (32k context)"),
]

SYSTEM_PROMPT = (
    "You are a helpful assistant chatting on WhatsApp. Reply in the same language and style "
    "as the user (Hinglish if they write Hinglish). Be concise. Plain text only; use "
    "WhatsApp formatting (*bold*, _italic_), no markdown headings (#) or tables."
)

MAX_TURNS = 8          # conversation memory pairs
HISTORY_TTL = 1800     # 30 minutes idle timeout
MAX_CHATS = 200        # in-memory chat cache bound
USER_COOLDOWN = 3.0    # min seconds between queries per user
USER_DAILY_CAP = 150   # max queries per user per day (owner exempt)
MAX_INPUT_CHARS = 2500
MAX_REPLY_CHARS = 2000
HTTP_TIMEOUT = 18

_cfg = {
    "api_key": "",
    "model": DEFAULT_MODEL,
    "owners": [],
    "allowed": [],
    "auto_chats": [],
    "system": ""
}
_ctx = None
_hist: "OrderedDict[str, dict]" = OrderedDict()
_last: dict = {}
_daily: dict = {}
_sem = None


class AIError(Exception):
    pass


# ----------------------------------------------------------------------------- Config Persistence
def _cfg_path():
    global _ctx
    if _ctx and hasattr(_ctx, "get_path"):
        return _ctx.get_path("config.json")
    try:
        from common import plugin_data_dir
        return os.path.join(plugin_data_dir(NAME), "config.json")
    except Exception:
        pass
    try:
        from common import private_dir
        return os.path.join(private_dir(), "groqchat.json")
    except Exception:
        base = os.path.expanduser("~/.wa_app")
        os.makedirs(base, exist_ok=True)
        return os.path.join(base, "groqchat.json")


def _load():
    global _ctx
    loaded = False
    # 1. Try ctx.load_json first
    if _ctx and hasattr(_ctx, "load_json"):
        data = _ctx.load_json("config.json", default=None)
        if data and isinstance(data, dict):
            _cfg.update(data)
            loaded = True

    # 2. Try direct config file path
    if not loaded:
        path = _cfg_path()
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    _cfg.update(json.load(f))
                    loaded = True
            except Exception as e:
                log.warning("could not read %s: %s", path, e)

    # 3. Check legacy paths if key is still missing
    if not loaded or not _cfg.get("api_key"):
        try:
            from common import private_dir
            for fname in ("groqchat.json", "aichat.json"):
                legacy = os.path.join(private_dir(), fname)
                if os.path.exists(legacy):
                    with open(legacy, "r", encoding="utf-8") as f:
                        leg_data = json.load(f)
                        if leg_data.get("api_key"):
                            _cfg.update(leg_data)
                            break
        except Exception:
            pass


def _save():
    global _ctx
    # 1. Save via ctx if available
    if _ctx and hasattr(_ctx, "save_json"):
        try:
            _ctx.save_json("config.json", _cfg)
        except Exception:
            pass

    # 2. Also save to direct filesystem path
    try:
        path = _cfg_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(_cfg, f, indent=2)
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        os.replace(tmp, path)
    except Exception as e:
        log.warning("could not save config: %s", e)


def _key():
    return os.environ.get("GROQ_API_KEY") or _cfg.get("api_key", "").strip()


async def setup(ctx):
    global _ctx
    _ctx = ctx
    _load()
    ctx.log.info("Groqchat ready. Model: %s, Key set: %s", _cfg.get("model", DEFAULT_MODEL), bool(_key()))


# ----------------------------------------------------------------------------- Helpers
def _num(jid: str) -> str:
    return (jid or "").split("@")[0].split(":")[0]


def _is_owner(msg) -> bool:
    if bool(msg.from_me):
        return True
    owners = _cfg.get("owners", [])
    if not owners:
        # If no owners configured yet, allow setup
        return True
    return _num(msg.sender_id) in owners


def _allowed(msg) -> bool:
    allowed = _cfg.get("allowed", [])
    return _is_owner(msg) or not allowed or _num(msg.sender_id) in allowed


def _history(chat_id):
    now = time.monotonic()
    h = _hist.get(chat_id)
    if h and now - h["t"] > HISTORY_TTL:
        h = None
    if h is None:
        h = {"t": now, "msgs": []}
    _hist[chat_id] = h
    _hist.move_to_end(chat_id)
    while len(_hist) > MAX_CHATS:
        _hist.popitem(last=False)
    h["t"] = now
    return h["msgs"]


def _over_cap(uid: str) -> bool:
    day = time.strftime("%Y-%m-%d")
    d, n = _daily.get(uid, (day, 0))
    if d != day:
        n = 0
    _daily[uid] = (day, n + 1)
    return n >= USER_DAILY_CAP


def _clip(text: str) -> str:
    if len(text) <= MAX_REPLY_CHARS:
        return text
    cut = text[:MAX_REPLY_CHARS]
    end = max(cut.rfind(". "), cut.rfind("\n"))
    return (cut[:end + 1] if end > MAX_REPLY_CHARS // 2 else cut).rstrip() + " …"


# ----------------------------------------------------------------------------- API Call (Worker Thread)
def _post(key: str, payload: dict) -> str:
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "User-Agent": "wa-core-groq/1.0"
    }

    data = None
    # 1. Try requests first (best SSL certifi handling & keepalive on Android)
    try:
        import requests
        resp = requests.post(API_URL, json=payload, headers=headers, timeout=HTTP_TIMEOUT)
        if resp.status_code == 200:
            data = resp.json()
        elif resp.status_code in (401, 403):
            raise AIError("Invalid Groq API key. Owner: set key using !ai setkey <gsk_...>")
        elif resp.status_code == 429:
            raise AIError("Groq rate limit reached. Please wait a few seconds and try again.")
        elif resp.status_code in (400, 404):
            err_msg = ""
            try:
                err_data = resp.json()
                err_msg = err_data.get("error", {}).get("message", "")
            except Exception:
                pass
            raise AIError(f"Groq API error ({resp.status_code}): {err_msg or 'Invalid model or parameters'}")
        else:
            raise AIError(f"Groq API server error ({resp.status_code})")
    except AIError:
        raise
    except ImportError:
        pass
    except Exception as e:
        if "timeout" in str(e).lower():
            raise AIError("Groq API request timed out. Please try again.")
        raise AIError(f"Network error contacting Groq: {e}")

    # 2. Fallback to urllib if requests was not present
    if data is None:
        import urllib.request
        import urllib.error
        import ssl
        try:
            import certifi
            ssl_ctx = ssl.create_default_context(cafile=certifi.where())
        except Exception:
            ssl_ctx = ssl.create_default_context()

        req = urllib.request.Request(
            API_URL, data=json.dumps(payload).encode("utf-8"),
            headers=headers, method="POST"
        )
        try:
            with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT, context=ssl_ctx) as r:
                data = json.load(r)
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8", errors="ignore")[:300]
            log.warning("groq urllib http %s: %s", e.code, err_body)
            if e.code in (401, 403):
                raise AIError("Invalid Groq API key. Owner: set key using !ai setkey <gsk_...>")
            if e.code == 429:
                raise AIError("Groq rate limit reached. Please wait a few seconds.")
            if e.code in (400, 404):
                raise AIError(f"Groq API error ({e.code}): Check model name with !ai models")
            raise AIError(f"Groq API error ({e.code})")
        except Exception:
            raise AIError("Could not reach Groq API servers (network error).")

    try:
        out = data["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError, TypeError):
        raise AIError("Unexpected response format from Groq.")

    # Clean DeepSeek / reasoning thinking tags if present
    cleaned = re.sub(r"<think>.*?</think>", "", out, flags=re.S).strip()
    if not cleaned:
        m_think = re.search(r"<think>(.*?)</think>", out, flags=re.S)
        if m_think:
            cleaned = m_think.group(1).strip()
    if not cleaned:
        raise AIError("Groq returned an empty response.")
    return cleaned


async def _ask(chat_id: str, prompt: str) -> str:
    hist = _history(chat_id)
    msgs = [{"role": "system", "content": _cfg.get("system") or SYSTEM_PROMPT}, *hist,
            {"role": "user", "content": prompt[:MAX_INPUT_CHARS]}]
    payload = {
        "model": _cfg.get("model") or DEFAULT_MODEL,
        "messages": msgs,
        "temperature": 0.7,
        "max_tokens": 1024
    }
    reply = await asyncio.to_thread(_post, _key(), payload)
    hist += [{"role": "user", "content": prompt[:MAX_INPUT_CHARS]}, {"role": "assistant", "content": reply}]
    del hist[:-MAX_TURNS * 2]
    return reply


async def _chat(ctx, msg, prompt):
    global _sem
    if not _key():
        return await ctx.reply(
            msg,
            "🔑 *Groq API key not set!*\n\n"
            "Set it with:\n"
            "`!ai setkey <your_groq_key>`\n\n"
            "Get a free API key from:\nhttps://console.groq.com/keys"
        )
    uid, now = _num(msg.sender_id), time.monotonic()
    if now - _last.get(uid, 0) < USER_COOLDOWN:
        return
    _last[uid] = now
    if not _is_owner(msg) and _over_cap(uid):
        return await ctx.reply(msg, "⚠️ Daily AI limit reached for your number. Resets tomorrow.")
    _sem = _sem or asyncio.Semaphore(2)
    if _sem.locked():
        return await ctx.reply(msg, "⏳ AI is processing another query. Please wait a moment...")
    async with _sem:
        try:
            reply = await _ask(msg.chat_id, prompt)
        except AIError as e:
            return await ctx.reply(msg, f"⚠️ {e}")
    await ctx.reply(msg, _clip(reply))


# ----------------------------------------------------------------------------- Entry Point
async def on_message(ctx, msg):
    text = (msg.text or "").strip()
    if not text:
        return
    low = text.lower()

    # Match any prefix: !ai or !groq
    matched_prefix = None
    for pfx in PREFIXES:
        if low == pfx or low.startswith(pfx + " "):
            matched_prefix = pfx
            break

    is_cmd = matched_prefix is not None
    auto = (not is_cmd and not msg.from_me and not text.startswith("!")
            and msg.chat_id in _cfg.get("auto_chats", []))

    if not (is_cmd or auto) or not _allowed(msg):
        return

    if auto:
        return await _chat(ctx, msg, text)

    arg = text[len(matched_prefix):].strip()
    head, _, rest = arg.partition(" ")
    cmd, rest = head.lower(), rest.strip()

    if cmd in ("", "help"):
        help_text = (
            "🤖 *Groq AI Assistant*\n\n"
            "• `!ai <question>` - Ask anything (with 30m chat memory)\n"
            "• `!ai reset` - Clear memory for this chat\n"
            "• `!ai models` - List available high-speed models\n"
            "• `!ai model [name]` - View or change active model\n"
            "• `!ai status` - View plugin health & config\n"
            "• `!ai persona [prompt]` - Set custom personality\n"
            "• `!ai on / off` - Auto-reply toggle for this chat\n"
            "• `!ai setkey <key>` - Save Groq API key\n"
            "• `!ai owners` - View authorized owners\n"
            "• `!ai addowner <num>` - Add new owner"
        )
        return await ctx.reply(msg, help_text)

    if cmd == "reset":
        _hist.pop(msg.chat_id, None)
        return await ctx.reply(msg, "🧹 Conversation memory cleared for this chat.")

    if cmd == "models":
        cur = _cfg.get("model", DEFAULT_MODEL)
        lines = ["⚡ *Recommended Groq Models:*"]
        for m_id, desc in POPULAR_MODELS:
            active_tag = " *(active)*" if m_id == cur else ""
            lines.append(f"• `{m_id}`{active_tag}\n  _{desc}_")
        lines.append(f"\n👉 Switch model with: `!ai model <model_name>`")
        return await ctx.reply(msg, "\n".join(lines))

    if cmd == "status":
        has_key = bool(_key())
        owners = _cfg.get("owners", [])
        owners_str = ", ".join(f"@{o}" for o in owners) if owners else "None (Open setup)"
        status_text = (
            "📊 *Groq AI Plugin Status:*\n"
            f"• 🟢 *API Key:* {'Configured ✅' if has_key else 'Missing ❌'}\n"
            f"• 🤖 *Active Model:* `{_cfg.get('model', DEFAULT_MODEL)}`\n"
            f"• 👑 *Owners:* {owners_str}\n"
            f"• 🧠 *Active Chats in Memory:* {len(_hist)}\n"
            f"• ⚡ *Auto-Reply Chats:* {len(_cfg.get('auto_chats', []))}"
        )
        return await ctx.reply(msg, status_text)

    if cmd in ("on", "off", "model", "persona", "setkey", "addowner", "delowner", "owners"):
        # Check authorization
        has_owners = bool(_cfg.get("owners"))
        is_owner = _is_owner(msg)

        # First-time setup: if no key or no owners set yet, allow whoever sends setkey
        if not has_owners or not _key():
            is_owner = True

        if not is_owner:
            owners_list = ", ".join(f"@{o}" for o in _cfg.get("owners", []))
            return await ctx.reply(
                msg,
                f"❌ *Permission Denied:*\nOnly registered bot owners ({owners_list}) can change AI settings or keys."
            )

        if cmd == "setkey":
            clean_key = rest.strip().strip("'\"")
            if not clean_key:
                return await ctx.reply(msg, "⚠️ *Usage:* `!ai setkey <your_groq_api_key>`\nExample: `!ai setkey gsk_...`")
            _cfg["api_key"] = clean_key
            sender_num = _num(msg.sender_id)
            if sender_num and sender_num not in _cfg.setdefault("owners", []):
                _cfg["owners"].append(sender_num)
            _save()
            return await ctx.reply(
                msg,
                f"🔑 *Groq API key saved successfully!*\n"
                f"👤 Registered @{sender_num} as Bot Owner.\n"
                f"🤖 *Active Model:* `{_cfg.get('model', DEFAULT_MODEL)}`\n\n"
                f"💡 *Ready!* Try asking anything:\n"
                f"`!ai hi`\n\n"
                f"⚠️ *Security Tip:* Please delete the message containing your API key from this chat!"
            )

        if cmd == "owners":
            owners = _cfg.get("owners", [])
            owners_str = "\n".join(f"• @{o}" for o in owners) if owners else "None registered"
            return await ctx.reply(msg, f"👑 *Registered Bot Owners:*\n{owners_str}\n\nAdd more with: `!ai addowner <phone_number>`")

        if cmd == "addowner":
            target = re.sub(r"[^0-9]", "", rest)
            if not target or len(target) < 10:
                return await ctx.reply(msg, "⚠️ *Usage:* `!ai addowner <country_code_number>` (e.g. `!ai addowner 919876543210`)")
            if target not in _cfg.setdefault("owners", []):
                _cfg["owners"].append(target)
                _save()
                return await ctx.reply(msg, f"✅ Added @{target} to Bot Owners.")
            return await ctx.reply(msg, f"ℹ️ @{target} is already a Bot Owner.")

        if cmd == "delowner":
            target = re.sub(r"[^0-9]", "", rest)
            if target in _cfg.get("owners", []):
                _cfg["owners"].remove(target)
                _save()
                return await ctx.reply(msg, f"🗑️ Removed @{target} from Bot Owners.")
            return await ctx.reply(msg, f"⚠️ Number @{target} was not in owners list.")

        if cmd in ("on", "off"):
            chats = set(_cfg.get("auto_chats", []))
            (chats.add if cmd == "on" else chats.discard)(msg.chat_id)
            _cfg["auto_chats"] = sorted(chats)
            _save()
            return await ctx.reply(msg, f"🤖 Auto-reply is now *{'ON' if cmd == 'on' else 'OFF'}* for this chat.")

        if cmd == "model":
            if rest:
                _cfg["model"] = rest.strip()
                _save()
                return await ctx.reply(msg, f"✅ Switched active Groq model to: `{_cfg['model']}`")
            return await ctx.reply(msg, f"🤖 Active Groq Model: `{_cfg.get('model', DEFAULT_MODEL)}`\nList all models with: `!ai models`")

        if cmd == "persona":
            _cfg["system"] = "" if rest.lower() in ("", "reset") else rest[:1000]
            _save()
            return await ctx.reply(msg, "✅ Custom persona saved." if _cfg["system"] else "✅ Persona reset to default.")

        return

    await _chat(ctx, msg, arg)
