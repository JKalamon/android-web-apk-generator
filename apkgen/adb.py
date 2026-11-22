"""Thin wrapper around adb for listing devices and installing APKs."""
from __future__ import annotations

import subprocess

from .builder import ACTIVITY


def _adb(adb: str, *args: str, timeout: int = 180) -> subprocess.CompletedProcess:
    return subprocess.run([adb, *args], capture_output=True, text=True, timeout=timeout)


def devices(adb: str) -> list[dict]:
    p = _adb(adb, "devices", "-l", timeout=20)
    result = []
    for line in p.stdout.splitlines()[1:]:
        parts = line.split()
        if len(parts) < 2:
            continue
        info = {"serial": parts[0], "state": parts[1]}
        for kv in parts[2:]:
            if ":" in kv:
                k, v = kv.split(":", 1)
                info[k] = v
        info["label"] = (info.get("model") or info.get("device") or parts[0]).replace("_", " ")
        result.append(info)
    return result


def connect(adb: str, address: str) -> dict:
    """`adb connect host[:port]` — for network devices such as Android TVs."""
    address = address.strip()
    if ":" not in address:
        address += ":5555"
    p = _adb(adb, "connect", address, timeout=20)
    out = (p.stdout + p.stderr).strip()
    ok = p.returncode == 0 and ("connected to" in out) and "failed" not in out.lower()
    return {"ok": ok, "output": out}


def install(adb: str, serial: str, apk: str, package: str, uninstall_first: bool, launch: bool) -> dict:
    log = []
    if uninstall_first:
        p = _adb(adb, "-s", serial, "uninstall", package, timeout=60)
        log.append(f"uninstall: {(p.stdout + p.stderr).strip()}")
    p = _adb(adb, "-s", serial, "install", "-r", apk)
    out = (p.stdout + p.stderr).strip()
    log.append(f"install: {out}")
    ok = p.returncode == 0 and "Success" in out
    hint = None
    if not ok and "INSTALL_FAILED_UPDATE_INCOMPATIBLE" in out:
        hint = "An app with this package but a different signature is installed. Tick 'Uninstall first'."
    elif not ok and "INSTALL_FAILED_VERSION_DOWNGRADE" in out:
        hint = "Device has a newer version code. Increase the version code or tick 'Uninstall first'."
    if ok and launch:
        p = _adb(adb, "-s", serial, "shell", "am", "start", "-n", f"{package}/{ACTIVITY}", timeout=30)
        log.append(f"launch: {(p.stdout + p.stderr).strip()}")
    return {"serial": serial, "ok": ok, "log": "\n".join(log), "hint": hint}
