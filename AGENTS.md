# AGENTS.md — Workspace & Build Environment Guide

## Environments

| Machine | Capabilities | Notes |
| :--- | :--- | :--- |
| **Termux (Local, `arm64`)** | Git, `python3`, `javac` 21, `gh` CLI | Coding, planning, local Java/Kotlin checks if SDK present; NO `go` binary installed here |
| **GitHub Codespace (`glowing-winner-q7wxv64qww6xc9v77`)** | Go toolchain, `android-ndk-r28c`, Android SDK (`build-tools/37.0.0`, `apksigner`, `zipalign`), Java 17, Gradle | Primary build & native verification host for `libneonize.so` (`arm64-v8a`) and signed APKs |

## Git Branch Hygiene

- **`main`:** Frozen/working Python + Kivy v0.3.0 release. Do not overwrite with Kotlin code.
- **`kotlin-native`:** Active branch for the pure Kotlin + Jetpack Compose + Go JNI rewrite.
- Always verify current branch (`git branch --show-current`) before committing.

## Native Go & C/JNI Workflow

1. **`go_bridge/build_so.sh`** clones/uses `krypton-byte/neonize` (`0.12.4`) and `purpshell/meowcaller`, patches `main.go` + `engine_media.go`, runs Go unit tests (`mlow`, `rtp`, `srtp`, `signaling`, `stun`, `relay`), and outputs `arm64-v8a` `libneonize.so` (`app/src/main/jniLibs/arm64-v8a/libneonize.so`).
2. **Go Test Execution on Codespace:**
   Copy `go_bridge/call_bridge_pcm_test.go` alongside `call_bridge.go` in `/home/codespace/neonize_build/neonize/goneonize/` and run `go test -v -run TestLive .`.
3. **JNI Bridge (`app/src/main/cpp/wa_jni.cpp`):**
   - Load `libneonize.so` (`System.loadLibrary("neonize")` + `System.loadLibrary("wa_jni")` or `dlopen("libneonize.so")` / link against it in `CMakeLists.txt`).
   - Hold a global `JavaVM*` cached in `JNI_OnLoad`. Callback trampolines (`func_string`, `func_callback_bytes`, `CallRegisterCallback`) attach native Go threads via `AttachCurrentThread`, invoke a Kotlin bridge receiver with `jbyteArray` / `jstring`, and detach cleanly if attached by the Go thread (`JNI_EDETACHED`).
   - Never cache raw `JNIEnv*` across threads.

## Codespace APK Signing

- Existing keystore on Codespace (`glowing-winner-q7wxv64qww6xc9v77`):
  - File: `/workspaces/wa-kivy-app/release.keystore` (never commit `.keystore` files to git)
  - Alias: `waapp` | Store & Key password: `android123`
- Deliver signed APKs via `gh codespace cp -e -c glowing-winner-q7wxv64qww6xc9v77 remote:... /sdcard/Download/...` and verify `sha256sum` + `apksigner verify -v`.
- Always stop Codespace (`gh codespace stop -c glowing-winner-q7wxv64qww6xc9v77`) when heavy builds conclude.
