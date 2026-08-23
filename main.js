const { app, BrowserWindow, ipcMain, dialog, Menu, shell, Tray, nativeImage, clipboard, Notification } = require('electron');

// ─── Single-instance lock + magnet protocol handler ───
// Quando l'utente clicca un magnet: nel browser, Windows lancia Maniac con il magnet
// come argv extra. Senza single-instance lock, partirebbe una seconda istanza.
const _gotInstanceLock = app.requestSingleInstanceLock();
if (!_gotInstanceLock) {
  app.quit();
} else {
  // Registriamo Maniac come default per magnet:. Se l'app non è installata,
  // Windows può richiedere accept manuale dalla "Default apps".
  try { app.setAsDefaultProtocolClient('magnet'); } catch(_){}
  try { app.setAsDefaultProtocolClient('maniac'); } catch(_){}

  // Estrae l'URL "vero" da un argomento maniac://download?url=… (se presente)
  function _parseManiacProtocol(s) {
    const m = /^maniac:\/\/(?:download)?\??(.*)$/i.exec(s || '');
    if (!m) return null;
    const params = new URLSearchParams(m[1] || '');
    return params.get('url') || params.get('u') || params.get('magnet') || null;
  }

  app.on('second-instance', (_event, argv /*, _cwd */) => {
    try {
      const maniacArg = (argv || []).find(a => /^maniac:/i.test(a || ''));
      const magnet = (argv || []).find(a => /^magnet:/i.test(a || ''));
      const httpUrl = (argv || []).find(a => /^https?:\/\//i.test(a || ''));
      if (maniacArg) {
        const inner = _parseManiacProtocol(maniacArg);
        if (inner) _handleInterceptedDownload(inner, /^magnet:/i.test(inner) ? 'magnet' : 'url');
      } else if (magnet) _handleInterceptedDownload(magnet, 'magnet');
      else if (httpUrl) _handleInterceptedDownload(httpUrl, 'url');
      // Riporta in primo piano
      if (typeof mainWindow !== 'undefined' && mainWindow && !mainWindow.isDestroyed()) {
        if (mainWindow.isMinimized()) mainWindow.restore();
        if (!mainWindow.isVisible()) mainWindow.show();
        mainWindow.focus();
      }
    } catch(_){}
  });

  // macOS-specific: open-url callback
  app.on('open-url', (event, url) => {
    event.preventDefault();
    try {
      if (/^maniac:/i.test(url)) {
        const inner = _parseManiacProtocol(url);
        if (inner) _handleInterceptedDownload(inner, /^magnet:/i.test(inner) ? 'magnet' : 'url');
      } else if (/^magnet:/i.test(url)) _handleInterceptedDownload(url, 'magnet');
      else if (/^https?:\/\//i.test(url)) _handleInterceptedDownload(url, 'url');
    } catch(_){}
  });
}

// Notifica OS + forward al renderer per autostart o banner.
// Definita *prima* del primo uso possibile (second-instance può scattare al boot).
function _handleInterceptedDownload(url, kind /* 'magnet'|'url' */) {
  try {
    if (typeof Notification !== 'undefined' && Notification.isSupported && Notification.isSupported()) {
      const n = new Notification({
        title: 'Maniac · ' + (kind === 'magnet' ? 'Magnet rilevato' : 'Video rilevato'),
        body: String(url || '').slice(0, 100),
        silent: false
      });
      n.on('click', () => {
        try {
          if (typeof mainWindow !== 'undefined' && mainWindow && !mainWindow.isDestroyed()) {
            if (mainWindow.isMinimized()) mainWindow.restore();
            mainWindow.show(); mainWindow.focus();
          }
        } catch(_){}
      });
      n.show();
    }
  } catch(_){}
  // Forward al renderer (se la finestra è pronta)
  setTimeout(() => {
    try {
      if (typeof mainWindow !== 'undefined' && mainWindow && !mainWindow.isDestroyed()) {
        mainWindow.webContents.send('download:intercepted', { url, kind });
      }
    } catch(_){}
  }, 200);
}
const path = require('path');
const fs = require('fs');
const crypto = require('crypto');
const { spawn } = require('child_process');
const pyBridge = require('./py-bridge');

Menu.setApplicationMenu(null);

const userDataPath = app.getPath('userData');
const configFile = path.join(userDataPath, 'maniac-config.json');

// ── Percorsi scrivibili a runtime ──────────────────────────────────────
// In packaged la cartella resources/ è in sola lettura: modelli, chiave
// StashDB e cache dominio SC vanno tenuti in userData (scrivibile) e
// passati ai sidecar Python via env. In dev si usano i path di progetto.
const mlModelsDir = path.join(userDataPath, 'models', 'ml');
try { fs.mkdirSync(mlModelsDir, { recursive: true }); } catch(_){}
if (app.isPackaged) process.env.MANIAC_MODELS_DIR = mlModelsDir;
process.env.STASHDB_KEY_FILE = path.join(userDataPath, 'stashdb_key.txt');
const scDataJsonFile = path.join(userDataPath, 'sc-data.json');
process.env.SC_DATA_JSON = scDataJsonFile;
try {
  if (!fs.existsSync(scDataJsonFile)) {
    const _scSeed = path.join(app.isPackaged ? process.resourcesPath : __dirname, 'StreamingCommunity_api-main', 'data.json');
    if (fs.existsSync(_scSeed)) fs.copyFileSync(_scSeed, scDataJsonFile);
  }
} catch(_){}

// Base risorse bundlate (resources/ in packaged, root in dev) + helper Python.
const _resBase = () => (app.isPackaged ? process.resourcesPath : __dirname);
const _pyInterp = () => pyBridge.resolvePython() || (process.platform === 'win32' ? 'python' : 'python3');
const _pyScript = (name) => path.join(pyBridge.resolvePyDir() || path.join(__dirname, 'python'), name);

// Scansione media asincrona e non-bloccante: usa fs.promises + withFileTypes
// (niente statSync per voce) così il processo main resta reattivo anche su
// librerie video grandi/profonde. Evita il freeze al caricamento cartelle.
const MEDIA_SCAN_MAX_DEPTH = 12;
async function scanMediaAsync(rootDir, withSize = false) {
  const out = [];
  async function walk(dir, depth) {
    if (depth > MEDIA_SCAN_MAX_DEPTH) return;
    let entries;
    try { entries = await fs.promises.readdir(dir, { withFileTypes: true }); }
    catch (e) { return; }
    for (const ent of entries) {
      const full = path.join(dir, ent.name);
      if (ent.isDirectory()) {
        await walk(full, depth + 1);
      } else if (ent.isFile()) {
        const ext = path.extname(ent.name).toLowerCase().slice(1);
        if (!ALL_MEDIA.includes(ext)) continue;
        if (withSize) {
          let size = 0;
          try { size = (await fs.promises.stat(full)).size; } catch (_) {}
          out.push({ path: full, name: ent.name, size });
        } else {
          out.push({ path: full, name: ent.name });
        }
      }
    }
  }
  await walk(rootDir, 0);
  return out;
}

try {
  const _cfgRaw = fs.existsSync(configFile) ? JSON.parse(fs.readFileSync(configFile,'utf8')) : null;
  if (_cfgRaw && _cfgRaw.hwAccel === false) {
    app.disableHardwareAcceleration();
  }
} catch(_){}

const unspokenFacesFile = path.join(userDataPath, 'unspokentitles_Faces.txt');
const analysisHistoryFile = path.join(userDataPath, 'maniac-analysis-history.json');
try {
  if (!fs.existsSync(unspokenFacesFile)) {
    fs.writeFileSync(unspokenFacesFile, '# Maniac — blocklist nomi volti (uno per riga, # per commenti)\n', 'utf8');
  }
} catch(_){}
const fileTagsFile = path.join(userDataPath, 'maniac-file-tags.json');
const facesDbFile = path.join(userDataPath, 'faces.db');
const playlistsDir = path.join(userDataPath, 'playlists');
const thumbsDir = path.join(userDataPath, 'thumbnails');
const smartLibFile = path.join(userDataPath, 'smartlib.json');
const lastSessionLogFile = path.join(userDataPath, 'last-session.log');
const currentSessionLogFile = path.join(userDataPath, 'current-session.log');
if (!fs.existsSync(playlistsDir)) fs.mkdirSync(playlistsDir, { recursive: true });
if (!fs.existsSync(thumbsDir)) fs.mkdirSync(thumbsDir, { recursive: true });

// Rotate logs on startup: current -> last
try {
  if (fs.existsSync(currentSessionLogFile)) {
    fs.copyFileSync(currentSessionLogFile, lastSessionLogFile);
  }
  fs.writeFileSync(currentSessionLogFile, '', 'utf8');
} catch(e){}

function _logErr(tag, e){
  try {
    const line = JSON.stringify({ ts: Date.now(), tag, msg: e && e.message, stack: e && e.stack ? String(e.stack).slice(0, 2000) : null }) + '\n';
    fs.appendFileSync(currentSessionLogFile, line, 'utf8');
  } catch(_){}
}
process.on('uncaughtException', (e) => _logErr('uncaughtException', e));
process.on('unhandledRejection', (e) => _logErr('unhandledRejection', e));
function _copyChromeCookies(){
  try {
    const local = process.env.LOCALAPPDATA;
    if (!local) return null;
    const candidates = [
      path.join(local, 'Google', 'Chrome', 'User Data', 'Default', 'Network', 'Cookies'),
      path.join(local, 'Google', 'Chrome', 'User Data', 'Default', 'Cookies')
    ];
    const src = candidates.find(p => { try { return fs.existsSync(p) && fs.statSync(p).size > 1024; } catch(_){ return false; } });
    if (!src) return null;
    const dst = path.join(userDataPath, 'tmp-chrome-cookies.sqlite');
    fs.copyFileSync(src, dst);
    return fs.existsSync(dst) && fs.statSync(dst).size > 1024 ? dst : null;
  } catch(_) { return null; }
}

function _wrapChildStderr(proc, tag){
  try {
    if (proc && proc.stderr && proc.stderr.on) {
      proc.stderr.on('data', (chunk) => {
        const s = chunk.toString('utf8');
        try {
          const line = JSON.stringify({ ts: Date.now(), tag: 'stderr:'+tag, msg: s.slice(0, 500) }) + '\n';
          fs.appendFileSync(currentSessionLogFile, line, 'utf8');
        } catch(_){}
      });
    }
  } catch(_){}
  return proc;
}

let mainWindow;

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1280, height: 800,
    minWidth: 900, minHeight: 550,
    icon: path.join(__dirname, 'assets', 'maniac_logo.ico'),
    frame: false, titleBarStyle: 'hidden',
    backgroundColor: '#0D0D12', show: false,
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true, nodeIntegration: false, webSecurity: false,
      webviewTag: true
    }
  });
  mainWindow.loadFile(path.join(__dirname, 'src', 'index.html'));
  mainWindow.once('ready-to-show', () => mainWindow.show());
  if (process.argv.includes('--dev')) mainWindow.webContents.openDevTools({ mode: 'detach' });
  mainWindow.on('maximize', () => mainWindow.webContents.send('window-state', 'maximized'));
  mainWindow.on('unmaximize', () => mainWindow.webContents.send('window-state', 'normal'));
  mainWindow.on('close', () => {
    // Stop torrent event emission verso una webContents in via di disposizione.
    // Webtorrent è async e continua a emettere 'download'/'upload' anche dopo close
    // della finestra → evita "Render frame was disposed".
    try {
      if (typeof _wtTorrents !== 'undefined' && _wtTorrents && _wtTorrents.size) {
        for (const [, t] of _wtTorrents) {
          try {
            t.removeAllListeners('download');
            t.removeAllListeners('upload');
            t.removeAllListeners('ready');
          } catch(_){}
        }
      }
    } catch(_){}
  });
  mainWindow.on('closed', () => { mainWindow = null; });
}

// Estensioni supportate (espanse: tutti i formati comuni + Apple ProRes + documenti)
const VIDEO_EXTS = ['mp4','mkv','avi','mov','wmv','flv','webm','m4v','mpg','mpeg','3gp','ts','vob','ogv','mts','m2ts','mxf','prores','dv','asf','rm','rmvb','f4v','divx','xvid'];
const AUDIO_EXTS = ['mp3','wav','flac','aac','ogg','wma','m4a','opus','aiff','aif','ape','alac','dsd','dsf','dff','mka','amr'];
const IMAGE_EXTS = ['jpg','jpeg','jpe','jfif','png','bmp','gif','webp','svg','tiff','tif','ico','heic','heif','raw','cr2','nef','arw','dng','psd','avif'];
const DOC_EXTS   = ['pdf'];
const ALL_MEDIA = [...VIDEO_EXTS, ...AUDIO_EXTS, ...IMAGE_EXTS, ...DOC_EXTS];

ipcMain.handle('open-file-dialog', async () => {
  return await dialog.showOpenDialog(mainWindow, {
    properties: ['openFile','multiSelections'],
    filters: [
      { name: 'Tutti i media', extensions: ALL_MEDIA },
      { name: 'Video', extensions: VIDEO_EXTS },
      { name: 'Audio', extensions: AUDIO_EXTS },
      { name: 'Immagini', extensions: IMAGE_EXTS },
      { name: 'Documenti', extensions: DOC_EXTS },
      { name: 'Tutti i file', extensions: ['*'] }
    ]
  });
});

// Dialog unificato: file O cartelle (Windows/Linux non permettono entrambi nel medesimo dialog,
// quindi mostriamo files con multi + treatPackagesAsDirectories; su macOS funziona nativamente)
ipcMain.handle('open-file-or-folder-dialog', async () => {
  const props = process.platform === 'darwin'
    ? ['openFile','openDirectory','multiSelections']
    : ['openFile','multiSelections'];
  const result = await dialog.showOpenDialog(mainWindow, {
    properties: props,
    title: 'Apri file o cartelle',
    filters: [
      { name: 'Tutti i media', extensions: ALL_MEDIA },
      { name: 'Tutti i file', extensions: ['*'] }
    ]
  });
  if (result.canceled || !result.filePaths.length) return result;
  // Per ogni path: se è cartella, scansiona e raccogli i file; se è file, tienilo
  const out = { canceled:false, items:[] };
  for (const p of result.filePaths) {
    try {
      const st = await fs.promises.stat(p);
      if (st.isDirectory()) {
        const files = await scanMediaAsync(p);
        out.items.push({ type:'folder', path:p, name:path.basename(p), files });
      } else {
        out.items.push({ type:'file', path:p, name:path.basename(p), size:st.size });
      }
    } catch(e){}
  }
  return out;
});

ipcMain.handle('open-folder-dialog', async () => {
  const result = await dialog.showOpenDialog(mainWindow, { properties: ['openDirectory'] });
  if (result.canceled || !result.filePaths.length) return result;
  const folder = result.filePaths[0];
  const files = await scanMediaAsync(folder);
  return { canceled: false, folderPath: folder, files };
});

ipcMain.handle('open-files-dialog', async () => {
  const result = await dialog.showOpenDialog(mainWindow, {
    properties: ['openFile', 'multiSelections'],
    title: 'Scegli uno o più file'
  });
  if (result.canceled || !result.filePaths || !result.filePaths.length) return { canceled: true };
  return { canceled: false, files: result.filePaths.slice() };
});

ipcMain.handle('save-playlist-dialog', async (_e, playlist) => {
  const result = await dialog.showSaveDialog(mainWindow, {
    defaultPath: path.join(playlistsDir, 'playlist.m3u'),
    filters: [{ name: 'Playlist M3U', extensions: ['m3u'] }, { name: 'JSON', extensions: ['json'] }]
  });
  if (result.canceled || !result.filePath) return { canceled: true };
  try {
    if (result.filePath.endsWith('.json')) fs.writeFileSync(result.filePath, JSON.stringify(playlist, null, 2), 'utf8');
    else {
      let c = '#EXTM3U\n';
      for (const it of playlist) c += `#EXTINF:${Math.floor(it.duration || 0)},${it.name}\n${it.path}\n`;
      fs.writeFileSync(result.filePath, c, 'utf8');
    }
    return { canceled: false, filePath: result.filePath };
  } catch (e) { return { canceled: true, error: e.message }; }
});

ipcMain.handle('import-playlist-dialog', async () => {
  const result = await dialog.showOpenDialog(mainWindow, {
    properties: ['openFile'],
    filters: [{ name: 'Playlist', extensions: ['m3u','m3u8','json','pls'] }]
  });
  if (result.canceled) return result;
  try {
    const fp = result.filePaths[0];
    const content = fs.readFileSync(fp, 'utf8');
    let items = [];
    if (fp.endsWith('.json')) items = JSON.parse(content);
    else {
      let name = '';
      for (const line of content.split(/\r?\n/)) {
        if (line.startsWith('#EXTINF')) {
          const c = line.indexOf(',');
          if (c > -1) name = line.slice(c+1).trim();
        } else if (line && !line.startsWith('#')) {
          items.push({ path: line.trim(), name: name || path.basename(line.trim()) });
          name = '';
        }
      }
    }
    return { canceled: false, items };
  } catch (e) { return { canceled: true, error: e.message }; }
});

ipcMain.handle('config-load', () => {
  try { if (fs.existsSync(configFile)) return JSON.parse(fs.readFileSync(configFile, 'utf8')); } catch (e) {}
  return {};
});
ipcMain.handle('config-save', async (_e, cfg) => {
  // Scrittura atomica e asincrona: niente blocco dell'event loop, niente file
  // corrotto se il processo termina a metà scrittura.
  try {
    const tmp = configFile + '.tmp';
    await fs.promises.writeFile(tmp, JSON.stringify(cfg, null, 2), 'utf8');
    await fs.promises.rename(tmp, configFile);
    return { success: true };
  } catch (e) { return { success: false, error: e.message }; }
});

// ═══ TAG PERSISTENTI PER-FILE (keyed by absolute path) ═══
ipcMain.handle('file-tags-load', () => {
  try { if (fs.existsSync(fileTagsFile)) return JSON.parse(fs.readFileSync(fileTagsFile, 'utf8')); } catch (e) {}
  return {};
});
ipcMain.handle('file-tags-save', async (_e, map) => {
  try {
    const tmp = fileTagsFile + '.tmp';
    await fs.promises.writeFile(tmp, JSON.stringify(map, null, 2), 'utf8');
    await fs.promises.rename(tmp, fileTagsFile);
    return { success: true };
  } catch (e) { return { success: false, error: e.message }; }
});

// ═══ Recording folder support ═══
ipcMain.handle('get-videos-path', () => {
  try { return app.getPath('videos'); } catch (e) { return app.getPath('userData'); }
});

ipcMain.handle('choose-folder-dialog', async () => {
  const result = await dialog.showOpenDialog(mainWindow, {
    properties: ['openDirectory', 'createDirectory'],
    title: 'Scegli cartella di salvataggio registrazioni'
  });
  if (result.canceled || !result.filePaths.length) return { canceled: true };
  return { canceled: false, path: result.filePaths[0] };
});

ipcMain.handle('save-recording', async (_e, { buffer, filename, folder }) => {
  try {
    const targetDir = folder && fs.existsSync(folder) ? folder : app.getPath('videos');
    if (!fs.existsSync(targetDir)) fs.mkdirSync(targetDir, { recursive: true });
    const full = path.join(targetDir, filename);
    fs.writeFileSync(full, Buffer.from(buffer));
    return { success: true, path: full };
  } catch (e) { return { success: false, error: e.message }; }
});

// Ri-codifica un file (es. il webm grezzo di MediaRecorder, che spesso è senza
// indice/durata → non-seekable e "a scatti") in un container pulito e seekable.
// Default: MP4 H.264 + AAC, framerate normalizzato, +faststart. Cancella il file
// sorgente a conversione riuscita. Se ffmpeg non è disponibile ritorna il sorgente.
ipcMain.handle('ffmpeg-convert', async (_e, { inputPath, outputFormat } = {}) => {
  try {
    if (!inputPath || !fs.existsSync(inputPath)) return { success: false, error: 'sorgente mancante' };
    const ffDir = _resolveFfmpeg();
    const ffmpeg = ffDir ? path.join(ffDir, process.platform === 'win32' ? 'ffmpeg.exe' : 'ffmpeg') : null;
    const fmt = (outputFormat || 'mp4').toLowerCase();
    if (!ffmpeg || !fs.existsSync(ffmpeg)) {
      // Nessun ffmpeg: lasciamo il file così com'è (webm grezzo).
      return { success: true, path: inputPath, converted: false, note: 'ffmpeg non disponibile' };
    }
    const dir = path.dirname(inputPath);
    const base = path.basename(inputPath, path.extname(inputPath));
    const outPath = path.join(dir, base + '.' + fmt);
    // Re-encode (non semplice remux): normalizza i timestamp irregolari di
    // MediaRecorder → niente freeze/desync. veryfast/crf20 = buon compromesso.
    const vArgs = ['-c:v', 'libx264', '-preset', 'veryfast', '-crf', '20',
                   '-pix_fmt', 'yuv420p', '-vsync', 'cfr', '-r', '30'];
    const aArgs = ['-c:a', 'aac', '-b:a', '192k'];
    const fmtArgs = (fmt === 'mp4' || fmt === 'mov') ? ['-movflags', '+faststart'] : [];
    const args = ['-y', '-fflags', '+genpts', '-i', inputPath, ...vArgs, ...aArgs, ...fmtArgs, outPath];
    const ok = await new Promise((resolve) => {
      let p;
      try { p = spawn(ffmpeg, args, { windowsHide: true }); }
      catch (err) { resolve(false); return; }
      let errTail = '';
      p.stderr.on('data', d => { errTail = (errTail + d.toString()).slice(-800); });
      p.on('error', () => resolve(false));
      p.on('close', code => resolve(code === 0));
    });
    if (!ok) return { success: true, path: inputPath, converted: false, note: 'conversione fallita, mantengo sorgente' };
    // Conversione riuscita: rimuovi il sorgente grezzo.
    if (outPath !== inputPath) { try { fs.unlinkSync(inputPath); } catch (_) {} }
    return { success: true, path: outPath, converted: true };
  } catch (e) { return { success: false, error: e.message }; }
});

ipcMain.handle('scan-folder', async (_e, folderPath) => {
  const files = await scanMediaAsync(folderPath);
  return files.sort((a, b) => a.name.localeCompare(b.name, undefined, { numeric: true, sensitivity: 'base' }));
});

ipcMain.handle('show-in-folder', (_e, filePath) => {
  try { shell.showItemInFolder(filePath); return { success: true }; }
  catch (e) { return { success: false, error: e.message }; }
});

// Native popup menu (estende fuori dalla finestra)
ipcMain.handle('show-popup-menu', async (_e, items) => {
  return await new Promise((resolve) => {
    let resolved = false;
    const template = (items || []).map((it, i) => {
      if (it.type === 'separator') return { type: 'separator' };
      return {
        label: it.label,
        type: it.checked ? 'checkbox' : 'normal',
        checked: !!it.checked,
        enabled: it.enabled !== false,
        click: () => { if (!resolved) { resolved = true; resolve({ id: it.id }); } }
      };
    });
    const menu = Menu.buildFromTemplate(template);
    menu.popup({
      window: mainWindow,
      callback: () => { if (!resolved) { resolved = true; resolve({ id: null }); } }
    });
  });
});

// ═══ VST plugin file picker (scaffold) ═══
ipcMain.handle('pick-vst-plugin', async () => {
  const r = await dialog.showOpenDialog(mainWindow, {
    properties: ['openFile','multiSelections'],
    title: 'Seleziona plugin VST',
    filters: [
      { name: 'VST Plugins', extensions: ['dll','vst3','vst','component'] },
      { name: 'Tutti i file', extensions: ['*'] }
    ]
  });
  if (r.canceled) return { canceled: true };
  return { canceled: false, paths: r.filePaths };
});

// ═══ ffmpeg / transcodifica on-demand ═══════════════════════════════
// Chromium (<video>) decodifica solo un set ristretto di codec: H.264/VP8/
// VP9/AV1 video + AAC/MP3/Opus/Vorbis/FLAC audio. File MKV o con HEVC/AC3/
// DTS falliscono con MEDIA_ELEMENT_ERROR pur essendo validi. Qui usiamo
// ffmpeg bundled per remuxare (stream copy, veloce) o transcodificare verso
// un MP4 riproducibile, con cache su userData per non ripetere il lavoro.
function _ffmpegBin() {
  const exe = process.platform === 'win32' ? 'ffmpeg.exe' : 'ffmpeg';
  const bundled = path.join(_resBase(), 'tools', 'ffmpeg', 'bin', exe);
  return fs.existsSync(bundled) ? bundled : 'ffmpeg';
}
const _transcodeDir = path.join(userDataPath, 'transcode');
try { fs.mkdirSync(_transcodeDir, { recursive: true }); } catch (_) {}
const _transcodeJobs = new Map();
const SUPPORTED_V = new Set(['h264', 'vp8', 'vp9', 'av1']);
const SUPPORTED_A = new Set(['aac', 'mp3', 'opus', 'vorbis', 'flac']);

// Rileva codec video/audio e durata via `ffmpeg -i` (niente ffprobe da bundlare).
function _probeMedia(srcPath) {
  return new Promise((resolve) => {
    let proc;
    try { proc = spawn(_ffmpegBin(), ['-hide_banner', '-i', srcPath], { windowsHide: true }); }
    catch (e) { return resolve(null); }
    let err = '';
    proc.stderr.on('data', d => { err += d.toString(); if (err.length > 20000) err = err.slice(0, 20000); });
    proc.on('error', () => resolve(null));
    proc.on('close', () => {
      const out = { vcodec: null, acodec: null, dur: 0 };
      const vm = err.match(/Stream #\d+:\d+[^\n]*?:\s*Video:\s*([a-z0-9_]+)/i);
      const am = err.match(/Stream #\d+:\d+[^\n]*?:\s*Audio:\s*([a-z0-9_]+)/i);
      const dm = err.match(/Duration:\s*(\d+):(\d+):(\d+)\.(\d+)/);
      if (vm) out.vcodec = vm[1].toLowerCase();
      if (am) out.acodec = am[1].toLowerCase();
      if (dm) out.dur = (+dm[1]) * 3600 + (+dm[2]) * 60 + (+dm[3]) + (+('0.' + dm[4]));
      resolve(out);
    });
  });
}

ipcMain.handle('media:transcode-cancel', (_e, id) => {
  const proc = _transcodeJobs.get(id);
  if (proc) { try { _killProcessTree(proc); } catch (_) {} _transcodeJobs.delete(id); return { ok: true }; }
  return { ok: false };
});

ipcMain.handle('media:transcode', async (_e, { id, srcPath } = {}) => {
  if (!srcPath) return { ok: false, error: 'percorso mancante' };
  let st;
  try { st = await fs.promises.stat(srcPath); } catch (e) { return { ok: false, error: 'file non trovato' }; }

  // Cache per (path + mtime + size): riusa il file già convertito.
  const key = crypto.createHash('sha1')
    .update(srcPath + '|' + st.mtimeMs + '|' + st.size).digest('hex').slice(0, 16);
  const outPath = path.join(_transcodeDir, key + '.mp4');
  if (fs.existsSync(outPath)) {
    try { if ((await fs.promises.stat(outPath)).size > 0) return { ok: true, outPath, mode: 'cache' }; } catch (_) {}
  }

  const info = await _probeMedia(srcPath);
  let vcodec = 'libx264', acodec = 'aac', dur = 0;
  if (info) {
    dur = info.dur || 0;
    if (info.vcodec) vcodec = SUPPORTED_V.has(info.vcodec) ? 'copy' : 'libx264';
    else vcodec = null; // niente video (es. solo audio)
    if (info.acodec) acodec = SUPPORTED_A.has(info.acodec) ? 'copy' : 'aac';
    else acodec = null;
  }

  const args = ['-y', '-i', srcPath];
  if (vcodec) { args.push('-c:v', vcodec); if (vcodec === 'libx264') args.push('-preset', 'veryfast', '-crf', '23', '-pix_fmt', 'yuv420p'); }
  if (acodec) { args.push('-c:a', acodec); if (acodec === 'aac') args.push('-b:a', '192k'); }
  args.push('-movflags', '+faststart', '-max_muxing_queue_size', '1024', outPath);

  const tmpOut = outPath + '.part';
  args[args.length - 1] = tmpOut;

  return await new Promise((resolve) => {
    let proc;
    try { proc = spawn(_ffmpegBin(), args, { windowsHide: true }); }
    catch (e) { return resolve({ ok: false, error: 'avvio ffmpeg: ' + e.message }); }
    _transcodeJobs.set(id, proc);
    let err = '';
    proc.stderr.on('data', d => {
      const s = d.toString(); err += s; if (err.length > 8000) err = err.slice(-8000);
      const m = s.match(/time=(\d+):(\d+):(\d+)\.(\d+)/);
      if (m && dur > 0) {
        const t = (+m[1]) * 3600 + (+m[2]) * 60 + (+m[3]) + (+('0.' + m[4]));
        const pct = Math.max(0, Math.min(99, Math.round((t / dur) * 100)));
        try { mainWindow && mainWindow.webContents.send('media:transcode-progress', { id, pct }); } catch (_) {}
      }
    });
    proc.on('error', e => { _transcodeJobs.delete(id); resolve({ ok: false, error: e.message }); });
    proc.on('close', code => {
      _transcodeJobs.delete(id);
      if (code === 0) {
        try { fs.renameSync(tmpOut, outPath); } catch (_) {}
        resolve({ ok: true, outPath, mode: (vcodec === 'copy' && acodec !== 'libx264') ? 'remux' : 'transcode' });
      } else {
        try { fs.existsSync(tmpOut) && fs.unlinkSync(tmpOut); } catch (_) {}
        resolve({ ok: false, error: (err || '').split('\n').filter(Boolean).slice(-1)[0] || ('ffmpeg exit ' + code) });
      }
    });
  });
});

// ═══ Thumbnail cache persistente ═══
function _thumbHash(p){ return crypto.createHash('sha1').update(String(p||'')).digest('hex'); }
ipcMain.handle('thumb-get', async (_e, filePath) => {
  // I/O asincrono: la cache thumbnail viene letta spesso in burst durante il
  // render dei pannelli; niente readFileSync per non bloccare il main.
  try {
    if (!filePath) return { found: false };
    const st = await fs.promises.stat(filePath);
    const h = _thumbHash(filePath);
    const metaFile = path.join(thumbsDir, h + '.json');
    const imgFile = path.join(thumbsDir, h + '.jpg');
    try {
      const meta = JSON.parse(await fs.promises.readFile(metaFile, 'utf8'));
      if (meta.mtimeMs === st.mtimeMs) {
        const buf = await fs.promises.readFile(imgFile);
        return { found: true, dataUrl: 'data:image/jpeg;base64,' + buf.toString('base64') };
      }
    } catch (_) {}
  } catch(e) {}
  return { found: false };
});
ipcMain.handle('thumb-save', async (_e, { filePath, dataUrl }) => {
  try {
    if (!filePath || !dataUrl) return { success:false };
    const m = /^data:image\/[a-z]+;base64,(.+)$/.exec(dataUrl);
    if (!m) return { success:false, error:'invalid dataUrl' };
    const st = fs.statSync(filePath);
    const h = _thumbHash(filePath);
    const imgFile = path.join(thumbsDir, h + '.jpg');
    const metaFile = path.join(thumbsDir, h + '.json');
    fs.writeFileSync(imgFile, Buffer.from(m[1], 'base64'));
    fs.writeFileSync(metaFile, JSON.stringify({ path:filePath, mtimeMs:st.mtimeMs }), 'utf8');
    return { success:true };
  } catch(e) { return { success:false, error:e.message }; }
});

// ═══ Blocklist nomi volti ═══
ipcMain.handle('unspoken-faces-load', () => {
  try {
    const txt = fs.existsSync(unspokenFacesFile) ? fs.readFileSync(unspokenFacesFile, 'utf8') : '';
    const lines = txt.split(/\r?\n/).map(s => s.trim()).filter(s => s && !s.startsWith('#'));
    return { ok:true, names: lines, raw: txt };
  } catch(e) { return { ok:false, error: e.message, names: [], raw: '' }; }
});

// ═══ Analysis history (elementi analizzati) ═══
ipcMain.handle('analysis:load', () => {
  try {
    if (!fs.existsSync(analysisHistoryFile)) return { elements: [] };
    const txt = fs.readFileSync(analysisHistoryFile, 'utf8');
    const d = JSON.parse(txt);
    return { elements: Array.isArray(d.elements) ? d.elements : [] };
  } catch(e) { return { elements: [] }; }
});
ipcMain.handle('analysis:save', (_e, data) => {
  try {
    const payload = { elements: Array.isArray(data && data.elements) ? data.elements : [] };
    fs.writeFileSync(analysisHistoryFile, JSON.stringify(payload, null, 2), 'utf8');
    return { success: true };
  } catch(e) { return { success: false, error: e.message }; }
});

// ═══ Session logs ═══
ipcMain.handle('log-append', (_e, entry) => {
  try {
    const line = JSON.stringify(entry) + '\n';
    fs.appendFileSync(currentSessionLogFile, line, 'utf8');
    return { success:true };
  } catch(e) { return { success:false }; }
});
ipcMain.handle('log-load-last', () => {
  try {
    if (!fs.existsSync(lastSessionLogFile)) return { entries: [] };
    const txt = fs.readFileSync(lastSessionLogFile, 'utf8');
    const entries = txt.split(/\r?\n/).filter(Boolean).map(l => { try { return JSON.parse(l); } catch(e){ return { msg:l }; } });
    return { entries };
  } catch(e) { return { entries: [] }; }
});

// ═══ COOKIES / BROWSER DETECTION ═══
ipcMain.handle('cookies:listBrowsers', async () => {
  // Forza un refresh della cache browsers e ritorna la lista
  _browserCache = { ts: 0, list: null };
  _detectActiveBrowser();
  return { browsers: _browserCache.list || [], detected: (_browserCache.list||[])[0] || null };
});

// ═══ DOWNLOAD (yt-dlp / VibraVid) ═══
const _dlProcs = new Map();
const _ytDlpDir = path.join(userDataPath, 'bin');
if (!fs.existsSync(_ytDlpDir)) fs.mkdirSync(_ytDlpDir, { recursive: true });
const _ytDlpLocal = path.join(_ytDlpDir, process.platform === 'win32' ? 'yt-dlp.exe' : 'yt-dlp');
const _ytDlpMeta  = path.join(_ytDlpDir, 'yt-dlp.meta.json');
const _UPDATE_INTERVAL_MS = 72 * 60 * 60 * 1000; // 72h
function _metaRead(){ try { return JSON.parse(fs.readFileSync(_ytDlpMeta,'utf8')); } catch(e){ return {}; } }
function _metaWrite(m){ try { fs.writeFileSync(_ytDlpMeta, JSON.stringify(m||{}), 'utf8'); } catch(e){} }
function _metaClearTs(){ const m = _metaRead(); delete m.lastUpdate; _metaWrite(m); }

function _httpsDownload(url, dest) {
  const https = require('https');
  return new Promise((resolve, reject) => {
    const tryGet = (u, n=0) => {
      https.get(u, { headers: { 'User-Agent': 'Maniac/1.0' } }, res => {
        if ([301,302,303,307,308].includes(res.statusCode) && res.headers.location && n < 6) {
          res.resume(); return tryGet(res.headers.location, n+1);
        }
        if (res.statusCode !== 200) { res.resume(); return reject(new Error('HTTP '+res.statusCode+' @ '+u)); }
        const tmp = dest + '.part';
        const f = fs.createWriteStream(tmp);
        res.pipe(f);
        f.on('finish', () => f.close(err => {
          if (err) return reject(err);
          try { fs.renameSync(tmp, dest); } catch(e) { return reject(e); }
          resolve(dest);
        }));
        f.on('error', e => { try{fs.unlinkSync(tmp);}catch(_){} reject(e); });
      }).on('error', reject);
    };
    tryGet(url);
  });
}
function _checkBin(bin) {
  return new Promise(resolve => {
    try {
      const p = spawn(bin, ['--version'], { windowsHide: true });
      let ok = false;
      p.on('error', () => resolve(false));
      p.on('close', c => resolve(c === 0 || ok));
      p.stdout.on('data', () => { ok = true; });
    } catch(e) { resolve(false); }
  });
}
function _ytDlpDownloadUrl(){
  return process.platform === 'win32'
    ? 'https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp.exe'
    : (process.platform === 'darwin'
        ? 'https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp_macos'
        : 'https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp');
}
async function _forceDownloadYtDlp(progressCb){
  progressCb && progressCb('Aggiorno yt-dlp…');
  try { if (fs.existsSync(_ytDlpLocal)) fs.unlinkSync(_ytDlpLocal); } catch(e){}
  await _httpsDownload(_ytDlpDownloadUrl(), _ytDlpLocal);
  if (process.platform !== 'win32') { try { fs.chmodSync(_ytDlpLocal, 0o755); } catch(e){} }
  _metaWrite({ lastUpdate: Date.now() });
  return _ytDlpLocal;
}
function _selfUpdate(bin){
  return new Promise(resolve => {
    try {
      const p = spawn(bin, ['-U'], { windowsHide: true });
      let ok = false;
      p.stdout.on('data', d => { if (/updated|up to date|latest/i.test(d.toString())) ok = true; });
      p.on('error', () => resolve(false));
      p.on('close', c => resolve(c === 0 && ok));
    } catch(e) { resolve(false); }
  });
}
// Detect browser installato per leggerne i cookies via yt-dlp.
// Cache 5min per non risondare il filesystem ogni download.
let _browserCache = { ts: 0, list: null };
function _detectActiveBrowser(prefer) {
  const now = Date.now();
  if (_browserCache.list && (now - _browserCache.ts) < 5*60*1000) {
    if (prefer && _browserCache.list.includes(prefer)) return prefer;
    return _browserCache.list[0] || null;
  }
  const local = process.env.LOCALAPPDATA || '';
  const roaming = process.env.APPDATA || '';
  const probes = [
    ['chrome',  path.join(local, 'Google', 'Chrome', 'User Data')],
    ['edge',    path.join(local, 'Microsoft', 'Edge', 'User Data')],
    ['brave',   path.join(local, 'BraveSoftware', 'Brave-Browser', 'User Data')],
    ['firefox', path.join(roaming, 'Mozilla', 'Firefox', 'Profiles')],
    ['opera',   path.join(roaming, 'Opera Software', 'Opera Stable')],
    ['vivaldi', path.join(local, 'Vivaldi', 'User Data')],
  ];
  const list = probes.filter(([,p]) => { try { return p && fs.existsSync(p); } catch(_) { return false; } }).map(([n]) => n);
  _browserCache = { ts: now, list };
  if (prefer && list.includes(prefer)) return prefer;
  return list[0] || null;
}
// User-Agent moderno (aggiornare ogni 6 mesi).
const _DEFAULT_UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36';
// Args base sempre presenti: cookies, UA, per-host extractor tweaks.
// Multi-segment download stile uTorrent/FDM: --concurrent-fragments 4 spezza
// HLS/DASH in 4 frammenti paralleli (3-4× più veloce su connessioni ad alta banda).
function _baseDownloadArgs(url, prefBrowser) {
  const out = ['--user-agent', _DEFAULT_UA, '--no-check-certificates',
               '--concurrent-fragments', '4',
               '--retries', '5', '--fragment-retries', '8',
               '--retry-sleep', '2'];
  const br = _detectActiveBrowser(prefBrowser);
  if (br) out.push('--cookies-from-browser', br);
  try {
    const u = new URL(url);
    const host = (u.hostname || '').toLowerCase();
    if (/(^|\.)youtube\.com$|(^|\.)youtu\.be$/.test(host)) {
      // Fallback android client aggira gating "Sign in to confirm".
      out.push('--extractor-args', 'youtube:player_client=web_safari,android,ios');
    } else if (/(^|\.)x\.com$|(^|\.)twitter\.com$/.test(host)) {
      out.push('--extractor-args', 'twitter:api=syndication');
    } else if (/instagram\.com/.test(host)) {
      out.push('--extractor-args', 'instagram:include_stories=false');
    }
  } catch(_) {}
  return out;
}
async function _resolveYtDlp(vibraVidPath, progressCb) {
  const candidates = [];
  if (vibraVidPath) {
    candidates.push(path.join(vibraVidPath, 'yt-dlp.exe'));
    candidates.push(path.join(vibraVidPath, 'yt-dlp'));
    candidates.push(path.join(vibraVidPath, 'bin', 'yt-dlp.exe'));
  }
  candidates.push(_ytDlpLocal);
  candidates.push('C:\\Users\\markt\\VibraVid\\yt-dlp.exe');
  let found = null;
  // Skippa esplicitamente i binari arm64 su sistemi non-arm.
  const isArmHost = /arm/i.test(process.arch);
  for (const c of candidates) {
    try {
      if (!fs.existsSync(c)) continue;
      if (!isArmHost && /[_-]arm64/i.test(c)) continue;
      found = c; break;
    } catch(e){}
  }
  if (!found && await _checkBin('yt-dlp')) return 'yt-dlp';
  if (found) {
    // Auto-update check (only for our managed local binary)
    if (found === _ytDlpLocal) {
      const meta = _metaRead();
      const age = Date.now() - (meta.lastUpdate || 0);
      if (age > _UPDATE_INTERVAL_MS) {
        // Async self-update; don't block
        (async () => {
          const ok = await _selfUpdate(_ytDlpLocal);
          if (ok) _metaWrite({ lastUpdate: Date.now() });
          else { try { await _forceDownloadYtDlp(progressCb); } catch(e){} }
        })();
      }
    }
    return found;
  }
  // Auto-download yt-dlp
  progressCb && progressCb('Scarico yt-dlp…');
  await _httpsDownload(_ytDlpDownloadUrl(), _ytDlpLocal);
  if (process.platform !== 'win32') { try { fs.chmodSync(_ytDlpLocal, 0o755); } catch(e){} }
  _metaWrite({ lastUpdate: Date.now() });
  return _ytDlpLocal;
}
function _isExtractorError(msg){
  if (!msg) return false;
  return /Unable to extract|This website is not supported|generic extractor|Unsupported URL/i.test(msg);
}
function _isBotOrAuthError(msg){
  if (!msg) return false;
  return /Sign in to confirm|not a bot|confirm you.re not|cookies?|cookie database|Could not copy|login required|account|age.restricted|private video|database.*locked|Permission denied/i.test(msg);
}
// Errore specifico: il DB cookie del browser è LOCKATO (browser aperto). In questo
// caso non ha senso provare gli altri browser (anche loro avrebbero lo stesso lock):
// si va direttamente a un retry SENZA cookie.
function _isCookieLockError(msg){
  if (!msg) return false;
  return /Could not copy.*cookie|cookies?.*locked|database.*locked|Permission denied/i.test(msg);
}
// Kill robusto cross-platform. Su Windows `child.kill()` invia un segnale che
// yt-dlp non gestisce → il processo continua. taskkill /F /T uccide anche i
// processi figli (es. ffmpeg invocato per il merge).
function _killProcessTree(p) {
  if (!p || !p.pid) return false;
  try {
    if (process.platform === 'win32') {
      require('child_process').execFile('taskkill', ['/PID', String(p.pid), '/T', '/F'], () => {});
      return true;
    }
    p.kill('SIGTERM');
    setTimeout(() => { try { p.kill('SIGKILL'); } catch(_){} }, 2000);
    return true;
  } catch(_) { return false; }
}
// Errore "Requested format is not available": il selector `bv*+ba` non matcha
// perché il sito serve un singolo file monolitico. Fallback: ritentare con -f best.
function _isFormatError(msg){
  if (!msg) return false;
  return /Requested format is not available|No video formats found/i.test(msg);
}
// Host i cui titoli yt-dlp estrae spesso come ID numerico (es. eporner: "12817912 720p").
// Per questi siti pre-fetchiamo la pagina HTML ed estraiamo og:title / <title>.
const _TITLE_HOSTS = /(eporner\.com|pornhub\.com|xvideos\.com|xnxx\.com|redtube\.com|youporn\.com|tube8\.com|spankbang\.com)/i;
function _looksLikeNumericTitle(s) {
  if (!s) return true;
  const t = String(s).trim();
  if (!t) return true;
  // "12817912 720p", "10392139 1080p av1", solo numeri/risoluzione/codec
  return /^\d{4,}(\s+\d{3,4}p?)?(\s+(av1|avc|hevc|h264|x264|x265))?$/i.test(t);
}
function _fetchPageTitle(url) {
  return new Promise(resolve => {
    let done = false;
    const finish = (v) => { if (!done) { done = true; resolve(v); } };
    const to = setTimeout(() => finish(null), 4000);
    try {
      const lib = url.startsWith('http://') ? require('http') : require('https');
      let _origin = '';
      try { const _u = new URL(url); _origin = _u.origin + '/'; } catch(_) {}
      const req = lib.get(url, {
        headers: {
          'User-Agent': _DEFAULT_UA,
          'Accept': 'text/html,application/xhtml+xml',
          'Accept-Language': 'en-US,en;q=0.9',
          // Cookie age-gate comuni: molti tube redirigono/oscurano og:title finché
          // l'età non è confermata. Inviamo i valori più diffusi così la pagina
          // restituisce il titolo reale invece dell'ID numerico.
          'Cookie': 'age_verified=1; ageVerified=1; age_gate=1; platform=pc',
          ...(_origin ? { 'Referer': _origin } : {})
        }
      }, res => {
        if ([301,302,303,307,308].includes(res.statusCode) && res.headers.location) {
          res.resume(); clearTimeout(to);
          return _fetchPageTitle(res.headers.location).then(v => finish(v));
        }
        if (res.statusCode !== 200) { res.resume(); clearTimeout(to); return finish(null); }
        let buf = ''; res.setEncoding('utf8');
        res.on('data', c => { buf += c; if (buf.length > 200_000) res.destroy(); });
        res.on('end', () => {
          clearTimeout(to);
          let s = null;
          const og = buf.match(/<meta[^>]+property=["']og:title["'][^>]+content=["']([^"']+)["']/i);
          if (og) s = og[1];
          if (!s) {
            const tw = buf.match(/<meta[^>]+name=["']twitter:title["'][^>]+content=["']([^"']+)["']/i);
            if (tw) s = tw[1];
          }
          if (!s) {
            const tit = buf.match(/<title[^>]*>([\s\S]*?)<\/title>/i);
            if (tit) s = tit[1];
          }
          if (s) {
            // decodifica entità di base
            s = s.replace(/&amp;/g,'&').replace(/&lt;/g,'<').replace(/&gt;/g,'>')
                 .replace(/&quot;/g,'"').replace(/&#39;|&apos;/g,"'").replace(/&nbsp;/g,' ')
                 .replace(/&#(\d+);/g, (_,n)=>String.fromCharCode(+n));
            // rimuove suffissi tipo " | EPORNER", " - Pornhub.com"
            s = s.replace(/\s*[|\-–—]\s*(EPORNER|Pornhub|XVideos|XNXX|RedTube|YouPorn|Tube8|SpankBang)[^<]*$/i, '').trim();
          }
          finish(s || null);
        });
        res.on('error', () => { clearTimeout(to); finish(null); });
      });
      req.on('error', () => { clearTimeout(to); finish(null); });
      req.setTimeout(4000, () => { req.destroy(); clearTimeout(to); finish(null); });
    } catch(_) { clearTimeout(to); finish(null); }
  });
}
function _sanitizeFilename(s) {
  return String(s || '').replace(/[\\/:*?"<>|\x00-\x1f]/g, '').replace(/\s+/g, ' ').trim().slice(0, 180);
}
function _resolveFfmpeg(vibraVidPath) {
  const candidates = [];
  // Priorità: ffmpeg bundled con l'app (tools/ffmpeg/bin) → funziona anche
  // sulle macchine degli utenti senza ffmpeg installato.
  candidates.push(path.join(_resBase(), 'tools', 'ffmpeg', 'bin', 'ffmpeg.exe'));
  if (vibraVidPath) {
    candidates.push(path.join(vibraVidPath, 'ffmpeg.exe'));
    candidates.push(path.join(vibraVidPath, 'bin', 'ffmpeg.exe'));
    candidates.push(path.join(vibraVidPath, 'binaries', 'ffmpeg.exe'));
  }
  candidates.push('C:\\Users\\markt\\VibraVid\\ffmpeg.exe');
  for (const c of candidates) { try { if (fs.existsSync(c)) return path.dirname(c); } catch(e){} }
  return null;
}
const AUDIO_FORMATS = new Set(['mp3','wav','m4a','opus','aac','flac','ogg']);
function _qualityArgs(quality, format) {
  const args = [];
  // Se l'utente ha scelto un container puramente audio (mp3/wav/m4a/opus/...),
  // estrai la traccia audio anche se la qualità non è 'audio'.
  const audioOnly = quality === 'audio' || AUDIO_FORMATS.has((format||'').toLowerCase());
  if (audioOnly) {
    const af = (format && AUDIO_FORMATS.has(format.toLowerCase())) ? format.toLowerCase() : 'mp3';
    args.push('-x', '--audio-format', af);
    if (af === 'mp3' || af === 'aac' || af === 'opus') args.push('--audio-quality', '0');
  } else {
    // Selectors con fallback a `best[height<=N]` e infine `best`:
    // serve per siti che servono UN solo file monolitico (eporner, tube vari)
    // dove `bv*+ba` fallisce con "Requested format is not available".
    const map = {
      'best':     'bv*+ba/b/best',
      'smallest': 'wv*+wa/w/worst',
      '4320p':    'bv*[height<=4320]+ba/b[height<=4320]/best[height<=4320]/best',
      '2160p':    'bv*[height<=2160]+ba/b[height<=2160]/best[height<=2160]/best',
      '1440p':    'bv*[height<=1440]+ba/b[height<=1440]/best[height<=1440]/best',
      '1080p':    'bv*[height<=1080]+ba/b[height<=1080]/best[height<=1080]/best',
      '720p':     'bv*[height<=720]+ba/b[height<=720]/best[height<=720]/best',
      '480p':     'bv*[height<=480]+ba/b[height<=480]/best[height<=480]/best',
      '360p':     'bv*[height<=360]+ba/b[height<=360]/best[height<=360]/best',
      '240p':     'bv*[height<=240]+ba/b[height<=240]/best[height<=240]/best'
    };
    args.push('-f', map[quality] || map.best);
    if (format) args.push('--merge-output-format', format);
  }
  return args;
}

function _probeFormats(bin, url) {
  return new Promise((resolve) => {
    try {
      const p = spawn(bin, ['-J', '--no-playlist', url], { windowsHide: true });
      let out = '', err = '';
      p.stdout.on('data', d => { out += d.toString(); });
      p.stderr.on('data', d => { err += d.toString(); });
      p.on('error', () => resolve({ ok: false, error: 'probe failed' }));
      p.on('close', code => {
        if (code !== 0) return resolve({ ok: false, error: err || ('code '+code) });
        try {
          const j = JSON.parse(out);
          const fmts = (j.formats || []).filter(f => f.vcodec && f.vcodec !== 'none' && f.height);
          const uniqHeights = [...new Set(fmts.map(f => f.height))].sort((a,b)=>b-a);
          const simplified = fmts.map(f => ({ format_id: f.format_id, height: f.height, ext: f.ext, filesize: f.filesize || f.filesize_approx || null, vcodec: f.vcodec, acodec: f.acodec }));
          resolve({ ok: true, heights: uniqHeights, formats: simplified, title: j.title, thumbnail: j.thumbnail || (j.thumbnails&&j.thumbnails.length?j.thumbnails[j.thumbnails.length-1].url:null) });
        } catch(e) { resolve({ ok: false, error: 'parse: '+e.message }); }
      });
    } catch(e) { resolve({ ok: false, error: e.message }); }
  });
}

const _dlPending = new Map(); // id -> { resolvePolicy }
ipcMain.handle('download:start', async (_e, { id, url, quality, format, outputDir, vibraVidPath, policy, cookieBrowser, resume, priority, resumeFilePath }) => {
  const send = (data) => { try { mainWindow?.webContents.send('download:progress', { id, ...data }); } catch(e){} };
  try {
    if (!url) return { success: false, error: 'URL mancante' };
    send({ status: 'Preparing' });
    let bin;
    try { bin = await _resolveYtDlp(vibraVidPath, msg => send({ status: 'Preparing', note: msg })); }
    catch(err) { send({ status: 'Error', error: 'Impossibile ottenere yt-dlp: '+err.message }); return { success: false, error: err.message }; }
    // Probe metadata to detect multiple renditions
    let chosenFmtId = null;
    let effQuality = quality;
    let probeTitle = null;
    if (quality !== 'audio') {
      send({ status: 'Preparing', note: 'Analizzo formati…' });
      const probe = await _probeFormats(bin, url);
      if (probe.ok && probe.title) probeTitle = probe.title;
      if (probe.ok && probe.thumbnail) send({ thumbnail: probe.thumbnail, title: probe.title });
      // Stima la dimensione max così il riquadro mostra subito un peso indicativo
      if (probe.ok && Array.isArray(probe.formats)) {
        const sizes = probe.formats.map(f => f.filesize).filter(x => typeof x === 'number' && x > 0);
        if (sizes.length) {
          const maxSize = Math.max(...sizes);
          send({ size: maxSize });
        }
      }
      if (probe.ok && probe.heights && probe.heights.length > 1) {
        // Apply policy if provided, else ask renderer
        let pol = policy;
        if (!pol) {
          // Await renderer choice
          pol = await new Promise(resolve => {
            _dlPending.set(id, resolve);
            try { mainWindow?.webContents.send('download:qualityChoice', { id, url, title: probe.title, heights: probe.heights, formats: probe.formats }); } catch(e){}
            // Safety timeout: default best after 5 min
            setTimeout(() => { if (_dlPending.has(id)) { _dlPending.delete(id); resolve({ kind: 'best' }); } }, 5*60*1000);
          });
        }
        if (pol && pol.kind === 'height') effQuality = pol.value + 'p';
        else if (pol && pol.kind === 'smallest') effQuality = 'smallest';
        else if (pol && pol.kind === 'format_id') chosenFmtId = pol.value;
        else effQuality = 'best';
      }
    }
    const outDir = outputDir && fs.existsSync(outputDir) ? outputDir : app.getPath('downloads');
    const qArgs = chosenFmtId ? ['-f', chosenFmtId + '+ba/b'] : _qualityArgs(effQuality, format);
    const baseArgs = _baseDownloadArgs(url, cookieBrowser);
    // Override titolo per host noti che yt-dlp serializza come ID numerico.
    // Pre-fetch <title>/og:title della pagina HTML, sanitizziamo, e usiamo come template -o.
    let _outTemplate = path.join(outDir, '%(title).200s.%(ext)s');
    // RESUME: se l'utente ha cliccato Riprendi e abbiamo già un filePath salvato,
    // riusiamo quel path ESATTO (nome + estensione). yt-dlp con --continue continuerà
    // sullo stesso .part invece di rigenerare un nuovo nome dal probe.
    if (resume && resumeFilePath && typeof resumeFilePath === 'string') {
      try {
        const dir = path.dirname(resumeFilePath);
        if (fs.existsSync(dir)) {
          // Se il file esiste già completo, niente da fare
          if (fs.existsSync(resumeFilePath) && fs.statSync(resumeFilePath).size > 0) {
            // Nome assoluto riusato: yt-dlp con --continue salta se già completo
            _outTemplate = resumeFilePath;
          } else {
            // .part presente o parziale → usa nome esatto come template
            _outTemplate = resumeFilePath;
          }
        }
      } catch(_){}
    }
    try {
      const u = new URL(url);
      if (_TITLE_HOSTS.test(u.hostname || '') || _looksLikeNumericTitle(probeTitle)) {
        send({ status: 'Preparing', note: 'Recupero titolo dalla pagina…' });
        const fetched = await _fetchPageTitle(url);
        if (fetched) {
          const safe = _sanitizeFilename(fetched);
          if (safe) {
            _outTemplate = path.join(outDir, safe + '.%(ext)s');
            try { send({ title: safe }); } catch(_){}
          }
        }
      }
    } catch(_) {}
    const args = ['--newline', '--no-colors', '--no-playlist'];
    // F4: resume — continua il download esistente (yt-dlp riprende dal byte mancante)
    if (resume) args.push('--continue');
    // Priorità banda per-download (alta = illimitato, media = 2M, bassa = 500K)
    if (priority === 'med') args.push('--limit-rate', '2M');
    else if (priority === 'low') args.push('--limit-rate', '500K');
    args.push(...baseArgs, '-o', _outTemplate, ...qArgs);
    if (format && !chosenFmtId && effQuality !== 'audio') { /* merge already in _qualityArgs */ }
    const ffDir = _resolveFfmpeg(vibraVidPath);
    if (ffDir) args.push('--ffmpeg-location', ffDir);
    args.push(url);
    const runOnce = (currentBin, runArgs) => new Promise(resolve => {
      let proc;
      const useArgs = runArgs || args;
      try { proc = spawn(currentBin, useArgs, { windowsHide: true }); }
      catch(err) { resolve({ ok:false, err:'Avvio yt-dlp fallito: '+err.message, retryable:false }); return; }
      _dlProcs.set(id, proc);
      send({ status: 'Downloading' });
      let lastTitle = '', lastErrLine = '', lastFilePath = '';
      proc.stdout.on('data', buf => {
        const lines = buf.toString().split(/\r?\n/);
        for (const line of lines) {
          if (!line) continue;
          const mm = line.match(/\[Merger\] Merging formats into\s+"?([^"\n]+)"?/);
          if (mm) { lastFilePath = mm[1].trim().replace(/"$/,''); send({ filePath: lastFilePath }); continue; }
          const tm = line.match(/\[download\] Destination:\s*(.+)/);
          if (tm) { lastFilePath = tm[1].trim(); lastTitle = path.basename(lastFilePath); send({ title: lastTitle, filePath: lastFilePath }); continue; }
          const pm = line.match(/\[download\]\s+([\d.]+)%\s+of\s+~?\s*([\d.]+)\s*([KMGT]?i?B)\s+at\s+([\d.]+\w+\/s)\s+ETA\s+([\d:]+)/i);
          if (pm) {
            const num = parseFloat(pm[2]);
            const unit = (pm[3] || '').toUpperCase();
            const mult = { 'B':1, 'KB':1024, 'KIB':1024, 'MB':1024*1024, 'MIB':1024*1024, 'GB':1024**3, 'GIB':1024**3, 'TB':1024**4, 'TIB':1024**4 }[unit] || 1;
            const sizeBytes = Math.round(num * mult);
            send({ percent: parseFloat(pm[1]), speed: pm[4], eta: pm[5], size: sizeBytes, status: 'Downloading' });
            continue;
          }
          const pm2 = line.match(/\[download\]\s+([\d.]+)%/);
          if (pm2) { send({ percent: parseFloat(pm2[1]), status: 'Downloading' }); continue; }
          if (/^ERROR:/i.test(line)) lastErrLine = line;
        }
      });
      proc.stderr.on('data', buf => {
        const s = buf.toString();
        const m = s.match(/ERROR:[^\n]*/i);
        if (m) lastErrLine = m[0];
      });
      proc.on('error', err => {
        if (_dlProcs.get(id) === proc) _dlProcs.delete(id);
        resolve({ ok:false, err: err.message, retryable:false });
      });
      proc.on('close', code => {
        if (_dlProcs.get(id) === proc) _dlProcs.delete(id);
        if (code === 0) resolve({ ok:true });
        else if (code === null) resolve({ ok:false, canceled:true });
        else resolve({
          ok:false,
          err: lastErrLine || ('yt-dlp uscito con codice '+code),
          retryable: _isExtractorError(lastErrLine),
          botAuth: _isBotOrAuthError(lastErrLine),
          formatError: _isFormatError(lastErrLine)
        });
      });
    });
    let r = await runOnce(bin, args);
    if (!r.ok && r.retryable) {
      send({ status: 'Preparing', note: 'Estrattore obsoleto — aggiorno yt-dlp e riprovo…' });
      try { _metaClearTs(); bin = await _forceDownloadYtDlp(msg => send({ status: 'Preparing', note: msg })); } catch(e){}
      r = await runOnce(bin, args);
    }
    // Fallback estrattore generico (stile FDM/JDownloader): per siti senza estrattore
    // dedicato yt-dlp scansiona comunque la pagina HTML per <video>/sorgenti HLS/DASH.
    // Gira solo se siamo ancora in errore da estrattore non supportato.
    if (!r.ok && _isExtractorError(r.err)) {
      send({ status: 'Preparing', note: 'Provo estrattore generico (qualsiasi sito)…' });
      r = await runOnce(bin, ['--force-generic-extractor', ...args]);
    }
    // Retry su "Requested format is not available": ritenta con -f best (file monolitico).
    if (!r.ok && r.formatError) {
      send({ status: 'Preparing', note: 'Formato richiesto non disponibile — riprovo con -f best…' });
      // Sostituisci il valore di -f con 'best' mantenendo gli altri flag.
      const argsFmtFb = []; let skipNext = false;
      for (let i = 0; i < args.length; i++) {
        if (skipNext) { skipNext = false; continue; }
        if (args[i] === '-f') { argsFmtFb.push('-f', 'best'); skipNext = true; continue; }
        argsFmtFb.push(args[i]);
      }
      r = await runOnce(bin, argsFmtFb);
    }
    // Retry auto con cookie se YouTube/simile blocca per bot/auth.
    // Il primo tentativo già usa --cookies-from-browser <detected>; qui proviamo sequenzialmente
    // gli altri browser disponibili e, come ultima risorsa, una copia del DB Chrome.
    if (!r.ok && r.botAuth) {
      // Strip eventuali --cookies-from-browser/--cookies presenti in args, così il retry
      // non finisce con due flag concorrenti (yt-dlp li accetta una sola volta).
      const _stripCookieFlags = (a) => {
        const out = []; for (let i=0; i<a.length; i++) {
          const v = a[i];
          if (v === '--cookies-from-browser' || v === '--cookies') { i++; continue; }
          out.push(v);
        }
        return out;
      };
      const cleanArgs = _stripCookieFlags(args);
      // FAST-PATH: se l'errore è un lock del DB cookie del browser (browser aperto),
      // non serve provare gli altri browser (avrebbero lo stesso lock). Vai dritto
      // al retry SENZA cookie — molti video funzionano lo stesso.
      const cookieLocked = _isCookieLockError(r.err);
      if (cookieLocked) {
        send({ status: 'Preparing', note: 'Cookie browser lockato (browser aperto?) — provo senza cookie…' });
        const rNoCookies = await runOnce(bin, cleanArgs);
        if (rNoCookies.ok) r = rNoCookies;
        else if (!rNoCookies.botAuth) r = rNoCookies;
        else {
          // Anche senza cookie l'host richiede auth. Prova un solo altro browser
          // (probabilmente Firefox che ha lock policy diversa) come ultima chance.
          const fxArgs = ['--cookies-from-browser', 'firefox', ...cleanArgs];
          send({ status: 'Preparing', note: 'Riprovo con cookie Firefox…' });
          const rFx = await runOnce(bin, fxArgs);
          if (rFx.ok) r = rFx; else r = rNoCookies;
        }
      } else {
        const tried = new Set();
        const detected = _detectActiveBrowser(cookieBrowser); if (detected) tried.add(detected);
        const order = ['edge', 'chrome', 'firefox', 'brave', 'opera', 'vivaldi'].filter(b => !tried.has(b));
        for (const br of order) {
          send({ status: 'Preparing', note: `Riprovo con cookie da ${br}…` });
          const argsTry = ['--cookies-from-browser', br, ...cleanArgs];
          const rTry = await runOnce(bin, argsTry);
          if (rTry.ok) { r = rTry; break; }
          // Se incontriamo un lock cookie su un altro browser, salta direttamente al
          // retry no-cookie senza ciclare tutti gli altri.
          if (_isCookieLockError(rTry.err)) { r = rTry; break; }
          if (!rTry.botAuth) { r = rTry; break; }
          tried.add(br);
        }
        // Penultima chance: copia DB cookie Chrome via --cookies file
        if (!r.ok && r.botAuth && !_isCookieLockError(r.err)) {
          const chromeCopy = _copyChromeCookies();
          if (chromeCopy) {
            send({ status: 'Preparing', note: 'Riprovo con cookie Chrome (file copiato)…' });
            const argsCopy = ['--cookies', chromeCopy, ...cleanArgs];
            const rCopy = await runOnce(bin, argsCopy);
            if (rCopy.ok) r = rCopy;
            else if (!rCopy.botAuth) r = rCopy;
          }
        }
        // Ultima risorsa: tentativo SENZA cookie (molti video funzionano lo stesso).
        if (!r.ok && r.botAuth) {
          send({ status: 'Preparing', note: 'Provo senza cookie (alcuni video funzionano lo stesso)…' });
          const rNoCookies = await runOnce(bin, cleanArgs);
          if (rNoCookies.ok) r = rNoCookies;
          else {
            if (_isCookieLockError(rNoCookies.err)) {
              r = { ok:false, err:'Cookie del browser bloccati: chiudi tutti i browser (Chrome/Edge/Brave) e riprova. In alternativa cambia "browser cookie" in Impostazioni → Downloader.' };
            } else r = rNoCookies;
          }
        }
      }
    }
    if (r.ok) send({ status: 'Done', percent: 100, note: '' });
    else if (r.canceled) send({ status: 'Canceled', note: '' });
    else send({ status: 'Error', error: r.err || 'Errore', note: '' });
    return { success: !!r.ok, error: r.err };
  } catch (e) { send({ status: 'Error', error: e.message }); return { success: false, error: e.message }; }
});
// ═══ FILE MOVE (utilizzato dal context-menu mediaOptions: "Sposta") ═══
// Apre un dialog "scegli cartella destinazione", poi sposta atomicamente
ipcMain.handle('file:move', async (_e, { oldPath, newDir } = {}) => {
  try {
    if (!oldPath) return { success: false, error: 'percorso mancante' };
    if (!fs.existsSync(oldPath)) return { success: false, error: 'file non esiste' };
    let targetDir = newDir;
    if (!targetDir) {
      const r = await dialog.showOpenDialog(mainWindow, { properties:['openDirectory','createDirectory'], title:'Scegli cartella destinazione' });
      if (r.canceled || !r.filePaths.length) return { canceled: true };
      targetDir = r.filePaths[0];
    }
    const filename = path.basename(oldPath);
    const newPath = path.join(targetDir, filename);
    if (fs.existsSync(newPath)) return { success: false, error: 'destinazione già esistente' };
    await fs.promises.rename(oldPath, newPath);
    return { success: true, path: newPath };
  } catch (e) { return { success: false, error: e.message }; }
});

// ═══ FILE DELETE (utilizzato dal context-menu mediaOptions) ═══
ipcMain.handle('file:delete', async (_e, { filePath } = {}) => {
  try {
    if (!filePath) return { success: false, error: 'percorso mancante' };
    if (!fs.existsSync(filePath)) return { success: false, error: 'file non esiste' };
    // Preferisci shell.trashItem (cestino) se disponibile, fallback unlink
    if (shell && shell.trashItem) {
      try { await shell.trashItem(filePath); return { success: true, trashed: true }; }
      catch (_) { /* fallback */ }
    }
    await fs.promises.unlink(filePath);
    return { success: true, trashed: false };
  } catch (e) { return { success: false, error: e.message }; }
});

// ═══ FILE RENAME (utilizzato dalla rinomina inline nei download) ═══
ipcMain.handle('file:exists', async (_e, p) => {
  try { return { exists: !!(p && fs.existsSync(p)) }; }
  catch(_) { return { exists: false }; }
});
ipcMain.handle('file:rename', async (_e, { oldPath, newPath } = {}) => {
  try {
    if (!oldPath || !newPath) return { success: false, error: 'percorsi mancanti' };
    if (!fs.existsSync(oldPath)) return { success: false, error: 'file non esiste' };
    if (oldPath === newPath) return { success: true, path: oldPath };
    if (fs.existsSync(newPath)) return { success: false, error: 'destinazione già esistente' };
    await fs.promises.rename(oldPath, newPath);
    return { success: true, path: newPath };
  } catch (e) { return { success: false, error: e.message }; }
});

// ═══ Universal Entity Resolver ═══
// Risolve un nome generico interrogando in cascata TMDB (people), MusicBrainz (artist),
// Wikipedia (page summary). Restituisce il primo match con confidenza buona o null.
// kindHint può essere: 'face' / 'celebrity' / 'actor' / 'musician' / 'youtuber' / 'historical' / 'auto'
const _entityCache = new Map();
async function _fetchJson(url, headers, timeoutMs) {
  const ctl = new AbortController();
  const to = setTimeout(() => ctl.abort(), timeoutMs || 8000);
  try {
    const r = await fetch(url, { headers: headers || {}, signal: ctl.signal });
    if (!r.ok) return null;
    return await r.json();
  } catch (_) { return null; }
  finally { clearTimeout(to); }
}
async function _resolveTMDB(name, apiKey) {
  if (!apiKey) return null;
  const u = `https://api.themoviedb.org/3/search/person?api_key=${encodeURIComponent(apiKey)}&query=${encodeURIComponent(name)}`;
  const j = await _fetchJson(u);
  if (!j || !j.results || !j.results.length) return null;
  const top = j.results[0];
  // Prendi anche dettagli (biografia, birth, etc.)
  const det = await _fetchJson(`https://api.themoviedb.org/3/person/${top.id}?api_key=${encodeURIComponent(apiKey)}`);
  return {
    name: top.name,
    photoUrl: top.profile_path ? `https://image.tmdb.org/t/p/w300${top.profile_path}` : null,
    source: 'tmdb',
    external_id: 'tmdb:' + top.id,
    score: top.popularity > 5 ? 0.9 : 0.7,
    metadata: {
      kind: 'celebrity',
      department: top.known_for_department || null,
      birthday: det && det.birthday || null,
      deathday: det && det.deathday || null,
      birthplace: det && det.place_of_birth || null,
      bio: det && det.biography ? det.biography.slice(0, 600) : null,
      homepage: det && det.homepage || null,
      tmdb_id: top.id
    }
  };
}
async function _resolveMusicBrainz(name) {
  const u = `https://musicbrainz.org/ws/2/artist/?query=${encodeURIComponent('artist:' + name)}&fmt=json&limit=3`;
  const j = await _fetchJson(u, { 'User-Agent': 'Maniac/1.0 ( https://maniac.local )' });
  if (!j || !j.artists || !j.artists.length) return null;
  const top = j.artists[0];
  if ((top.score || 0) < 75) return null;
  return {
    name: top.name,
    photoUrl: null,
    source: 'musicbrainz',
    external_id: 'mbid:' + top.id,
    score: Math.min(1, (top.score || 0) / 100),
    metadata: {
      kind: 'musician',
      type: top.type || null,
      country: top.country || null,
      gender: top.gender || null,
      lifeBegin: (top['life-span'] || {}).begin || null,
      lifeEnd: (top['life-span'] || {}).end || null,
      mbid: top.id
    }
  };
}
async function _resolveWikipedia(name) {
  const u = `https://en.wikipedia.org/api/rest_v1/page/summary/${encodeURIComponent(name.replace(/\s+/g, '_'))}`;
  const j = await _fetchJson(u);
  if (!j || j.type === 'disambiguation' || !j.title) return null;
  return {
    name: j.title,
    photoUrl: (j.thumbnail && j.thumbnail.source) || (j.originalimage && j.originalimage.source) || null,
    source: 'wikipedia',
    external_id: 'wp:' + (j.pageid || j.title),
    score: 0.85,
    metadata: {
      kind: 'historical',
      description: j.description || null,
      bio: j.extract || null,
      url: (j.content_urls && j.content_urls.desktop && j.content_urls.desktop.page) || null
    }
  };
}
ipcMain.handle('entity:resolve', async (_e, { name, kindHint, tmdbKey } = {}) => {
  if (!name) return { ok: false, error: 'name mancante' };
  const key = (kindHint || 'auto') + '|' + name.toLowerCase().trim();
  if (_entityCache.has(key)) return { ok: true, result: _entityCache.get(key), cached: true };
  // Ordine in base al kind hint
  const order = (() => {
    const k = (kindHint || 'auto').toLowerCase();
    if (k === 'musician' || k === 'artist' || k === 'band') return ['mb', 'wiki', 'tmdb'];
    if (k === 'actor' || k === 'celebrity' || k === 'face') return ['tmdb', 'wiki', 'mb'];
    if (k === 'youtuber' || k === 'influencer') return ['wiki', 'tmdb', 'mb'];
    if (k === 'historical' || k === 'politician' || k === 'athlete' || k === 'scientist') return ['wiki', 'tmdb', 'mb'];
    return ['tmdb', 'mb', 'wiki']; // auto
  })();
  let result = null;
  for (const step of order) {
    if (step === 'tmdb') result = await _resolveTMDB(name, tmdbKey);
    else if (step === 'mb') result = await _resolveMusicBrainz(name);
    else if (step === 'wiki') result = await _resolveWikipedia(name);
    if (result && result.name) break;
  }
  if (result) _entityCache.set(key, result);
  return { ok: true, result };
});

// ═══ VIBRAVID BRIDGE (CLI interattivo per StreamingCommunity) ═══
// Se l'utente ha installato vibravid (CLI Python che gestisce siti come
// streamingcommunityz.ooo) lo lanciamo come child process, leggiamo lo
// stdout per capire quando chiede un input (titolo/stagione/episodio) ed
// emettiamo un evento al renderer che mostra una GUI invece del prompt
// del terminale. Lo stdin viene scritto via "vibravid:sendInput".
const _vvProcs = new Map(); // jobId -> { proc, buf, lastPromptAt }
function _resolveVibravid(vibraVidPath) {
  // Cerca un binario `vibravid`/`vibravid.exe`/script python
  const candidates = [];
  if (vibraVidPath) {
    candidates.push(path.join(vibraVidPath, 'vibravid.exe'));
    candidates.push(path.join(vibraVidPath, 'vibravid'));
    candidates.push(path.join(vibraVidPath, 'vibravid.py'));
    candidates.push(path.join(vibraVidPath, 'main.py'));
  }
  candidates.push('vibravid'); // PATH
  for (const c of candidates) {
    try { if (c.includes(path.sep) ? fs.existsSync(c) : true) return c; } catch(_) {}
  }
  return null;
}

ipcMain.handle('vibravid:start', async (_e, { jobId, url, vibraVidPath } = {}) => {
  try {
    if (!jobId || !url) return { ok: false, error: 'jobId/url mancanti' };
    const bin = _resolveVibravid(vibraVidPath);
    if (!bin) return { ok: false, error: 'vibravid non trovato. Imposta il percorso nelle impostazioni Downloader.' };
    const isPy = bin.endsWith('.py');
    const cmd = isPy ? 'python' : bin;
    const args = isPy ? [bin, url] : [url];
    let proc;
    try { proc = spawn(cmd, args, { windowsHide: true, stdio: ['pipe','pipe','pipe'] }); }
    catch (err) { return { ok: false, error: 'avvio fallito: ' + err.message }; }
    const state = { proc, buf: '', errBuf: '', lastPromptAt: 0 };
    _vvProcs.set(jobId, state);
    const send = (channel, data) => { try { mainWindow?.webContents.send(channel, { jobId, ...data }); } catch(_){} };
    // Heuristica: una "prompt" è una riga che termina con ':' o '?' senza newline
    // oppure contiene parole come "Scegli", "Select", "Stagione", "Episodio"
    const flushBuffer = () => {
      const lines = state.buf.split(/\r?\n/);
      state.buf = lines.pop() || '';
      lines.forEach(line => {
        if (!line.trim()) return;
        send('vibravid:log', { type: 'stdout', text: line });
        // riconosci elenchi numerati "1) Nome" o "1. Nome"
      });
      // Buffer residuo che non termina con newline e sembra un prompt → richiedi input GUI
      const tail = state.buf.trim();
      const isPromptish = /[:?>]\s*$/.test(tail) || /(scegli|select|inserisci|stagione|episodio|season|episode|titolo|title)/i.test(tail);
      if (tail && isPromptish && Date.now() - state.lastPromptAt > 200) {
        state.lastPromptAt = Date.now();
        // Estrai opzioni numerate dalle ultime righe
        const allLines = (state.buf.match(/.*\n/g) || []).concat([tail]);
        const options = [];
        for (const ln of allLines) {
          const m = ln.match(/^\s*\(?(\d+)[\).]\s+(.+?)\s*$/);
          if (m) options.push({ index: m[1], label: m[2] });
        }
        send('vibravid:prompt', { question: tail, options, raw: state.buf });
      }
    };
    proc.stdout.on('data', buf => { state.buf += buf.toString(); flushBuffer(); });
    proc.stderr.on('data', buf => {
      const s = buf.toString();
      state.errBuf += s;
      send('vibravid:log', { type: 'stderr', text: s });
    });
    proc.on('error', err => {
      send('vibravid:done', { ok: false, error: err.message });
      _vvProcs.delete(jobId);
    });
    proc.on('close', code => {
      // Emetti eventuali residui come log
      if (state.buf.trim()) send('vibravid:log', { type: 'stdout', text: state.buf });
      // Cerca path del file scaricato negli output (heuristica)
      const all = state.buf + '\n' + state.errBuf;
      const m = all.match(/(?:saved|salvato|downloaded|scaricato)[^\n]*?[:\s]\s*("?)([A-Za-z]:[^\n"']+|\/[^\n"']+)\1/i);
      const filePath = m ? m[2].trim() : null;
      send('vibravid:done', { ok: code === 0, code, filePath, error: code === 0 ? null : (state.errBuf || ('exit '+code)) });
      _vvProcs.delete(jobId);
    });
    return { ok: true };
  } catch(e) { return { ok: false, error: e.message }; }
});

ipcMain.handle('vibravid:sendInput', (_e, { jobId, line } = {}) => {
  const st = _vvProcs.get(jobId);
  if (!st || !st.proc || !st.proc.stdin) return { ok: false, error: 'job non attivo' };
  try { st.proc.stdin.write(String(line == null ? '' : line) + '\n'); return { ok: true }; }
  catch(e) { return { ok: false, error: e.message }; }
});

ipcMain.handle('vibravid:cancel', (_e, jobId) => {
  const st = _vvProcs.get(jobId);
  if (!st) return { ok: false };
  _killProcessTree(st.proc);
  _vvProcs.delete(jobId);
  return { ok: true };
});

// Sposta un file nel Cestino di Windows (recuperabile). Usa shell.trashItem.
ipcMain.handle('download:trashFile', async (_e, filePath) => {
  try {
    if (!filePath || typeof filePath !== 'string') return { ok:false, error:'path mancante' };
    if (!fs.existsSync(filePath)) return { ok:false, error:'file non esistente' };
    await shell.trashItem(filePath);
    return { ok:true };
  } catch(e) { return { ok:false, error: e.message || String(e) }; }
});

// Estrai un frame come thumbnail dal file scaricato (cache in userData/thumbnails).
const _thumbCacheDir = path.join(userDataPath, 'thumbnails');
try { fs.mkdirSync(_thumbCacheDir, { recursive: true }); } catch(_){}
function _sha1Hex(s){
  return require('crypto').createHash('sha1').update(String(s)).digest('hex');
}
ipcMain.handle('download:extractThumb', async (_e, filePath) => {
  try {
    if (!filePath || !fs.existsSync(filePath)) return { ok:false, error:'file mancante' };
    const dst = path.join(_thumbCacheDir, _sha1Hex(filePath) + '.jpg');
    if (fs.existsSync(dst) && fs.statSync(dst).size > 1024) {
      return { ok:true, path: dst, dataUrl: 'file:///' + dst.replace(/\\/g,'/') };
    }
    const ffDir = _resolveFfmpeg(null);
    const ffBin = ffDir ? path.join(ffDir, 'ffmpeg.exe') : 'ffmpeg';
    return await new Promise(resolve => {
      let proc;
      try { proc = spawn(ffBin, ['-y','-ss','00:00:03','-i', filePath, '-frames:v','1','-vf','scale=320:-2','-q:v','3', dst], { windowsHide:true }); }
      catch(e) { return resolve({ ok:false, error: e.message }); }
      let errBuf = '';
      proc.stderr.on('data', d => { errBuf += d.toString(); });
      const to = setTimeout(() => { _killProcessTree(proc); resolve({ ok:false, error:'timeout' }); }, 15000);
      proc.on('error', e => { clearTimeout(to); resolve({ ok:false, error: e.message }); });
      proc.on('close', code => {
        clearTimeout(to);
        if (code === 0 && fs.existsSync(dst) && fs.statSync(dst).size > 0) {
          resolve({ ok:true, path: dst, dataUrl: 'file:///' + dst.replace(/\\/g,'/') });
        } else {
          resolve({ ok:false, error: 'ffmpeg code '+code+(errBuf?': '+errBuf.slice(-200):'') });
        }
      });
    });
  } catch(e) { return { ok:false, error: e.message }; }
});

ipcMain.handle('download:cancel', (_e, id) => {
  const p = _dlProcs.get(id);
  if (p) { _killProcessTree(p); _dlProcs.delete(id); return { success: true }; }
  // Also release any pending quality choice
  const r = _dlPending.get(id);
  if (r) { _dlPending.delete(id); r({ kind: 'best' }); }
  return { success: false };
});
ipcMain.handle('download:pickQuality', (_e, { id, policy }) => {
  const r = _dlPending.get(id);
  if (r) { _dlPending.delete(id); r(policy || { kind: 'best' }); return { success: true }; }
  return { success: false };
});
ipcMain.handle('download:check', async (_e, { vibraVidPath } = {}) => {
  try {
    const bin = await _resolveYtDlp(vibraVidPath);
    const ff = _resolveFfmpeg(vibraVidPath);
    return { success: true, ytDlp: bin, ffmpeg: ff };
  } catch(e) { return { success: false, error: e.message }; }
});
ipcMain.handle('download:pickDir', async () => {
  const r = await dialog.showOpenDialog(mainWindow, { properties: ['openDirectory', 'createDirectory'], title: 'Cartella download' });
  if (r.canceled || !r.filePaths.length) return { canceled: true };
  return { canceled: false, path: r.filePaths[0] };
});
ipcMain.handle('download:updateBin', async () => {
  try {
    _metaClearTs();
    const p = await _forceDownloadYtDlp(msg => { try { mainWindow?.webContents.send('download:progress', { id:'_sys', status:'Preparing', note: msg }); } catch(e){} });
    return { success: true, path: p };
  } catch(e) { return { success: false, error: e.message }; }
});

// ═══ TORRENT (webtorrent) ═══
let _wtClient = null;
let _WebTorrentCtor = null;
const _wtTorrents = new Map(); // id -> torrent
async function _getWT(){
  if (_wtClient) return { ok:true, client:_wtClient };
  try {
    if (!_WebTorrentCtor) {
      // webtorrent >= 2 is ESM-only — use dynamic import from CommonJS
      const mod = await import('webtorrent');
      _WebTorrentCtor = mod.default || mod;
    }
    _wtClient = new _WebTorrentCtor();
    _wtClient.on('error', () => {});
    return { ok:true, client:_wtClient };
  } catch(e) {
    return { ok:false, error:'Installa webtorrent: npm install webtorrent  —  '+(e.message||'') };
  }
}
// Tracker list auto-update (ngosang/trackerslist, 24h cache)
let _trackersCache = { list:null, ts:0 };
async function _getTrackers(){
  const now = Date.now();
  if (_trackersCache.list && (now - _trackersCache.ts) < 24*60*60*1000) return _trackersCache.list;
  // Cached file
  try {
    const p = path.join(app.getPath('userData'), 'trackers_best.txt');
    const st = fs.existsSync(p) ? fs.statSync(p) : null;
    if (st && (now - st.mtimeMs) < 24*60*60*1000) {
      const txt = fs.readFileSync(p, 'utf8');
      const list = txt.split(/\r?\n/).map(s=>s.trim()).filter(Boolean);
      if (list.length){ _trackersCache = { list, ts:now }; return list; }
    }
  } catch(e) {}
  // Fetch fresh
  try {
    const urls = [
      'https://cdn.jsdelivr.net/gh/ngosang/trackerslist/trackers_best.txt',
      'https://raw.githubusercontent.com/ngosang/trackerslist/master/trackers_best.txt'
    ];
    const https = require('https');
    for (const u of urls) {
      try {
        const txt = await new Promise((resolve, reject) => {
          https.get(u, res => {
            if (res.statusCode !== 200) { reject(new Error('HTTP '+res.statusCode)); return; }
            let d=''; res.setEncoding('utf8');
            res.on('data', c=>d+=c); res.on('end', ()=>resolve(d));
          }).on('error', reject);
        });
        const list = txt.split(/\r?\n/).map(s=>s.trim()).filter(Boolean);
        if (list.length) {
          _trackersCache = { list, ts:now };
          try { fs.writeFileSync(path.join(app.getPath('userData'), 'trackers_best.txt'), txt, 'utf8'); } catch(e){}
          return list;
        }
      } catch(e) { /* try next */ }
    }
  } catch(e) {}
  // Fallback static minimal list
  const fallback = [
    'udp://tracker.opentrackr.org:1337/announce',
    'udp://open.tracker.cl:1337/announce',
    'udp://tracker.openbittorrent.com:6969/announce',
    'udp://9.rarbg.com:2810/announce',
    'udp://tracker.internetwarriors.net:1337/announce',
    'udp://exodus.desync.com:6969/announce',
    'udp://tracker.torrent.eu.org:451/announce',
    'udp://tracker.moeking.me:6969/announce'
  ];
  _trackersCache = { list: fallback, ts: now };
  return fallback;
}
async function _enrichMagnet(magnetOrUrl){
  if (!/^magnet:/i.test(magnetOrUrl)) return magnetOrUrl;
  try {
    const list = await _getTrackers();
    const existing = new Set();
    magnetOrUrl.replace(/[?&]tr=([^&]+)/g, (_,v)=>{ existing.add(decodeURIComponent(v)); return ''; });
    const toAdd = list.filter(u => !existing.has(u)).slice(0, 30);
    if (!toAdd.length) return magnetOrUrl;
    const extra = toAdd.map(u => 'tr=' + encodeURIComponent(u)).join('&');
    return magnetOrUrl + (magnetOrUrl.includes('?') ? '&' : '?') + extra;
  } catch(e) { return magnetOrUrl; }
}
// Helper safe-send: evita crash "Render frame was disposed" quando webtorrent emette
// progress su una finestra appena chiusa. Guard espliciti su mainWindow / webContents.
function _safeSend(channel, payload) {
  try {
    if (!mainWindow || mainWindow.isDestroyed()) return;
    const wc = mainWindow.webContents;
    if (!wc || wc.isDestroyed()) return;
    wc.send(channel, payload);
  } catch(_){}
}
function _tSend(id, data){ _safeSend('torrent:progress', { id, ...data }); }
function _tEmitProgress(id, t){
  // Stima tracker online vs totali: webtorrent espone il client tracker
  // tramite t.discovery._trackerWS / t.discovery.tracker. Fallback robusto:
  // contiamo gli announce raggiungibili a partire dagli url annunciati.
  let trackersTotal=0, trackersOk=0;
  try {
    if (Array.isArray(t.announce)) trackersTotal = t.announce.length;
    const tr = t.discovery && t.discovery.tracker;
    // Conteggio "online" approssimato: tracker che hanno completato l'announce
    // (presence di _trackers con readyState/announceMap valorizzato).
    if (tr && tr._trackers && Array.isArray(tr._trackers)) {
      for (const sub of tr._trackers) {
        if (!sub) continue;
        if (sub.client && sub.client.connected) { trackersOk++; continue; }
        if (sub.socket && sub.socket.connected) { trackersOk++; continue; }
        if (sub.destroyed === false) trackersOk++;
      }
    }
    // Se non riusciamo a contare attivi ma abbiamo peer, almeno 1 tracker funziona
    if (trackersOk === 0 && (t.numPeers||0) > 0) trackersOk = 1;
  } catch(_){}
  _tSend(id, {
    percent: +(t.progress*100).toFixed(2),
    speed: t.downloadSpeed,
    downloaded: t.downloaded,
    length: t.length,
    peers: t.numPeers,
    seeds: (t.numPeers||0),
    trackers: trackersTotal,
    trackersOnline: trackersOk,
    dhtReady: !!(t.discovery && t.discovery.dht && t.discovery.dht.ready),
    eta: isFinite(t.timeRemaining) ? Math.round(t.timeRemaining/1000) : null,
    status: t.done ? 'Done' : (t.paused ? 'Paused' : 'Downloading')
  });
}
ipcMain.handle('torrent:add', async (_e, { magnetOrUrl, outputDir }) => {
  const g = await _getWT(); if (!g.ok) return { ok:false, error:g.error };
  try {
    const outDir = outputDir && fs.existsSync(outputDir) ? outputDir : app.getPath('downloads');
    const id = 't_' + Date.now() + '_' + Math.random().toString(36).slice(2,7);
    // Enrich magnet with fresh tracker list
    const enriched = await _enrichMagnet(magnetOrUrl);
    const announce = await _getTrackers();
    const opts = { path: outDir, announce };
    const t = g.client.add(enriched, opts);
    _wtTorrents.set(id, t);
    let readyTimer = setTimeout(() => {
      if (!t.ready) _tSend(id, { status:'Downloading', note:'Connessione tracker/DHT…' });
    }, 4000);
    t.on('ready', () => { clearTimeout(readyTimer); _tSend(id, { name: t.name, length: t.length, status:'Downloading' }); });
    t.on('download', () => _tEmitProgress(id, t));
    t.on('upload', () => _tEmitProgress(id, t));
    t.on('done', () => {
      _tEmitProgress(id, t);
      let fp = '';
      try {
        const vids = (t.files||[]).filter(f => /\.(mp4|mkv|webm|mov|avi|m4v|flv|wmv|mpg|mpeg|ts|m2ts|ogv|3gp)$/i.test(f.name));
        const pick = (vids.length ? vids : (t.files||[])).slice().sort((a,b)=>(b.length||0)-(a.length||0))[0];
        if (pick) fp = path.join(outDir, pick.path || pick.name);
      } catch(e){}
      _tSend(id, { status:'Done', percent:100, filePath: fp, folderPath: outDir });
    });
    t.on('error', err => _tSend(id, { status:'Error', error: err.message||String(err) }));
    return { ok:true, id, name: t.name || magnetOrUrl };
  } catch(e) { return { ok:false, error:e.message }; }
});
ipcMain.handle('torrent:pickFile', async () => {
  try {
    const r = await dialog.showOpenDialog(mainWindow, {
      properties:['openFile'],
      filters:[{ name:'Torrent', extensions:['torrent'] }]
    });
    if (r.canceled || !r.filePaths[0]) return { success:false, canceled:true };
    const buffer = fs.readFileSync(r.filePaths[0]);
    return { success:true, name: path.basename(r.filePaths[0]), buffer: buffer.buffer.slice(buffer.byteOffset, buffer.byteOffset + buffer.byteLength) };
  } catch(e) { return { success:false, error:e.message }; }
});
ipcMain.handle('torrent:addBuffer', async (_e, { buffer, outputDir }) => {
  const g = await _getWT(); if (!g.ok) return { success:false, error:g.error };
  try {
    const outDir = outputDir && fs.existsSync(outputDir) ? outputDir : app.getPath('downloads');
    const id = 't_' + Date.now() + '_' + Math.random().toString(36).slice(2,7);
    const buf = Buffer.from(buffer);
    const t = g.client.add(buf, { path: outDir });
    _wtTorrents.set(id, t);
    t.on('ready', () => { _tSend(id, { name: t.name, length: t.length, status:'Downloading' }); });
    t.on('download', () => _tEmitProgress(id, t));
    t.on('upload', () => _tEmitProgress(id, t));
    t.on('done', () => {
      _tEmitProgress(id, t);
      let fp = '';
      try {
        const vids = (t.files||[]).filter(f => /\.(mp4|mkv|webm|mov|avi|m4v|flv|wmv|mpg|mpeg|ts|m2ts|ogv|3gp)$/i.test(f.name));
        const pick = (vids.length ? vids : (t.files||[])).slice().sort((a,b)=>(b.length||0)-(a.length||0))[0];
        if (pick) fp = path.join(outDir, pick.path || pick.name);
      } catch(e){}
      _tSend(id, { status:'Done', percent:100, filePath: fp, folderPath: outDir });
    });
    t.on('error', err => _tSend(id, { status:'Error', error: err.message||String(err) }));
    return { ok:true, id, name: t.name || 'torrent' };
  } catch(e) { return { ok:false, error:e.message }; }
});
ipcMain.handle('torrent:pause', (_e, id) => {
  const t = _wtTorrents.get(id); if (!t) return { success:false };
  try { t.pause(); _tSend(id, { status:'Paused' }); return { success:true }; } catch(e){ return { success:false, error:e.message }; }
});
ipcMain.handle('torrent:resume', (_e, id) => {
  const t = _wtTorrents.get(id); if (!t) return { success:false };
  try { t.resume(); _tSend(id, { status:'Downloading' }); return { success:true }; } catch(e){ return { success:false, error:e.message }; }
});
ipcMain.handle('torrent:remove', (_e, id) => {
  const t = _wtTorrents.get(id); if (!t) return { success:false };
  try { t.destroy(); _wtTorrents.delete(id); _tSend(id, { status:'Canceled' }); return { success:true }; }
  catch(e){ return { success:false, error:e.message }; }
});
ipcMain.handle('torrent:prioritize', (_e, { id, priority }) => {
  const t = _wtTorrents.get(id); if (!t) return { success:false };
  try {
    // Map priority: high=all files selected + high; normal=all default; low=deselect all then select lightly
    if (Array.isArray(t.files)) {
      t.files.forEach(f => {
        if (priority === 'low') f.deselect();
        else f.select();
      });
    }
    return { success:true };
  } catch(e){ return { success:false, error:e.message }; }
});

// ═══ G11: Save frame ═══
ipcMain.handle('save-frame', async (_e, { buffer, ext }) => {
  try {
    const r = await dialog.showSaveDialog(mainWindow, {
      defaultPath: path.join(app.getPath('pictures'), 'frame.'+(ext||'png')),
      filters: [{ name: 'Image', extensions: [ext||'png'] }]
    });
    if (r.canceled || !r.filePath) return { success:false, canceled:true };
    fs.writeFileSync(r.filePath, Buffer.from(buffer));
    return { success:true, path:r.filePath };
  } catch(e) { return { success:false, error:e.message }; }
});

// ═══ G6: Resolve URL with yt-dlp ═══
ipcMain.handle('url:resolve', async (_e, { url, vibraVidPath }) => {
  try {
    if (!url) return { success:false, error:'URL mancante' };
    const bin = await _resolveYtDlp(vibraVidPath);
    return await new Promise(resolve => {
      const p = spawn(bin, ['-g', '--no-warnings', '--no-playlist', url], { windowsHide: true });
      let out = '', err = '';
      p.stdout.on('data', d => out += d.toString());
      p.stderr.on('data', d => err += d.toString());
      p.on('error', e => resolve({ success:false, error:e.message }));
      p.on('close', code => {
        if (code === 0) {
          const line = out.split(/\r?\n/).find(l => /^https?:\/\//i.test(l.trim()));
          if (line) return resolve({ success:true, url: line.trim() });
          resolve({ success:false, error:'yt-dlp: nessuna URL diretta' });
        } else resolve({ success:false, error:(err||'code '+code).slice(0,500) });
      });
    });
  } catch(e) { return { success:false, error:e.message }; }
});

// ═══ G14: Tag export/import ═══
ipcMain.handle('tags:export', async (_e, data) => {
  try {
    const r = await dialog.showSaveDialog(mainWindow, {
      defaultPath: path.join(app.getPath('documents'), 'maniac-tags.json'),
      filters: [{ name: 'JSON', extensions: ['json'] }]
    });
    if (r.canceled || !r.filePath) return { success:false, canceled:true };
    fs.writeFileSync(r.filePath, JSON.stringify(data||{}, null, 2), 'utf8');
    return { success:true, path:r.filePath };
  } catch(e) { return { success:false, error:e.message }; }
});
ipcMain.handle('tags:import', async () => {
  try {
    const r = await dialog.showOpenDialog(mainWindow, {
      properties:['openFile'],
      filters:[{ name:'JSON', extensions:['json'] }]
    });
    if (r.canceled || !r.filePaths.length) return { success:false, canceled:true };
    const txt = fs.readFileSync(r.filePaths[0], 'utf8');
    const data = JSON.parse(txt);
    return { success:true, data, path:r.filePaths[0] };
  } catch(e) { return { success:false, error:e.message }; }
});

// ═══ H1: Clipboard scan page — deep video URL extraction ═══
function _httpsFetchText(url, maxRedirects=6){
  const https = require('https'); const http = require('http');
  return new Promise((resolve, reject) => {
    const go = (u, n=0) => {
      let mod;
      try { mod = /^https:/i.test(u) ? https : http; } catch(e) { return reject(e); }
      const req = mod.get(u, {
        headers: {
          'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36',
          'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8'
        }
      }, res => {
        if ([301,302,303,307,308].includes(res.statusCode) && res.headers.location && n < maxRedirects){
          res.resume();
          const next = res.headers.location.startsWith('http') ? res.headers.location : new URL(res.headers.location, u).toString();
          return go(next, n+1);
        }
        if (res.statusCode !== 200){ res.resume(); return reject(new Error('HTTP '+res.statusCode)); }
        let data=''; res.setEncoding('utf8');
        res.on('data', c => { data += c; if (data.length > 5_000_000){ req.destroy(); resolve(data); } });
        res.on('end', () => resolve(data));
      });
      req.on('error', reject);
      req.setTimeout(15000, () => { try{req.destroy();}catch(_){} reject(new Error('timeout')); });
    };
    go(url);
  });
}

// ═══════════════════════════════════════════════
// AGGIORNAMENTI (electron-updater · GitHub Releases)
// ═══════════════════════════════════════════════
// Il flusso è in tre passi espliciti, mai automatico: l'utente controlla,
// decide se scaricare, e installa quando gli fa comodo. Un aggiornamento che
// parte da solo mentre si guarda un video è la cosa peggiore per un player.
let _updater = null;
let _updateInfo = null;      // ultima versione trovata
let _updateDownloaded = false;

function _upSend(payload) {
  try { mainWindow?.webContents.send('update:progress', payload); } catch (_) {}
}

function _getUpdater() {
  if (_updater) return _updater;
  let autoUpdater;
  try { ({ autoUpdater } = require('electron-updater')); }
  catch (e) { return null; }
  // Una richiesta di controllo deve interrogare davvero il server: senza
  // questo, dopo il primo controllo la risposta può arrivare dalla cache HTTP
  // e una versione appena pubblicata resta invisibile per minuti.
  autoUpdater.requestHeaders = { 'Cache-Control': 'no-cache', Pragma: 'no-cache' };
  autoUpdater.autoDownload = false;          // scarica solo su richiesta
  autoUpdater.autoInstallOnAppQuit = false;  // installa solo su richiesta
  autoUpdater.allowDowngrade = false;
  autoUpdater.on('download-progress', p => _upSend({
    stage: 'downloading',
    percent: Math.max(0, Math.min(100, Math.round(p.percent || 0))),
    transferred: p.transferred || 0,
    total: p.total || 0,
    bytesPerSecond: p.bytesPerSecond || 0,
  }));
  autoUpdater.on('update-downloaded', info => {
    _updateDownloaded = true;
    _upSend({ stage: 'ready', version: info?.version || null });
  });
  autoUpdater.on('error', err => _upSend({
    stage: 'error', error: String((err && err.message) || err).slice(0, 300),
  }));
  _updater = autoUpdater;
  return _updater;
}

ipcMain.handle('update:check', async () => {
  const up = _getUpdater();
  if (!up) return { ok: false, error: 'modulo aggiornamenti non disponibile' };
  // In sviluppo electron-updater rifiuta di lavorare: senza questo controllo
  // l'utente vedrebbe un errore criptico invece di una spiegazione.
  if (!app.isPackaged) {
    return { ok: false, dev: true,
             error: 'Gli aggiornamenti funzionano solo nell\'app installata.' };
  }
  try {
    const r = await up.checkForUpdates();
    const remote = r?.updateInfo?.version || null;
    const current = app.getVersion();

    // Se non si è riusciti a leggere la versione pubblicata, NON si dice che
    // l'utente è aggiornato: sarebbe una bugia identica a quella del vecchio
    // pulsante, solo più difficile da scoprire. Meglio ammettere che il
    // controllo non è riuscito.
    if (!remote) {
      return { ok: false, current,
               error: 'Non sono riuscito a leggere la versione pubblicata. '
                    + 'Controlla la connessione e riprova.' };
    }

    const available = remote !== current;
    _updateInfo = available ? r.updateInfo : null;
    return {
      ok: true, available, current, version: remote,
      notes: _stripNotes(r?.updateInfo?.releaseNotes),
      date: r?.updateInfo?.releaseDate || null,
      size: (r?.updateInfo?.files || [])[0]?.size || null,
    };
  } catch (e) {
    return { ok: false, error: String(e.message || e).slice(0, 300) };
  }
});

// Le note di rilascio arrivano in HTML da GitHub: le riduciamo a testo, così
// la finestra le mostra senza rischiare di iniettare markup.
function _stripNotes(notes) {
  if (!notes) return '';
  let t = Array.isArray(notes)
    ? notes.map(n => (n && n.note) || '').join('\n')
    : String(notes);
  t = t.replace(/<br\s*\/?>/gi, '\n')
       .replace(/<\/(p|li|h\d)>/gi, '\n')
       .replace(/<li>/gi, '· ')
       .replace(/<[^>]+>/g, '')
       .replace(/&nbsp;/g, ' ').replace(/&amp;/g, '&')
       .replace(/&lt;/g, '<').replace(/&gt;/g, '>');
  return t.replace(/\n{3,}/g, '\n\n').trim().slice(0, 4000);
}

ipcMain.handle('update:download', async () => {
  const up = _getUpdater();
  if (!up) return { ok: false, error: 'modulo aggiornamenti non disponibile' };
  if (!_updateInfo) return { ok: false, error: 'nessun aggiornamento da scaricare' };
  try {
    _upSend({ stage: 'downloading', percent: 0 });
    await up.downloadUpdate();
    return { ok: true };
  } catch (e) {
    const msg = String(e.message || e).slice(0, 300);
    _upSend({ stage: 'error', error: msg });
    return { ok: false, error: msg };
  }
});

ipcMain.handle('update:install', () => {
  const up = _getUpdater();
  if (!up || !_updateDownloaded) {
    return { ok: false, error: 'aggiornamento non ancora scaricato' };
  }
  // isSilent=false mostra l'installer; isForceRunAfter riapre Maniac al termine.
  setTimeout(() => { try { up.quitAndInstall(false, true); } catch (_) {} }, 200);
  return { ok: true };
});

// Versione dell'app: unica fonte è package.json, così l'interfaccia non può
// mostrare un numero diverso da quello effettivamente installato.
ipcMain.handle('app:version', () => {
  try { return { ok: true, version: app.getVersion() }; }
  catch (e) { return { ok: false, version: null }; }
});

ipcMain.handle('clipboard:scanPage', async (_e, url) => {
  try {
    if (!url || !/^https?:\/\//i.test(url)) return { ok:false, error:'invalid url' };
    const found = [];
    const push = (u, type, title) => {
      if (!u || typeof u !== 'string') return;
      u = u.trim().replace(/&amp;/g,'&');
      if (!/^https?:\/\//i.test(u)) return;
      if (found.some(x => x.url === u)) return;
      found.push({ url:u, type:type||'video', title: title||null });
    };
    let html='';
    try { html = await _httpsFetchText(url); } catch(e){ html=''; }
    if (html){
      // Extract title
      let pageTitle = null;
      const tm = html.match(/<title[^>]*>([^<]+)<\/title>/i); if (tm) pageTitle = tm[1].trim();
      // canonical
      const cm = html.match(/<link[^>]+rel=["']canonical["'][^>]+href=["']([^"']+)["']/i);
      if (cm) push(cm[1], 'canonical', pageTitle);
      // og:video / twitter:player:stream
      const ogRe = /<meta[^>]+property=["'](og:video(?::url|:secure_url)?|og:video:url)["'][^>]+content=["']([^"']+)["']/gi;
      let m; while ((m = ogRe.exec(html))) push(m[2], 'og:video', pageTitle);
      const twRe = /<meta[^>]+name=["'](twitter:player:stream|twitter:image)["'][^>]+content=["']([^"']+)["']/gi;
      while ((m = twRe.exec(html))) push(m[2], 'twitter', pageTitle);
      // <video src> / <source src>
      const vRe = /<(?:video|source|iframe)[^>]+src=["']([^"']+)["']/gi;
      while ((m = vRe.exec(html))) push(m[1].startsWith('http')?m[1]:new URL(m[1], url).toString(), 'video-tag', pageTitle);
      // JSON-LD VideoObject
      const ldRe = /<script[^>]+type=["']application\/ld\+json["'][^>]*>([\s\S]*?)<\/script>/gi;
      while ((m = ldRe.exec(html))){
        try {
          const j = JSON.parse(m[1].trim());
          const walk = (o) => {
            if (!o || typeof o !== 'object') return;
            if (Array.isArray(o)){ o.forEach(walk); return; }
            if (o['@type'] === 'VideoObject' || o['@type'] === 'Movie'){
              const u = o.contentUrl || o.embedUrl;
              if (u) push(u, 'json-ld', o.name || pageTitle);
            }
            Object.values(o).forEach(walk);
          };
          walk(j);
        } catch(_){}
      }
      // Direct media URLs regex
      const urlRe = /https?:\/\/[^"'\s<>]+\.(?:mp4|m3u8|mpd|webm|mkv)(?:\?[^"'\s<>]*)?/gi;
      while ((m = urlRe.exec(html))) push(m[0], 'media', pageTitle);
      // Episodio/stagione links (streamingcommunity/animeunity/animeworld pattern)
      const epRe = /<a[^>]+href=["']([^"']+)["'][^>]*>([\s\S]*?)<\/a>/gi;
      while ((m = epRe.exec(html))){
        const href = m[1]; const inner = (m[2]||'').replace(/<[^>]+>/g,'').trim();
        if (!/episod|stagion|season|ep\.?\s*\d|s\d+e\d+|puntata/i.test(href+' '+inner)) continue;
        let abs = href;
        try { abs = new URL(href, url).toString(); } catch(e){ continue; }
        if (abs === url) continue;
        if (!found.some(x => x.url === abs)){
          found.push({ url: abs, type: 'episode', title: inner.slice(0,140) || abs });
        }
      }
    }
    if (found.length) return { ok:true, results: found };
    // Fallback: yt-dlp probe
    try {
      const bin = await _resolveYtDlp();
      const probe = await _probeFormats(bin, url);
      if (probe.ok && probe.formats && probe.formats.length){
        const out = probe.formats.slice(0,10).map(f => ({ url: url, type:'yt-dlp-format', title: probe.title, height: f.height, format_id: f.format_id }));
        return { ok:true, results: out, title: probe.title, viaYtDlp:true };
      }
    } catch(_){}
    return { ok:true, results: [] };
  } catch(e){ return { ok:false, error: e.message }; }
});

// ═══ H5: Save frame directly to a chosen dir with explicit filename ═══
ipcMain.handle('save-frame-direct', async (_e, { dir, filename, buffer }) => {
  try {
    if (!dir || !filename || !buffer) return { success:false, error:'missing args' };
    if (!fs.existsSync(dir)) fs.mkdirSync(dir, { recursive:true });
    const full = path.join(dir, filename);
    fs.writeFileSync(full, Buffer.from(buffer));
    return { success:true, path:full };
  } catch(e){ return { success:false, error:e.message }; }
});

// ═══ H11: Relaunch app ═══
ipcMain.handle('app:restart', () => {
  try { app.relaunch(); app.exit(0); } catch(e){}
});

// ═══ OS default app settings ═══
ipcMain.handle('os:open-default-apps', async () => {
  try {
    if (process.platform === 'win32') {
      await shell.openExternal('ms-settings:defaultapps');
      return { success: true, hint: 'Pannello App predefinite aperto. Scorri fino a "Maniac" per impostarlo come player.' };
    } else if (process.platform === 'darwin') {
      await shell.openExternal('x-apple.systempreferences:');
      return { success: true, hint: 'Preferenze di Sistema aperto. Vai su "File predefinito" dal menù contestuale di un video.' };
    } else {
      return { success: false, error: 'Impostazione default app non supportata su questa piattaforma' };
    }
  } catch (e) { return { success: false, error: e.message }; }
});

// ═══ AI / Python venv bridge ═══
let _venvStatusCache = null;
ipcMain.handle('ai:venv-check', async (_e, { force } = {}) => {
  if (_venvStatusCache && !force) return _venvStatusCache;
  _venvStatusCache = await pyBridge.venvCheck();
  return _venvStatusCache;
});
ipcMain.handle('ai:python-path', () => ({ path: pyBridge.resolvePython() }));

// Installa pacchetti python opzionali (es. mutagen, pyacoustid) tramite pip dell'venv311.
// Usato all'avvio per silenziare l'avviso "AI parzialmente disponibile" quando questi
// moduli mancano ma sono recuperabili senza intervento dell'utente.
ipcMain.handle('ai:install-optional', async (_e, { packages } = {}) => {
  try {
    const r = await pyBridge.installOptionalPackages(packages || []);
    if (r && r.ok) _venvStatusCache = null; // forza rinfresco al prossimo venv-check
    return r;
  } catch (e) { return { ok: false, error: e.message }; }
});

// Analyze folder (face/etc) via Python deepface
const _aiJobs = new Map();
ipcMain.handle('ai:analyze-folder', async (_e, { jobId, folder, files, modes, maxFiles, gender,
    tmdbKey, omdbKey, adultColonyBase, stashdbKey,
    useWikidata, useWikipedia, verifyPhoto,
    confirmThreshold, candidateThreshold,
    acoustidKey, musicWriteMode, musicNoBackup, musicUseShazam } = {}) => {
  try {
    const useFiles = Array.isArray(files) && files.length > 0;
    if (!useFiles && (!folder || !fs.existsSync(folder))) return { ok:false, error:'cartella non valida' };
    jobId = jobId || ('ai_' + Date.now());
    const args = [
      '--modes', (modes||['face']).join(','),
      '--max-files', String(maxFiles||200),
      '--gender', String(gender||'both'),
      '--face-db', facesDbFile,
      '--entity-db', facesDbFile,
    ];
    if (useFiles) {
      // Scope ristretto: solo i file passati esplicitamente. Niente scan folder.
      files.forEach(f => { args.push('--files', String(f)); });
    } else {
      args.push('--folder', folder);
    }
    if (tmdbKey) args.push('--tmdb-key', String(tmdbKey));
    if (omdbKey) args.push('--omdb-key', String(omdbKey));
    if (adultColonyBase) args.push('--adultcolony', String(adultColonyBase));
    if (stashdbKey) args.push('--stashdb-key', String(stashdbKey));
    if (useWikidata) args.push('--use-wikidata');
    if (useWikipedia) args.push('--use-wikipedia');
    if (verifyPhoto === false) args.push('--no-verify-photo');
    if (typeof confirmThreshold === 'number') args.push('--confirm-threshold', String(confirmThreshold));
    if (typeof candidateThreshold === 'number') args.push('--candidate-threshold', String(candidateThreshold));
    if (acoustidKey) args.push('--acoustid-key', String(acoustidKey));
    if (musicWriteMode) args.push('--music-write-mode', String(musicWriteMode));
    if (musicNoBackup) args.push('--music-no-backup');
    if (!musicUseShazam) args.push('--music-no-shazam');
    const proc = pyBridge.spawnWorker('analyze.py', args);
    if (!proc) return { ok:false, error:'impossibile avviare Python (venv311)' };
    _aiJobs.set(jobId, proc);
    let buf = '';
    const send = (obj) => { try { mainWindow?.webContents.send('ai:progress', { jobId, ...obj }); } catch(e){} };
    proc.stdout.on('data', d => {
      buf += d.toString();
      let i;
      while ((i = buf.indexOf('\n')) >= 0) {
        const line = buf.slice(0, i).trim(); buf = buf.slice(i+1);
        if (!line) continue;
        try { send(JSON.parse(line)); }
        catch(e) { send({ type:'log', line }); }
      }
    });
    proc.stderr.on('data', d => send({ type:'stderr', line: d.toString() }));
    proc.on('close', code => { _aiJobs.delete(jobId); send({ type:'exit', code }); });
    proc.on('error', err => { _aiJobs.delete(jobId); send({ type:'error', error: err.message }); });
    return { ok:true, jobId };
  } catch(e) { return { ok:false, error:e.message }; }
});
ipcMain.handle('ai:cancel', (_e, jobId) => {
  const p = _aiJobs.get(jobId);
  if (p) { _killProcessTree(p); _aiJobs.delete(jobId); return { ok:true }; }
  return { ok:false };
});

// ═══ Face DB (SQLite) bridge ═══
function runFaceDb(cmdArgs, payload) {
  return new Promise((resolve) => {
    const tmpDir = app.getPath('temp');
    let payloadFile = null;
    try {
      if (payload !== undefined && payload !== null) {
        payloadFile = path.join(tmpDir, 'facedb-' + Date.now() + '-' + Math.random().toString(36).slice(2) + '.json');
        fs.writeFileSync(payloadFile, JSON.stringify(payload), 'utf8');
      }
      const args = ['--db', facesDbFile, ...cmdArgs];
      if (payloadFile) args.push(payloadFile);
      pyBridge.runScript('facedb.py', args, { timeoutMs: 30000 }).then(r => {
        try { if (payloadFile && fs.existsSync(payloadFile)) fs.unlinkSync(payloadFile); } catch(e){}
        if (!r.ok) return resolve({ ok:false, error: r.error || r.stderr || ('exit ' + r.code) });
        const line = (r.stdout || '').trim().split(/\r?\n/).reverse().find(l => l.startsWith('{'));
        if (!line) return resolve({ ok:false, error:'output facedb vuoto' });
        try { resolve(JSON.parse(line)); }
        catch(e) { resolve({ ok:false, error:'parse: ' + e.message }); }
      });
    } catch(e) {
      try { if (payloadFile && fs.existsSync(payloadFile)) fs.unlinkSync(payloadFile); } catch(_){}
      resolve({ ok:false, error: e.message });
    }
  });
}
ipcMain.handle('ai:face-db-match', (_e, payload) => runFaceDb(['match'], payload));
ipcMain.handle('ai:face-db-add', (_e, payload) => runFaceDb(['add'], payload));
ipcMain.handle('ai:face-db-list', () => runFaceDb(['list']));
ipcMain.handle('ai:face-db-delete', (_e, id) => runFaceDb(['delete', String(id)]));

// ═══ Entity DB (SQLite, polimorfico: face/object/place/animal/genre/category) ═══
function runEntityDb(cmdArgs, payload) {
  return new Promise((resolve) => {
    const tmpDir = app.getPath('temp');
    let payloadFile = null;
    try {
      if (payload !== undefined && payload !== null) {
        payloadFile = path.join(tmpDir, 'entitydb-' + Date.now() + '-' + Math.random().toString(36).slice(2) + '.json');
        fs.writeFileSync(payloadFile, JSON.stringify(payload), 'utf8');
      }
      const args = ['--db', facesDbFile, ...cmdArgs];
      if (payloadFile) args.push(payloadFile);
      pyBridge.runScript('entitydb.py', args, { timeoutMs: 30000 }).then(r => {
        try { if (payloadFile && fs.existsSync(payloadFile)) fs.unlinkSync(payloadFile); } catch(e){}
        if (!r.ok) return resolve({ ok:false, error: r.error || r.stderr || ('exit ' + r.code) });
        const line = (r.stdout || '').trim().split(/\r?\n/).reverse().find(l => l.startsWith('{'));
        if (!line) return resolve({ ok:false, error:'output entitydb vuoto' });
        try { resolve(JSON.parse(line)); }
        catch(e) { resolve({ ok:false, error:'parse: ' + e.message }); }
      });
    } catch(e) {
      try { if (payloadFile && fs.existsSync(payloadFile)) fs.unlinkSync(payloadFile); } catch(_){}
      resolve({ ok:false, error: e.message });
    }
  });
}
ipcMain.handle('ai:entity-db-match', (_e, payload) => runEntityDb(['match'], payload));
ipcMain.handle('ai:entity-db-add', (_e, payload) => runEntityDb(['add'], payload));
ipcMain.handle('ai:entity-db-list', (_e, kind) => runEntityDb(['list', ...(kind ? [String(kind)] : [])]));
ipcMain.handle('ai:entity-db-delete', (_e, id) => runEntityDb(['delete', String(id)]));
ipcMain.handle('ai:entity-db-search', (_e, { kind, query } = {}) => runEntityDb(['search', String(kind||'face'), String(query||'')]));
ipcMain.handle('ai:entity-db-migrate', () => runEntityDb(['migrate']));

// ═══ Sottotitoli (subtitles.py) ═══
const _subsJobs = new Map();
function _streamWorker(scriptName, args, channel, jobId) {
  const proc = pyBridge.spawnWorker(scriptName, args);
  if (!proc) return null;
  const send = (obj) => { try { mainWindow?.webContents.send(channel, { jobId, ...obj }); } catch(e){} };
  let buf = '';
  proc.stdout.on('data', d => {
    buf += d.toString();
    let i;
    while ((i = buf.indexOf('\n')) >= 0) {
      const line = buf.slice(0, i).trim(); buf = buf.slice(i+1);
      if (!line) continue;
      try { send(JSON.parse(line)); } catch(e) { send({ type:'log', line }); }
    }
  });
  proc.stderr.on('data', d => send({ type:'stderr', line: d.toString() }));
  proc.on('close', code => send({ type:'exit', code }));
  proc.on('error', err => send({ type:'error', error: err.message }));
  return proc;
}
function _runOneShot(scriptName, args) {
  return new Promise(resolve => {
    pyBridge.runScript(scriptName, args, { timeoutMs: 120000 }).then(r => {
      if (!r.ok) return resolve({ ok:false, error: r.error || r.stderr || ('exit ' + r.code) });
      const line = (r.stdout || '').trim().split(/\r?\n/).reverse().find(l => l.startsWith('{'));
      if (!line) return resolve({ ok:false, error:'output vuoto' });
      try { resolve(JSON.parse(line)); } catch(e) { resolve({ ok:false, error:'parse: ' + e.message }); }
    });
  });
}

ipcMain.handle('subs:generate', (_e, { jobId, video, lang, model, out, engine, geminiKey, translateTo } = {}) => {
  if (!video || !fs.existsSync(video)) return { ok:false, error:'video non valido' };
  jobId = jobId || ('subs_' + Date.now());
  // engine='aisub' richiede una Gemini key valida. Senza key, fallback a faster-whisper.
  if (engine === 'aisub' && (geminiKey || process.env.GOOGLE_API_KEY || process.env.GEMINI_API_KEY)) {
    const outDir = out && fs.existsSync(out) ? out : path.dirname(video);
    const aArgs = ['--video', video, '--out-dir', outDir];
    if (lang && lang !== 'auto') aArgs.push('--lang', String(lang));
    if (translateTo) aArgs.push('--translate-to', String(translateTo));
    if (geminiKey) aArgs.push('--api-key', String(geminiKey));
    const proc = _streamWorker('aisub_runner.py', aArgs, 'subs:progress', jobId);
    if (!proc) return { ok:false, error:'spawn aisub_runner.py fallito' };
    _subsJobs.set(jobId, proc);
    proc.on('close', () => _subsJobs.delete(jobId));
    return { ok:true, jobId, engine: 'aisub' };
  }
  // Fallback: faster-whisper (subtitles.py) — locale, no API key.
  const args = ['generate', '--video', video, '--lang', String(lang || 'auto')];
  if (model) args.push('--model', String(model));
  if (out)   args.push('--out', String(out));
  const proc = _streamWorker('subtitles.py', args, 'subs:progress', jobId);
  if (!proc) return { ok:false, error:'spawn subtitles.py fallito' };
  _subsJobs.set(jobId, proc);
  proc.on('close', () => _subsJobs.delete(jobId));
  return { ok:true, jobId, engine: 'whisper' };
});
ipcMain.handle('subs:translate', (_e, { jobId, srt, fromLang, toLang, out } = {}) => {
  if (!srt || !fs.existsSync(srt)) return { ok:false, error:'srt non valido' };
  jobId = jobId || ('subs_' + Date.now());
  const args = ['translate', '--srt', srt, '--from', String(fromLang), '--to', String(toLang)];
  if (out) args.push('--out', String(out));
  const proc = _streamWorker('subtitles.py', args, 'subs:progress', jobId);
  if (!proc) return { ok:false, error:'spawn subtitles.py fallito' };
  _subsJobs.set(jobId, proc);
  proc.on('close', () => _subsJobs.delete(jobId));
  return { ok:true, jobId };
});
ipcMain.handle('subs:list-langs', () => _runOneShot('subtitles.py', ['list-langs']));
ipcMain.handle('subs:install-lang', (_e, { jobId, fromLang, toLang } = {}) => {
  jobId = jobId || ('subs_' + Date.now());
  const proc = _streamWorker('subtitles.py',
    ['install-lang', '--from', String(fromLang), '--to', String(toLang)],
    'subs:progress', jobId);
  if (!proc) return { ok:false, error:'spawn subtitles.py fallito' };
  _subsJobs.set(jobId, proc);
  proc.on('close', () => _subsJobs.delete(jobId));
  return { ok:true, jobId };
});
ipcMain.handle('subs:cancel', (_e, jobId) => {
  const p = _subsJobs.get(jobId);
  if (p) { _killProcessTree(p); _subsJobs.delete(jobId); return { ok:true }; }
  return { ok:false };
});

// Cerca un file di sottotitoli "fratello" del video (stesso path, .srt/.vtt).
// Preferisce localizzati: <name>.<lang>.srt > <name>.srt > <name>.vtt
ipcMain.handle('subs:find-sibling', (_e, videoPath) => {
  try {
    if (!videoPath || !fs.existsSync(videoPath)) return { ok:false };
    const dir = path.dirname(videoPath);
    const base = path.basename(videoPath, path.extname(videoPath));
    const entries = fs.readdirSync(dir);
    // Match case-insensitive: stesso basename + (.lang)?.srt|.vtt
    const re = new RegExp('^' + base.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + '(?:\\.[a-zA-Z-]+)?\\.(srt|vtt)$', 'i');
    const hits = entries.filter(n => re.test(n)).map(n => path.join(dir, n));
    if (!hits.length) return { ok:true, path:null };
    // Priorità: .srt prima, poi .vtt; senza lang prima di con-lang
    hits.sort((a, b) => {
      const ax = path.extname(a).toLowerCase(), bx = path.extname(b).toLowerCase();
      if (ax !== bx) return ax === '.srt' ? -1 : 1;
      const aHasLang = path.basename(a, ax).toLowerCase() !== base.toLowerCase();
      const bHasLang = path.basename(b, bx).toLowerCase() !== base.toLowerCase();
      if (aHasLang !== bHasLang) return aHasLang ? 1 : -1;
      return a.localeCompare(b);
    });
    return { ok:true, path: hits[0] };
  } catch(e) { return { ok:false, error: String(e.message||e) }; }
});

// Legge il contenuto di un file SRT/VTT come testo (per il parsing client-side)
ipcMain.handle('subs:read-text', (_e, filePath) => {
  try {
    if (!filePath || !fs.existsSync(filePath)) return { ok:false, error:'file non trovato' };
    const text = fs.readFileSync(filePath, 'utf8');
    return { ok:true, text };
  } catch(e) { return { ok:false, error: String(e.message||e) }; }
});

// OpenAI cloud (whisper-1 + gpt-4o-mini per traduzione)
function _resolveFfmpegBin() {
  const dir = _resolveFfmpeg(null);
  return dir ? path.join(dir, 'ffmpeg.exe') : null;
}
ipcMain.handle('subs:openai-generate', (_e, { jobId, video, lang, apiKey, model, out } = {}) => {
  if (!video || !fs.existsSync(video)) return { ok:false, error:'video non valido' };
  if (!apiKey) return { ok:false, error:'API key OpenAI mancante' };
  jobId = jobId || ('subs_oa_' + Date.now());
  const args = ['generate', '--video', video,
                '--api-key', String(apiKey),
                '--lang', String(lang || 'auto')];
  const ff = _resolveFfmpegBin();
  if (ff) args.push('--ffmpeg', ff);
  if (model) args.push('--model', String(model));
  if (out)   args.push('--out', String(out));
  const proc = _streamWorker('subs_openai.py', args, 'subs:progress', jobId);
  if (!proc) return { ok:false, error:'spawn subs_openai.py fallito' };
  _subsJobs.set(jobId, proc);
  proc.on('close', () => _subsJobs.delete(jobId));
  return { ok:true, jobId };
});
ipcMain.handle('subs:openai-translate', (_e, { jobId, srt, toLang, apiKey, model, out } = {}) => {
  if (!srt || !fs.existsSync(srt)) return { ok:false, error:'srt non valido' };
  if (!apiKey) return { ok:false, error:'API key OpenAI mancante' };
  jobId = jobId || ('subs_oa_tr_' + Date.now());
  const args = ['translate', '--srt', srt,
                '--to', String(toLang),
                '--api-key', String(apiKey)];
  if (model) args.push('--model', String(model));
  if (out)   args.push('--out', String(out));
  const proc = _streamWorker('subs_openai.py', args, 'subs:progress', jobId);
  if (!proc) return { ok:false, error:'spawn subs_openai.py fallito' };
  _subsJobs.set(jobId, proc);
  proc.on('close', () => _subsJobs.delete(jobId));
  return { ok:true, jobId };
});

// ═══ Organizer (organizer.py) ═══
const _organizerJobs = new Map();
function _snapshotsDir() {
  const d = path.join(app.getPath('userData'), 'organizer_snapshots');
  try { fs.mkdirSync(d, { recursive: true }); } catch(_){}
  return d;
}
function _writeJsonTemp(prefix, obj) {
  const p = path.join(app.getPath('temp'),
    `${prefix}-${Date.now()}-${Math.random().toString(36).slice(2,8)}.json`);
  fs.writeFileSync(p, JSON.stringify(obj), 'utf8');
  return p;
}

ipcMain.handle('organizer:scan', (_e, { jobId, folders, max } = {}) => {
  if (!Array.isArray(folders) || !folders.length) return { ok:false, error:'cartelle mancanti' };
  jobId = jobId || ('org_' + Date.now());
  const args = ['scan', '--folders', folders.join(',')];
  if (max) args.push('--max', String(max));
  const proc = _streamWorker('organizer.py', args, 'organizer:progress', jobId);
  if (!proc) return { ok:false, error:'spawn organizer.py fallito' };
  _organizerJobs.set(jobId, proc);
  proc.on('close', () => _organizerJobs.delete(jobId));
  return { ok:true, jobId };
});
ipcMain.handle('organizer:build-ops', async (_e, { files, root, by } = {}) => {
  if (!Array.isArray(files) || !files.length) return { ok:false, error:'files vuoti' };
  if (!root) return { ok:false, error:'root mancante' };
  const fp = _writeJsonTemp('org-files', files);
  try {
    const r = await _runOneShot('organizer.py',
      ['build-ops', '--files-json', fp, '--root', root,
       '--by', (Array.isArray(by) ? by.join(',') : (by || 'type,year'))]);
    return r;
  } finally { try { fs.unlinkSync(fp); } catch(_){} }
});
ipcMain.handle('organizer:execute', async (_e, { jobId, ops } = {}) => {
  if (!Array.isArray(ops) || !ops.length) return { ok:false, error:'ops vuoti' };
  jobId = jobId || ('org_' + Date.now());
  const fp = _writeJsonTemp('org-ops', ops);
  const args = ['execute', '--ops-json', fp, '--snapshots-dir', _snapshotsDir()];
  const proc = _streamWorker('organizer.py', args, 'organizer:progress', jobId);
  if (!proc) { try { fs.unlinkSync(fp); } catch(_){} return { ok:false, error:'spawn fallito' }; }
  _organizerJobs.set(jobId, proc);
  proc.on('close', () => { _organizerJobs.delete(jobId); try { fs.unlinkSync(fp); } catch(_){} });
  return { ok:true, jobId };
});
ipcMain.handle('organizer:snapshots', () =>
  _runOneShot('organizer.py', ['snapshots', '--snapshots-dir', _snapshotsDir()]));
ipcMain.handle('organizer:restore', (_e, snapshotPath) => {
  if (!snapshotPath || !fs.existsSync(snapshotPath))
    return { ok:false, error:'snapshot non valido' };
  const jobId = 'org_' + Date.now();
  const proc = _streamWorker('organizer.py',
    ['restore', '--snapshot', snapshotPath], 'organizer:progress', jobId);
  if (!proc) return { ok:false, error:'spawn fallito' };
  _organizerJobs.set(jobId, proc);
  proc.on('close', () => _organizerJobs.delete(jobId));
  return { ok:true, jobId };
});
ipcMain.handle('organizer:cancel', (_e, jobId) => {
  const p = _organizerJobs.get(jobId);
  if (p) { _killProcessTree(p); _organizerJobs.delete(jobId); return { ok:true }; }
  return { ok:false };
});

// ═══ i18n persistence (locale code in userData/locale.json) ═══
function _localeFile() { return path.join(app.getPath('userData'), 'locale.json'); }
ipcMain.handle('i18n:get-locale', () => {
  try {
    const p = _localeFile();
    if (fs.existsSync(p)) return JSON.parse(fs.readFileSync(p, 'utf8')).code || 'it';
  } catch(_){}
  return 'it';
});
ipcMain.handle('i18n:set-locale', (_e, code) => {
  try {
    fs.writeFileSync(_localeFile(),
      JSON.stringify({ code: String(code || 'it') }), 'utf8');
    return { ok:true };
  } catch(e) { return { ok:false, error: e.message }; }
});

ipcMain.handle('window-minimize', () => mainWindow?.minimize());
ipcMain.handle('window-maximize-toggle', () => {
  if (!mainWindow) return;
  if (mainWindow.isMaximized()) mainWindow.unmaximize(); else mainWindow.maximize();
});
ipcMain.handle('window-close', () => mainWindow?.close());
ipcMain.handle('window-is-maximized', () => mainWindow?.isMaximized() || false);
ipcMain.handle('window-set-always-on-top', (_e, on) => {
  if (!mainWindow) return false;
  mainWindow.setAlwaysOnTop(!!on, 'screen-saver');
  return mainWindow.isAlwaysOnTop();
});
ipcMain.handle('window-is-always-on-top', () => mainWindow?.isAlwaysOnTop() || false);
ipcMain.handle('open-external', (_e, url) => shell.openExternal(url));

// ═══════════════════════════════════════════════
// STASHDB GraphQL CLIENT (search performer/scene + auto-tag library)
// ═══════════════════════════════════════════════
function _stashdbScriptPath() {
  const dir = pyBridge.resolvePyDir() || path.join(__dirname, 'python');
  return path.join(dir, 'stashdb.py');
}
function _stashdbInterp() {
  return pyBridge.resolvePython() || (process.platform === 'win32' ? 'python' : 'python3');
}
const _stashdbJobs = new Map();

function _runStashdb(args, timeoutMs) {
  return new Promise(resolve => {
    const interp = _stashdbInterp();
    const proc = spawn(interp, [_stashdbScriptPath(), ...args], { windowsHide: true });
    let out = '', err = '';
    proc.stdout.on('data', d => { out += d.toString(); });
    proc.stderr.on('data', d => { err += d.toString(); });
    const to = setTimeout(() => { _killProcessTree(proc); resolve({ok:false,error:'timeout'}); }, timeoutMs || 25000);
    proc.on('close', () => {
      clearTimeout(to);
      const line = out.split(/\r?\n/).reverse().find(l => l.trim().startsWith('{')) || '';
      try { resolve(JSON.parse(line)); }
      catch(_) { resolve({ ok:false, error: 'parse: ' + (err || '').slice(0,200) }); }
    });
    proc.on('error', e => { clearTimeout(to); resolve({ ok:false, error: e.message }); });
  });
}

ipcMain.handle('stashdb:setKey',         async (_e, key) => await _runStashdb(['set-key', '--key', String(key || '')]));
ipcMain.handle('stashdb:searchPerformer',async (_e, { q, limit }) => await _runStashdb(['search-performer', '--q', String(q || ''), '--limit', String(limit || 10)]));
ipcMain.handle('stashdb:searchScene',    async (_e, { q, limit }) => await _runStashdb(['search-scene', '--q', String(q || ''), '--limit', String(limit || 10)]));
ipcMain.handle('stashdb:performerInfo',  async (_e, id) => await _runStashdb(['performer-info', '--id', String(id || '')]));

// API key check (per il guard del wizard riconoscimento attori)
const _stashdbKeyFile = process.env.STASHDB_KEY_FILE;
ipcMain.handle('stashdb:hasKey', () => {
  try {
    if (process.env.STASHDB_API_KEY && process.env.STASHDB_API_KEY.trim()) return { ok:true, hasKey:true };
    if (!_stashdbKeyFile || !fs.existsSync(_stashdbKeyFile)) return { ok:true, hasKey:false };
    const k = (fs.readFileSync(_stashdbKeyFile, 'utf8') || '').trim();
    return { ok:true, hasKey: k.length > 0 };
  } catch(_) { return { ok:true, hasKey:false }; }
});

// Resolver attrici: clustering cross-file + StashDB photo-vs-photo
const _actorResolveJobs = new Map();
ipcMain.handle('ai:actor-resolve', async (_e, opts) => {
  const { jobId, clusters, mergeThreshold, confirmThreshold, topk,
          maxPhotosPerPerformer, maxPerformersPerQuery, photoVotes,
          tmdbKey, useWikidata, useWikipedia, noStashdb } = opts || {};
  if (!Array.isArray(clusters) || !clusters.length) return { ok:false, error:'no_clusters' };
  const id = jobId || ('actorres_' + Date.now() + '_' + Math.random().toString(36).slice(2,6));
  const tmpFile = path.join(userDataPath, 'tmp-actor-clusters-' + id + '.json');
  try { fs.writeFileSync(tmpFile, JSON.stringify({ clusters }), 'utf8'); }
  catch (e) { return { ok:false, error:'tmp write: ' + e.message }; }
  const args = [
    'resolve',
    '--clusters', tmpFile,
    '--facedb', facesDbFile,
    '--merge-threshold', String(mergeThreshold || 0.62),
    '--confirm-threshold', String(confirmThreshold || 0.55),
    '--topk', String(topk || 5),
    '--max-photos-per-performer', String(maxPhotosPerPerformer || 10),
    '--max-performers-per-query', String(maxPerformersPerQuery || 5),
    '--photo-votes', String(photoVotes || 2),
  ];
  // Fonti oltre StashDB: coprono attori, musicisti, sportivi e volti pubblici,
  // che nessun database adult può contenere.
  if (tmdbKey) args.push('--tmdb-key', String(tmdbKey));
  if (useWikidata)  args.push('--use-wikidata');
  if (useWikipedia) args.push('--use-wikipedia');
  if (noStashdb)    args.push('--no-stashdb');
  const proc = _streamWorker('face_actor_resolver.py', args, 'ai:actor-resolve:progress', id);
  if (!proc) { try { fs.unlinkSync(tmpFile); } catch(_){}; return { ok:false, error:'spawn failed' }; }
  _actorResolveJobs.set(id, proc);
  proc.on('close', () => {
    _actorResolveJobs.delete(id);
    try { fs.unlinkSync(tmpFile); } catch(_){}
  });
  return { ok:true, jobId:id };
});
ipcMain.handle('ai:actor-resolve-cancel', (_e, jobId) => {
  const p = _actorResolveJobs.get(jobId);
  if (p) { _killProcessTree(p); _actorResolveJobs.delete(jobId); return { ok:true }; }
  return { ok:false };
});

ipcMain.handle('stashdb:autoTag', async (_e, { jobId, files } = {}) => {
  jobId = jobId || ('stashdb_' + Date.now());
  if (!Array.isArray(files) || !files.length) return { ok:false, error: 'lista files vuota' };
  const tmpFile = path.join(userDataPath, 'tmp-stashdb-files.json');
  try { fs.writeFileSync(tmpFile, JSON.stringify(files), 'utf8'); }
  catch(e) { return { ok:false, error: 'tmp write: ' + e.message }; }
  const interp = _stashdbInterp();
  const args = [_stashdbScriptPath(), 'scene-by-filename', '--files-list', tmpFile];
  let proc;
  try { proc = spawn(interp, args, { windowsHide: true }); }
  catch(err) { return { ok:false, error: 'spawn: ' + err.message }; }
  _stashdbJobs.set(jobId, proc);
  let buf = '';
  proc.stdout.on('data', d => {
    buf += d.toString();
    let nl;
    while ((nl = buf.indexOf('\n')) >= 0) {
      const line = buf.slice(0, nl).trim(); buf = buf.slice(nl + 1);
      if (!line) continue;
      try { _safeSend('stashdb:progress', { jobId, ...JSON.parse(line) }); }
      catch(_) { _safeSend('stashdb:progress', { jobId, type: 'log', text: line.slice(0, 200) }); }
    }
  });
  proc.on('close', () => {
    _stashdbJobs.delete(jobId);
    try { fs.unlinkSync(tmpFile); } catch(_){}
  });
  return { ok:true, jobId };
});

ipcMain.handle('stashdb:cancel', (_e, jobId) => {
  const p = _stashdbJobs.get(jobId);
  if (p) { _killProcessTree(p); _stashdbJobs.delete(jobId); return { ok:true }; }
  return { ok:false };
});

// ═══════════════════════════════════════════════
// LIBRARY AUTO-TAG via stash community-scrapers (consenso multi-fonte)
// ═══════════════════════════════════════════════
const _scrapersDir = path.join(_resBase(), 'models', 'scrapers');
const _scrapeJobs = new Map();

ipcMain.handle('scrape:listScrapers', async () => {
  return await new Promise(resolve => {
    const interp = _pyInterp();
    const proc = spawn(interp, [_pyScript('scraper_engine.py'),
                                'list-scrapers', '--dir', _scrapersDir], { windowsHide: true });
    let out = '', err = '';
    proc.stdout.on('data', d => { out += d.toString(); });
    proc.stderr.on('data', d => { err += d.toString(); });
    const to = setTimeout(() => { _killProcessTree(proc); resolve({ok:false,error:'timeout'}); }, 20000);
    proc.on('close', () => {
      clearTimeout(to);
      const line = out.split(/\r?\n/).reverse().find(l => l.trim().startsWith('{')) || '';
      try { resolve(JSON.parse(line)); } catch(_) { resolve({ ok:false, error:'parse: ' + (err||'').slice(0,200) }); }
    });
    proc.on('error', e => { clearTimeout(to); resolve({ ok:false, error: e.message }); });
  });
});

ipcMain.handle('scrape:autoTagFiles', async (_e, { jobId, files, minVotes, maxSources } = {}) => {
  jobId = jobId || ('scr_' + Date.now());
  if (!Array.isArray(files) || !files.length) return { ok:false, error:'lista files vuota' };
  if (!fs.existsSync(_scrapersDir)) {
    return { ok:false, error:'cartella models/scrapers non trovata. Scarica gli scrapers da github.com/stashapp/community-scrapers' };
  }
  // Salva files-list in tmp JSON
  const tmpFile = path.join(userDataPath, 'tmp-autotag-files.json');
  try { fs.writeFileSync(tmpFile, JSON.stringify(files), 'utf8'); }
  catch(e) { return { ok:false, error:'scrittura tmp: ' + e.message }; }
  const interp = _pyInterp();
  const args = [_pyScript('library_autotag.py'),
                '--scrapers-dir', _scrapersDir,
                '--files-list', tmpFile,
                '--min-votes', String(minVotes || 3),
                '--max-sources', String(maxSources || 6)];
  let proc;
  try { proc = spawn(interp, args, { windowsHide: true }); }
  catch(err) { return { ok:false, error: 'spawn: ' + err.message }; }
  _scrapeJobs.set(jobId, proc);
  let buf = '';
  proc.stdout.on('data', d => {
    buf += d.toString();
    let nl;
    while ((nl = buf.indexOf('\n')) >= 0) {
      const line = buf.slice(0, nl).trim(); buf = buf.slice(nl + 1);
      if (!line) continue;
      try { mainWindow?.webContents.send('scrape:progress', { jobId, ...JSON.parse(line) }); }
      catch(_) { mainWindow?.webContents.send('scrape:progress', { jobId, type:'log', text: line.slice(0,200) }); }
    }
  });
  proc.on('close', () => { _scrapeJobs.delete(jobId); try { fs.unlinkSync(tmpFile); } catch(_){} });
  return { ok:true, jobId };
});

ipcMain.handle('scrape:cancel', (_e, jobId) => {
  const p = _scrapeJobs.get(jobId);
  if (p) { _killProcessTree(p); _scrapeJobs.delete(jobId); return { ok:true }; }
  return { ok:false };
});

// ═══════════════════════════════════════════════
// EPORNER CATEGORIES DB (tassonomia SFW/NSFW + cache video)
// ═══════════════════════════════════════════════
const _epornerDb = path.join(userDataPath, 'eporner_cats.db');
function _runEporner(args) {
  return new Promise(resolve => {
    const interp = _pyInterp();
    const proc = spawn(interp, [_pyScript('eporner_cats.py'),
                                 '--db', _epornerDb, ...args], { windowsHide: true });
    let out = '', err = '';
    proc.stdout.on('data', d => { out += d.toString(); });
    proc.stderr.on('data', d => { err += d.toString(); });
    const to = setTimeout(() => { try { _killProcessTree(proc); } catch(_){} resolve({ok:false,error:'timeout'}); }, 30000);
    proc.on('close', () => {
      clearTimeout(to);
      const line = out.split(/\r?\n/).reverse().find(l => l.trim().startsWith('{')) || '';
      try { resolve(JSON.parse(line)); } catch(_) { resolve({ ok:false, error:'parse: ' + (err||'').slice(0,200) }); }
    });
    proc.on('error', e => { clearTimeout(to); resolve({ ok:false, error: e.message }); });
  });
}
ipcMain.handle('eporner:seed',           async () => await _runEporner(['seed']));
ipcMain.handle('eporner:listCategories', async (_e, kind) => {
  const args = ['list']; if (kind) args.push('--kind', String(kind));
  return await _runEporner(args);
});
ipcMain.handle('eporner:search',         async (_e, query) => await _runEporner(['search', '--q', String(query || '')]));
ipcMain.handle('eporner:byCategory',     async (_e, cat)   => await _runEporner(['by-cat', '--c', String(cat || '')]));
// Auto-seed al primo avvio (se DB non esiste).
app.whenReady().then(() => {
  setTimeout(() => {
    if (!fs.existsSync(_epornerDb)) { _runEporner(['seed']).catch(()=>{}); }
  }, 8000);
});

// ═══════════════════════════════════════════════
// WIZARD STREAMINGCOMMUNITY (search/info/seasons via headless BrowserWindow)
// ═══════════════════════════════════════════════
// Il sito espone /api/search ma protegge tutto con un challenge FingerprintJS:
// le richieste fatte da Python ricevono sempre HTML di redirect, anche con cookie.
// Soluzione: tengo aperto in background un BrowserWindow non visibile che ha
// già superato il challenge, ed eseguo le query API tramite webContents.fetch
// (executeJavaScript). I download veri (M3U8) passano invece da Python perché
// lì serve l'integrazione con ffmpeg + le funzioni del repo SC.
let _scBrowser = null;
let _scBrowserBaseUrl = '';
let _scBrowserReadyAt = 0;
let _scWarmingUp = null; // Promise di warmup in corso
const _scShellTtlMs = 30 * 60 * 1000; // riusa la stessa shell per 30 min
let _scCookieCache = { ts: 0, header: '', baseUrl: '' };
const _scWarmupTtlMs = 30 * 60 * 1000; // 30 min

async function _resolveScDomain(force=false) {
  return await new Promise((resolve) => {
    const interp = _pyInterp();
    const proc = spawn(interp, [_pyScript('sc_domain.py'), ...(force?['--force']:[])], { windowsHide: true });
    let out=''; let err='';
    proc.stdout.on('data', d=> out+=d.toString());
    proc.stderr.on('data', d=> err+=d.toString());
    const to = setTimeout(()=>{ try{proc.kill();}catch(_){} resolve({ok:false,error:'timeout'}); }, 30000);
    proc.on('close', () => {
      clearTimeout(to);
      const line = out.split(/\r?\n/).reverse().find(l => l.trim().startsWith('{')) || '';
      try { resolve(JSON.parse(line)); } catch(_) { resolve({ok:false, error:'parse '+(err||'').slice(0,120)}); }
    });
    proc.on('error', e => { clearTimeout(to); resolve({ok:false, error:e.message}); });
  });
}

// Avvia (o riusa) un BrowserWindow nascosto che ha già superato il challenge JS.
// Idempotente: se è in corso un warmup, ritorna la stessa Promise.
async function _scEnsureShell(force=false) {
  if (_scWarmingUp) return _scWarmingUp;
  if (!force && _scBrowser && !_scBrowser.isDestroyed() && (Date.now() - _scBrowserReadyAt) < _scShellTtlMs) {
    return { ok:true, baseUrl: _scBrowserBaseUrl, cached:true };
  }
  _scWarmingUp = (async () => {
    try {
      if (_scBrowser && !_scBrowser.isDestroyed()) {
        try { _scBrowser.destroy(); } catch(_){}
      }
      const dom = await _resolveScDomain(force);
      if (!dom || !dom.ok || !dom.url) return { ok:false, error:'dominio SC non risolto' };
      const baseUrl = dom.url;
      const win = new BrowserWindow({
        width: 1024, height: 768, show: false,
        webPreferences: {
          javascript: true,
          contextIsolation: true,
          sandbox: true,
          // niente offscreen: alcuni siti rilevano canvas/WebGL via FingerprintJS
        }
      });
      try { win.webContents.setUserAgent(_DEFAULT_UA); } catch(_){}
      try { await win.loadURL(baseUrl); } catch(loadErr) {
        return { ok:false, error:'load '+baseUrl+': '+(loadErr.message||loadErr) };
      }
      // Attendi che il challenge JS rediriga e setti i cookie + Inertia state.
      // Il sito esegue redirect via JS al primo load: aspettiamo qualche tick
      // dopo did-finish-load PRIMA che il fetch all'API risponda con JSON.
      // Retry con tempi crescenti (3s + 4s + 4s = 11s totale) per cope anti-bot lenti.
      for (const wait of [3000, 4000, 4000]) {
        await new Promise(r => setTimeout(r, wait));
        try {
          // Verifica leggera: prova un fetch all'API per vedere se torna JSON
          const probe = await win.webContents.executeJavaScript(
            "(async()=>{try{const r=await fetch('/api/search?q=test',{credentials:'include'});const ct=r.headers.get('content-type')||'';return{status:r.status,ct,ok:r.ok&&ct.includes('json')};}catch(e){return{status:0,error:String(e)};}})()",
            true
          );
          if (probe && probe.ok) break;
        } catch(_) {}
      }
      // Verifica: l'URL corrente deve essere lo stesso dominio (non di redirect altrove).
      const finalUrl = win.webContents.getURL() || baseUrl;
      _scBrowser = win;
      _scBrowserBaseUrl = (() => { try { const u = new URL(finalUrl); return u.origin; } catch(_) { return baseUrl; } })();
      _scBrowserReadyAt = Date.now();
      // Esporta i cookies (per i download Python)
      try {
        const cookies = await win.webContents.session.cookies.get({ url: _scBrowserBaseUrl });
        const header = cookies.map(c => `${c.name}=${c.value}`).join('; ');
        _scCookieCache = { ts: Date.now(), header, baseUrl: _scBrowserBaseUrl };
      } catch(_){}
      return { ok:true, baseUrl: _scBrowserBaseUrl };
    } catch(e) {
      return { ok:false, error:'shell SC fallita: '+e.message };
    } finally {
      _scWarmingUp = null;
    }
  })();
  return _scWarmingUp;
}

// Esegue una fetch JSON DENTRO la shell (così riusa cookie + fingerprint validi).
async function _scFetchJSON(relPath, opts={}) {
  const sh = await _scEnsureShell(false);
  if (!sh.ok) return { ok:false, error: sh.error || 'shell non pronta' };
  if (!_scBrowser || _scBrowser.isDestroyed()) return { ok:false, error:'shell distrutta' };
  const fullUrl = relPath.startsWith('http') ? relPath : (_scBrowserBaseUrl.replace(/\/$/,'') + relPath);
  const headers = Object.assign({ 'Accept':'application/json, text/plain, */*' }, opts.headers || {});
  const code = `(async () => {
    try {
      const r = await fetch(${JSON.stringify(fullUrl)}, { credentials:'include', headers:${JSON.stringify(headers)} });
      const ct = r.headers.get('content-type') || '';
      const txt = await r.text();
      return { ok:true, status:r.status, ct, body:txt.slice(0,200000) };
    } catch(e) { return { ok:false, error:String(e&&e.message||e) }; }
  })()`;
  let res;
  try { res = await _scBrowser.webContents.executeJavaScript(code, true); }
  catch(e) { return { ok:false, error:'executeJavaScript: '+e.message }; }
  if (!res || !res.ok) return res || { ok:false, error:'no-result' };
  // Se ricevuto HTML di challenge, rifai warmup e riprova UNA volta
  const looksHtmlChallenge = !res.ct.includes('json') && /redirect_link|fingerprint/i.test(res.body);
  if (looksHtmlChallenge && !opts._retried) {
    await _scEnsureShell(true);
    return _scFetchJSON(relPath, { ...opts, _retried:true });
  }
  if (!res.ct.includes('json')) {
    return { ok:false, error:'non-JSON response (status '+res.status+')', preview: res.body.slice(0,400) };
  }
  try { return { ok:true, status:res.status, json: JSON.parse(res.body) }; }
  catch(e) { return { ok:false, error:'JSON parse: '+e.message }; }
}

// Estrae poster URL dall'array images della response.
function _scCoverUrl(images) {
  if (!Array.isArray(images)) return null;
  const cdn = (_scBrowserBaseUrl.replace(/^https?:\/\/[^.]+/, 'https://cdn') || 'https://cdn.streamingcommunity.computer');
  const fallback = 'https://cdn.streamingcommunity.computer';
  for (const kind of ['poster','cover','background','logo']) {
    for (const img of images) {
      if (img && img.type === kind && img.filename) return `${fallback}/images/${img.filename}`;
    }
  }
  for (const img of images) {
    if (img && img.filename) return `${fallback}/images/${img.filename}`;
  }
  return null;
}

// Compatibilità con i chiamanti precedenti (Python warmup).
async function _scWarmupCookies(force=false) {
  if (!force && _scCookieCache.header && (Date.now() - _scCookieCache.ts) < _scWarmupTtlMs) {
    return { ok:true, header:_scCookieCache.header, baseUrl:_scCookieCache.baseUrl, cached:true };
  }
  const sh = await _scEnsureShell(force);
  if (!sh.ok) return { ok:false, error: sh.error || 'shell non pronta' };
  return { ok:true, header:_scCookieCache.header, baseUrl:_scCookieCache.baseUrl };
}

function _runScScript(args, { onProgress } = {}) {
  return new Promise(async (resolve) => {
    const wu = await _scWarmupCookies(false);
    const env = Object.assign({}, process.env);
    if (wu.ok && wu.header) env.SCWIZ_COOKIE = wu.header;
    const interp = _pyInterp();
    const proc = spawn(interp, [_pyScript('sc_search.py'), ...args], { windowsHide: true, env });
    let out = ''; let err = '';
    proc.stdout.on('data', d => {
      const s = d.toString(); out += s;
      if (onProgress) {
        for (const line of s.split(/\r?\n/)) {
          const t = line.trim();
          if (t.startsWith('{') && t.includes('"event"')) {
            try { onProgress(JSON.parse(t)); } catch(_) {}
          }
        }
      }
    });
    proc.stderr.on('data', d => { err += d.toString(); });
    const to = setTimeout(()=>{ try{proc.kill();}catch(_){} resolve({ok:false,error:'timeout'}); }, 90_000);
    proc.on('close', () => {
      clearTimeout(to);
      const line = out.split(/\r?\n/).reverse().find(l => l.trim().startsWith('{') && l.includes('"ok"')) || '';
      try { resolve(JSON.parse(line)); } catch(_) { resolve({ok:false, error:'parse: '+(err||'').slice(0,200)}); }
    });
    proc.on('error', e => { clearTimeout(to); resolve({ok:false, error:e.message}); });
  });
}

ipcMain.handle('wizard:scResolveDomain', async () => {
  // Forza un warmup della shell + ritorna lo stato.
  const sh = await _scEnsureShell(true);
  return sh.ok ? { ok:true, url: _scBrowserBaseUrl, source:'shell' } : sh;
});

// Imposta manualmente il dominio SC scrivendo data.json + ricreando la shell.
ipcMain.handle('wizard:scSetDomain', async (_e, domain) => {
  if (!domain || typeof domain !== 'string') return { ok:false, error:'dominio vuoto' };
  let url = String(domain).trim();
  if (!url) return { ok:false, error:'dominio vuoto' };
  // normalizza: rimuovi schema/percorso, riapplica https://
  url = url.replace(/^https?:\/\//i, '').replace(/\/.*$/, '').replace(/\/$/, '');
  if (!/^[a-z0-9.-]+\.[a-z]{2,}$/i.test(url)) return { ok:false, error:'dominio non valido' };
  const fullUrl = 'https://' + url;
  const tld = url.split('.').pop().toLowerCase();
  try {
    const dataPath = process.env.SC_DATA_JSON;
    fs.writeFileSync(dataPath, JSON.stringify({ domain: tld, url: fullUrl, cached_at: Math.floor(Date.now()/1000) }, null, 2));
  } catch (e) {
    return { ok:false, error:'scrittura data.json: '+e.message };
  }
  if (_scBrowser && !_scBrowser.isDestroyed()) { try { _scBrowser.destroy(); } catch(_){} }
  _scBrowser = null; _scBrowserBaseUrl = ''; _scBrowserReadyAt = 0;
  _scCookieCache = { ts: 0, header: '', baseUrl: '' };
  const sh = await _scEnsureShell(true);
  if (!sh || !sh.ok) return { ok:false, error:(sh && sh.error)||'warmup fallito', url: fullUrl };
  return { ok:true, url: _scBrowserBaseUrl };
});

ipcMain.handle('wizard:scGetDomain', () => {
  try {
    const dataPath = process.env.SC_DATA_JSON;
    if (fs.existsSync(dataPath)) {
      const j = JSON.parse(fs.readFileSync(dataPath, 'utf8'));
      return { ok:true, url: j.url||'', domain: j.domain||'', cached_at: j.cached_at||0, current: _scBrowserBaseUrl||'' };
    }
  } catch(_) {}
  return { ok:true, url:'', domain:'', cached_at:0, current: _scBrowserBaseUrl||'' };
});

// Estrae site_version dal data-page del div#app (richiesto come header X-Inertia-Version).
async function _scSiteVersion() {
  if (!_scBrowser || _scBrowser.isDestroyed()) return '';
  try {
    return await _scBrowser.webContents.executeJavaScript(
      `(function(){try{const n=document.getElementById('app');if(!n)return '';const m=JSON.parse(n.getAttribute('data-page'));return m&&m.version||'';}catch(_){return '';}})()`,
      true
    ) || '';
  } catch(_) { return ''; }
}

ipcMain.handle('wizard:scSearch', async (_e, { query, forceRefresh }) => {
  if (!query || !String(query).trim()) return { ok:false, error:'query vuota' };
  const q = encodeURIComponent(String(query).trim());
  // Se l'utente ha cliccato "Refresh dominio" o se il fetch fallisce, ri-risolvi.
  if (forceRefresh) {
    try { await _scEnsureShell(true); } catch(_){}
  }
  let r = await _scFetchJSON(`/api/search?q=${q}`);
  // Se la prima fetch è errata o ritorna 0 risultati su una query non vuota,
  // forziamo un refresh dominio + retry. Capita quando il sito ha appena
  // cambiato TLD (es. ooo → organic) e la cache è ancora valida ma "vecchia".
  let usedRefresh = false;
  if (!r.ok || !r.json || !((r.json.data || []).length)) {
    if (!forceRefresh) {
      try { await _scEnsureShell(true); usedRefresh = true; } catch(_){}
      r = await _scFetchJSON(`/api/search?q=${q}`);
    }
  }
  if (!r.ok) return { ...r, refreshed: usedRefresh };
  const data = (r.json && r.json.data) || [];
  const results = data.slice(0, 30).map(t => ({
    id: t.id,
    slug: t.slug,
    name: t.name,
    type: t.type === 'tv' ? 'series' : (t.type || 'movie'),
    year: (t.last_air_date || t.release_date || '').slice(0,4) || null,
    score: t.score || null,
    plot: t.plot || null,
    seasons: t.type === 'tv' ? (t.seasons_count || null) : null,
    cover: _scCoverUrl(t.images),
    url: `${_scBrowserBaseUrl}/titles/${t.id}-${t.slug}`
  }));
  return { ok:true, base_url: _scBrowserBaseUrl, results, refreshed: usedRefresh };
});

ipcMain.handle('wizard:scInfo', async (_e, { id, slug }) => {
  if (!id || !slug) return { ok:false, error:'id/slug mancanti' };
  const version = await _scSiteVersion();
  const r = await _scFetchJSON(`/titles/${encodeURIComponent(id)}-${encodeURIComponent(slug)}`,
    { headers: { 'X-Inertia':'true', 'X-Inertia-Version': version || '' } });
  if (!r.ok) return r;
  const t = (r.json && r.json.props && r.json.props.title) || {};
  return { ok:true, version, title: {
    id: t.id, slug: t.slug, name: t.name, plot: t.plot,
    type: t.type === 'tv' ? 'series' : (t.type || 'movie'),
    year: (t.last_air_date || t.release_date || '').slice(0,4) || null,
    seasons: t.seasons_count || null,
    runtime: t.runtime || null, score: t.score || null,
    cover: _scCoverUrl(t.images),
    genres: (t.genres || []).map(g => g && g.name).filter(Boolean)
  }};
});

ipcMain.handle('wizard:scSeasonEpisodes', async (_e, { id, slug, n }) => {
  if (!id || !slug || !n) return { ok:false, error:'parametri mancanti' };
  const version = await _scSiteVersion();
  const r = await _scFetchJSON(
    `/titles/${encodeURIComponent(id)}-${encodeURIComponent(slug)}/stagione-${encodeURIComponent(n)}`,
    { headers: { 'X-Inertia':'true', 'X-Inertia-Version': version || '' } });
  if (!r.ok) return r;
  const loaded = (r.json && r.json.props && r.json.props.loadedSeason) || {};
  const eps = (loaded.episodes || []).map(ep => ({
    id: ep.id, n: ep.number, name: ep.name, plot: ep.plot,
    duration: ep.duration, cover: _scCoverUrl(ep.images)
  }));
  return { ok:true, season:n, episodes: eps };
});
ipcMain.handle('wizard:scDownload', async (_e, opts) => {
  const args = ['download', '--id', String(opts.id), '--slug', String(opts.slug)];
  if (opts.season) args.push('--season', String(opts.season));
  if (opts.episode) args.push('--episode', String(opts.episode));
  return await _runScScript(args, {
    onProgress: (ev) => { try { mainWindow?.webContents.send('wizard:scProgress', ev); } catch(_){} }
  });
});

// ═══════════════════════════════════════════════
// TRAY + CLIPBOARD INTERCEPTOR (background-friendly)
// ═══════════════════════════════════════════════
let _tray = null;
// closeToTray = comportamento del pulsante X / chiusura nativa.
// exitToTray  = comportamento di File → Esci.  Indipendenti e configurabili.
let _trayPrefs = { minimizeToTray: true, clipboardEnabled: true, closeToTray: undefined, exitToTray: undefined };
const _trayPrefsFile = path.join(userDataPath, 'tray-prefs.json');
function _loadTrayPrefs(){
  try { Object.assign(_trayPrefs, JSON.parse(fs.readFileSync(_trayPrefsFile,'utf8'))); } catch(_) {}
  // Migrazione dal vecchio flag unico: X→tray (come prima), Esci→chiudi davvero.
  if (typeof _trayPrefs.closeToTray !== 'boolean') _trayPrefs.closeToTray = (_trayPrefs.minimizeToTray !== false);
  if (typeof _trayPrefs.exitToTray  !== 'boolean') _trayPrefs.exitToTray  = false;
}
function _saveTrayPrefs(){
  try { fs.writeFileSync(_trayPrefsFile, JSON.stringify(_trayPrefs), 'utf8'); } catch(_) {}
}
_loadTrayPrefs();

function _showMainWindow(){
  if (!mainWindow) { createWindow(); return; }
  if (mainWindow.isMinimized()) mainWindow.restore();
  if (!mainWindow.isVisible()) mainWindow.show();
  mainWindow.focus();
}

function _buildTray(){
  if (_tray) return _tray;
  let img = null;
  try { img = nativeImage.createFromPath(path.join(__dirname, 'assets', 'maniac_logo.ico')); }
  catch(_) {}
  if (!img || img.isEmpty()) {
    img = nativeImage.createEmpty();
  }
  _tray = new Tray(img);
  _tray.setToolTip('Maniac');
  _tray.setContextMenu(_trayMenu());
  _tray.on('click', _showMainWindow);
  _tray.on('double-click', _showMainWindow);
  return _tray;
}

function _trayMenu(){
  return Menu.buildFromTemplate([
    { label: 'Apri Maniac', click: _showMainWindow },
    { type: 'separator' },
    {
      label: 'Cattura clipboard',
      type: 'checkbox',
      checked: !!_trayPrefs.clipboardEnabled,
      click: (mi) => { _trayPrefs.clipboardEnabled = mi.checked; _saveTrayPrefs(); _restartClipboardWatcher(); }
    },
    {
      label: 'Chiusura (X) → minimizza al tray',
      type: 'checkbox',
      checked: !!_trayPrefs.closeToTray,
      click: (mi) => { _trayPrefs.closeToTray = mi.checked; _saveTrayPrefs(); }
    },
    {
      label: 'Esci (menu File) → minimizza al tray',
      type: 'checkbox',
      checked: !!_trayPrefs.exitToTray,
      click: (mi) => { _trayPrefs.exitToTray = mi.checked; _saveTrayPrefs(); }
    },
    { type: 'separator' },
    { label: 'Esci', click: () => { _trayQuitting = true; app.quit(); } }
  ]);
}

let _trayQuitting = false;
function _wireWindowMinimizeToTray(){
  if (!mainWindow) return;
  mainWindow.on('close', (event) => {
    if (_trayQuitting) return;
    if (!_trayPrefs.closeToTray) return;   // X / chiusura nativa → esci davvero
    event.preventDefault();
    if (!_tray) { try { _buildTray(); } catch(_){} }
    mainWindow.hide();
  });
}

// ── Clipboard watcher ──
// Estesa: include i principali host adult/streaming + pattern generici per
// permettere il rilevamento clipboard JDownloader-style su molti siti.
// NB: il check sull'estensione .torrent / magnet: è in _isKnownVideoUrl().
const _VIDEO_HOST_RX = /(^|\.)((youtube\.com)|(youtu\.be)|(x\.com)|(twitter\.com)|(vimeo\.com)|(streamingcommunity[a-z0-9-]*\.[a-z]{2,})|(twitch\.tv)|(instagram\.com)|(tiktok\.com)|(facebook\.com)|(dailymotion\.com)|(reddit\.com)|(eporner\.com)|(pornhub\.com)|(xvideos\.com)|(xhamster\.com)|(redtube\.com)|(youporn\.com)|(spankbang\.com)|(xnxx\.com)|(rumble\.com)|(bitchute\.com)|(odysee\.com)|(soundcloud\.com)|(bandcamp\.com)|(mixcloud\.com)|(streamable\.com)|(doodstream\.com)|(mixdrop\.[a-z]{2,})|(streamtape\.com)|(uptobox\.com)|(1fichier\.com)|(mediafire\.com)|(mega\.nz))$/i;
function _isKnownVideoUrl(url){
  try {
    if (!/^https?:\/\//i.test(url) && !/^magnet:/i.test(url)) return false;
    if (/^magnet:/i.test(url)) return true;
    const u = new URL(url);
    return _VIDEO_HOST_RX.test(u.hostname || '');
  } catch(_) { return false; }
}
function _humanSize(bytes){
  if (typeof bytes !== 'number' || !isFinite(bytes) || bytes <= 0) return '';
  const u = ['B','KB','MB','GB','TB']; let i=0; let n=bytes;
  while (n >= 1024 && i < u.length-1) { n /= 1024; i++; }
  return n.toFixed(n>=10||i===0?0:1) + ' ' + u[i];
}
let _clipTimer = null;
let _clipLast = '';
const _clipNotifiedAt = new Map(); // url -> ts

async function _probeUrlMeta(url) {
  try {
    const bin = await _resolveYtDlp(null, ()=>{});
    if (!bin) return null;
    const baseArgs = _baseDownloadArgs(url);
    return await new Promise((resolve) => {
      let proc;
      try { proc = spawn(bin, ['--dump-json', '--skip-download', '--no-warnings', '--no-playlist', ...baseArgs, url], { windowsHide: true }); }
      catch(_) { return resolve(null); }
      let buf = '';
      const to = setTimeout(() => { try { proc.kill(); } catch(_){} resolve(null); }, 8000);
      proc.stdout.on('data', d => { buf += d.toString(); });
      proc.on('error', () => { clearTimeout(to); resolve(null); });
      proc.on('close', () => {
        clearTimeout(to);
        try {
          const line = buf.split(/\r?\n/).find(l => l.trim().startsWith('{'));
          if (!line) return resolve(null);
          const j = JSON.parse(line);
          resolve({
            title: j.title || j.fulltitle || null,
            thumbnail: j.thumbnail || (Array.isArray(j.thumbnails) && j.thumbnails.length ? j.thumbnails[j.thumbnails.length-1].url : null),
            filesize: j.filesize || j.filesize_approx || null,
            duration: j.duration || null
          });
        } catch(_) { resolve(null); }
      });
    });
  } catch(_) { return null; }
}

async function _downloadIconToTemp(url) {
  if (!url) return null;
  try {
    const https = require('https');
    const dest = path.join(userDataPath, 'tmp-clip-icon.jpg');
    return await new Promise((resolve) => {
      const tryGet = (u, n=0) => {
        https.get(u, { headers: { 'User-Agent': 'Maniac/1.0' } }, res => {
          if ([301,302,303,307,308].includes(res.statusCode) && res.headers.location && n < 5) {
            res.resume(); return tryGet(res.headers.location, n+1);
          }
          if (res.statusCode !== 200) { res.resume(); return resolve(null); }
          const f = fs.createWriteStream(dest);
          res.pipe(f);
          f.on('finish', () => f.close(() => resolve(fs.existsSync(dest) ? dest : null)));
          f.on('error', () => resolve(null));
        }).on('error', () => resolve(null));
      };
      tryGet(url);
    });
  } catch(_) { return null; }
}

async function _handleClipboardUrl(url) {
  // Anti-loop: stessa URL notificata negli ultimi 60s → skip
  const last = _clipNotifiedAt.get(url) || 0;
  if (Date.now() - last < 60_000) return;
  _clipNotifiedAt.set(url, Date.now());

  const meta = await _probeUrlMeta(url);
  const title = (meta && meta.title) || 'Video rilevato';
  const sizeStr = meta ? _humanSize(meta.filesize) : '';
  const iconPath = meta && meta.thumbnail ? await _downloadIconToTemp(meta.thumbnail) : null;

  // Manda evento al renderer (per pre-fill UI / toast in-app)
  try { mainWindow?.webContents.send('clipboard:videoDetected', { url, title, thumbnail: meta?.thumbnail || null, size: meta?.filesize || null }); } catch(_) {}

  // Notifica OS (popup vicino orologio Windows)
  try {
    if (Notification.isSupported()) {
      const body = sizeStr ? `${title} · ${sizeStr}` : title;
      const n = new Notification({
        title: 'Maniac · Video rilevato',
        body,
        icon: iconPath || path.join(__dirname, 'assets', 'maniac_logo.ico'),
        silent: false
      });
      n.on('click', () => {
        _showMainWindow();
        try { mainWindow?.webContents.send('clipboard:videoDetected', { url, title, thumbnail: meta?.thumbnail || null, size: meta?.filesize || null, openWizard: true }); } catch(_){}
      });
      n.show();
    }
  } catch(_) {}
}

function _restartClipboardWatcher(){
  if (_clipTimer) { clearInterval(_clipTimer); _clipTimer = null; }
  if (!_trayPrefs.clipboardEnabled) return;
  _clipLast = (clipboard.readText() || '').trim();
  _clipTimer = setInterval(() => {
    let txt = '';
    try { txt = (clipboard.readText() || '').trim(); } catch(_) { return; }
    if (!txt || txt === _clipLast) return;
    _clipLast = txt;
    if (!_isKnownVideoUrl(txt)) return;
    _handleClipboardUrl(txt);
  }, 1500);
}

ipcMain.handle('clipboard:setEnabled', async (_e, on) => {
  _trayPrefs.clipboardEnabled = !!on;
  _saveTrayPrefs();
  _restartClipboardWatcher();
  return { ok: true, enabled: _trayPrefs.clipboardEnabled };
});
ipcMain.handle('clipboard:isEnabled', async () => ({ enabled: !!_trayPrefs.clipboardEnabled }));
ipcMain.handle('tray:setMinimizeToTray', async (_e, on) => {
  _trayPrefs.minimizeToTray = !!on;
  _saveTrayPrefs();
  return { ok: true, minimizeToTray: _trayPrefs.minimizeToTray };
});
// Comportamenti chiusura/uscita indipendenti.
ipcMain.handle('tray:getPrefs', async () => ({
  ok: true,
  closeToTray: !!_trayPrefs.closeToTray,
  exitToTray: !!_trayPrefs.exitToTray,
  clipboardEnabled: !!_trayPrefs.clipboardEnabled
}));
ipcMain.handle('tray:setCloseToTray', async (_e, on) => {
  _trayPrefs.closeToTray = !!on; _saveTrayPrefs();
  if (_tray) { try { _tray.setContextMenu(_trayMenu()); } catch(_){} }
  return { ok: true, closeToTray: _trayPrefs.closeToTray };
});
ipcMain.handle('tray:setExitToTray', async (_e, on) => {
  _trayPrefs.exitToTray = !!on; _saveTrayPrefs();
  if (_tray) { try { _tray.setContextMenu(_trayMenu()); } catch(_){} }
  return { ok: true, exitToTray: _trayPrefs.exitToTray };
});
// Auto-rilevamento chiavi API: cerca nelle variabili d'ambiente e nei file
// chiave persistiti. Ritorna solo i valori trovati (mai stringhe vuote).
// Parser minimale di file .env (righe KEY=VALUE, ignora commenti # e righe vuote,
// rimuove apici attorno al valore). Tollerante: ritorna {} su qualsiasi errore.
function _parseEnvFile(filePath) {
  const map = {};
  try {
    if (!filePath || !fs.existsSync(filePath)) return map;
    const txt = fs.readFileSync(filePath, 'utf8');
    for (const raw of txt.split(/\r?\n/)) {
      const line = raw.trim();
      if (!line || line.startsWith('#')) continue;
      const eq = line.indexOf('=');
      if (eq <= 0) continue;
      const k = line.slice(0, eq).trim().replace(/^export\s+/, '');
      let v = line.slice(eq + 1).trim();
      if ((v.startsWith('"') && v.endsWith('"')) || (v.startsWith("'") && v.endsWith("'"))) v = v.slice(1, -1);
      if (k) map[k] = v;
    }
  } catch (_) {}
  return map;
}

ipcMain.handle('api:autoDetect', async () => {
  // Sorgenti, in ordine di priorità:
  //  1) variabili d'ambiente del processo/sistema
  //  2) file .env / .env.local in: root app, cwd, userData, home
  //  3) config salvata dell'app (chiavi già inserite dall'utente)
  //  4) file chiave StashDB persistito
  const sources = [];
  sources.push(process.env);
  const envDirs = [];
  try { envDirs.push(app.getAppPath()); } catch (_) {}
  try { envDirs.push(__dirname); } catch (_) {}
  try { envDirs.push(process.cwd()); } catch (_) {}
  try { envDirs.push(userDataPath); } catch (_) {}
  try { envDirs.push(app.getPath('home')); } catch (_) {}
  const seenDirs = new Set();
  for (const d of envDirs) {
    if (!d || seenDirs.has(d)) continue; seenDirs.add(d);
    for (const fn of ['.env', '.env.local']) {
      const parsed = _parseEnvFile(path.join(d, fn));
      if (Object.keys(parsed).length) sources.push(parsed);
    }
  }
  const pick = (...names) => {
    for (const src of sources) {
      for (const n of names) { const v = ((src && src[n]) || '').toString().trim(); if (v) return v; }
    }
    return '';
  };
  const found = {
    tmdbKey:    pick('TMDB_API_KEY', 'TMDB_KEY', 'TMDB_V3_API_KEY', 'MANIAC_TMDB_KEY'),
    omdbKey:    pick('OMDB_API_KEY', 'OMDB_KEY', 'MANIAC_OMDB_KEY'),
    stashdbKey: pick('STASHDB_API_KEY', 'STASHDB_KEY', 'STASHDB_TOKEN', 'STASHDB_APIKEY'),
    openaiKey:  pick('OPENAI_API_KEY', 'OPENAI_KEY'),
    geminiKey:  pick('GEMINI_API_KEY', 'GOOGLE_API_KEY', 'GOOGLE_GEMINI_API_KEY')
  };
  // Chiavi già inserite dall'utente e salvate nella config dell'app.
  try {
    if (fs.existsSync(configFile)) {
      const cfg = JSON.parse(fs.readFileSync(configFile, 'utf8')) || {};
      const wc = cfg.wizardCfg || {};
      if (!found.tmdbKey   && wc.tmdbKey)    found.tmdbKey   = String(wc.tmdbKey).trim();
      if (!found.omdbKey   && wc.omdbKey)    found.omdbKey   = String(wc.omdbKey).trim();
      if (!found.stashdbKey&& wc.stashdbKey) found.stashdbKey= String(wc.stashdbKey).trim();
      if (!found.openaiKey && cfg.openaiKey) found.openaiKey = String(cfg.openaiKey).trim();
      if (!found.geminiKey && cfg.geminiKey) found.geminiKey = String(cfg.geminiKey).trim();
    }
  } catch (_) {}
  // StashDB anche dal file chiave persistito.
  try {
    const kf = process.env.STASHDB_KEY_FILE;
    if (!found.stashdbKey && kf && fs.existsSync(kf)) {
      const k = fs.readFileSync(kf, 'utf8').trim();
      if (k) found.stashdbKey = k;
    }
  } catch (_) {}
  const cleaned = {};
  for (const [k, v] of Object.entries(found)) if (v) cleaned[k] = v;
  // Servizi sempre disponibili senza chiave (riconoscimento attori mainstream/niche).
  const keyless = ['Wikidata', 'Wikipedia'];
  return { ok: true, found: cleaned, count: Object.keys(cleaned).length, keyless };
});

// File → Esci: rispetta exitToTray (indipendente dal pulsante X).
ipcMain.handle('app:exit', async () => {
  if (_trayPrefs.exitToTray && mainWindow) {
    if (!_tray) { try { _buildTray(); } catch(_){} }
    mainWindow.hide();
    return { ok: true, tray: true };
  }
  _trayQuitting = true;
  app.quit();
  return { ok: true, tray: false };
});
ipcMain.handle('tray:showWindow', async () => { _showMainWindow(); return { ok: true }; });

// ════════════════════════════════════════════════════════════════════
// SMART LIBRARY — watch folder + auto analyze + inbox
// Pattern: l'utente segna una cartella come "smart". Maniac monitora
// l'arrivo di nuovi video/audio/immagini (fs.watch ricorsivo + debounce)
// e li analizza automaticamente con analyze.py. I risultati finiscono
// in una "inbox" pendente di review (pannello dedicato lato renderer).
// Persistenza: userData/smartlib.json {folders:[], inbox:[], known:{}}
// ════════════════════════════════════════════════════════════════════

const SMART_VIDEO_EXT = new Set(['.mp4','.mkv','.webm','.mov','.avi','.m4v','.flv','.wmv','.mpg','.mpeg','.ts','.m2ts','.ogv','.3gp']);
const SMART_AUDIO_EXT = new Set(['.mp3','.flac','.m4a','.aac','.ogg','.opus','.wma','.wav','.aiff','.aif']);
const SMART_IMAGE_EXT = new Set(['.jpg','.jpeg','.png','.bmp','.webp','.gif']);
function _smartIsMedia(p){
  const e = path.extname(p).toLowerCase();
  return SMART_VIDEO_EXT.has(e) || SMART_AUDIO_EXT.has(e) || SMART_IMAGE_EXT.has(e);
}

let _smartState = { folders: [], inbox: [], known: {} };
const _smartWatchers = new Map();   // folderPath -> fs.FSWatcher
const _smartDebounce = new Map();   // filePath -> Timeout
const _smartQueue = [];             // [{filePath, folderPath, addedAt}]
let _smartProcessing = false;
let _smartProcUid = 0;

function _smartLoad(){
  try {
    if (!fs.existsSync(smartLibFile)) return;
    const raw = JSON.parse(fs.readFileSync(smartLibFile, 'utf8'));
    _smartState = {
      folders: Array.isArray(raw.folders) ? raw.folders : [],
      inbox:   Array.isArray(raw.inbox)   ? raw.inbox   : [],
      known:   (raw.known && typeof raw.known === 'object') ? raw.known : {}
    };
  } catch(e) { _smartState = { folders:[], inbox:[], known:{} }; }
}
function _smartSave(){
  try {
    // capa l'inbox a 200 elementi (più recente prima) per non gonfiare il file
    _smartState.inbox = (_smartState.inbox || []).slice(0, 200);
    fs.writeFileSync(smartLibFile, JSON.stringify(_smartState, null, 2), 'utf8');
  } catch(e){}
}
function _smartSendEvt(evt){
  try { mainWindow?.webContents.send('smartlib:event', evt); } catch(_) {}
}
function _smartListExisting(folderPath){
  const out = new Set();
  if (!fs.existsSync(folderPath)) return out;
  const stack = [folderPath];
  while (stack.length) {
    const dir = stack.pop();
    let entries = [];
    try { entries = fs.readdirSync(dir, { withFileTypes: true }); } catch(_){ continue; }
    for (const ent of entries) {
      const full = path.join(dir, ent.name);
      if (ent.isDirectory()) stack.push(full);
      else if (ent.isFile() && _smartIsMedia(full)) out.add(full);
    }
  }
  return out;
}
function _smartScheduleAnalyze(folderPath, filePath){
  // Debounce 5s: l'utente potrebbe star ancora copiando il file (cresce in size).
  // Aspettiamo che la size sia stabile per >5s prima di processarlo.
  if (_smartDebounce.has(filePath)) clearTimeout(_smartDebounce.get(filePath));
  let lastSize = -1; let stableHits = 0;
  const tick = () => {
    let size = -1;
    try { size = fs.statSync(filePath).size; } catch(_) { _smartDebounce.delete(filePath); return; }
    if (size === lastSize) stableHits++;
    else { stableHits = 0; lastSize = size; }
    if (stableHits >= 2) {  // 2 hit stabili a distanza 2.5s = ~5s
      _smartDebounce.delete(filePath);
      _smartEnqueue(folderPath, filePath);
    } else {
      _smartDebounce.set(filePath, setTimeout(tick, 2500));
    }
  };
  _smartDebounce.set(filePath, setTimeout(tick, 2500));
}
function _smartEnqueue(folderPath, filePath){
  // dedup
  if (_smartQueue.find(x => x.filePath === filePath)) return;
  if ((_smartState.inbox || []).find(i => i.path === filePath && i.status === 'analyzing')) return;
  _smartQueue.push({ folderPath, filePath, addedAt: Date.now() });
  _smartSendEvt({ kind:'queued', path:filePath, folder:folderPath, queueLen:_smartQueue.length });
  _smartProcess();
}
async function _smartProcess(){
  if (_smartProcessing) return;
  if (!_smartQueue.length) return;
  _smartProcessing = true;
  const job = _smartQueue.shift();
  const uid = ++_smartProcUid;
  // Inbox entry
  const inboxEntry = {
    id: 'sl_' + Date.now() + '_' + uid,
    path: job.filePath, folder: job.folderPath,
    name: path.basename(job.filePath),
    status: 'analyzing', startedAt: Date.now(),
    autoOrganize: !!(_smartState.folders.find(f => f.path === job.folderPath) || {}).autoOrganize
  };
  _smartState.inbox.unshift(inboxEntry);
  _smartSave();
  _smartSendEvt({ kind:'analyzing', entry:inboxEntry });
  try {
    // Esegui analyze.py SOLO sul singolo file: passiamo la cartella che lo contiene
    // ma con --max-files 1 (analyze.py prende il primo) — un po' rough ma evita
    // di toccare troppo lo schema CLI esistente. Per single-file faremo un minor
    // change: invece di --folder, usiamo una temp folder con un symlink, oppure
    // accettiamo che processi tutta la cartella dei nuovi file una volta sola.
    // Per ora: scan limitato (max-files 1) sulla cartella padre del file.
    const parentDir = path.dirname(job.filePath);
    const args = [
      '--folder', parentDir,
      '--modes', 'face,object,place',
      '--max-files', '1',
      '--gender', 'both',
      '--face-db', facesDbFile,
      '--entity-db', facesDbFile,
    ];
    const proc = pyBridge.spawnWorker('analyze.py', args);
    if (!proc) throw new Error('venv311 non disponibile');
    let buf = '';
    let lastResults = [];
    proc.stdout.on('data', d => {
      buf += d.toString();
      let i;
      while ((i = buf.indexOf('\n')) >= 0) {
        const line = buf.slice(0, i).trim(); buf = buf.slice(i+1);
        if (!line) continue;
        try {
          const j = JSON.parse(line);
          if (j.type === 'done' && Array.isArray(j.results)) lastResults = j.results;
        } catch(_){}
      }
    });
    await new Promise((resolve) => {
      proc.on('close', () => resolve());
      proc.on('error', () => resolve());
      // Timeout duro 10 minuti per file
      setTimeout(() => { try { proc.kill(); } catch(_){} resolve(); }, 10*60*1000);
    });
    inboxEntry.status = 'done';
    inboxEntry.completedAt = Date.now();
    inboxEntry.results = lastResults;
    inboxEntry.summary = _smartSummarizeResults(lastResults);
    // Se la cartella ha autoOrganize ON e abbiamo abbastanza confidence, marca come "auto-applied"
    inboxEntry.autoApplied = inboxEntry.autoOrganize && lastResults.some(r => r.localMatch || (r.apiMatch && r.apiMatch.confidence === 'confirmed'));
    // Aggiungi il file ai known del folder
    _smartState.known[job.folderPath] = _smartState.known[job.folderPath] || [];
    if (!_smartState.known[job.folderPath].includes(job.filePath)) _smartState.known[job.folderPath].push(job.filePath);
    _smartSave();
    _smartSendEvt({ kind:'done', entry:inboxEntry });
    // Notifica desktop
    try {
      if (Notification.isSupported()) {
        const n = new Notification({
          title: 'Maniac · Smart Library',
          body: `${inboxEntry.name}\n${inboxEntry.summary || 'Analisi completata'}`,
          silent: true
        });
        n.on('click', () => { _showMainWindow(); _smartSendEvt({ kind:'open-inbox' }); });
        n.show();
      }
    } catch(_){}
  } catch(e) {
    inboxEntry.status = 'error';
    inboxEntry.error = e.message || String(e);
    _smartSave();
    _smartSendEvt({ kind:'error', entry:inboxEntry });
  } finally {
    _smartProcessing = false;
    setImmediate(() => _smartProcess());
  }
}
function _smartSummarizeResults(results){
  if (!Array.isArray(results) || !results.length) return 'Nessun elemento riconosciuto';
  const counts = {};
  results.forEach(r => { const k = r.type||'face'; counts[k] = (counts[k]||0) + 1; });
  const labels = { face:'volti', object:'oggetti', place:'luoghi', animal:'animali', music:'musica', genre:'generi', category:'categorie' };
  return Object.entries(counts).map(([k,v]) => `${v} ${labels[k]||k}`).join(' · ');
}
function _smartStartWatcher(folder){
  if (_smartWatchers.has(folder.path)) return;
  if (!fs.existsSync(folder.path)) return;
  // Snapshot iniziale: i file già presenti sono "known", non li ri-analizziamo a meno che folder sia nuovo.
  const existing = _smartListExisting(folder.path);
  _smartState.known[folder.path] = _smartState.known[folder.path] || [];
  const known = new Set(_smartState.known[folder.path]);
  // Aggiungi i file pre-esistenti al known set se è la prima volta
  if (folder._firstScan) {
    existing.forEach(p => known.add(p));
    _smartState.known[folder.path] = Array.from(known);
    _smartSave();
  } else {
    // Ripresa: nei file esistenti che NON sono known, mettiamoli in coda per analisi
    if (folder.autoAnalyze) {
      existing.forEach(p => { if (!known.has(p)) _smartScheduleAnalyze(folder.path, p); });
    }
  }
  try {
    const w = fs.watch(folder.path, { recursive: true }, (eventType, filename) => {
      if (!filename) return;
      const full = path.join(folder.path, filename);
      if (!_smartIsMedia(full)) return;
      // Verifica esistenza (rename event può scattare per delete)
      try { if (!fs.existsSync(full)) return; } catch(_) { return; }
      if ((_smartState.known[folder.path] || []).includes(full)) return;
      if (folder.autoAnalyze) _smartScheduleAnalyze(folder.path, full);
      else {
        // Solo notifica: aggiungi all'inbox come 'pending' senza processare
        _smartState.inbox.unshift({
          id:'sl_'+Date.now()+'_'+(++_smartProcUid),
          path: full, folder: folder.path, name: path.basename(full),
          status:'pending', detectedAt: Date.now()
        });
        _smartSave();
        _smartSendEvt({ kind:'detected', path:full, folder:folder.path });
      }
    });
    _smartWatchers.set(folder.path, w);
  } catch(e) { _logErr('smartlib-watch', e); }
}
function _smartStopWatcher(folderPath){
  const w = _smartWatchers.get(folderPath);
  if (w) { try { w.close(); } catch(_){} _smartWatchers.delete(folderPath); }
}
function _smartStartAll(){
  _smartLoad();
  for (const f of (_smartState.folders || [])) _smartStartWatcher(f);
}

// ─── IPC Smart Library ───
ipcMain.handle('smartlib:list', () => {
  return { ok:true, folders: _smartState.folders || [], inbox: _smartState.inbox || [],
           queueLen: _smartQueue.length, processing: _smartProcessing };
});
ipcMain.handle('smartlib:addFolder', async (_e, { folderPath, autoAnalyze, autoOrganize }) => {
  if (!folderPath || !fs.existsSync(folderPath)) return { ok:false, error:'cartella non valida' };
  if ((_smartState.folders || []).find(f => f.path === folderPath))
    return { ok:false, error:'già monitorata' };
  const folder = {
    path: folderPath, autoAnalyze: autoAnalyze !== false, autoOrganize: !!autoOrganize,
    addedAt: Date.now(), _firstScan: true
  };
  _smartState.folders.push(folder);
  _smartSave();
  _smartStartWatcher(folder);
  // dopo lo start, _firstScan è ridondante — lo togliamo
  delete folder._firstScan;
  _smartSave();
  return { ok:true };
});
ipcMain.handle('smartlib:removeFolder', (_e, folderPath) => {
  _smartStopWatcher(folderPath);
  _smartState.folders = (_smartState.folders || []).filter(f => f.path !== folderPath);
  delete _smartState.known[folderPath];
  _smartSave();
  return { ok:true };
});
ipcMain.handle('smartlib:setFolderConfig', (_e, { folderPath, autoAnalyze, autoOrganize }) => {
  const f = (_smartState.folders || []).find(f => f.path === folderPath);
  if (!f) return { ok:false, error:'non trovata' };
  if (typeof autoAnalyze === 'boolean') f.autoAnalyze = autoAnalyze;
  if (typeof autoOrganize === 'boolean') f.autoOrganize = autoOrganize;
  _smartSave();
  return { ok:true, folder:f };
});
ipcMain.handle('smartlib:dismissInbox', (_e, id) => {
  _smartState.inbox = (_smartState.inbox || []).filter(i => i.id !== id);
  _smartSave();
  return { ok:true };
});
ipcMain.handle('smartlib:clearInbox', () => {
  _smartState.inbox = [];
  _smartSave();
  return { ok:true };
});
ipcMain.handle('smartlib:rescan', async (_e, folderPath) => {
  // Re-analizza file non-known nella cartella indicata (o tutte se non specificata)
  const folders = folderPath
    ? (_smartState.folders || []).filter(f => f.path === folderPath)
    : (_smartState.folders || []);
  for (const f of folders) {
    if (!f.autoAnalyze) continue;
    const existing = _smartListExisting(f.path);
    const known = new Set(_smartState.known[f.path] || []);
    existing.forEach(p => { if (!known.has(p)) _smartScheduleAnalyze(f.path, p); });
  }
  return { ok:true, queued:_smartQueue.length };
});

app.whenReady().then(() => {
  createWindow();
  _wireWindowMinimizeToTray();
  try { _buildTray(); } catch(e) { _logErr('tray-build', e); }
  _restartClipboardWatcher();
  // Avvia Smart Library (carica cartelle + watcher)
  try { _smartStartAll(); } catch(e) { _logErr('smartlib-start', e); }
  // Se l'app è stata lanciata DAL CLICK su un magnet:/URL nel browser, argv
  // contiene l'URL → inoltralo al renderer non appena è pronto.
  // Fix race: se did-finish-load è già scattato (cold-start veloce),
  // `webContents.once('did-finish-load')` non spara mai. Usiamo isLoading()
  // come fallback e dispatch immediato.
  try {
    const initialArg = (process.argv || []).find(a =>
      /^maniac:/i.test(a || '') || /^magnet:/i.test(a || '') || /^https?:\/\//i.test(a || ''));
    let resolvedInitial = initialArg, resolvedKind = null;
    if (initialArg && /^maniac:/i.test(initialArg)) {
      const m = /^maniac:\/\/(?:download)?\??(.*)$/i.exec(initialArg);
      if (m) {
        const params = new URLSearchParams(m[1] || '');
        resolvedInitial = params.get('url') || params.get('u') || params.get('magnet') || null;
      }
    }
    if (resolvedInitial) resolvedKind = /^magnet:/i.test(resolvedInitial) ? 'magnet' : 'url';
    if (resolvedInitial && mainWindow && mainWindow.webContents) {
      const fire = () => _handleInterceptedDownload(resolvedInitial, resolvedKind);
      if (mainWindow.webContents.isLoading()) {
        mainWindow.webContents.once('did-finish-load', fire);
      } else {
        // Già caricato: dispatch immediato
        setTimeout(fire, 100);
      }
    }
  } catch(_){}
  app.on('activate', () => { if (BrowserWindow.getAllWindows().length === 0) { createWindow(); _wireWindowMinimizeToTray(); } });
});
app.on('before-quit', () => { _trayQuitting = true; });
app.on('window-all-closed', () => {
  // Su Windows con "chiusura → tray" attivo NON chiudiamo: resta in background.
  if (process.platform !== 'darwin' && !_trayPrefs.closeToTray) app.quit();
  if (process.platform === 'darwin') {/* mac default behaviour */}
});
