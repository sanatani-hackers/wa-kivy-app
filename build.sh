#!/usr/bin/env bash
set -e

echo "=== 1. Installing System Dependencies ==="
sudo apt-get update -y
sudo apt-get install -y git zip unzip openjdk-17-jdk python3-pip python3-setuptools \
    autoconf libtool pkg-config zlib1g-dev libncurses5-dev libncursesw5-dev \
    libtinfo5 cmake libffi-dev libssl-dev build-essential ccache libltdl-dev

export JAVA_HOME="/usr/lib/jvm/java-17-openjdk-amd64"
export PATH="$JAVA_HOME/bin:$PATH"

echo "=== 2. Installing Buildozer & Cython ==="
pip install --upgrade pip
pip install --upgrade "Cython<3.0" buildozer

echo "=== 2.5 Updating Bundled Plugins JSON ==="
python3 -c "
import os, json
p_dir = 'plugins'
bundle = {}
for f in sorted(os.listdir(p_dir)):
    if f.endswith('.py') and not f.startswith('_'):
        with open(os.path.join(p_dir, f), 'r', encoding='utf-8') as fp:
            bundle[f[:-3]] = fp.read()
with open('bundled_plugins.json', 'w', encoding='utf-8') as fp:
    json.dump(bundle, fp, indent=2)
print('bundled_plugins.json updated with', len(bundle), 'plugins')
"

echo "=== 3. Running Buildozer Android Debug ==="
buildozer -v android debug

echo "=== 4. Checking Output APK ==="
ls -la bin/

APK_FILE=$(ls bin/*.apk | head -n 1)
echo "Generated APK: $APK_FILE"

echo "=== 5. Signing APK with custom release keystore ==="
KEYSTORE="release.keystore"
if [ ! -f "$KEYSTORE" ]; then
    keytool -genkey -v -keystore "$KEYSTORE" -alias waapp -keyalg RSA -keysize 2048 -validity 10000 \
        -dname "CN=Sanatani, OU=Dev, O=Sanatani, L=Delhi, S=Delhi, C=IN" \
        -storepass android123 -keypass android123
fi

# Find apksigner and zipalign in ~/.buildozer
APKSIGNER=$(find ~/.buildozer -name apksigner 2>/dev/null | head -n 1)
ZIPALIGN=$(find ~/.buildozer -name zipalign 2>/dev/null | head -n 1)

SIGNED_APK="bin/waapp-0.1.0-arm64-v8a-signed.apk"

if [ -n "$ZIPALIGN" ] && [ -n "$APKSIGNER" ]; then
    echo "Using buildozer SDK tools for alignment and signing..."
    $ZIPALIGN -v -p 4 "$APK_FILE" "bin/temp_aligned.apk" || cp "$APK_FILE" "bin/temp_aligned.apk"
    $APKSIGNER sign --ks "$KEYSTORE" --ks-pass pass:android123 --key-pass pass:android123 \
        --ks-key-alias waapp --out "$SIGNED_APK" "bin/temp_aligned.apk"
    $APKSIGNER verify --verbose "$SIGNED_APK"
    rm -f "bin/temp_aligned.apk"
else
    echo "Using jarsigner..."
    cp "$APK_FILE" "$SIGNED_APK"
    jarsigner -verbose -sigalg SHA256withRSA -digestalg SHA-256 -keystore "$KEYSTORE" \
        -storepass android123 -keypass android123 "$SIGNED_APK" waapp
fi

echo "=== Build & Sign Complete ==="
ls -lh bin/
