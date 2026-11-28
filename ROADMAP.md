# WA power — Implementation Roadmap

Execute incrementally in sprints, verifying compilation and device/emulator tests at each milestone.

## Sprint 1: Android Project Scaffold (`kt.sanatani.wapower`) + Go JNI Proof of Life
- [ ] Initialize Gradle Kotlin DSL Android project (`settings.gradle.kts`, `build.gradle.kts`, `app/build.gradle.kts`, `gradle-wrapper`) targeting API 26–34, `arm64-v8a`.
- [ ] Sync `.proto` definitions from `krypton-byte/neonize` (`0.12.4` `defproto/`) and configure `protobuf-gradle-plugin` (`javalite`).
- [ ] Build `libneonize.so` via `go_bridge/build_so.sh` on Codespace into `app/src/main/jniLibs/arm64-v8a/libneonize.so`.
- [ ] Implement `app/src/main/cpp/wa_jni.cpp` + `NeonizeBridge.kt` (load `.so`, handle `FreeString`/`FreeBytesStruct`, marshall JNI <-> Go event/bytes callbacks safely via `JavaVM::AttachCurrentThread`).
- [ ] Build minimal Jetpack Compose `ConnectScreen` (8-digit pairing code + QR display) + Foreground Service and verify live WhatsApp Web login & pairing!

## Sprint 2: Room Database, Sync Gate & Wire Ghost Mode
- [ ] Implement `WaDatabase` (Room WAL) with `ChatEntity`, `MessageEntity`, `ContactEntity`, `CallLogEntity` + Reactive `Flow` DAOs.
- [ ] Port `SyncStateMachine` (`OfflineSyncCompletedEv` gate + `3.5s` fallback) and `ProcessLifecycleOwner` foreground/background `Presence.AVAILABLE` / `UNAVAILABLE`.
- [ ] Implement `HistorySyncEv`, `ContactSyncEv`, `MessageEv`, `ReceiptEv`, `PresenceEv`, `ChatPresenceEv`, `MessageDeleteForMeEv` event ingestion.
- [ ] Wire `GhostPolicy` (DataStore) inside `WaRepository`: suppress `markRead`, `sendPresence(AVAILABLE)`, `sendChatPresence`, and view-once receipts natively on wire.

## Sprint 3: 120fps Jetpack Compose Client UI
- [ ] Build Obsidian Dark Material 3 theme (`Theme.kt`) and bottom navigation (`Chats | Contacts | Calls | Tools | Settings`).
- [ ] Build `ChatsScreen` (search, `All/Unread/Groups/Archived` pills, pin/archive/mute actions, Coil avatars, unread badges).
- [ ] Build `ChatDetailScreen` (`LazyColumn` reverse thread, swipe-to-reply, reactions picker, inline Coil/ExoPlayer media viewer, Deleted/Edited/View-Once badges, manual **Mark Read** CTA when Ghost Read is ON, attachment picker).
- [ ] Build `ContactsScreen` (`getAllContacts` + `getJoinedGroups`, `isOnWhatsApp` checker, 1-tap Chat/Voice/Video).

## Sprint 4: Built-in VoIP & Live YouTube Video/Audio Streaming
- [ ] Copy `media_ref/WaMediaTranscoder.java` into `app/src/main/java/kt/sanatani/wapower/WaMediaTranscoder.java`.
- [ ] Build `YouTubeInnerTube.kt` (OkHttp client cascade + 360p/480p adaptive `itag 134/135` + `140` / muxed `18` resolver) and `CallController.kt` bridging `CallPlayPCMStream` & `CallPlayAVStream`.
- [ ] Build `CallsScreen` (Active Call controls, rich notification w/ Answer+Music & Decline, YouTube Live Video/Voice Deck, local track picker, call history).

## Sprint 5: Built-in Power Tools (Zero Plugin Overhead)
- [ ] Build `AntiDeleteHandler` + `AntiViewOnceHandler` + `CallGuardEngine` + `AiAssistantFeature` (Base URL, API Key, OpenAI / Anthropic / Gemini multi-format adapter + response auto-detector) + `AutoResponderFeature`.
- [ ] Build `ToolsScreen` (native Compose cards for configuring AI Base URL / API Key / Provider format / Model / Persona, Call Guard VIP/Block/Modes, Auto-Reply keywords, and Session `.sqlite3` Backup/Restore).
- [ ] Wire `SettingsScreen` (Ghost switches, Notification/Storage permissions, Resync Contacts/History, OTA check).

## Sprint 6: Release Verification on Codespace
- [ ] Run Go `meowcaller` + `call_bridge_pcm_test.go`, Gradle `./gradlew testDebugUnitTest assembleDebug`, `zipalign`, and `apksigner` (`release.keystore`).
- [ ] Verify `libneonize.so` + `libwa_jni.so` presence, exported symbols, and signed APK install on Android device.
