const { contextBridge, ipcRenderer, webUtils } = require('electron');

contextBridge.exposeInMainWorld('maniac', {
  // Risolve il percorso assoluto di un File trascinato (drag&drop).
  // Electron 33 ha rimosso File.path: questa è l'API canonica e affidabile,
  // valida sia per file che per cartelle.
  getPathForFile: (file) => {
    try { return webUtils.getPathForFile(file) || ''; } catch (_) { return ''; }
  },

  // File dialogs
  openFile: () => ipcRenderer.invoke('open-file-dialog'),
  openFolder: () => ipcRenderer.invoke('open-folder-dialog'),
  openFiles: () => ipcRenderer.invoke('open-files-dialog'),
  openFileOrFolder: () => ipcRenderer.invoke('open-file-or-folder-dialog'),
  savePlaylist: (playlist) => ipcRenderer.invoke('save-playlist-dialog', playlist),
  importPlaylist: () => ipcRenderer.invoke('import-playlist-dialog'),

  // Config
  configLoad: () => ipcRenderer.invoke('config-load'),
  configSave: (cfg) => ipcRenderer.invoke('config-save', cfg),

  // Tag persistenti per file
  fileTagsLoad: () => ipcRenderer.invoke('file-tags-load'),
  fileTagsSave: (map) => ipcRenderer.invoke('file-tags-save', map),

  // Window controls
  minimize: () => ipcRenderer.invoke('window-minimize'),
  maximizeToggle: () => ipcRenderer.invoke('window-maximize-toggle'),
  close: () => ipcRenderer.invoke('window-close'),
  isMaximized: () => ipcRenderer.invoke('window-is-maximized'),
  setAlwaysOnTop: (on) => ipcRenderer.invoke('window-set-always-on-top', on),
  isAlwaysOnTop: () => ipcRenderer.invoke('window-is-always-on-top'),
  onWindowState: (cb) => ipcRenderer.on('window-state', (_e, state) => cb(state)),

  // External
  openExternal: (url) => ipcRenderer.invoke('open-external', url),

  // File system: rinomina su disco (per rinomina file scaricati)
  fileRename: (oldPath, newPath) => ipcRenderer.invoke('file:rename', { oldPath, newPath }),
  fileExists: (p) => ipcRenderer.invoke('file:exists', p),
  // File system: elimina file (mediaOptions context-menu)
  fileDelete: (filePath) => ipcRenderer.invoke('file:delete', { filePath }),
  entityResolve: (opts) => ipcRenderer.invoke('entity:resolve', opts || {}),
  // File system: sposta file in nuova cartella (mediaOptions context-menu)
  fileMove: (oldPath, newDir) => ipcRenderer.invoke('file:move', { oldPath, newDir }),

  // Vibravid bridge: spawn interattivo per StreamingCommunity & co.
  vibravid: {
    start: (opts) => ipcRenderer.invoke('vibravid:start', opts),
    sendInput: (jobId, line) => ipcRenderer.invoke('vibravid:sendInput', { jobId, line }),
    cancel: (jobId) => ipcRenderer.invoke('vibravid:cancel', jobId),
    onPrompt: (cb) => ipcRenderer.on('vibravid:prompt', (_e, data) => cb(data)),
    onLog: (cb) => ipcRenderer.on('vibravid:log', (_e, data) => cb(data)),
    onDone: (cb) => ipcRenderer.on('vibravid:done', (_e, data) => cb(data))
  },

  // Recording
  getVideosPath: () => ipcRenderer.invoke('get-videos-path'),
  chooseFolder: () => ipcRenderer.invoke('choose-folder-dialog'),
  saveRecording: (buffer, filename, folder) => ipcRenderer.invoke('save-recording', { buffer, filename, folder }),
  showInFolder: (p) => ipcRenderer.invoke('show-in-folder', p),
  scanFolder: (p) => ipcRenderer.invoke('scan-folder', p),

  // Native popup menu (estende oltre i confini della finestra)
  showPopupMenu: (items) => ipcRenderer.invoke('show-popup-menu', items),

  // VST plugin scaffold
  pickVstPlugin: () => ipcRenderer.invoke('pick-vst-plugin'),

  // ffmpeg post-processing placeholder
  ffmpegConvert: (inputPath, outputFormat) => ipcRenderer.invoke('ffmpeg-convert', { inputPath, outputFormat }),

  // Transcodifica on-demand per codec non supportati da Chromium (<video>).
  media: {
    transcode: (id, srcPath) => ipcRenderer.invoke('media:transcode', { id, srcPath }),
    cancel: (id) => ipcRenderer.invoke('media:transcode-cancel', id),
    onProgress: (cb) => ipcRenderer.on('media:transcode-progress', (_e, data) => cb(data))
  },

  // Thumbnail cache (persistente in userData/thumbnails)
  thumbGet: (filePath) => ipcRenderer.invoke('thumb-get', filePath),
  thumbSave: (filePath, dataUrl) => ipcRenderer.invoke('thumb-save', { filePath, dataUrl }),

  // Session logs persistenza
  logAppend: (entry) => ipcRenderer.invoke('log-append', entry),
  logLoadLast: () => ipcRenderer.invoke('log-load-last'),

  // Blocklist nomi volti (unspokentitles_Faces.txt)
  unspokenFacesLoad: () => ipcRenderer.invoke('unspoken-faces-load'),

  // Storico elementi analizzati
  analysisLoad: () => ipcRenderer.invoke('analysis:load'),
  analysisSave: (data) => ipcRenderer.invoke('analysis:save', data),

  // Download (yt-dlp / VibraVid)
  downloadStart: (opts) => ipcRenderer.invoke('download:start', opts),
  downloadCancel: (id) => ipcRenderer.invoke('download:cancel', id),
  downloadPickDir: () => ipcRenderer.invoke('download:pickDir'),
  downloadPickQuality: (id, policy) => ipcRenderer.invoke('download:pickQuality', { id, policy }),
  downloadUpdateBin: () => ipcRenderer.invoke('download:updateBin'),
  onDownloadProgress: (cb) => ipcRenderer.on('download:progress', (_e, data) => cb(data)),
  onDownloadQualityChoice: (cb) => ipcRenderer.on('download:qualityChoice', (_e, data) => cb(data)),

  // Download utilities (cestino + estrazione thumbnail dal file scaricato)
  download: {
    trashFile:   (filePath) => ipcRenderer.invoke('download:trashFile', filePath),
    extractThumb:(filePath) => ipcRenderer.invoke('download:extractThumb', filePath),
    // Listener per magnet/url intercettati dal protocollo OS o single-instance handler
    onIntercepted: (cb) => ipcRenderer.on('download:intercepted', (_e, data) => cb(data))
  },

  // Torrent (webtorrent)
  torrentAdd: (magnetOrUrl, outputDir) => ipcRenderer.invoke('torrent:add', { magnetOrUrl, outputDir }),
  torrentPickFile: () => ipcRenderer.invoke('torrent:pickFile'),
  torrentAddBuffer: (opts) => ipcRenderer.invoke('torrent:addBuffer', opts),
  torrentPause: (id) => ipcRenderer.invoke('torrent:pause', id),
  torrentResume: (id) => ipcRenderer.invoke('torrent:resume', id),
  torrentRemove: (id) => ipcRenderer.invoke('torrent:remove', id),
  torrentPrioritize: (id, priority) => ipcRenderer.invoke('torrent:prioritize', { id, priority }),
  onTorrentProgress: (cb) => ipcRenderer.on('torrent:progress', (_e, data) => cb(data)),

  // G11: Save frame
  saveFrame: (opts) => ipcRenderer.invoke('save-frame', opts),
  // H5: direct frame save
  saveFrameDirect: (opts) => ipcRenderer.invoke('save-frame-direct', opts),
  // H1: Clipboard deep page scan
  clipboardScanPage: (url) => ipcRenderer.invoke('clipboard:scanPage', url),
  // H11: restart
  appRestart: () => ipcRenderer.invoke('app:restart'),
  // G6: Resolve URL via yt-dlp
  urlResolve: (url, vibraVidPath) => ipcRenderer.invoke('url:resolve', { url, vibraVidPath }),
  // G14: Tag export/import
  tagsExport: (data) => ipcRenderer.invoke('tags:export', data),
  tagsImport: () => ipcRenderer.invoke('tags:import'),

  // OS integration
  osOpenDefaultApps: () => ipcRenderer.invoke('os:open-default-apps'),

  // Sottotitoli (faster-whisper + argostranslate, oppure OpenAI cloud)
  subs: {
    generate: (opts) => ipcRenderer.invoke('subs:generate', opts),
    translate: (opts) => ipcRenderer.invoke('subs:translate', opts),
    openaiGenerate: (opts) => ipcRenderer.invoke('subs:openai-generate', opts),
    openaiTranslate: (opts) => ipcRenderer.invoke('subs:openai-translate', opts),
    findSibling: (videoPath) => ipcRenderer.invoke('subs:find-sibling', videoPath),
    readText: (filePath) => ipcRenderer.invoke('subs:read-text', filePath),
    listLangs: () => ipcRenderer.invoke('subs:list-langs'),
    installLang: (opts) => ipcRenderer.invoke('subs:install-lang', opts),
    cancel: (jobId) => ipcRenderer.invoke('subs:cancel', jobId),
    onProgress: (cb) => ipcRenderer.on('subs:progress', (_e, data) => cb(data))
  },

  // Organizer
  organizer: {
    scan: (opts) => ipcRenderer.invoke('organizer:scan', opts),
    buildOps: (opts) => ipcRenderer.invoke('organizer:build-ops', opts),
    execute: (opts) => ipcRenderer.invoke('organizer:execute', opts),
    snapshots: () => ipcRenderer.invoke('organizer:snapshots'),
    restore: (snapshotPath) => ipcRenderer.invoke('organizer:restore', snapshotPath),
    cancel: (jobId) => ipcRenderer.invoke('organizer:cancel', jobId),
    onProgress: (cb) => ipcRenderer.on('organizer:progress', (_e, data) => cb(data))
  },

  // i18n persistence
  i18n: {
    getLocale: () => ipcRenderer.invoke('i18n:get-locale'),
    setLocale: (code) => ipcRenderer.invoke('i18n:set-locale', code)
  },

  // AI / Python venv
  ai: {
    venvCheck: (force) => ipcRenderer.invoke('ai:venv-check', { force: !!force }),
    pythonPath: () => ipcRenderer.invoke('ai:python-path'),
    installOptional: (packages) => ipcRenderer.invoke('ai:install-optional', { packages }),
    analyzeFolder: (opts) => ipcRenderer.invoke('ai:analyze-folder', opts),
    cancel: (jobId) => ipcRenderer.invoke('ai:cancel', jobId),
    onProgress: (cb) => ipcRenderer.on('ai:progress', (_e, data) => cb(data)),
    actorResolve: (opts) => ipcRenderer.invoke('ai:actor-resolve', opts),
    actorResolveCancel: (jobId) => ipcRenderer.invoke('ai:actor-resolve-cancel', jobId),
    onActorResolveProgress: (cb) => ipcRenderer.on('ai:actor-resolve:progress', (_e, data) => cb(data)),
    faceDb: {
      match: (payload) => ipcRenderer.invoke('ai:face-db-match', payload),
      add: (payload) => ipcRenderer.invoke('ai:face-db-add', payload),
      list: () => ipcRenderer.invoke('ai:face-db-list'),
      delete: (id) => ipcRenderer.invoke('ai:face-db-delete', id)
    },
    // Generico polimorfico (kind: face|object|place|animal|genre|category)
    entityDb: {
      match: (payload) => ipcRenderer.invoke('ai:entity-db-match', payload),
      add: (payload) => ipcRenderer.invoke('ai:entity-db-add', payload),
      list: (kind) => ipcRenderer.invoke('ai:entity-db-list', kind),
      delete: (id) => ipcRenderer.invoke('ai:entity-db-delete', id),
      search: (kind, query) => ipcRenderer.invoke('ai:entity-db-search', { kind, query }),
      migrate: () => ipcRenderer.invoke('ai:entity-db-migrate')
    }
  },

  // Eporner: tassonomia categorie (SFW/NSFW) + cache video via API v2
  eporner: {
    seed:            () => ipcRenderer.invoke('eporner:seed'),
    listCategories:  (kind) => ipcRenderer.invoke('eporner:listCategories', kind),
    search:          (query) => ipcRenderer.invoke('eporner:search', query),
    byCategory:      (cat) => ipcRenderer.invoke('eporner:byCategory', cat)
  },

  // StashDB integration: GraphQL client per metadata performer/scene/studio/tag
  stashdb: {
    setKey:           (key) => ipcRenderer.invoke('stashdb:setKey', key),
    hasKey:           () => ipcRenderer.invoke('stashdb:hasKey'),
    searchPerformer:  (q, limit) => ipcRenderer.invoke('stashdb:searchPerformer', { q, limit }),
    searchScene:      (q, limit) => ipcRenderer.invoke('stashdb:searchScene', { q, limit }),
    performerInfo:    (id) => ipcRenderer.invoke('stashdb:performerInfo', id),
    autoTag:          (opts) => ipcRenderer.invoke('stashdb:autoTag', opts),
    cancel:           (jobId) => ipcRenderer.invoke('stashdb:cancel', jobId),
    onProgress:       (cb) => ipcRenderer.on('stashdb:progress', (_e, data) => cb(data))
  },

  // Library auto-tag via Stash community-scrapers (consenso multi-fonte)
  scrape: {
    autoTagFiles:    (opts) => ipcRenderer.invoke('scrape:autoTagFiles', opts),
    listScrapers:    () => ipcRenderer.invoke('scrape:listScrapers'),
    onProgress:      (cb) => ipcRenderer.on('scrape:progress', (_e, data) => cb(data)),
    cancel:          (jobId) => ipcRenderer.invoke('scrape:cancel', jobId)
  },

  // Cookies / browser detection (per yt-dlp --cookies-from-browser)
  cookies: {
    listBrowsers: () => ipcRenderer.invoke('cookies:listBrowsers')
  },

  // Clipboard interceptor (background-friendly)
  clipboard: {
    setEnabled: (on) => ipcRenderer.invoke('clipboard:setEnabled', !!on),
    isEnabled:  () => ipcRenderer.invoke('clipboard:isEnabled'),
    onVideoDetected: (cb) => ipcRenderer.on('clipboard:videoDetected', (_e, data) => cb(data))
  },

  // Wizard StreamingCommunity (search/info/seasons/download via Python helper)
  wizard: {
    scResolveDomain: () => ipcRenderer.invoke('wizard:scResolveDomain'),
    scSetDomain:     (domain) => ipcRenderer.invoke('wizard:scSetDomain', domain),
    scGetDomain:     () => ipcRenderer.invoke('wizard:scGetDomain'),
    scSearch:        (query, forceRefresh) => ipcRenderer.invoke('wizard:scSearch', { query, forceRefresh: !!forceRefresh }),
    scInfo:          (opts)  => ipcRenderer.invoke('wizard:scInfo', opts),
    scSeasonEpisodes:(opts)  => ipcRenderer.invoke('wizard:scSeasonEpisodes', opts),
    scDownload:      (opts)  => ipcRenderer.invoke('wizard:scDownload', opts),
    onScProgress:    (cb)    => ipcRenderer.on('wizard:scProgress', (_e, data) => cb(data))
  },

  // Tray / window background behaviour
  tray: {
    setMinimizeToTray: (on) => ipcRenderer.invoke('tray:setMinimizeToTray', !!on),
    showWindow:        () => ipcRenderer.invoke('tray:showWindow'),
    getPrefs:          () => ipcRenderer.invoke('tray:getPrefs'),
    setCloseToTray:    (on) => ipcRenderer.invoke('tray:setCloseToTray', !!on),
    setExitToTray:     (on) => ipcRenderer.invoke('tray:setExitToTray', !!on)
  },
  // File → Esci (rispetta il toggle exitToTray, indipendente dalla X)
  appExit: () => ipcRenderer.invoke('app:exit'),

  // Auto-rilevamento chiavi API (env + file persistiti)
  apiAutoDetect: () => ipcRenderer.invoke('api:autoDetect'),

  isElectron: true
});
