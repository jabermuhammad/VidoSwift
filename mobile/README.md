# VidoSwift mobile projects

This folder contains the Android client and its FastAPI service. The desktop application at the repository root remains separate.

## Android client

`android-app/` is the Expo / React Native app copied from the Google AI Studio source supplied for this project. It targets Expo SDK 57 and uses the API configured in `android-app/src/config.ts` (default: `https://vidoswift.onrender.com`). Set `EXPO_PUBLIC_API_BASE_URL` at build time to use another service.

From PowerShell:

```powershell
cd mobile/android-app
npm.cmd ci
npm.cmd run typecheck
npm.cmd start
```

Scan the Expo QR code with a compatible Expo Go installation. To create a standalone Android APK using the included EAS preview profile, configure EAS for the project, then run `eas build --platform android --profile preview`.

The app calls `POST /v1/analyze`, streams downloads with `GET /v1/download`, and checks `GET /v1/health`. Downloadable audio-only entries are excluded because the current API download endpoint accepts video quality selections.

## API service

`api/` is the FastAPI service. See [`api/README.md`](api/README.md) for dependencies, local startup, endpoints, safety limits, and deployment notes. The Render YouTube extraction failure is not considered resolved until a deployed Analyze request returns HTTP 200 with metadata and formats.
