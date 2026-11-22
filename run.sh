#!/usr/bin/env bash
# Start the APK Generator UI (opens http://127.0.0.1:8765 in your browser).
cd "$(dirname "$0")" && exec python3 -m apkgen "$@"
