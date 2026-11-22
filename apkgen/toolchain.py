"""Locate the Android SDK, a JDK and adb on this machine."""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path


def _version_key(name: str) -> tuple:
    return tuple(int(p) if p.isdigit() else 0 for p in re.split(r"[.\-]", name))


def find_sdk() -> Path | None:
    candidates = [
        os.environ.get("ANDROID_HOME"),
        os.environ.get("ANDROID_SDK_ROOT"),
        str(Path.home() / "Library/Android/sdk"),
        str(Path.home() / "Android/Sdk"),
        "/opt/homebrew/share/android-commandlinetools",
        "/usr/local/share/android-sdk",
    ]
    for c in candidates:
        if c and (Path(c) / "build-tools").is_dir() and (Path(c) / "platforms").is_dir():
            return Path(c)
    return None


def _java_major(java_bin: Path) -> int | None:
    try:
        out = subprocess.run([str(java_bin), "-version"], capture_output=True, text=True, timeout=15)
    except Exception:
        return None
    m = re.search(r'version "(\d+)', out.stderr + out.stdout)
    return int(m.group(1)) if m and out.returncode == 0 else None


def find_java_home() -> Path | None:
    """Return a JDK home (with javac) of version >= 17."""
    candidates: list[str] = []
    if os.environ.get("JAVA_HOME"):
        candidates.append(os.environ["JAVA_HOME"])
    try:
        out = subprocess.run(["/usr/libexec/java_home", "-v", "17+"], capture_output=True, text=True, timeout=10)
        if out.returncode == 0:
            candidates.append(out.stdout.strip())
    except Exception:
        pass
    candidates += [
        "/Applications/Android Studio.app/Contents/jbr/Contents/Home",
        "/opt/homebrew/opt/openjdk@17",
        "/opt/homebrew/opt/openjdk@21",
        "/opt/homebrew/opt/openjdk",
        "/usr/local/opt/openjdk@17",
        "/usr/local/opt/openjdk",
        "/usr/lib/jvm/default-java",
    ]
    for c in candidates:
        home = Path(c)
        java, javac = home / "bin/java", home / "bin/javac"
        if java.exists() and javac.exists() and (_java_major(java) or 0) >= 17:
            return home
    return None


def find_adb(sdk: Path | None) -> str | None:
    if sdk and (sdk / "platform-tools/adb").exists():
        return str(sdk / "platform-tools/adb")
    return shutil.which("adb")


@dataclass
class Toolchain:
    sdk: Path | None = None
    build_tools: Path | None = None
    platform_jar: Path | None = None
    java_home: Path | None = None
    adb: str | None = None
    problems: list[str] = field(default_factory=list)

    @property
    def can_build(self) -> bool:
        return bool(self.build_tools and self.platform_jar and self.java_home)

    def env(self) -> dict:
        env = dict(os.environ)
        if self.java_home:
            env["JAVA_HOME"] = str(self.java_home)
            env["PATH"] = f"{self.java_home / 'bin'}{os.pathsep}{env.get('PATH', '')}"
        return env

    def tool(self, name: str) -> str:
        return str(self.build_tools / name)

    def to_dict(self) -> dict:
        return {
            "sdk": str(self.sdk) if self.sdk else None,
            "build_tools": self.build_tools.name if self.build_tools else None,
            "platform": self.platform_jar.parent.name if self.platform_jar else None,
            "java_home": str(self.java_home) if self.java_home else None,
            "adb": self.adb,
            "can_build": self.can_build,
            "problems": self.problems,
        }


def detect() -> Toolchain:
    tc = Toolchain()
    tc.sdk = find_sdk()
    if not tc.sdk:
        tc.problems.append("Android SDK not found. Install Android Studio or set ANDROID_HOME.")
    else:
        bts = sorted((p for p in (tc.sdk / "build-tools").iterdir()
                      if (p / "aapt2").exists() and (p / "d8").exists() and (p / "apksigner").exists()),
                     key=lambda p: _version_key(p.name))
        if bts:
            tc.build_tools = bts[-1]
        else:
            tc.problems.append("No usable build-tools in SDK (need aapt2, d8, apksigner).")
        plats = sorted((p for p in (tc.sdk / "platforms").iterdir() if (p / "android.jar").exists()),
                       key=lambda p: _version_key(p.name.replace("android-", "")))
        if plats:
            tc.platform_jar = plats[-1] / "android.jar"
        else:
            tc.problems.append("No Android platform (android.jar) installed in SDK.")
    tc.java_home = find_java_home()
    if not tc.java_home:
        tc.problems.append("JDK 17+ not found. Install one, e.g. `brew install openjdk@17`, or set JAVA_HOME.")
    tc.adb = find_adb(tc.sdk)
    if not tc.adb:
        tc.problems.append("adb not found (needed only for installing to devices).")
    return tc
