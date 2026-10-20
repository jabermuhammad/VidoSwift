# VidoSwift Android client (next phase)

The Android client is intentionally a separate Capacitor/React application. Its first implementation should use a mobile-specific API adapter for `mobile/api` and must not depend on `window.pywebview.api` or modify the Windows desktop `ui/` directory.

Planned structure:

```text
android-app/
  android/                 # Generated Capacitor Android project, checked in after initialization
  src/
    api/                   # Typed HTTP client for the mobile API
    platform/              # Android file/download integration
    screens/               # Mobile-specific responsive screens
    theme/                 # Copied VidoSwift colors, typography, and spacing tokens
```

The React/Vite app shell and Android platform project will be initialized in the next mobile-client phase. No Android native project is generated in this API-only phase.
