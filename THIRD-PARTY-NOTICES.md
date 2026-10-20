# Third-party notices

VidoSwift includes and uses third-party software. The source code and license terms remain with their respective authors.

- React and React DOM 19 — MIT; https://github.com/facebook/react
- Vite 8 and TypeScript 5.9 — MIT; https://github.com/vitejs/vite and https://github.com/microsoft/TypeScript
- yt-dlp 2026.10.10.233141.dev0 — The Unlicense; https://github.com/yt-dlp/yt-dlp
- Node.js 24.19.0 runtime (bundled as `bin/node.exe`) — MIT license; upstream license and third-party notices are included in `bin/node-LICENSE.txt`; https://nodejs.org/
- pywebview 6.2.1 — BSD 3-Clause; https://github.com/r0x0r/pywebview
- PyInstaller 6.22.3 — GPL with the PyInstaller exception; https://pyinstaller.org/en/stable/license.html
- imageio-ffmpeg 0.6.0 wrapper — BSD 2-Clause; https://github.com/imageio/imageio-ffmpeg
- FFmpeg 7.1 Essentials binary supplied by imageio-ffmpeg — GPLv3 build (`--enable-gpl`, `--enable-version3`), with codecs and libraries shown by `ffmpeg -version`. FFmpeg's license information: https://ffmpeg.org/legal.html. FFmpeg source: https://ffmpeg.org/releases/ffmpeg-7.1.tar.xz. Windows build provenance and build information: https://www.gyan.dev/ffmpeg/builds/.
- Microsoft WebView2 Runtime — Microsoft software, installed separately with supported Windows versions or from Microsoft's official WebView2 distribution page: https://developer.microsoft.com/microsoft-edge/webview2/.

The bundled FFmpeg executable is a separate command-line program invoked by yt-dlp for stream merging and audio conversion; it is not linked into VidoSwift. The executable identifies itself as `7.1-essentials_build-www.gyan.dev` and reports GPLv3 in its license output. The original FFmpeg source archive is the same upstream 7.1 release. The upstream source repositories and build references are provided above. Confirm all corresponding source and notice obligations before redistributing modified or third-party builds.

FFmpeg source and build distributions can contain separately licensed codec libraries. Review the exact `ffmpeg -version` configuration and the upstream FFmpeg/Gyan license documentation when redistributing this build.
