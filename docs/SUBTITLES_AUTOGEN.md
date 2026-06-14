# Sottotitoli — Generazione automatica + Traduzione (F5)

Maniac integra una pipeline completa per generare e tradurre sottotitoli da
**video** e **file audio** (mp3/wav/flac/m4a/podcast/voice).

## Architettura

```
┌─────────────────┐  IPC subs:generate   ┌──────────────────┐
│  Renderer (UI)  │ ────────────────────▶│  Main process    │
│                 │                       │  spawn python ── │ ─▶ python/subtitles.py
│  Settings →     │  IPC subs:translate   │                  │       (faster-whisper)
│  Sottotitoli    │ ────────────────────▶│                  │       (argostranslate)
└─────────────────┘  ◀── subs:progress ──┴──────────────────┘
       │ done
       ▼
   <track> SRT/VTT caricato sul player
```

### Backend Python — `python/subtitles.py`

Comandi CLI:

| Comando | Argomenti | Output |
|---------|-----------|--------|
| `generate` | `--video PATH --lang auto\|it\|en --model tiny\|base\|small\|medium\|large [--out PATH]` | `.srt` accanto al sorgente |
| `translate` | `--srt PATH --from LANG --to LANG [--out PATH]` | `.<to>.srt` accanto al sorgente |
| `list-langs` | — | JSON delle lingue disponibili (Argos packs installati) |
| `install-lang` | `--from LANG --to LANG` | scarica e installa pacchetto Argos |

Eventi JSONL stampati su stdout durante l'esecuzione:
```json
{"type":"progress","jobId":"…","percent":42.0,"phase":"transcribe","text":"…"}
{"type":"done","jobId":"…","outPath":"C:\\file.srt","detectedLang":"en"}
{"type":"error","jobId":"…","error":"…"}
```

### Modelli Whisper

I modelli vengono **scaricati on-demand** da HuggingFace al primo uso e
cachati in `~/.cache/huggingface/hub/`. Dimensioni indicative:

| Modello | Dimensione | RTF (Real-Time Factor) | Uso consigliato |
|---------|-----------|-------------------------|------------------|
| tiny    | 75 MB     | ~0.05× (CPU)            | Test rapidi |
| base    | 140 MB    | ~0.1×                    | Note vocali |
| small   | 470 MB    | ~0.3×                    | **Default consigliato** |
| medium  | 1.5 GB    | ~0.7×                    | Podcast / video parlati |
| large   | 3 GB      | ~1.3×                    | Lavorazione professionale |

> **RTF**: rapporto tempo elaborazione / durata audio. Es. `small RTF 0.3` =
> un audio di 10 minuti viene trascritto in ~3 minuti su CPU.

GPU: `faster-whisper` rileva automaticamente CUDA se disponibile,
dimezzando i tempi.

### Traduzione — Argos Translate

Pacchetti scaricati on-demand (~150 MB ciascuno). Coppia `it→en` viene
installata automaticamente al primo uso. Per altre coppie, click su
*Installa pack lingua* nelle Impostazioni → Sottotitoli.

## UI utente

**Impostazioni → Sottotitoli**:

- **Modello**: dropdown con i 5 size whisper.
- **Lingua sorgente**: `auto` (whisper rileva) o forzata.
- **🎙 Genera da audio**: invoca `subs:generate` sul file corrente del
  player attivo. Il file `.srt` viene caricato come `<track>` automaticamente.
- **✨ Rileva e traduci**: pipeline completa
  1. `subs:generate` con `lang=auto`
  2. al `done`, legge `detectedLang`
  3. invoca `subs:translate` da quella lingua → `S.translatorLang`
  4. carica il SRT tradotto come traccia attiva.

## Formati supportati

- **Video**: mp4, mkv, webm, mov, avi, mpeg-2 — il backend estrae l'audio
  via ffmpeg interno.
- **Audio**: mp3, wav, flac, m4a, ogg, opus.

## Troubleshooting

| Sintomo | Causa probabile | Soluzione |
|---------|------------------|-----------|
| `faster-whisper non trovato` | venv311 non popolato | Esegui `python -m pip install faster-whisper` nel venv |
| Generazione lentissima | CPU, modello grande | Usa `small` o abilita CUDA (richiede driver NVIDIA) |
| `argostranslate.errors.PackageNotFound` | Coppia lingua non installata | Click su "Installa pack lingua" |
| Lingua source sbagliata | Whisper detect fallito | Forza la lingua manualmente nel dropdown |

## Persistenza

Le preferenze utente (`subsModel`, `subsSrcLang`, `translatorLang`,
`subsFromMetadata`, `subsSize`, `subsColor`) sono salvate in
`%APPDATA%/maniac/maniac-config.json` via `configSave`.
