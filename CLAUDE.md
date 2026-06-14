# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
npm install         # bootstrap node_modules (Electron + webtorrent)
npm start           # launch app (Electron main → loads src/index.html)
npm run dev         # same with --dev flag
npm run build       # build NSIS installer → dist/Maniac Setup x.x.x.exe
```

The Python venv `venv311/` is bundled (extraResource) and required for AI features. Diagnose with:

```bash
"venv311/Scripts/python.exe" python/bootstrap.py --check     # JSON status of every module + ML weight
```

There is **no test runner**. Smoke tests for the Python pipeline live at `python/test_analyze_smoke.py`.

After editing the renderer, **always verify the inline `<script>` parses** — one syntax error blanks the whole UI:

```bash
node -e "const fs=require('fs');const m=fs.readFileSync('src/index.html','utf8').match(/<script>([\s\S]*?)<\/script>/);try{new Function(m[1]);console.log('JS OK')}catch(e){console.log('ERR:',e.message)}"
```

`main.js`/`preload.js`/`py-bridge.js` are also pure CommonJS — `node --check <file>` works for syntax-only validation.

## Architecture

### Three-tier process model

```
┌──────────── renderer (src/index.html) ────────────┐
│  ONE 10k-line HTML file with inline <script>       │
│  Global state object  S  · re-rendered by          │
│  renderPanel() → render<Panel>() helpers           │
│  Calls IPC via window.maniac.*                     │
└────────────────────────┬───────────────────────────┘
                         │ contextBridge (preload.js)
┌────────────────────────▼───────────────────────────┐
│  main.js (Electron main process)                   │
│  ipcMain.handle('<scope>:<action>', …) surface     │
│  Spawns Python sidecars via py-bridge.js           │
│  Hidden BrowserWindow for SC anti-bot warmup       │
└────────────────────────┬───────────────────────────┘
                         │ child_process.spawn
┌────────────────────────▼───────────────────────────┐
│  python/  (venv311 sidecar)                        │
│  analyze.py · subtitles.py · organizer.py          │
│  sc_search.py · sc_domain.py · facedb.py           │
│  entitydb.py · musicid.py                          │
│  Workers emit JSONL on stdout (one event per line) │
└────────────────────────────────────────────────────┘
```

### Renderer (`src/index.html`)

Everything is a single HTML file with one `<script>` block (~9k lines of JS).

- **Global state**: a single `S` object (~line 1080) holds players, panels, settings, tags, AI history. Treat all panel state mutations as "set value, then call `renderPanel()`".
- **Render entry point**: `renderPanel()` switches on `S.panel` ('file', 'playlist', 'explore', 'ai', 'downloader', 'history', 'settings', 'webcam', 'audioin', 'subtitles', 'info') and dispatches to dedicated `render<Name>()` functions.
- **Persistence**: `saveConfig()` / `loadConfig()` (~line 9020) serialise `S` slice → `config-load`/`config-save` IPC → `%APPDATA%/maniac-player/maniac-config.json`. When you add a new persistent field, **add it to BOTH** save and load.
- **Reusable UI helpers** (defined near the top of the script): `iconBtn`, `pchipBtn`, `viewModeCycleBtn`, `elementSelector`, `mediaOptionsCtx`, `tagOptionsCtx`, `panelPlayerBtn`, `panelCloseBtn`, `renderSearchBar`, `renderPlayerCycleBtn`. New panels should compose these for visual consistency.
- **Standard panel header**: `[panelPlayerBtn (in #spMulti)] [search flex:1] [elementSelector] [viewModeCycleBtn] [hideListEye] [hideThumbs]` — applied to Playlist, Explore, Tags, AI, History.
- **Panel header injection**: the title bar `<div class="sp-head">` contains `#spMulti` (filled at panel open) + `#spTitle` + close X. `openPanel(name)` (~line 3940) decides what goes in `#spMulti` per panel.

### IPC conventions (preload.js)

- All IPC bound to `window.maniac.*` via `contextBridge`. Never reach `ipcRenderer` directly from the renderer.
- Naming pattern: `<scope>:<action>` for handlers (e.g. `subs:generate`, `download:start`, `wizard:scSearch`).
- Long-running operations stream progress as `<scope>:progress` events, and the renderer hooks `onProgress(cb)` once.
- `S._workers.<scope>` flags drive the topbar `#aiLoader` spinner so background jobs don't block the UI.

### Python sidecars

- `py-bridge.js` exposes `resolvePython()`, `runScript(name, args)`, `spawnWorker(name, args)`, `venvCheck()`, `installOptionalPackages([])`.
- Every CLI prints **one final line** of JSON `{ok, ...}` via `print(json.dumps(...))`. Long-running ones additionally emit JSONL progress events on intermediate lines.
- `bootstrap.py --check` is the single source of truth for "what's installed". Renderer's `initVenvCheck()` (~line 9460) auto-installs `mutagen`/`pyacoustid` once per 24h via `ai:install-optional`.
- ML weights (`yolov8n.pt`, `resnet18_places365.pth.tar`) live in `models/ml/` and are lazy-downloaded by `analyze.py` on first use.
- External binaries in `tools/` (`ffmpeg`, `exiftool`, `mediainfo`) are bundled as `extraResources`; `bootstrap.py probe_external_tools()` resolves them.

### StreamingCommunity flow

The site has a JS anti-bot challenge that ordinary `requests.get()` can't solve. Architecture:

1. `_resolveScDomain()` calls `python/sc_domain.py` (cached 6h) to get the current rotating domain.
2. `_scEnsureShell()` opens a **hidden Electron BrowserWindow** at that domain → waits for the JS challenge to set cookies + Inertia state (retry probe up to ~11s).
3. `_scFetchJSON(relPath)` runs `fetch()` *inside* that window via `executeJavaScript`, reusing its cookies and fingerprint.
4. Cookie header is exported to `SCWIZ_COOKIE` env so `python/sc_search.py` can also be invoked directly for downloads.

If the wizard returns nothing, the warmup probably timed out — see `_scEnsureShell` retry budget.

### Download pipeline (yt-dlp)

- `download:start` (`main.js` ~600) builds args via `_baseDownloadArgs` (UA + `--cookies-from-browser <detected>` + per-host extractor tweaks).
- Failure path: classify error via `_isExtractorError` / `_isBotOrAuthError`. Recovery cascade:
  1. force-update yt-dlp + retry
  2. cycle through other browsers (`edge`, `chrome`, `firefox`, `brave`, `opera`, `vivaldi`)
  3. `_copyChromeCookies()` to a private sqlite copy → `--cookies <path>`
  4. final attempt **without cookies** (many videos still work)
- Renderer-side: each download item is a row in `S.downloads`; `_dlRenderList` lays them out in a fixed grid (`thumb 56 | info 1fr | size 90 | actions 124`). Status mapping in `STATUS_LABEL`. Retry with `resumeId` reuses the same row + `--continue`.

### Config & data paths

| Path | Content |
|------|---------|
| `userData/maniac-config.json` | UI state, settings, tags, recents, downloads, favorites |
| `userData/maniac-file-tags.json` | per-file tags keyed by absolute path |
| `userData/maniac-analysis-history.json` | analyzed elements (faces/places/objects) |
| `userData/faces.db` (SQLite) | face/entity embeddings for `match`/`add` |
| `userData/thumbnails/` | per-file thumbnail cache (sha1 of path) |
| `userData/playlists/` | exported M3U/JSON |
| `userData/unspokentitles_Faces.txt` | name blocklist for face naming |

### Light mode

Override-driven: every dark surface needs an explicit `[data-theme="light"] .selector { … }` rule. When introducing a new component, add **both** themes in the same CSS block. The Streamline icon set (`<img class="mi">`) is dark-by-default — light mode applies `filter:invert()` globally; pure SVG with `fill="currentColor"` follows text colour automatically.

### Webcam pitfall

`refreshWebcamDevices()` must NOT call `getUserMedia({audio:true, video:true})` at boot: if any webcam is locked by another app you get `MFT hardware 0xC00D3704` before the UI mounts. Permissions are now requested lazily, only when the user opens Settings → Webcam or starts a stream.

## Repository layout (top-level)

```
main.js                 Electron main process (IPC + spawn + window mgmt)
preload.js              contextBridge → window.maniac.*
py-bridge.js            venv resolver + script runner
src/index.html          renderer (UI + state + render functions, all inline)
src/locales/<lang>/     i18n JSON (it / en / es / de / ja / zh)
src/worklets/lufs-processor.js  AudioWorklet for LUFS metering
python/                 sidecar workers (analyze, subtitles, sc_search, organizer, …)
models/ml/              yolov8n.pt, places365 weights, argos lang packs
tools/                  bundled exiftool, mediainfo, ffmpeg
venv311/                Python 3.11 venv (extraResource at build)
StreamingCommunity_api-main/  vendored downloader used by sc_search.py download cmd
docs/SUBTITLES_AUTOGEN.md     subtitle pipeline reference
AI_ROADMAP.md           face/scene roadmap
```
