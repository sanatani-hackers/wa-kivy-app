"""callmusic — Voice & Video Call Handling + In-Call Music & Video DJ Plugin for WA Core.

Powered by meowcaller VoIP (MLow / DTLS / SRTP) inside WA Core.

Commands (prefix !call or !music):
  !call help                       Show command guide
  !call status                     View Call Guard modes, active calls & music status
  !call tracks                     List available audio & video files with index numbers
  !call dial <number|me> [track]   Call someone and stream music when they pick up
  !call playcall [track]           Call current chat target and play music (no number needed)
  !call vcall <number|me> [video]  Place a video call and stream H.264/MP4 or music
  !call play [track|#index]        Play audio immediately in the active call
  !call queue <track|#index>       Add a track to the active call DJ queue
  !call skip                       Skip to next track in the queue
  !call pause / resume / stop      Playback controls
  !call answer / decline / hangup  Call lifecycle controls
  !call rec [stop]                 Record caller audio to WAV
  !call vrec [stop]                Record caller video to H.264
  !call react <emoji>              Send live call emoji reaction
  !call history                    Recent call history from SQLite

Owner Configuration:
  !call mode <off|music|reject|vip>    Incoming voice call policy
  !call vmode <same|music|reject|off>  Incoming video call policy
  !call track <track|#index>           Set default auto-answer hold music
  !call vtrack <video|#index>          Set default video clip for video calls
  !call loop <on|off>                  Loop single track playback
  !call delay <seconds>                Seconds before auto-answering (0..10)
  !call public <on|off>                Allow others to queue music / dial via commands
  !call vip <add|del|list> [num]       Manage VIP whitelist
  !call block <add|del|list> [num]     Manage blocked callers
  !call replymsg <text|off>            Auto-reply message on rejected/answered calls
"""
import asyncio
import logging
import os
import re
import time

NAME = "callmusic"
log = logging.getLogger("plugin.callmusic")

PREFIXES = ("!call", "!music")

DEFAULT_CFG = {
    "mode": "off",          # off | music | reject | vip
    "video_mode": "same",   # same | music | reject | off
    "default_track": "",
    "video_file": "",
    "loop": True,
    "auto_delay": 1.2,
    "public_dj": False,
    "reply_on_reject": "",
    "reply_on_answer": "",
    "vip": [],
    "blocked": [],
    "owners": [],
}

_cfg = dict(DEFAULT_CFG)
_ctx = None
_PENDING_PLAY: dict[str, dict] = {}
_DJ_QUEUES: dict[str, list[dict]] = {}
_RINGING_TRACKER: set[str] = set()


def _num(jid: str) -> str:
    return (jid or "").split("@")[0].split(":")[0].lstrip("+")


def _save():
    if _ctx and hasattr(_ctx, "save_json"):
        _ctx.save_json("config.json", _cfg)


def _load():
    if _ctx and hasattr(_ctx, "load_json"):
        data = _ctx.load_json("config.json", default=None)
        if isinstance(data, dict):
            _cfg.update({k: v for k, v in data.items() if k in DEFAULT_CFG})


def _is_owner(msg) -> bool:
    if bool(getattr(msg, "from_me", False)):
        return True
    owners = _cfg.get("owners", [])
    if not owners:
        return True
    return _num(getattr(msg, "sender_id", "")) in owners


def _resolve_indexed_or_name(arg: str, media_type: str = "audio") -> tuple[str, str]:
    """Return (resolved_path, display_name) for '#2' index or filename/query."""
    from core import call_media
    tracks = call_media.list_media_tracks()
    pool = tracks["video"] if media_type == "video" else tracks["audio"]
    clean = (arg or "").strip()

    if clean.startswith("#") and clean[1:].isdigit():
        idx = int(clean[1:]) - 1
        if 0 <= idx < len(pool):
            p = pool[idx]
            return p, os.path.basename(p)
        return "", ""
    if clean.isdigit():
        idx = int(clean) - 1
        if 0 <= idx < len(pool):
            p = pool[idx]
            return p, os.path.basename(p)

    p = call_media.resolve_track_path(clean, media_type=media_type)
    if p:
        return p, os.path.basename(p)
    return "", ""


async def setup(ctx):
    global _ctx
    _ctx = ctx
    _load()
    try:
        from core import call_media
        call_media.ensure_builtin_tracks()
        vf = _cfg.get("video_file", "")
        if vf and os.path.isfile(vf):
            asyncio.create_task(asyncio.to_thread(call_media.prepare_video_for_call, vf, 15))
    except Exception:
        pass
    ctx.log.info(
        "CallMusic ready. Mode=%s VideoMode=%s Supported=%s",
        _cfg.get("mode"), _cfg.get("video_mode"), ctx.calls.is_supported(),
    )


async def _start_call_media(ctx, call_id: str, spec: dict):
    kind = spec.get("kind", "audio")
    track = spec.get("track", "")
    loop = bool(spec.get("loop", _cfg.get("loop", True)))
    notify_chat = spec.get("notify_chat")
    is_video = bool(spec.get("is_video", False))

    if kind == "video":
        res = await ctx.play_call_video(call_id, track, fps=15, loop=loop, with_audio=True)
        if not res.get("ok"):
            # Fallback to voice track if video file unavailable
            fallback = track if track and not track.lower().endswith((".mp4", ".h264", ".mkv")) else _cfg.get("default_track", "")
            res = await ctx.play_call_audio(call_id, fallback, loop=loop)
    else:
        res = await ctx.play_call_audio(call_id, track, loop=loop)

    if notify_chat:
        if res.get("ok"):
            fname = os.path.basename(res.get("path") or track or "Hold Music")
            await ctx.send(
                notify_chat,
                f"🎧 *Call Connected {'(Video)' if is_video else '(Voice)'}!*\n"
                f"▶️ Now Playing: `{fname}`\n"
                f"🔁 Loop: *{'ON' if loop else 'OFF'}*\n\n"
                f"Use `!call skip`, `!call pause`, `!call stop`, or `!call hangup`."
            )
        else:
            await ctx.send(notify_chat, f"⚠️ Call connected, but audio failed: {res.get('error')}")


async def on_call(ctx, call_ev):
    ev = call_ev.event
    cid = call_ev.call_id
    peer_num = _num(call_ev.peer)

    if ev == "incoming":
        _RINGING_TRACKER.add(cid)
        try:
            from common import send_call_notification
            send_call_notification(cid, peer_num or "Unknown Caller", is_video=call_ev.is_video)
        except Exception:
            pass

        # 1. Blocked list always declines immediately
        if peer_num and peer_num in _cfg.get("blocked", []):
            await ctx.decline_call(cid)
            _RINGING_TRACKER.discard(cid)
            try:
                from common import cancel_android_notification
                cancel_android_notification(3001)
            except Exception:
                pass
            if _cfg.get("reply_on_reject"):
                await ctx.send(call_ev.peer, _cfg["reply_on_reject"])
            return

        vmode = _cfg.get("video_mode", "same")
        effective_mode = _cfg.get("mode", "off")
        if call_ev.is_video and vmode != "same":
            effective_mode = vmode

        is_vip = bool(peer_num and peer_num in _cfg.get("vip", []))
        if effective_mode == "vip":
            effective_mode = "off" if is_vip else "reject"

        if effective_mode == "reject":
            await ctx.decline_call(cid)
            _RINGING_TRACKER.discard(cid)
            try:
                from common import cancel_android_notification
                cancel_android_notification(3001)
            except Exception:
                pass
            if _cfg.get("reply_on_reject"):
                await ctx.send(call_ev.peer, _cfg["reply_on_reject"])
            return

        if effective_mode == "music":
            delay = max(0.0, min(10.0, float(_cfg.get("auto_delay", 1.2))))
            if delay > 0:
                await asyncio.sleep(delay)
            if cid not in _RINGING_TRACKER:
                return
            track = _cfg.get("video_file", "") if (call_ev.is_video and _cfg.get("video_file")) else _cfg.get("default_track", "")
            kind = "video" if (call_ev.is_video and _cfg.get("video_file")) else "audio"
            _PENDING_PLAY[cid] = {
                "kind": kind,
                "track": track,
                "loop": bool(_cfg.get("loop", True)),
                "is_video": call_ev.is_video,
            }
            ans = await ctx.answer_call(cid)
            if not ans.get("ok"):
                _PENDING_PLAY.pop(cid, None)
            elif _cfg.get("reply_on_answer"):
                await ctx.send(call_ev.peer, _cfg["reply_on_answer"])

    elif ev == "ready":
        _RINGING_TRACKER.discard(cid)
        try:
            from common import cancel_android_notification
            cancel_android_notification(3001)
        except Exception:
            pass
        spec = _PENDING_PLAY.pop(cid, None)
        if spec:
            await _start_call_media(ctx, cid, spec)

    elif ev == "audio_finish":
        q = _DJ_QUEUES.get(cid)
        if q:
            nxt = q.pop(0)
            res = await ctx.play_call_audio(cid, nxt["track"], loop=bool(nxt.get("loop", False)))
            if res.get("ok") and nxt.get("notify_chat"):
                await ctx.send(nxt["notify_chat"], f"⏭️ *Now Playing from Queue:* `{os.path.basename(res.get('path') or nxt['track'])}`")

    elif ev == "end":
        _RINGING_TRACKER.discard(cid)
        spec = _PENDING_PLAY.pop(cid, None)
        _DJ_QUEUES.pop(cid, None)
        try:
            from common import cancel_android_notification
            cancel_android_notification(3001)
        except Exception:
            pass
        if spec and spec.get("notify_chat"):
            reason = call_ev.reason or "ended"
            await ctx.send(spec["notify_chat"], f"📴 Call with @{peer_num} ended (`{reason}`).")


async def on_message(ctx, msg):
    text = (msg.text or "").strip()
    if not text:
        return
    low = text.lower()

    matched_prefix = None
    for pfx in PREFIXES:
        if low == pfx or low.startswith(pfx + " "):
            matched_prefix = pfx
            break
    if matched_prefix is None:
        return

    arg = text[len(matched_prefix):].strip()
    head, _, rest = arg.partition(" ")
    cmd = head.lower()
    rest = rest.strip()

    is_owner = _is_owner(msg)
    if not is_owner and not _cfg.get("public_dj", False) and cmd not in ("help", "tracks", "status"):
        return await ctx.reply(msg, "❌ Owner-only command. Bot owner can enable public DJ via `!call public on`.")

    if cmd in ("", "help"):
        return await ctx.reply(
            msg,
            "🎧 *Call & Video Music DJ Guide*\n\n"
            "*Play Music on Calls:*\n"
            "• `!call tracks` — List music & video files\n"
            "• `!call dial <num|me> [song|#]` — Call & play song\n"
            "• `!call playcall [song|#]` — Call this chat & play\n"
            "• `!call vcall <num|me> [video|#]` — Video call stream\n"
            "• `!call play [song|#]` — Play/switch song in active call\n"
            "• `!call queue <song|#>` — Add song to DJ queue\n"
            "• `!call skip` | `pause` | `resume` | `stop`\n\n"
            "*Call Controls & Recording:*\n"
            "• `!call answer` | `decline` | `hangup`\n"
            "• `!call rec` / `!call rec stop` — Record caller audio\n"
            "• `!call vrec` / `!call vrec stop` — Record caller video\n"
            "• `!call react 🔥` — Live call reaction\n\n"
            "*Automation (Owner):*\n"
            "• `!call mode <off|music|reject|vip>`\n"
            "• `!call vmode <same|music|reject|off>`\n"
            "• `!call track <song|#>` — Default hold song\n"
            "• `!call loop <on|off>` | `status` | `history`"
        )

    if cmd == "tracks":
        from core import call_media
        data = call_media.list_media_tracks()
        lines = ["🎵 *Available Call Audio Tracks:*"]
        if data["audio"]:
            for i, p in enumerate(data["audio"], 1):
                sz = max(1, os.path.getsize(p) // 1024)
                lines.append(f"  `#{i}` {os.path.basename(p)} _({sz} KB)_")
        else:
            lines.append("  _(none found)_")

        if data["video"]:
            lines.append("\n🎬 *Available Video Clips (.mp4 / .h264):*")
            for i, p in enumerate(data["video"], 1):
                sz = max(1, os.path.getsize(p) // 1024)
                lines.append(f"  `#{i}` {os.path.basename(p)} _({sz} KB)_")

        lines.append(f"\n📁 Drop `.mp3` / `.wav` / `.mp4` files in:\n`{call_media.call_music_dir()}`")
        return await ctx.reply(msg, "\n".join(lines))

    if cmd == "status":
        from core import call_media
        tracks = call_media.list_media_tracks()
        active = await ctx.calls.list_active()
        act_str = "None"
        if active:
            act_lines = []
            for a in active:
                t_name = a.get("audio_path") or "No audio"
                act_lines.append(f"{a.get('peer')} [{a.get('phase')}] ({t_name})")
            act_str = "\n  " + "\n  ".join(act_lines)
        return await ctx.reply(
            msg,
            "📞 *CallMusic & Guard Status*\n"
            f"• ⚙️ *VoIP Engine:* {'Ready ✅' if ctx.calls.is_supported() else 'Upgrade .so Required ❌'}\n"
            f"• 🎙️ *Voice Policy:* `{_cfg.get('mode')}`\n"
            f"• 🎥 *Video Policy:* `{_cfg.get('video_mode')}`\n"
            f"• 🎵 *Hold Track:* `{os.path.basename(_cfg.get('default_track') or 'auto (hold_chime.wav)')}`\n"
            f"• 🔁 *Loop:* `{'ON' if _cfg.get('loop') else 'OFF'}` | *Delay:* `{_cfg.get('auto_delay')}s`\n"
            f"• ⭐ *VIPs:* `{len(_cfg.get('vip', []))}` | 🚫 *Blocked:* `{len(_cfg.get('blocked', []))}`\n"
            f"• 📂 *Library:* `{len(tracks['audio'])} audio, {len(tracks['video'])} video`\n"
            f"• 📡 *Active Calls:* {act_str}"
        )

    if cmd == "history":
        rows = ctx.calls.history(10)
        if not rows:
            return await ctx.reply(msg, "📭 No call records yet.")
        lines = ["📜 *Recent Call Log:*"]
        for r in rows:
            kind = "🎥" if r.get("is_video") else "📞"
            arrow = "↙️" if r.get("direction") == "in" else "↗️"
            ts_str = time.strftime("%d/%m %H:%M", time.localtime(r.get("started_at") or 0))
            trk = f" ♪ {r['track_played']}" if r.get("track_played") else ""
            lines.append(f"{kind}{arrow} @{r.get('peer_number')} • *{r.get('status')}* ({ts_str}){trk}")
        return await ctx.reply(msg, "\n".join(lines))

    # ---- Dialing / Outbound Voice & Video Calls
    if cmd in ("dial", "place", "playcall", "vcall"):
        is_video = (cmd == "vcall")
        target = ""
        song_q = ""
        if cmd == "playcall":
            target = msg.chat_id
            song_q = rest
        else:
            p1, _, p2 = rest.partition(" ")
            if not p1:
                return await ctx.reply(msg, f"⚠️ Usage: `!call {cmd} <phone|me> [song_or_#index]`")
            if p1.lower() == "me":
                target = msg.sender_id
            elif p1.lower() == "here":
                target = msg.chat_id
            else:
                target = re.sub(r"[^0-9@a-zA-Z.]", "", p1)
            song_q = p2.strip()

        from core import call_media

        resolved_path = ""
        disp_name = "default hold track"
        media_kind = "audio"

        if is_video:
            if song_q:
                v_path, v_disp = _resolve_indexed_or_name(song_q, "video")
                if v_path:
                    media_kind = "video"
                    resolved_path, disp_name = v_path, v_disp
                else:
                    a_path, a_disp = _resolve_indexed_or_name(song_q, "audio")
                    if a_path:
                        media_kind = "audio"
                        resolved_path, disp_name = a_path, a_disp
                    else:
                        return await ctx.reply(msg, f"⚠️ Video/Audio `{song_q}` not found. Check `!call tracks`.")
            elif _cfg.get("video_file"):
                v_path, v_disp = _resolve_indexed_or_name(_cfg["video_file"], "video")
                if v_path:
                    media_kind = "video"
                    resolved_path, disp_name = v_path, v_disp
        else:
            q = song_q or _cfg.get("default_track", "")
            if q:
                resolved_path, disp_name = _resolve_indexed_or_name(q, "audio")
                if not resolved_path:
                    return await ctx.reply(msg, f"⚠️ Track `{q}` not found. Check `!call tracks`.")

        # Pre-prepare / hardware-transcode BEFORE dialing so playback starts instantaneously at 1.0x sync
        if media_kind == "video" and resolved_path:
            await ctx.reply(msg, f"⏳ Preparing `{disp_name}` (<=480p 15fps H.264 + 16kHz audio) before dialing...")
            ok_v, _, _, status_v = await asyncio.to_thread(call_media.prepare_video_for_call, resolved_path, 15)
            if not ok_v:
                return await ctx.reply(msg, f"❌ Video preparation failed: {status_v}")
        elif media_kind == "audio" and resolved_path:
            ok_a, status_a = await asyncio.to_thread(call_media.prepare_audio_for_call, resolved_path)
            if not ok_a:
                return await ctx.reply(msg, f"❌ Audio preparation failed: {status_a}")

        await ctx.reply(msg, f"📲 Calling `{_num(target)}` {'(Video Call)' if is_video else '(Voice Call)'}...\n🎵 Queued on pickup: `{disp_name}`")
        res = await ctx.call(target, video=is_video)
        if not res.get("ok"):
            return await ctx.reply(msg, f"❌ Failed to place call: {res.get('error')}")

        cid = res.get("call_id", "")
        _PENDING_PLAY[cid] = {
            "kind": media_kind,
            "track": resolved_path,
            "loop": bool(_cfg.get("loop", True)),
            "notify_chat": msg.chat_id,
            "is_video": is_video,
        }
        return

    # ---- Active Call Playback & Controls
    active_cid = ctx.calls.latest_active_call_id()

    if cmd == "play":
        if not active_cid:
            return await ctx.reply(msg, "📭 No active call right now. Use `!call dial <number> [song]` or `!call playcall [song]`!")
        q = rest or _cfg.get("default_track", "")
        path, disp = _resolve_indexed_or_name(q, "audio")
        if q and not path:
            return await ctx.reply(msg, f"⚠️ Track `{q}` not found. Check `!call tracks`.")
        res = await ctx.play_call_audio(active_cid, path or q, loop=bool(_cfg.get("loop", True)))
        if res.get("ok"):
            return await ctx.reply(msg, f"🎵 *Playing:* `{os.path.basename(res.get('path') or disp or 'Hold Music')}`")
        return await ctx.reply(msg, f"❌ Playback failed: {res.get('error')}")

    if cmd == "queue":
        if not active_cid:
            return await ctx.reply(msg, "📭 No active call to queue tracks into.")
        if not rest:
            return await ctx.reply(msg, "⚠️ Usage: `!call queue <track_or_#index>`")
        path, disp = _resolve_indexed_or_name(rest, "audio")
        if not path:
            return await ctx.reply(msg, f"⚠️ Track `{rest}` not found.")
        cur_state = ctx.calls._active.get(active_cid, {}).get("audio_state", "idle")
        if cur_state == "idle":
            res = await ctx.play_call_audio(active_cid, path, loop=False)
            if res.get("ok"):
                return await ctx.reply(msg, f"🎵 *Started Playing:* `{disp}`")
            return await ctx.reply(msg, f"❌ Error: {res.get('error')}")
        q_list = _DJ_QUEUES.setdefault(active_cid, [])
        q_list.append({"track": path, "loop": False, "notify_chat": msg.chat_id})
        return await ctx.reply(msg, f"📋 Added `{disp}` to DJ Queue (Position #{len(q_list)}).")

    if cmd == "skip":
        if not active_cid:
            return await ctx.reply(msg, "📭 No active call.")
        q_list = _DJ_QUEUES.get(active_cid, [])
        if not q_list:
            await ctx.stop_call_audio(active_cid)
            return await ctx.reply(msg, "⏹️ Queue empty — stopped current playback.")
        nxt = q_list.pop(0)
        res = await ctx.play_call_audio(active_cid, nxt["track"], loop=False)
        if res.get("ok"):
            return await ctx.reply(msg, f"⏭️ *Skipped to:* `{os.path.basename(res.get('path') or nxt['track'])}`")
        return await ctx.reply(msg, f"❌ Skip failed: {res.get('error')}")

    if cmd == "pause":
        if not active_cid:
            return await ctx.reply(msg, "📭 No active call.")
        res = await ctx.pause_call_audio(active_cid)
        return await ctx.reply(msg, "⏸️ Audio paused." if res.get("ok") else f"❌ {res.get('error')}")

    if cmd == "resume":
        if not active_cid:
            return await ctx.reply(msg, "📭 No active call.")
        res = await ctx.resume_call_audio(active_cid)
        return await ctx.reply(msg, "▶️ Audio resumed." if res.get("ok") else f"❌ {res.get('error')}")

    if cmd == "stop":
        if not active_cid:
            return await ctx.reply(msg, "📭 No active call.")
        _DJ_QUEUES.pop(active_cid, None)
        await ctx.stop_call_video(active_cid)
        res = await ctx.stop_call_audio(active_cid)
        return await ctx.reply(msg, "⏹️ Playback stopped." if res.get("ok") else f"❌ {res.get('error')}")

    if cmd == "answer":
        if not active_cid:
            return await ctx.reply(msg, "📭 No ringing call.")
        if rest:
            p, _ = _resolve_indexed_or_name(rest, "audio")
            _PENDING_PLAY[active_cid] = {"kind": "audio", "track": p or rest, "loop": bool(_cfg.get("loop", True)), "notify_chat": msg.chat_id}
        res = await ctx.answer_call(active_cid)
        return await ctx.reply(msg, "📞 Answering call..." if res.get("ok") else f"❌ {res.get('error')}")

    if cmd in ("decline", "reject"):
        if not active_cid:
            return await ctx.reply(msg, "📭 No ringing call.")
        res = await ctx.decline_call(active_cid)
        return await ctx.reply(msg, "📵 Call declined." if res.get("ok") else f"❌ {res.get('error')}")

    if cmd in ("hangup", "end"):
        if not active_cid:
            return await ctx.reply(msg, "📭 No active call.")
        res = await ctx.hangup_call(active_cid)
        return await ctx.reply(msg, "📴 Call ended." if res.get("ok") else f"❌ {res.get('error')}")

    if cmd == "rec":
        if not active_cid:
            return await ctx.reply(msg, "📭 No active call.")
        stop_flag = rest.lower() in ("off", "stop", "0")
        res = await ctx.record_call_audio(active_cid, "" if stop_flag else "auto")
        if not res.get("ok"):
            return await ctx.reply(msg, f"❌ Record failed: {res.get('error')}")
        return await ctx.reply(msg, "⏹️ Recording saved." if stop_flag else f"🎙️ Recording caller audio to:\n`{res.get('path')}`")

    if cmd == "vrec":
        if not active_cid:
            return await ctx.reply(msg, "📭 No active call.")
        stop_flag = rest.lower() in ("off", "stop", "0")
        res = await ctx.record_call_video(active_cid, "" if stop_flag else "auto")
        if not res.get("ok"):
            return await ctx.reply(msg, f"❌ Video record failed: {res.get('error')}")
        return await ctx.reply(msg, "⏹️ Video recording saved." if stop_flag else f"🎥 Recording caller H.264 to:\n`{res.get('path')}`")

    if cmd == "react":
        if not active_cid:
            return await ctx.reply(msg, "📭 No active call.")
        emoji = rest or "🎵"
        res = await ctx.send_call_reaction(active_cid, emoji)
        return await ctx.reply(msg, f"✨ Sent reaction `{emoji}`" if res.get("ok") else f"❌ {res.get('error')}")

    # ---- Owner Settings Commands
    if not is_owner:
        return await ctx.reply(msg, "❌ Only bot owner can modify Call Guard settings.")

    sender_num = _num(msg.sender_id)
    if sender_num and sender_num not in _cfg.setdefault("owners", []):
        _cfg["owners"].append(sender_num)

    if cmd == "mode":
        m = rest.lower()
        if m not in ("off", "music", "reject", "vip"):
            return await ctx.reply(msg, "⚠️ Choose mode: `off`, `music`, `reject`, or `vip`.")
        _cfg["mode"] = m
        _save()
        return await ctx.reply(msg, f"✅ Voice call auto-policy set to: *{m}*")

    if cmd == "vmode":
        m = rest.lower()
        if m not in ("same", "music", "reject", "off"):
            return await ctx.reply(msg, "⚠️ Choose vmode: `same`, `music`, `reject`, or `off`.")
        _cfg["video_mode"] = m
        _save()
        return await ctx.reply(msg, f"✅ Video call auto-policy set to: *{m}*")

    if cmd == "track":
        if not rest:
            return await ctx.reply(msg, f"🎵 Default track: `{_cfg.get('default_track') or 'auto'}`")
        path, disp = _resolve_indexed_or_name(rest, "audio")
        if not path:
            return await ctx.reply(msg, f"⚠️ Track `{rest}` not found. See `!call tracks`.")
        _cfg["default_track"] = path
        _save()
        return await ctx.reply(msg, f"✅ Default hold music set to: `{disp}`")

    if cmd == "vtrack":
        if not rest:
            return await ctx.reply(msg, f"🎬 Default video: `{_cfg.get('video_file') or 'none'}`")
        path, disp = _resolve_indexed_or_name(rest, "video")
        if not path:
            return await ctx.reply(msg, f"⚠️ Video `{rest}` not found. See `!call tracks`.")
        _cfg["video_file"] = path
        _save()
        from core import call_media
        asyncio.create_task(asyncio.to_thread(call_media.prepare_video_for_call, path, 15))
        return await ctx.reply(msg, f"✅ Default video stream set to: `{disp}` (pre-transcoding in background)")

    if cmd == "loop":
        val = rest.lower() in ("on", "1", "true", "yes")
        _cfg["loop"] = val
        _save()
        return await ctx.reply(msg, f"🔁 Loop playback: *{'ON' if val else 'OFF'}*")

    if cmd == "delay":
        try:
            d = max(0.0, min(10.0, float(rest)))
            _cfg["auto_delay"] = d
            _save()
            return await ctx.reply(msg, f"⏱️ Auto-answer delay set to `{d}s`")
        except ValueError:
            return await ctx.reply(msg, "⚠️ Usage: `!call delay 1.5`")

    if cmd == "public":
        val = rest.lower() in ("on", "1", "true", "yes")
        _cfg["public_dj"] = val
        _save()
        return await ctx.reply(msg, f"🎛️ Public DJ commands: *{'ON' if val else 'OFF'}*")

    if cmd in ("vip", "block"):
        sub, _, num_arg = rest.partition(" ")
        sub = sub.lower()
        target_key = "vip" if cmd == "vip" else "blocked"
        items = list(_cfg.get(target_key, []))
        if sub in ("", "list"):
            lbl = "⭐ VIP List" if cmd == "vip" else "🚫 Blocked Callers"
            body = "\n".join(f"• @{n}" for n in items) if items else "_Empty_"
            return await ctx.reply(msg, f"*{lbl}:*\n{body}")
        clean_num = re.sub(r"[^0-9]", "", num_arg)
        if not clean_num:
            return await ctx.reply(msg, f"⚠️ Usage: `!call {cmd} add <phone>` or `del <phone>`")
        if sub == "add":
            if clean_num not in items:
                items.append(clean_num)
                _cfg[target_key] = items
                _save()
            return await ctx.reply(msg, f"✅ Added `+{clean_num}` to {target_key}.")
        if sub in ("del", "remove"):
            if clean_num in items:
                items.remove(clean_num)
                _cfg[target_key] = items
                _save()
            return await ctx.reply(msg, f"🗑️ Removed `+{clean_num}` from {target_key}.")

    if cmd == "replymsg":
        _cfg["reply_on_reject"] = "" if rest.lower() in ("", "off", "none") else rest[:500]
        _save()
        return await ctx.reply(msg, "✅ Auto-reply message updated." if _cfg["reply_on_reject"] else "✅ Reject auto-reply disabled.")

    await ctx.reply(msg, "❓ Unknown subcommand. Type `!call help`.")
