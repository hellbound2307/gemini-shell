# gemini-shell

A minimal Android launcher for Gemini web, built to replace the "web-to-apk"
wrappers that ship ads, analytics, and a desktop user-agent spoof.

## Why this exists

The Google app was removed from the device as bloat, and the official Gemini
app depends on it. Two third-party wrappers were evaluated:

| | `webtoapk` template | Android Studio WebView template |
|---|---|---|
| Engine | Capacitor + `WebView` | `WebView` |
| User agent | default | spoofed to desktop `Chrome/120` |
| Bundled SDKs | AdMob, Unity Ads, OneSignal, Firebase, ML Kit, Play Billing | none |
| Permissions | camera, location, storage, DUMP | minimal |

Both render the page in `android.webkit.WebView` with a spoofed desktop user
agent, which is exactly why the mobile layout degrades. Neither shares Chrome's
cookie jar, so an existing Google session is not reused.

Chrome's own "install this site as an app" path is not available for Gemini:
`gemini.google.com` returns 404 for `/manifest.json`, and its CSP declares
`manifest-src 'none'`, which instructs the browser to refuse any manifest for
the origin. `x-frame-options: DENY` also rules out framing.

## What this does instead

- Opens `https://gemini.google.com/app` in a **Custom Tab** — Chrome's engine,
  Chrome's cookie jar, so an existing sign-in is reused with no re-authentication.
- No `WebView`, so no user-agent spoofing and no login wall.
- No third-party SDKs. The only declared permission is `INTERNET`.
- Toolbar tinted to the Gemini surface colour so it reads as an app.
- Background warm-up plus `mayLaunchUrl` prefetch to avoid a cold-start flash.
- Falls back to `ACTION_VIEW` if no Custom Tabs provider is installed.

## Build

Built by GitHub Actions without Gradle: `aapt2` for resources, `javac` for the
single activity, `d8` for dexing, `zipalign` and `apksigner` for the artifact.
See `.github/workflows/build.yml`.

## Scope

This is a shell, not a client. It does not authenticate, store, or intercept
anything; it hands the URL to Chrome and exits.