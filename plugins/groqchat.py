"""groqchat — High-speed Groq-backed AI chat plugin for WA Core.
Powered by Groq's LPU inference engine (Meta Llama 3.3, Llama 3.1, DeepSeek R1).

Commands (prefix !ai or !groq):
  !ai <question>        Ask the AI (per-chat conversation memory, 30 min idle TTL)
  !ai reset             Forget conversation memory for this chat
  !ai help              Show command help
  !ai models            List recommended Groq models
Owner only (your own messages, or numbers in config 'owners'):
  !ai on / !ai off      Toggle automatic AI replies for THIS chat
  !ai model [id]        Show or change the active model (default: llama-3.3-70b-versatile)
  !ai persona <prompt>  Customize the system persona (!ai persona reset -> default)
  !ai setkey <KEY>      Save Groq API key (gsk_...)
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
    # Check ctx.load_json first
    if _ctx and hasattr(_ctx, "load_json"):
        data = _ctx.load_json("config.json", default=None)
        if data and isinstance(data, dict):
            _cfg.update(data)
            return

    # Check direct config file
    path = _cfg_path()
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                _cfg.update(json.load(f))
                return
        except Exception as e:
            log.warning("could not read %s: %s", path, e)

    # Legacy check: aichat.json in private_dir()
    try:
        from common import private_dir
        legacy = os.path.join(private_dir(), "aichat.json")
        if os.path.exists(legacy):
            with open(legacy, "r", encoding="utf-8") as f:
                _cfg.update(json.load(f))
    except Exception:
        pass


def _save():
    global _ctx
    if _ctx and hasattr(_ctx, "save_json"):
        _ctx.save_json("config.json", _cfg)
        return

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


def _key():
    return os.environ.get("GROQ_API_KEY") or _cfg.get("api_key", "").strip()


async def setup(ctx):
    global _ctx
    _ctx = ctx
    _load()
    ctx.log.info("Groqchat ready. Model: %s", _cfg.get("model", DEFAULT_MODEL))


# ----------------------------------------------------------------------------- Helpers
def _num(jid: str) -> str:
    return (jid or "").split("@")[0].split(":")[0]


def _is_owner(msg) -> bool:
    return bool(msg.from_me) or _num(msg.sender_id) in _cfg.get("owners", [])


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
            "🔑 *Groq API key not set!*\nSet it with: `!ai setkey <your_groq_key>`\n"
            "Get a free key from: https://console.groq.com/keys"
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
            "• `!ai persona [prompt]` - Set custom personality\n"
            "• `!ai on / off` - Auto-reply toggle for this chat\n"
            "• `!ai setkey <key>` - Save Groq API key (Owner only)"
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

    if cmd in ("on", "off", "model", "persona", "setkey"):
        if not _is_owner(msg):
            return
        if cmd in ("on", "off"):
            chats = set(_cfg.get("auto_chats", []))
            (chats.add if cmd == "on" else chats.discard)(msg.chat_id)
            _cfg["auto_chats"] = sorted(chats)
            _save()
            return await ctx.reply(msg, f"🤖 Auto-reply is now *{'ON' if cmd == 'on' else 'OFF'}* for this chat.")
        if cmd == "model":
            if rest:
                _cfg["model"] = rest
                _save()
            return await ctx.reply(msg, f"Active Groq Model: `{_cfg.get('model', DEFAULT_MODEL)}`")
        if cmd == "persona":
            _cfg["system"] = "" if rest.lower() in ("", "reset") else rest[:1000]
            _save()
            return await ctx.reply(msg, "Persona updated." if _cfg["system"] else "Persona reset to default.")
        if cmd == "setkey" and rest:
            _cfg["api_key"] = rest
            _save()
            return await ctx.reply(msg, "🔑 Groq API key saved successfully! Please delete your message from chat.")
        return

    await _chat(ctx, msg, arg)
