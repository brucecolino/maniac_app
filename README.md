# Maniac — AI-Powered Media Player

**Versione 1.0-1**

Electron desktop app (Windows) con player video multi-vista, cronologia, tag, webcam, sottotitoli, AI analysis, VST scaffold.

## Requisiti

- Node.js v18+
- Windows 10/11

## Installazione

```bash
cd maniac-app
npm install
```

## Sviluppo

```bash
npm start
```

## Build .exe (NSIS installer)

```bash
npm run build
```

Output: `dist/Maniac-1.0-1-Setup.exe` (icona `assets/maniac_logo.ico`).

## Modelli AI & primo avvio

Per tenere l'installer leggero (~3.5 GB), i **pesi dei modelli AI non sono bundlati**: vengono scaricati **on-demand al primo utilizzo** della relativa feature e salvati in `%APPDATA%/maniac-player/models/ml/` (scrivibile).

- **Sottotitoli (whisper)**: il modello faster-whisper (~3 GB per `large-v3`) si scarica alla prima generazione.
- **Traduzione (Argos)**: il pacchetto lingua richiesto si scarica alla prima traduzione (pivota su EN se manca il pair diretto).
- **Scene/oggetti (YOLO, Places365)**: pesi scaricati al primo riconoscimento.

⚠️ Il primo uso di ciascuna feature AI richiede **connessione internet**. Quel che è bundlato: venv Python, tool nativi (`tools/`), scrapers (`models/scrapers/`), moduli vendored (`StreamingCommunity_api-main/`, `ai-sub-main/`).

## Struttura

```
maniac-app/
├── assets/                (icone app + installer: maniac_logo.ico)
├── src/index.html         (renderer: UI + stato + render functions, inline)
├── main.js                (main process Electron: IPC + spawn sidecar)
├── preload.js             (contextBridge → window.maniac.*)
├── py-bridge.js           (resolver venv + runner script Python)
├── python/                (sidecar: analyze, subtitles, sc_search, stashdb, …)
├── tools/                 (ffmpeg, exiftool, mediainfo — bundlati)
├── models/scrapers/       (scrapers stash community — bundlati)
├── StreamingCommunity_api-main/ · ai-sub-main/   (vendored, bundlati)
├── venv311/               (Python 3.11 venv — bundlato a build, non versionato)
├── models/ml/             (pesi AI — scaricati on-demand, non versionati)
├── package.json
├── CLAUDE.md · AI_ROADMAP.md · README.md
```

## Stato feature (35 totali, 3 iterazioni)

### Core player
- Multi-view 1/2/3/4 player indipendenti
- Playlist per-player + playlist globale
- Drag&drop file/cartelle su griglia e pannelli playlist
- Pannello File laterale (coerente con altri tab)
- Apri file / cartella / URL (anche via ctx menu sottomenu Apri)
- Recenti (persistiti)
- Cronologia con snapshot multi (50 voci)
- Scelta single/multi su riapertura entry cronologia
- Shuffle con stack di navigazione (Precedente torna nella history shuffle)
- Multi-Next / Multi-Prev (avanza tutti i player)
- Auto-fill: player vuoti pescano dall'ultima cartella caricata
- Loop globale e per-player

### Audio
- Output device per-player
- Input device + gain + noise gate + EQ input
- EQ globale 3-band per-player
- Stereo/Mono + balance + audio delay sync
- Voice AI filter
- Boost 100-300%
- VST2/VST3 plugin UI (scaffold, lista attiva/disattiva, TODO hosting)

### Video
- Filtri colore per-player (luminosità/contrasto/saturazione/hue/gamma)
- Lente d'ingrandimento con minimap
- Sottotitoli .srt/.vtt import/export + traduttore auto
- Webcam + IP Cam
- Registrazione con pausa
- Formato export: WebM/MP4/MKV/MOV (WebM nativo via MediaRecorder, gli altri convertiti con ffmpeg)
- Cartella export configurabile

### UI
- Temi Dark/Light
- IT/EN
- Pannelli laterali ridimensionabili
- Animazioni finestre/pannelli rimosse — aperture istantanee
- Scorciatoie configurabili
- Tag system (built-in + custom, per-file persistente)
- Assegna tag per-player o ALL (applica a tutti i file visibili)
- Preferiti ★

### AI e riconoscimento
- Riconoscimento persone da volto, verificato foto-vs-foto su più scatti
- Fonti: StashDB (adult), Wikidata e Wikipedia (attori, musicisti, sportivi,
  volti pubblici), TMDB con chiave utente
- Riconoscimento oggetti, animali e luoghi (YOLOv8n + Places365)
- Sottotitoli automatici (faster-whisper) e traduzione (Argos, OpenAI)
- Identificazione musica (AcoustID, Shazam)
- Organizer libreria e auto-tag da scraper multi-fonte

### Download
- yt-dlp: YouTube, link diretti mp4/mkv/mov, cattura da appunti
- Torrent e magnet via WebTorrent
- StreamingCommunity

### Sistema
- Save/Load config persistente (`%APPDATA%/maniac-player/`)
- Modelli AI scaricati on-demand in cartella scrivibile
- Log viewer in tab Info
- Aggiornamenti in-app da GitHub Releases, con barra di download e note di versione

Il dettaglio delle versioni è in [CHANGELOG.md](CHANGELOG.md).

## Changelog

### Iterazione 3 — 2026-04-22
Nuove 13 feature:
1. Cronologia: dialog single/multi su click voce multi-sessione
2. File come pannello laterale (sostituisce dropdown titlebar)
3. Apri URL nel pannello File + ctx menu sottomenu Apri (File/Cartella/URL)
4. Icona .exe corretta (maniac_logo.ico sia in dev che in build, NSIS installer icons configurati)
5. Tags → Assegna → pulsante ALL (applica tag a tutti i file visibili)
6. Rimosse animazioni slide/fade/scale su finestre, pannelli, dropdown — aperture istantanee
7. Drag&drop su schede Playlist (generale sp e drawer per-player)
8. Multi + cartella: auto-fill player vuoti dalla shared queue (ultima cartella)
9. Shortcut Multi-Next / Multi-Prev (default Ctrl+Shift+Right / Ctrl+Shift+Left)
10. VST2/VST3 plugin UI scaffold (IPC pick-vst-plugin, lista in config)
11. Webcam export format dropdown (WebM/MP4/MKV/MOV + post-process ffmpeg placeholder)
12. Shuffle con stack navigazione (Precedente torna indietro nella history shuffle)
13. Aggiornamento README + AI_ROADMAP + Changelog

### Iterazione 2
- Pannelli laterali ridimensionabili
- Cronologia con anteprime thumbnails
- Tag system custom + palette 32 colori
- Webcam settings pendenti + apply
- Filtri colore per-player

### Iterazione 1
- Multi-view 1-4 player
- Drag&drop iniziale
- EQ 3-band + boost
- i18n IT/EN
- Temi dark/light
- Scorciatoie configurabili
- Playlist save/import (M3U/JSON)

## Note

- Hosting reale VST richiede libreria nativa (juce, node-vst). UI pronta, processing TODO.
- Conversione ffmpeg per MKV/MOV: IPC `ffmpeg-convert` placeholder, da bundle ffmpeg-static.
- Playlist generale condivisa tra player attivi = `S.sharedFolderQueue`.
