#!/data/data/com.termux/files/usr/bin/bash
# android-build-minimal.sh — خط تولید APK حداقلی و واقعی روی Termux
# زنجیره: aapt2 → javac (vs android.jar) → d8 → بسته‌بندی → zipalign-mini → apksigner
# بدون Gradle و بدون Android SDK کامل؛ android.jar از پلتفرم رسمی Google.
set -eu
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PROJ="${1:-$ROOT/projects/af02-android-test}"
OUT="$PROJ/build"
SDK_JAR="$HOME/android-sdk-termux/platforms/android-35/android.jar"
KS="$HOME/.af-keys/aifactory-release.jks"
KENV="$HOME/.af-keys/aifactory-release.env"
MIN_SDK=24
TGT_SDK=35
VNAME="1.0.0"
VCODE=1

[ -f "$SDK_JAR" ] || { echo "❌ android.jar یافت نشد: $SDK_JAR"; exit 1; }
[ -f "$KS" ] || { echo "❌ keystore یافت نشد: $KS"; exit 1; }
[ -f "$KENV" ] || { echo "❌ env کلید یافت نشد: $KENV"; exit 1; }
. "$KENV"

rm -rf "$OUT"
mkdir -p "$OUT/gen" "$OUT/res-flat" "$OUT/dex"

echo "== 1) aapt2 compile =="
for f in "$PROJ"/res/values/*.xml; do
  aapt2 compile -o "$OUT/res-flat" "$f"
done
ls "$OUT/res-flat"

echo "== 2) aapt2 link =="
aapt2 link -I "$SDK_JAR" \
  --manifest "$PROJ/AndroidManifest.xml" \
  $(for f in "$OUT"/res-flat/*.flat; do printf '%s ' "$f"; done) \
  --java "$OUT/gen" \
  --min-sdk-version $MIN_SDK --target-sdk-version $TGT_SDK \
  --version-code $VCODE --version-name $VNAME \
  -o "$OUT/unsigned-base.apk"

echo "== 3) javac =="
find "$OUT/gen" "$PROJ/java" -name '*.java' > "$OUT/sources.txt"
javac --release 11 -cp "$SDK_JAR" -d "$OUT/classes" @"$OUT/sources.txt"

echo "== 4) d8 =="
find "$OUT/classes" -name '*.class' > "$OUT/classes.txt"
d8 --min-api $MIN_SDK --output "$OUT/dex" @"$OUT/classes.txt"

echo "== 5) بسته‌بندی dex در APK =="
cp "$OUT/unsigned-base.apk" "$OUT/unsigned-nodex.apk"
cd "$OUT/dex"
zip -q -j "$OUT/unsigned-nodex.apk" classes.dex

echo "== 6) zipalign-mini =="
python3 "$ROOT/scripts/android_zipalign_mini.py" "$OUT/unsigned-nodex.apk" "$OUT/aligned.apk"

echo "== 7) apksigner sign (release) =="
apksigner sign --ks "$KS" --ks-key-alias "$KEY_ALIAS" --ks-pass "pass:$KS_PASS" \
  --key-pass "pass:$KEY_PASS" \
  --out "$OUT/app-release.apk" "$OUT/aligned.apk"

echo "== 8) verify =="
apksigner verify -v "$OUT/app-release.apk" | head -20
echo "== 9) badging =="
aapt2 dump badging "$OUT/app-release.apk" | grep -E "package|sdkVersion|targetSdk|application-label" | head -6
echo "APK: $OUT/app-release.apk"
