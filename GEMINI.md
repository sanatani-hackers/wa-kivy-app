# WA power (`kotlin-native` Branch) — Project Guide

> **Mission:** Build **WA power** (`kt.sanatani.wapower`) from scratch on branch `kotlin-native` as a lag-free, 60/120fps pure **Kotlin + Jetpack Compose + Material 3 + Room SQLite + Android NDK JNI + Go `libneonize.so`** WhatsApp Web MD client — with **all power automation built in natively** and **zero Python / Kivy / plugin system**.

---

## Core Architecture Decisions (Locked)

1. **App Identity & Zero Plugin Loader:**
   - **App Name:** `WA power`
   - **Application ID / Package:** `kt.sanatani.wapower` (`minSdk = 26`, `targetSdk = 34`, `arm64-v8a`)
   - Do **not** add Python, Kivy, Chaquopy, Buildozer, UDP OSC, or dynamic plugin scripting (`QuickJS`, `Lua`, `.py` plugins).
   - Every capability is a clean, first-class **built-in Kotlin module** controlled from the native Settings/Tools UI (including a **universal AI Assistant** supporting custom Base URL + API Key across **OpenAI / Anthropic / Gemini** formats).
2. **Legacy Reference Branch (`main`):**
   - The working Python/Kivy v0.3.0 app lives untouched on `main`. Never force-push or break `main`.
   - Whenever you need exact protocol mappings, InnerTube payloads, AI conversation prompts, or edge-case fixes already solved in v0.3.0, inspect them directly via `git show main:<path>`:
     - `git show main:neonize/_binder.py` — Exact C-FFI signatures & `FreeString` / `FreeBytesStruct` memory rules
     - `git show main:core/wa_client.py` — Offline sync gate (`OfflineSyncCompletedEv`), `Presence` lifecycle, protobuf event handling
     - `git show main:core/client_db.py` & `core/ghost.py` — Schema, anti-delete/anti-edit/view-once & wire Ghost Mode rules
     - `git show main:plugins/ytcall.py` — YouTube InnerTube cascade (`VISIONOS`, `ANDROID_VR`, `TVHTML5`, `WEB_REMIX`, `IOS`), `itag 134/135/140/18` adaptive quality selector
     - `git show main:plugins/callmusic.py` — Call Guard (`off | music | reject | vip`), VIP/Block lists, DJ queue
     - `git show main:plugins/groqchat.py` — Conversation memory TTL, rate caps & Hinglish system prompt reference (adapt into multi-provider OpenAI / Anthropic / Gemini client!)
     - `git show main:plugins/antidelete.py` & `plugins/antiviewonce.py` — Revoke / Edit / View-Once unlock specs
3. **Reuse Proven Native C/Go/Java Components Already on This Branch:**
   - [`go_bridge/call_bridge.go`](go_bridge/call_bridge.go) — `meowcaller` VoIP + 9s (`150`-frame) live PCM stream + 45-AU live H.264 video stream (`CallPlayAVStream`)
   - [`go_bridge/call_bridge_pcm_test.go`](go_bridge/call_bridge_pcm_test.go) — Go unit tests (`TestLivePCMSourceLargeBuffer`, `TestLiveVideoSourceFraming`)
   - [`go_bridge/build_so.sh`](go_bridge/build_so.sh) — Builds `arm64-v8a` `libneonize.so` from `krypton-byte/neonize` (`0.12.4`) + `purpshell/meowcaller` + `call_bridge.go`
   - [`media_ref/WaMediaTranscoder.java`](media_ref/WaMediaTranscoder.java) — Android hardware `MediaExtractor` + `MediaCodec` + `EGL14` GPU scaler + 512 KiB / 12-chunk / 2-lookahead `RangedHttpMediaDataSource`

---

## Visual Design Language

- **Obsidian Imperial (Luxury Hacker Dark Material 3):**
  - Background: `#000000` (secondary `#080808`) | Surface: `#101010` (`#15130F` variant, `#1D1A14` elevated) | Card: `#12110F` / Border `#30291E` (`#393124` border, `#292319` divider)
  - Primary Accent: `#D6B56D` (Champagne Gold, light `#E8D5A8`, dark `#A88950`, gradient `#D6B56D -> #A88950`, glow `#D6B56D @ 0.12`)
  - Text: `#F2EBDD` primary | `#A69B88` secondary | `#786F60` muted
  - Status: Success `#B5A078` | Warning `#E0B65C` | Error `#D95B55` | Info `#9CABB8`
  - Shapes: Card `14dp`, Button `12dp`, Input `10dp`, Border `1dp` | Typography: Inter / Roboto / JetBrains Mono aesthetics (`4dp`/`8dp`/`16dp` grid), no purple AI gradients, no emoji clutter in navigation labels.
- **Navigation (Paired):** `Chats | Contacts | Calls | Tools | Settings` (and `Connect` onboarding flow before pairing).
- **Performance Rules:**
  - `LazyColumn` with stable `key`s (`jid`, `message.id`) backed by Room `Flow<PagingData / List>`.
  - Never run JNI calls, disk I/O, or MediaCodec on `Dispatchers.Main` — use `Dispatchers.IO` / structured coroutines inside a Foreground `WaConnectionService`.

---

## Coding & Safety Standards

- **Memory safety in JNI is non-negotiable:** Every `*C.char` returned by `goneonize` (`MarkRead`, `SendPresence`, `CallPlayAVStream`, etc.) MUST be copied and freed via `FreeString(ptr)`. Every `*BytesReturn` MUST be copied and freed via `FreeBytesStruct(ptr)`. Failing to do so leaks native heap on every WhatsApp event or message!
- **Proto schemas:** Extract exact `.proto` files from `krypton-byte/neonize` tag `0.12.4` (`defproto/`, already cached on Codespace at `/home/codespace/neonize_build/neonize/` or downloadable cleanly) and compile with `protobuf-javalite` / `protobuf-kotlin-lite`.
- **Single source of truth for Ghost Mode:** `GhostConfig` in DataStore/Room controls wire behavior directly inside `WaConnectionRepository` (suppress `markRead`, force `Presence.UNAVAILABLE`, skip `sendChatPresence`, unlock View-Once inline without receipt).
- Always run unit/compile/APK verification after edits before claiming anything passes.
