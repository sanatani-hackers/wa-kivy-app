# Legacy v0.3.0 (`main` Branch) — Complete Features Inventory

Reference catalog of **every feature implemented in the Python/Kivy v0.3.0 app (`main` branch, commit `92a6fe7`)** so none are missed when building **WA power (`kt.sanatani.wapower`)** natively in Kotlin + Jetpack Compose.

*(Every legacy source file remains readable via `git show main:<path>`.)*

---

## 1. WhatsApp Connection, Sync & Background Service
- **Pairing & Login (`ConnectScreen`):**
  - 8-digit phone-number pairing code (`PairPhone` + 1-tap Copy)
  - Live QR code pairing (`segno` PNG render -> native Bitmap/ZXing in Kotlin)
  - Clear Session / Logout (`session.sqlite3` + WAL reset)
- **Deterministic Sync Gate (`core/wa_client.py`, `core/throttle.py`):**
  - `sync_state` state machine: `stopped -> connecting -> ws_connected -> syncing_offline -> ready`
  - Hooks `OfflineSyncPreviewEv`, `OfflineSyncCompletedEv`, `AppStateSyncCompleteEv` + `3.5s` fallback timeout
  - Outbound message & `CallEngine.place_call` readiness gate (`wait_until_ready`) preventing pre-sync packet drops
  - App lifecycle presence: `AVAILABLE` + `set_passive(False)` on foreground/ready, `UNAVAILABLE` after background idle (`on_resume` / `on_pause`)
- **Anti-Spam Outbound Queue (`core/throttle.py`):**
  - Paced per-chat gap, global per-minute cap, natural typing indicator duration (`SendChatPresence`)
- **Foreground Service & Rich Notifications (`service/main.py`, `common.py`):**
  - Persistent `START_STICKY` foreground service (Channel `service_channel_v4`, ID `1`) with live stats (`Online / Syncing / Offline`, `RAM MB`, active call status `♪ Call • Peer`)
  - Notification action buttons: `■ STOP` / `▶ START`
  - Android 16 Promoted Ongoing (`android.requestPromotedOngoing`) & Samsung One UI NowBar (`com.samsung.android.notification.live`) status chip / notch capsule extras
  - Incoming Call notification (ID `3001`) with `Answer+Music` & `Decline` buttons
  - Heads-up `BigTextStyle` alerts (IDs `2001` Anti-Delete, `2002` Anti-Edit, `2003` Reminders)

---

## 2. Full WhatsApp Client (`main.py`, `core/client_db.py`, `core/wa_client.py`)
- **Local SQLite WAL Client Store (`wa_client.sqlite3`):**
  - `chats`, `messages`, `contacts` tables hydrated from `HistorySyncEv`, `ContactSyncEv`, `get_all_contacts()`, `get_joined_groups()`, `MessageEv`, `ReceiptEv`, `PresenceEv`, `ChatPresenceEv`, `MessageDeleteForMeEv`
- **Chats List (`ChatsScreen`):**
  - Live search, filter tabs (`All`, `Unread`, `Groups`, `Archived`)
  - Pin / Unpin, Archive / Unarchive, Mute / Unmute (`chat_settings`), Unread counter badges
- **Chat Thread (`ChatRoomScreen`):**
  - Live peer header (`Online`, `Typing...`, `Last seen`) via `subscribe_presence`
  - Message bubbles with sender names (groups), quoted replies, reactions (`👍 ❤️ 😂`), receipt ticks (`sent`, `delivered`, `read`), inline media download & viewer
  - Send text, quoted reply, and attachments (Photo, Video, Audio, Document)
  - Quick dial Voice Call, Video Call, and YouTube Live Stream modal directly from chat header
- **Contacts (`ContactsScreen`):**
  - Synced people + joined groups, `All / People / Groups` filters, `Resync`, `is_on_whatsapp` phone checker, 1-tap Chat / Voice / Video

---

## 3. Real Wire-Level Ghost Mode (`core/ghost.py`)
- **`ghost_read`:** Suppresses `mark_read` receipts on WhatsApp wire when opening chats; exposes manual **Mark Read** button in chat header
- **`ghost_presence`:** Forces `Presence.UNAVAILABLE` and blocks `AVAILABLE` on connect/foreground
- **`ghost_typing`:** Suppresses `SendChatPresence(COMPOSING)` when replying
- **`ghost_viewonce_inline`:** Auto-decrypts & saves View-Once media permanently into `view_once_inline/` inside message thread without sending View-Once opened receipts
- **`auto_wipe` & `anti_delete_relay`:** Toggles persisted in `ghost.json`

---

## 4. VoIP, Video Calls & Live YouTube Streaming (`go_bridge/`, `WaMediaTranscoder.java`, `callmusic`, `ytcall`)
- **Go `meowcaller` VoIP Bridge (`call_bridge.go`):**
  - Outbound & incoming Voice/Video calls (`CallPlace`, `CallAnswer`, `CallDecline`, `CallHangup`, `CallStartVideo`, `CallAcceptVideo`, `CallStopVideo`)
  - Live call audio recording (`.wav` via `WAVRecorder`) & video recording (`.h264` via `AnnexBRecorder`), live call emoji reactions (`CallSendReaction`), persistent SQLite `call_log` history
  - **9.0s PCM Jitter Buffer (`CallPlayPCMStream`):** `150`-frame 16kHz mono s16le loopback socket with tunable `prebufFrames` (`fast=4`, `normal=8`, `slow=14`)
  - **Live Video + Audio Dual Socket (`CallPlayAVStream`):** `audio_port` + `video_port` (`45`-AU bounded H.264 Annex-B ring channel) synced to audio clock with composite cancellation
- **Android Hardware Media Transcoder (`WaMediaTranscoder.java`):**
  - `extractAudioToWav16k` (tracks `INFO_OUTPUT_FORMAT_CHANGED` for HE-AAC SBR/PS)
  - `transcodeVideoToH264` (`EGL14` + `SurfaceTexture` `GlRenderer` -> `<=480p`, `15fps`, Baseline, 0 B-frames, 1s IDR)
  - `RangedHttpMediaDataSource`: `512 KiB` chunks, `12`-chunk LRU cache, **2-chunk async `ExecutorService` lookahead prefetcher**
  - `streamUrlToPcmSocket` & `streamAvUrlsToSockets` (zero full-file disk download)
- **Built-in Call Guard & DJ (`plugins/callmusic.py`):**
  - Commands: `!call help|status|tracks|dial|playcall|vcall|play|queue|skip|pause|resume|stop|answer|decline|hangup|rec|vrec|react|history`
  - Incoming policies: `mode <off|music|reject|vip>`, `vmode <same|music|reject|off>`, `vip`, `block`, `delay`, `loop`, `replymsg`, plus `CallsScreen` UI controls
- **Built-in YouTube Live DJ (`plugins/ytcall.py`):**
  - Pure InnerTube client cascade (`VISIONOS`, `ANDROID_VR`, `TVHTML5`, `WEB_REMIX`, `IOS`) + Search (`!yt search`)
  - Adaptive `360p` (`itag 134`) / `480p` (`itag 135`) `avc1` + `130kbps` M4A (`itag 140`) or muxed `18` fallback
  - Commands: `!yt play|dial|playcall|vcall|queue|q|skip|save|info|pause|resume|stop|hangup|mode|loop|quality|vquality|buffer|maxmins|public|status|clearcache`
  - `CallsScreen` **YouTube Live Stream Deck** card (`Live Voice Stream` / `Live Video Stream`, `480p/360p`, `fast/normal/slow`)

---

## 5. Built-in Automation & Power Tools (To Be Native Kotlin, Zero Plugin System)
- **Anti-Delete & Anti-Edit (`plugins/antidelete.py` + `ClientDB`):**
  - Intercepts `ProtocolMessage` `REVOKE` (`type 0`) & `MESSAGE_EDIT` (`type 14`), preserves `original_body` inline in chat & fires Android notifications (`2001`/`2002`)
- **Anti-ViewOnce (`plugins/antiviewonce.py` + `ClientDB`):**
  - Unwraps `viewOnceMessage`, `viewOnceMessageV2`, `viewOnceMessageV2Extension`, decrypts photo/video/voice note permanently & stores path inline
- **AI Chat Assistant (`plugins/groqchat.py` -> upgrade to Universal AI in `WA power`):**
  - `!ai <prompt>`, `!ai reset`, `!ai model`, `!ai persona`, `!ai on/off` (per-chat auto-reply), conversation memory (`MAX_TURNS=8`, `30m` TTL), per-user cooldown & daily cap
  - **New in `WA power`:** User-configurable **Base URL + API Key + Model + Format (`auto | openai | anthropic | gemini`)**
- **Utilities (`autoreply.py`, `echo.py`, `ping.py`, `remind.py`, `stats.py`):**
  - Greeting auto-responder (`hi/hello/hey/hlo/namaste/salam`), `!echo`, `!ping`, `!help`, `!remind <10s|5m|1h> <task>` (with notification ID `2003`), `!stats` (uptime, queue depth)

---

## 6. Session Backup, Logs & OTA Updater
- **Session Backup & Restore (`common.py`):**
  - Export/Import `.zip` / `.sqlite3` session archives to external storage / `/sdcard/Download/` + Storage & Battery Exemption permission prompts
- **Activity Logs (`common.py`, `ConsoleScreen`):**
  - Persistent `wa_activity.log`, 1-tap export to file, live in-app Console viewer with auto-scroll
- **OTA Self-Updater (`core/updater.py`):**
  - Checks GitHub Releases API (`sanatani-hackers/wa-kivy-app/releases/latest`), downloads signed APK with progress bar, launches Android package installer
