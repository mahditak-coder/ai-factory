"""AF-49 — Android asset packaging regression (REAL build, no Mock)."""
import shutil
import unittest
import zipfile
from pathlib import Path

import core.config as config
from core.engines import android as andmod


class AndroidAssetPackagingTest(unittest.TestCase):
    def test_assets_are_packaged_and_verified(self):
        name = "af49-assets-unit"
        d = config.PROJECTS_DIR / name
        shutil.rmtree(d, ignore_errors=True)
        (d / "java/com/aifactory/test").mkdir(parents=True)
        (d / "res/values").mkdir(parents=True)
        (d / "assets").mkdir()
        (d / "meta.json").write_text('{"name":"%s","type":"android-app","version":"1.0.0"}' % name,
                                     encoding="utf-8")
        (d / "AndroidManifest.xml").write_text(
            '<manifest xmlns:android="http://schemas.android.com/apk/res/android" '
            'package="com.aifactory.test" android:versionCode="10000" '
            'android:versionName="1.0.0"><uses-sdk android:minSdkVersion="24" '
            'android:targetSdkVersion="34"/><application android:label="T">'
            '<activity android:name=".MainActivity"><intent-filter>'
            '<action android:name="android.intent.action.MAIN"/>'
            '<category android:name="android.intent.category.LAUNCHER"/>'
            '</intent-filter></activity></application></manifest>', encoding="utf-8")
        (d / "res/values/strings.xml").write_text(
            '<resources><string name="app_name">T</string></resources>', encoding="utf-8")
        (d / "java/com/aifactory/test/MainActivity.java").write_text(
            'package com.aifactory.test;\nimport android.app.Activity;\n'
            'import android.os.Bundle;\n'
            'public class MainActivity extends Activity {\n'
            ' @Override protected void onCreate(Bundle b){super.onCreate(b);}\n}\n',
            encoding="utf-8")
        (d / "assets/index.html").write_text('<html><body>real</body></html>',
                                             encoding="utf-8")
        (d / "assets/app.js").write_text('window.x=1;', encoding="utf-8")
        try:
            rep = andmod.AndroidEngine().build(
                name, {"kind": "apk", "sign": False}, andmod.StepLog().add)
            apk = Path(rep["artifacts"]["apk"]["path"])
            with zipfile.ZipFile(apk) as z:
                names = set(z.namelist())
            self.assertIn("assets/index.html", names)
            self.assertIn("assets/app.js", names)
            r = andmod.AndroidEngine().verify_artifacts(
                Path(rep["output_dir"]), andmod.StepLog().add)
            self.assertTrue(r.get("ok"), r)
        finally:
            shutil.rmtree(d, ignore_errors=True)
            shutil.rmtree(config.EXPORTS_DIR / "android-builds" / name,
                          ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
