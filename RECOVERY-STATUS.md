# VidoSwift recovery snapshot

This source tree was reconstructed from the local Codex session patch history together with the supplied YouTubeFix backend archive and Figma UI archive. It is a recovery copy, not a byte-for-byte copy of the deleted working directory. The recovered React UI contains the API-connected VidoSwift application source recorded in the Codex session; the standalone Figma archive by itself was only a visual prototype.

The supplied `VidoSwift.ico` is wired into the Windows executable icon configuration and the browser favicon. The pre-icon recovery copy is backed up separately as `VidoSwift-session-recovery-before-icon.zip`.

## Verification

- Python syntax compilation passed for `backend/app.py` and `backend/VidoSwift.spec`.
- Icon smoke check passed: the UI favicon is embedded as a data URI, the HTML favicon points to `VidoSwift.ico`, the PyInstaller spec references the ICO, and both ICO copies match.
- A complete build was not produced. UI dependencies are absent; the `pnpm` build attempt stopped because the sandbox denied pnpm's project symlink (`EPERM`). The recovered snapshot also does not contain bundled Node.js/FFmpeg runtime files, so it needs the build prerequisites before packaging.
