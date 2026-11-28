# WA Core Native — Technical Architecture (`kotlin-native`)

## 1. End-to-End Architecture

```
+-------------------------------------------------------------------+
|  Kotlin Jetpack Compose UI (MainActivity + NavHost)               |
|  Connect | Chats | ChatDetail | Contacts | Calls | Tools | Settings|
+-------------------------------+-----------------------------------+
                                | Room Flows + StateFlow (main-safe)
+-------------------------------v-----------------------------------+
|  WaForegroundService (START_STICKY, Connected Foreground Notif)   |
|  +-- WaEngine (SyncStateMachine, GhostGuard, OutboundPipeline)    |
|  +-- Room WAL Database (chats, messages, contacts, call_log)      |
|  +-- Built-in Feature Engines:                                    |
|  |     * YouTubeStreamDj  (OkHttp InnerTube + Exo/MediaCodec)     |
|  |     * CallGuardEngine  (Auto-answer / VIP / Block / DJ queue)  |
|  |     * AntiDeleteEngine (Inline original_body + Edit history)   |
|  |     * AntiViewOnce     (Decrypt & persist view-once inline)    |
|  |     * GroqAiEngine     (!ai / !groq auto-chat + cooldowns)     |
|  |     * AutoResponder    (!ping, greetings, reminders, stats)    |
|  +-- WaMediaTranscoder.java (512KiB Lookahead + Hardware H.264)   |
+-------------------------------+-----------------------------------+
                                | NDK JNI (wa_jni.cpp + Protobuf-Lite)
+-------------------------------v-----------------------------------+
|  libneonize.so (Go whatsmeow + krypton-byte/goneonize + meowcaller)|
|  Loopback 127.0.0.1 TCP Sockets <--- PCM 16kHz & H.264 Annex-B    |
+-------------------------------------------------------------------+
```

---

## 2. Target Android / Gradle Package Layout

Recommended structure inside `wa_app/` (`package = org.sanatani.waapp`, `minSdk = 26`, `targetSdk = 34`, `abiFilters "arm64-v8a"`):

```
app/
  build.gradle.kts
  src/main/
    AndroidManifest.xml
    cpp/
      CMakeLists.txt
      wa_jni.cpp              # dlopen/link libneonize.so, JNI <-> C FFI & callback trampolines
    java/org/sanatani/waapp/
      WaMediaTranscoder.java  # ported directly from media_ref/WaMediaTranscoder.java
      WaApp.kt                # Application, Hilt/manual DI container, Notification channels
      jni/
        NeonizeBridge.kt      # external fun declarations + Protobuf byte marshalling
        CallBridge.kt         # JSON + event SharedFlow from CallRegisterCallback
      service/
        WaForegroundService.kt # Persistent status notification, Stop/Start/Answer+Music actions
      core/
        SyncStateMachine.kt   # STOPPED -> CONNECTING -> WS_CONNECTED -> SYNCING_OFFLINE -> READY
        GhostPolicy.kt        # DataStore config + GhostGuard gates (read/presence/typing/viewOnce)
        WaRepository.kt       # Event router (MessageEv, HistorySyncEv, ContactSyncEv, ReceiptEv, PresenceEv)
        OutboundQueue.kt      # Paced Mutex/Channel sender awaiting SyncState.READY + connectivity
      db/
        WaDatabase.kt         # Room (@Database WAL mode)
        Entities.kt           # ChatEntity, MessageEntity, ContactEntity, CallLogEntity
        Daos.kt               # Flow<List<...>> queries + atomic upsert helpers
      calls/
        CallController.kt     # Call lifecycle + loopback TCP AV/PCM orchestrator
        YouTubeInnerTube.kt   # Native Kotlin OkHttp InnerTube cascade + adaptive itag 134/135/140 selector
        CallGuardEngine.kt    # Built-in CallMusic DJ policy (off | music | reject | vip)
      features/
        AntiDeleteHandler.kt  # Inline REVOKE (type 0) & MESSAGE_EDIT (type 14) preservation
        AntiViewOnceHandler.kt# Auto downloadAny into filesDir/view_once_inline without receipt
        GroqAiFeature.kt      # OkHttp streaming/completion client for !ai / !groq
        CommandRouter.kt      # Built-in command handler (!yt, !call, !ai, !ping, !stats)
      ui/
        theme/Theme.kt        # Obsidian Dark palette (#080C14 / #0F172A / #10B981)
        screens/
          ConnectScreen.kt    # Phone pairing code + ZXing/Bitmap QR scanner
          ChatsScreen.kt      # Search, All/Unread/Groups/Archived filters, Pin/Archive/Mute
          ChatDetailScreen.kt # LazyColumn reverseLayout bubbles, swipe-to-reply, media preview, Ghost Read button
          ContactsScreen.kt   # Synced contacts & groups, isOnWhatsApp checker, 1-tap Call/Chat
          CallsScreen.kt      # Active call deck, YouTube Live Video/Audio streamer, local tracks, log
          ToolsScreen.kt      # Built-in Power Controls: Groq AI config, Call Guard, Auto-Reply, Session Backup
          SettingsScreen.kt   # Ghost Mode switches, Service & Battery, Updater, About
    jniLibs/arm64-v8a/
      libneonize.so           # Built by go_bridge/build_so.sh
    proto/                    # Copied from neonize 0.12.4 defproto/ -> compiled by protobuf-gradle-plugin
```

---

## 3. Native Go FFI Contract (`libneonize.so`)

All signatures are established in `git show main:neonize/_binder.py` and `go_bridge/call_bridge.go`:
- **Lifecycle & Auth:** `Neonize(dbPath, id, logLevel, ...)`, `Stop(id)`, `IsConnected(id)`, `IsLoggedIn(id)`, `PairPhone(id, ...)`, `Logout(id)`, `GetVersion()`, `FreeString(ptr)`, `FreeBytesStruct(ptr)`.
- **Messaging & Sync:** `SendMessage`, `SendChatPresence`, `SendPresence`, `SetPassive`, `MarkRead`, `BuildReaction`, `BuildEdit`, `BuildRevoke`, `DownloadAny`, `Upload`, `GetAllContacts`, `GetJoinedGroups`, `IsOnWhatsApp`, `SubscribePresence`, `GetProfilePicture`, `PutPinned`, `PutArchived`, `PutMutedUntil`.
- **VoIP & Live AV Streaming (from `go_bridge/call_bridge.go`):**
  - `CallRegisterCallback(cb)`
  - `CallPlace(id, target, isVideo)`
  - `CallAnswer(id, callId)` / `CallDecline(id, callId)` / `CallHangup(id, callId)`
  - `CallPlayAudio` / `CallPauseAudio` / `CallResumeAudio` / `CallStopAudio`
  - `CallPlayPCMStream(id, callId, label, prebufFrames)` — 150-frame (9s) PCM loopback buffer
  - `CallPlayAV(id, callId, h264Path, audioPath, fps, loop)` — file-based AV
  - `CallPlayAVStream(id, callId, label, fps, prebufFrames)` — dual loopback ports (`audio_port` + 45-AU `video_port`)
  - `CallStopVideoStream` / `CallStartVideo` / `CallAcceptVideo` / `CallStopVideo`
  - `CallRecordAudio` / `CallRecordVideo` / `CallSendReaction` / `CallListActive`

---

## 4. Legacy v0.3.0 -> Built-in Native Feature Map

Instead of runtime plugins, implement each cleanly with UI toggles + Kotlin config persistence:

| Legacy Plugin (`git show main:plugins/...`) | Native Kotlin Module | Key Capabilities |
| :--- | :--- | :--- |
| `ytcall.py` | `YouTubeInnerTube.kt` + `CallController.kt` | Zero-download `CallPlayPCMStream` & `CallPlayAVStream` (`360p`/`480p` adaptive `itag 134/135` + `140` / muxed `18`), buffer profiles (`4/8/14` frames), search & queue |
| `callmusic.py` | `CallGuardEngine.kt` | Incoming call policies (`off`, `music`, `reject`, `vip`), VIP/blocked numbers, hold track loop, auto-delay, auto-reply |
| `groqchat.py` | `GroqAiFeature.kt` | `!ai` / `!groq` LPU chat (`llama-3.3-70b-versatile`, `llama-3.1-8b-instant`, `deepseek-r1-distill-llama-70b`), per-chat memory, system persona UI editor |
| `antidelete.py` | `AntiDeleteHandler.kt` | Stores original message inline in `messages.original_body`, flags `is_deleted` / `is_edited`, posts heads-up notification |
| `antiviewonce.py` | `AntiViewOnceHandler.kt` | Auto-decrypts `viewOnceMessage*` into `filesDir/view_once_inline` and renders permanently in `ChatDetailScreen` |
| `autoreply`, `echo`, `ping`, `remind`, `stats` | `AutoResponderFeature.kt` + `CommandRouter.kt` | Built-in lightweight triggers & WorkManager / coroutine scheduled reminders |
