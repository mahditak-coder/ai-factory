#!/data/data/com.termux/files/usr/bin/bash
# android-build-aab.sh — ساخت Android App Bundle (AAB) واقعی روی Termux
# زنجیره: aapt2 --proto-format → بسته‌بندی base/ → BundleConfig.pb → تأیید با bundletool
# خروجی: .aab + APK universal امضاشدهٔ ناشی از AAB (اثبات اعتبار)
set -eu
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PROJ="${1:-$ROOT/projects/af02-android-test}"
OUT="$PROJ/build/aab"
SDK_JAR="$HOME/android-sdk-termux/platforms/android-35/android.jar"
BT="$HOME/android-sdk-termux/bundletool.jar"
AAPT2="$(command -v aapt2)"
KS="$HOME/.af-keys/aifactory-release.jks"
KENV="$HOME/.af-keys/aifactory-release.env"
MIN_SDK=24; TGT_SDK=35; VNAME="1.0.0"; VCODE=1

[ -f "$SDK_JAR" ] && [ -f "$BT" ] && [ -f "$KS" ] && [ -f "$KENV" ] || { echo "پیش‌نیاز ناقص"; exit 1; }
. "$KENV"

rm -rf "$OUT/stage" "$OUT/res-flat-proto"
mkdir -p "$OUT/stage/base/manifest" "$OUT/stage/base/dex" "$OUT/res-flat-proto"

echo "== 1) aapt2 compile (proto) =="
for f in "$PROJ"/res/values/*.xml; do aapt2 compile -o "$OUT/res-flat-proto" "$f"; done

echo "== 2) aapt2 link --proto-format =="
aapt2 link -I "$SDK_JAR" --manifest "$PROJ/AndroidManifest.xml" \
  $(for f in "$OUT"/res-flat-proto/*.flat; do printf '%s ' "$f"; done) \
  --proto-format --java "$OUT/gen" \
  --min-sdk-version $MIN_SDK --target-sdk-version $TGT_SDK \
  --version-code $VCODE --version-name $VNAME \
  -o "$OUT/base.proto.apk"

echo "== 3) بسته‌بندی ماژول base =="
cd "$OUT"
unzip -q -o base.proto.apk -d stage
mv -f stage/AndroidManifest.xml stage/base/manifest/AndroidManifest.xml
mv -f stage/resources.pb stage/base/resources.pb 2>/dev/null || true
cp "$PROJ/build/dex/classes.dex" stage/base/dex/classes.dex
python3 - <<'EOF'
# BundleConfig.pb: Bundletool{ string version=2 }
import os, zipfile
home = os.path.expanduser("~")
v = b"1.18.3"
inner = bytes([0x12, len(v)]) + v
blob = bytes([0x0A, len(inner)]) + inner
base = os.path.dirname(os.path.abspath(__file__)) if "__file__" in dir() else os.getcwd()
stage = os.path.join(os.getcwd(), "stage")
open(os.path.join(stage, "BundleConfig.pb"), "wb").write(blob)
aab = os.path.join(os.getcwd(), "app-release.aab")
with zipfile.ZipFile(aab, "w", zipfile.ZIP_DEFLATED) as z:
    for root, dirs, files in os.walk(stage):
        for f in sorted(files):
            full = os.path.join(root, f)
            z.write(full, os.path.relpath(full, stage).replace(os.sep, "/"))
print("AAB:", aab)
EOF

echo "== 4) bundletool: اعتبارسنجی عمیق + APK universal امضاشده از AAB =="
java -jar "$BT" build-apks --bundle="$OUT/app-release.aab" --output="$OUT/app-universal.apks" \
  --mode=universal --aapt2="$AAPT2" --ks="$KS" --ks-key-alias "$KEY_ALIAS" \
  --ks-pass "pass:$KS_PASS" --key-pass "pass:$KEY_PASS" --overwrite > "$OUT/bundletool.log" 2>&1 \
  || { tail -5 "$OUT/bundletool.log"; exit 1; }

python3 - <<'EOF'
import zipfile, os
apks = os.path.join(os.getcwd(), "app-universal.apks")
with zipfile.ZipFile(apks) as z:
    z.extract("universal.apk", os.getcwd())
print("universal APK از AAB:", os.path.join(os.getcwd(), "universal.apk"))
EOF

echo "== 5) تأیید امضای APK ناشی از AAB =="
apksigner verify -v "$OUT/universal.apk" | head -4
aapt2 dump badging "$OUT/universal.apk" | grep -E "^package:|application-label:"
echo "AAB: $OUT/app-release.aab"
