# APK Generator

A small local tool that turns a website into an Android app (a fullscreen WebView), with a custom
icon, and installs it on every device connected through `adb`. It runs as a web UI in your browser.

It has no dependencies beyond Python 3 and does not use Gradle: the APK is built directly with the
Android SDK build tools (`aapt2` → `javac` → `d8` → `zipalign` → `apksigner`), which takes a few seconds.

## Requirements

- Python 3.9+
- Android SDK with `build-tools` and at least one `platforms/android-*` (Android Studio installs them
  in `~/Library/Android/sdk`; otherwise set `ANDROID_HOME`)
- JDK 17+ (found automatically through `JAVA_HOME`, `/usr/libexec/java_home`, Android Studio's
  bundled JBR, or Homebrew `openjdk@17`)
- `adb` (from SDK `platform-tools` or on `PATH`), needed only to install on devices

## Usage

```sh
./run.sh            # or: python3 -m apkgen [--port 8765] [--no-browser]
```

Then in the browser:

1. Enter the **app name**, **website URL** and **package name** (suggested from the URL).
2. Pick or drop an **icon** image, or leave it empty to get a letter icon. You can set the background,
   shape and padding. The tool renders every launcher density for you.
3. Check the **Android TV banner** (320×180, shown on the TV home screen). It is generated from the
   icon and name, or you can use your own image.
4. Click **Build APK**. The signed APK is saved in `output/` and can be downloaded from the UI.
5. Select connected devices and click **Install on selected**, or use **Build + install** to do both.

## Android TV

Every APK works on both phones/tablets and Android TV (including Google TV and Fire TV): it shows up
in the TV launcher with its banner, doesn't require a touchscreen, and the page is navigable with the
remote's D-pad (arrows move focus/scroll, OK clicks, Back goes back).

To install on a TV over the network:
1. On the TV: Settings → About → click *Build* 7× to unlock Developer options, then turn on
   **USB debugging** / **Network debugging** (the name varies by vendor).
2. In the UI, enter the TV's IP address in the **Connect** box and accept the prompt shown on the TV.
3. The TV appears in the device list; install as usual.

The website itself should be usable with a remote (focusable links/buttons). The WebView can't add
remote navigation to a site that only reacts to mouse or touch.

## App options

| Option | Effect |
| --- | --- |
| Orientation | Lock to portrait/landscape or follow the device |
| Fullscreen | Hides the status and navigation bars (immersive mode) |
| Keep screen on | The display won't sleep while the app is open |
| Open other domains in browser | Links to other hosts open in the system browser |
| Can be used as home screen | Adds a HOME intent filter so the app can act as a kiosk launcher |

The generated app has JavaScript, DOM storage, cookies, file upload (`<input type=file>`), back-button
navigation, keeps state across rotation, and allows `http://` URLs. Supports Android 5.0+ (API 21).

## Signing & updates

The first build creates a signing key in `keystore/apkgen.keystore` (password `android`). Keep this
file: Android only installs an update over an existing app if it is signed with the same key.
To update an installed app, keep the **package name** and raise the **version code**.
If the device has the app signed with a different key, tick **Uninstall first** (this clears the app's data).

## Layout

```
apkgen/
  server.py                 local HTTP server + JSON API
  builder.py                APK build pipeline
  toolchain.py              SDK / JDK / adb discovery
  adb.py                    device listing and installing
  template/MainActivity.java  WebView shell (reads its settings from generated resources)
  static/index.html         the UI
```
