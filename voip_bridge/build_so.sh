#!/usr/bin/env bash
set -euo pipefail

# Build custom neonize-android-arm64.so embedding purpshell/meowcaller VoIP/Video + MLow codec
# Intended to run inside Linux x86_64 build container / GitHub Codespace with Go + Android NDK.

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
APP_DIR="$(dirname "$SCRIPT_DIR")"
BUILD_ROOT="${BUILD_ROOT:-/home/codespace/neonize_build}"
mkdir -p "$BUILD_ROOT"
cd "$BUILD_ROOT"

if [ ! -d neonize ]; then
    git clone --depth 1 --branch 0.12.4 https://github.com/krypton-byte/neonize.git
fi
if [ ! -d meowcaller ]; then
    git clone --depth 1 https://github.com/purpshell/meowcaller.git
fi

cd "$BUILD_ROOT/neonize/goneonize"
git checkout -- main.go go.mod go.sum 2>/dev/null || true

rm -rf meowcaller
cp -r "$BUILD_ROOT/meowcaller" meowcaller
rm -rf meowcaller/.git meowcaller/examples
find meowcaller -type f \( -name "*.go" -o -name "go.mod" \) -exec sed -i "s|github.com/polymorfa/hypermeow|go.mau.fi/whatsmeow|g" {} +

# Sync meowcaller module deps with neonize's exact whatsmeow revision
(
  cd meowcaller
  go mod edit -require go.mau.fi/whatsmeow@v0.0.0-20261116172437-19d61352436f
  go mod tidy
)

cp "$SCRIPT_DIR/call_bridge.go" ./call_bridge.go

# Micro-pace multi-packet video frames (especially IDR keyframes) so UDP relay doesn't drop bursts
python3 - <<'PY'
em_path = "meowcaller/engine_media.go"
with open(em_path, "r", encoding="utf-8") as f:
    src = f.read()

old_loop = """\tfor _, pkt := range packets {
\t\tif _, err := vs.ch.Send(pkt); err != nil {
\t\t\treturn
\t\t}
\t\tsent++
\t\twireBytes += len(pkt)
\t}"""

new_loop = """\tfor pktIdx, pkt := range packets {
\t\tif _, err := vs.ch.Send(pkt); err != nil {
\t\t\treturn
\t\t}
\t\tsent++
\t\twireBytes += len(pkt)
\t\tif len(packets) > 6 && pktIdx < len(packets)-1 {
\t\t\tpace := (duration / 4) / time.Duration(len(packets))
\t\t\tif pace > 1500*time.Microsecond {
\t\t\t\tpace = 1500 * time.Microsecond
\t\t\t}
\t\t\tif pace >= 200*time.Microsecond {
\t\t\t\ttime.Sleep(pace)
\t\t\t}
\t\t}
\t}"""

if new_loop not in src:
    assert old_loop in src, "expected videoSender.send loop not found in engine_media.go"
    src = src.replace(old_loop, new_loop, 1)
    with open(em_path, "w", encoding="utf-8") as f:
        f.write(src)
    print("Patched meowcaller/engine_media.go with RTP micro-pacing")
PY

# Patch goneonize main.go deterministically to hook meowcaller lifecycle
python3 - <<'PY'
with open("main.go", "r", encoding="utf-8") as f:
    src = f.read()

marker_init = "setClient(uuid, client)"
if "initCallClient(uuid, client)" not in src:
    assert marker_init in src, "missing setClient(uuid, client) in main.go"
    src = src.replace(
        marker_init,
        marker_init + "\n\tinitCallClient(uuid, client)",
        1,
    )

marker_stop = 'deleteClient(C.GoString(id))\n\t}'
if "cleanupCallClient(C.GoString(id))" not in src:
    assert marker_stop in src, "missing Stop deleteClient block in main.go"
    src = src.replace(
        marker_stop,
        'cleanupCallClient(C.GoString(id))\n\t\t' + marker_stop,
        1,
    )

with open("main.go", "w", encoding="utf-8") as f:
    f.write(src)
print("Patched main.go successfully")
PY

go mod edit -replace github.com/purpshell/meowcaller=./meowcaller
go mod edit -require github.com/purpshell/meowcaller@v0.0.0-00010101000000-000000000000
go mod tidy

# Run meowcaller unit tests (excluding examples which need CGO audio hardware)
echo "=== Running meowcaller Go unit tests ==="
(cd meowcaller && go test ./mlow/... ./rtp/... ./srtp/... ./signaling/... ./stun/... ./relay/...)

# Locate Android NDK clang compiler (API 28 matches neonize release target)
NDK_CLANG="${ANDROID_NDK_CLANG:-}"
if [ -z "$NDK_CLANG" ]; then
    NDK_CLANG="$(find /home/codespace/.buildozer /opt "$HOME" -name 'aarch64-linux-android28-clang' 2>/dev/null | head -n 1 || true)"
fi
if [ -z "$NDK_CLANG" ]; then
    NDK_CLANG="$(find /home/codespace/.buildozer /opt "$HOME" -name 'aarch64-linux-android26-clang' 2>/dev/null | head -n 1 || true)"
fi
if [ -z "$NDK_CLANG" ]; then
    echo "ERROR: Android NDK aarch64 clang not found!" >&2
    exit 1
fi
NDK_CXX="${NDK_CLANG}++"
echo "Using NDK CC=$NDK_CLANG"

export CGO_ENABLED=1
export GOOS=android
export GOARCH=arm64
export CC="$NDK_CLANG"
export CXX="$NDK_CXX"

OUT_SO="$APP_DIR/neonize/neonize-android-arm64.so"
echo "=== Building $OUT_SO ==="
go build -buildmode=c-shared -ldflags="-s -w" -o "$OUT_SO" .
rm -f "${OUT_SO%.so}.h" "$APP_DIR/neonize/neonize-android-arm64.h"
ls -lh "$OUT_SO"
nm -D "$OUT_SO" | grep -E " (T) Call|RejectCall|GetVersion" || true
