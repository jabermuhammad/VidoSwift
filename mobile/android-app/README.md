# VidoSwift Android app

This is the Expo React Native Android client, based on the source supplied from Google AI Studio. It targets Expo SDK 57 and calls the VideoSwift FastAPI service.

## Run in development

Install the Node.js version supported by this Expo SDK, then run these commands in PowerShell:

```powershell
cd mobile/android-app
npm.cmd ci
npm.cmd run typecheck
npm.cmd start
```

Scan the QR code using an Expo Go release compatible with SDK 57. Set the API origin with `EXPO_PUBLIC_API_BASE_URL` before starting if you are testing against a different backend. The default is `https://vidoswift.onrender.com`.

## Android APK

The included `eas.json` defines a preview profile that produces an installable APK. After configuring the Expo project and EAS account:

```powershell
eas build --platform android --profile preview
```

The build requires an EAS login and a configured EAS project ID. No APK build or production deployment is implied by these source files.

## API contract

- `GET /v1/health` checks service status.
- `POST /v1/analyze` accepts `{"url":"https://..."}` and returns metadata and formats.
- `GET /v1/download?url=...&quality=...` streams a selected video format.

The app filters audio-only formats because this download route is for video. Public YouTube extraction can still be restricted by YouTube or by the hosting network; a healthy API response alone does not verify successful analysis.
