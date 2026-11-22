"""Build a signed WebView APK using only Android SDK build-tools (no Gradle)."""
from __future__ import annotations

import base64
import re
import shutil
import struct
import subprocess
import tempfile
import zipfile
import zlib
from dataclasses import dataclass
from pathlib import Path
from xml.sax.saxutils import escape

from .toolchain import Toolchain

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE_DIR = Path(__file__).resolve().parent / "template"
OUTPUT_DIR = ROOT / "output"
KEYSTORE = ROOT / "keystore" / "apkgen.keystore"
KEY_ALIAS = "apkgen"
KEY_PASS = "android"

ACTIVITY = "webapk.app.MainActivity"
MIN_SDK = 21
TARGET_SDK = 35

# Launcher icon densities -> pixel size
DENSITIES = {"mdpi": 48, "hdpi": 72, "xhdpi": 96, "xxhdpi": 144, "xxxhdpi": 192}
ORIENTATIONS = {"unspecified", "portrait", "landscape", "sensorPortrait", "sensorLandscape", "fullSensor"}
PACKAGE_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9_]*(\.[a-zA-Z][a-zA-Z0-9_]*)+$")


class BuildError(Exception):
    def __init__(self, message: str, log: str = ""):
        super().__init__(message)
        self.log = log


@dataclass
class AppConfig:
    name: str
    url: str
    package: str
    version_code: int = 1
    version_name: str = "1.0"
    orientation: str = "unspecified"
    fullscreen: bool = False
    keep_screen_on: bool = False
    external_links_in_browser: bool = False
    home_launcher: bool = False
    icons: dict[str, bytes] | None = None  # density -> PNG bytes
    banner: bytes | None = None  # 320x180 PNG for the Android TV home screen

    @classmethod
    def from_json(cls, d: dict) -> "AppConfig":
        def decode(data_url: str) -> bytes:
            return base64.b64decode(data_url.split(",", 1)[1] if "," in data_url else data_url)

        icons = None
        if d.get("icons"):
            icons = {k: decode(v) for k, v in d["icons"].items() if k in DENSITIES}
        return cls(
            name=str(d.get("name", "")).strip(),
            url=str(d.get("url", "")).strip(),
            package=str(d.get("package", "")).strip(),
            version_code=int(d.get("versionCode") or 1),
            version_name=str(d.get("versionName") or "1.0").strip(),
            orientation=str(d.get("orientation") or "unspecified"),
            fullscreen=bool(d.get("fullscreen")),
            keep_screen_on=bool(d.get("keepScreenOn")),
            external_links_in_browser=bool(d.get("externalLinksInBrowser")),
            home_launcher=bool(d.get("homeLauncher")),
            icons=icons,
            banner=decode(d["banner"]) if d.get("banner") else None,
        )

    def validate(self) -> None:
        if not self.name:
            raise BuildError("App name is required.")
        if not re.match(r"^https?://\S+$", self.url):
            raise BuildError("URL must start with http:// or https://")
        if not PACKAGE_RE.match(self.package):
            raise BuildError("Package name must look like com.example.app (letters, digits, underscores, at least two segments).")
        if self.package.split(".")[0] in ("java", "android") or self.package.startswith("webapk.app"):
            raise BuildError("Package name may not start with java., android. or webapk.app.")
        if not (1 <= self.version_code <= 2100000000):
            raise BuildError("Version code must be a positive integer.")
        if self.orientation not in ORIENTATIONS:
            raise BuildError(f"Unknown orientation: {self.orientation}")


def _res_string(value: str) -> str:
    """Escape a value for an Android string resource."""
    v = escape(value).replace("\\", "\\\\").replace("'", "\\'").replace('"', '\\"')
    if v[:1] in ("@", "?"):
        v = "\\" + v
    return v


def _attr(value: str) -> str:
    return escape(value, {'"': "&quot;"})


def _manifest(cfg: AppConfig) -> str:
    home = ""
    if cfg.home_launcher:
        home = """
            <intent-filter>
                <action android:name="android.intent.action.MAIN" />
                <category android:name="android.intent.category.HOME" />
                <category android:name="android.intent.category.DEFAULT" />
            </intent-filter>"""
    return f"""<?xml version="1.0" encoding="utf-8"?>
<manifest xmlns:android="http://schemas.android.com/apk/res/android"
    package="{_attr(cfg.package)}"
    android:versionCode="{cfg.version_code}"
    android:versionName="{_attr(cfg.version_name)}">

    <uses-sdk android:minSdkVersion="{MIN_SDK}" android:targetSdkVersion="{TARGET_SDK}" />
    <uses-permission android:name="android.permission.INTERNET" />
    <uses-permission android:name="android.permission.ACCESS_NETWORK_STATE" />

    <!-- Android TV: declare leanback support and don't require phone hardware -->
    <uses-feature android:name="android.software.leanback" android:required="false" />
    <uses-feature android:name="android.hardware.touchscreen" android:required="false" />
    <uses-feature android:name="android.hardware.faketouch" android:required="false" />
    <uses-feature android:name="android.hardware.screen.portrait" android:required="false" />
    <uses-feature android:name="android.hardware.screen.landscape" android:required="false" />

    <application
        android:label="@string/app_name"
        android:icon="@mipmap/ic_launcher"
        android:roundIcon="@mipmap/ic_launcher"
        android:banner="@drawable/banner"
        android:allowBackup="true"
        android:usesCleartextTraffic="true"
        android:hardwareAccelerated="true"
        android:theme="@android:style/Theme.Material.NoActionBar">
        <activity
            android:name="{ACTIVITY}"
            android:exported="true"
            android:launchMode="singleTask"
            android:screenOrientation="{cfg.orientation}"
            android:configChanges="orientation|screenSize|smallestScreenSize|screenLayout|keyboard|keyboardHidden|navigation|uiMode|density">
            <intent-filter>
                <action android:name="android.intent.action.MAIN" />
                <category android:name="android.intent.category.LAUNCHER" />
                <category android:name="android.intent.category.LEANBACK_LAUNCHER" />
            </intent-filter>{home}
        </activity>
    </application>
</manifest>
"""


def _config_xml(cfg: AppConfig) -> str:
    b = lambda v: "true" if v else "false"  # noqa: E731
    return f"""<?xml version="1.0" encoding="utf-8"?>
<resources>
    <string name="app_name">{_res_string(cfg.name)}</string>
    <string name="start_url" translatable="false">{_res_string(cfg.url)}</string>
    <bool name="fullscreen">{b(cfg.fullscreen)}</bool>
    <bool name="keep_screen_on">{b(cfg.keep_screen_on)}</bool>
    <bool name="external_links_in_browser">{b(cfg.external_links_in_browser)}</bool>
</resources>
"""


def _run(cmd: list[str], tc: Toolchain, log: list[str], cwd: Path | None = None) -> None:
    log.append("$ " + " ".join(c if " " not in c else f'"{c}"' for c in cmd))
    p = subprocess.run(cmd, capture_output=True, text=True, env=tc.env(), cwd=cwd, timeout=300)
    out = (p.stdout + p.stderr).strip()
    if out:
        log.append(out)
    if p.returncode != 0:
        raise BuildError(f"{Path(cmd[0]).name} failed (exit {p.returncode})", "\n".join(log))


def ensure_keystore(tc: Toolchain, log: list[str]) -> Path:
    if KEYSTORE.exists():
        return KEYSTORE
    KEYSTORE.parent.mkdir(parents=True, exist_ok=True)
    _run([str(tc.java_home / "bin/keytool"), "-genkeypair", "-v",
          "-keystore", str(KEYSTORE), "-storetype", "PKCS12",
          "-alias", KEY_ALIAS, "-keyalg", "RSA", "-keysize", "2048", "-validity", "10000",
          "-storepass", KEY_PASS, "-keypass", KEY_PASS,
          "-dname", "CN=APK Generator, O=APK Generator, C=US"], tc, log)
    return KEYSTORE


def _solid_png(width: int, height: int | None = None, rgb: tuple[int, int, int] = (0x3D, 0x5A, 0xFE)) -> bytes:
    """Solid-colour PNG; fallback icon/banner when the UI sent none."""
    height = height or width

    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    row = b"\x00" + bytes(rgb) * width
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(row * height, 9))
            + chunk(b"IEND", b""))


def build(cfg: AppConfig, tc: Toolchain) -> tuple[Path, str]:
    """Build and sign an APK. Returns (apk_path, log)."""
    cfg.validate()
    if not tc.can_build:
        raise BuildError("Toolchain incomplete: " + "; ".join(tc.problems))

    log: list[str] = [f"Build-tools {tc.build_tools.name}, platform {tc.platform_jar.parent.name}, JDK {tc.java_home}"]
    android_jar = str(tc.platform_jar)
    with tempfile.TemporaryDirectory(prefix="apkgen-") as tmp_s:
        tmp = Path(tmp_s)
        res = tmp / "res"
        (res / "values").mkdir(parents=True)
        (res / "values/config.xml").write_text(_config_xml(cfg), encoding="utf-8")
        for density, px in DENSITIES.items():
            d = res / f"mipmap-{density}"
            d.mkdir()
            png = (cfg.icons or {}).get(density) or _solid_png(px)
            if not png.startswith(b"\x89PNG"):
                raise BuildError(f"Icon for {density} is not a PNG image.")
            (d / "ic_launcher.png").write_bytes(png)
        (res / "drawable-xhdpi").mkdir()
        banner = cfg.banner or _solid_png(320, 180)
        if not banner.startswith(b"\x89PNG"):
            raise BuildError("TV banner is not a PNG image.")
        (res / "drawable-xhdpi/banner.png").write_bytes(banner)
        manifest = tmp / "AndroidManifest.xml"
        manifest.write_text(_manifest(cfg), encoding="utf-8")

        # 1. Resources
        compiled = tmp / "compiled.zip"
        _run([tc.tool("aapt2"), "compile", "--dir", str(res), "-o", str(compiled)], tc, log)
        base_apk = tmp / "base.apk"
        _run([tc.tool("aapt2"), "link", "-o", str(base_apk), "-I", android_jar,
              "--manifest", str(manifest), str(compiled)], tc, log)

        # 2. Code
        classes = tmp / "classes"
        classes.mkdir()
        _run([str(tc.java_home / "bin/javac"), "-nowarn", "-Xlint:none", "-source", "8", "-target", "8",
              "-bootclasspath", android_jar, "-classpath", android_jar,
              "-d", str(classes), str(TEMPLATE_DIR / "MainActivity.java")], tc, log)
        dex_dir = tmp / "dex"
        dex_dir.mkdir()
        class_files = [str(p) for p in classes.rglob("*.class")]
        _run([tc.tool("d8"), "--release", "--min-api", str(MIN_SDK), "--lib", android_jar,
              "--output", str(dex_dir), *class_files], tc, log)

        # 3. Package
        with zipfile.ZipFile(base_apk, "a", compression=zipfile.ZIP_DEFLATED) as z:
            z.write(dex_dir / "classes.dex", "classes.dex")
        aligned = tmp / "aligned.apk"
        _run([tc.tool("zipalign"), "-f", "-p", "4", str(base_apk), str(aligned)], tc, log)

        # 4. Sign
        ks = ensure_keystore(tc, log)
        OUTPUT_DIR.mkdir(exist_ok=True)
        safe_ver = re.sub(r"[^A-Za-z0-9._-]", "_", cfg.version_name)
        out = OUTPUT_DIR / f"{cfg.package}-{safe_ver}-{cfg.version_code}.apk"
        _run([tc.tool("apksigner"), "sign", "--ks", str(ks), "--ks-key-alias", KEY_ALIAS,
              "--ks-pass", f"pass:{KEY_PASS}", "--key-pass", f"pass:{KEY_PASS}",
              "--out", str(out), str(aligned)], tc, log)
        idsig = out.with_suffix(".apk.idsig")
        if idsig.exists():
            idsig.unlink()
        _run([tc.tool("apksigner"), "verify", str(out)], tc, log)

    log.append(f"OK -> {out}")
    return out, "\n".join(log)


def list_outputs() -> list[dict]:
    if not OUTPUT_DIR.exists():
        return []
    files = sorted(OUTPUT_DIR.glob("*.apk"), key=lambda p: p.stat().st_mtime, reverse=True)
    return [{"name": p.name, "size": p.stat().st_size, "mtime": p.stat().st_mtime} for p in files]


def clean_outputs() -> None:
    if OUTPUT_DIR.exists():
        shutil.rmtree(OUTPUT_DIR)
