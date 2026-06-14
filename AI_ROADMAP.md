# Maniac · AI Face Recognition Roadmap

Analysis based on the Gemini conversation shared by the user. Practical plan for shipping face-recognition and content-analysis features inside the Electron player.

---

## 1. Goal

Let the user scan a library of video files locally and produce:

- A per-video index of detected faces (timestamps + thumbnails)
- A cross-library "who appears in which file" catalog
- Search by face ("find every clip where person X appears")
- Optional: scene/object tags, speech-to-text captions

All processing must run **locally on the user's machine** — no uploads, no API keys required.

---

## 2. Tech stack (recommended)

| Concern | Choice | Notes |
|---|---|---|
| Face detection | **face-api.js** (TinyFaceDetector) | Runs in-renderer via WebGL/WASM. ~200 KB model, fast enough for real-time. |
| Face descriptors | **face-api.js FaceRecognitionNet** | 128-d embeddings, compare via Euclidean distance. |
| Scene/object tags | **YOLOv8n** (objects/animals, 80 COCO classes) + **Places365 ResNet18** (365 scenes) | Implemented in `python/analyze.py` via the `venv311` worker. Weights lazy-downloaded into `models/ml/`. Per-file SQLite cache keyed by mtime+size avoids re-inference. |
| Speech → text (optional) | **whisper.cpp** via native addon, or **@xenova/transformers** (Whisper-tiny ONNX) | Transformers.js runs in-renderer. |
| Storage | JSON index file in `userData/ai-catalog.json` + thumbnail blobs | Keep it simple; move to SQLite only if catalog > 10 k faces. |

Everything ships inside the app — no backend.

---

## 3. Architecture

```
┌─────────────── renderer (index.html) ───────────────┐
│                                                     │
│  AI Catalog panel  ──►  queue of videos to scan     │
│                                                     │
│  Scan worker (Web Worker)                           │
│    • loads models once                              │
│    • for each video:                                │
│        - grab frames every N seconds                │
│        - detect faces → embeddings + thumbnails     │
│        - cluster embeddings (simple DBSCAN)         │
│        - emit progress events                       │
│                                                     │
│  Index store (JSON)                                 │
│    { files: [...], persons: [...], matches: [...] } │
│                                                     │
└─────────────────────────────────────────────────────┘
```

Main process stays thin: it only exposes file paths and saves/loads the index JSON.

---

## 4. Incremental milestones

### M1 — Plumbing (1–2 days)
- Add `/models` folder with face-api.js weights (bundled, not downloaded).
- New `AI Catalog` panel is already in the UI — wire it to show a file list + "Scan" button.
- IPC: `ai-catalog-load`, `ai-catalog-save` (mirror the existing config pattern).

### M2 — Face detection (2–3 days)
- Off-main-thread Web Worker that accepts `{videoPath, interval}` and emits frames.
- Use a hidden `<video>` + `<canvas>` to grab frames every 2 s (configurable).
- Run TinyFaceDetector → bounding boxes → 128-d descriptors.
- Store `{fileUid, t, bbox, descriptor, thumbDataUrl}` per detection.

### M3 — Clustering (1–2 days)
- After scan, cluster descriptors with a simple greedy threshold (distance < 0.6).
- Prompt user to name each cluster ("Person 1" → "Alice").
- Persist `persons[]` with merged thumbnail.

### M4 — Search (1 day)
- In the panel, list persons with thumbnails.
- Click a person → filter playlist to files containing them, with seek-to-timestamp buttons.
- Keyboard shortcut to jump to next appearance.

### M5 — Live face overlay (optional, 1–2 days)
- In the per-player toolbar add a "face overlay" toggle.
- Draw bounding boxes + names on a canvas overlay in real time (detect on every Nth frame, not every frame).

### M6 — Scene tags + transcript (optional, 3–5 days)
- Add COCO-SSD pass during the same scan loop (cheap).
- Add Whisper-tiny transcript via Transformers.js for audio.
- Full-text search across transcripts inside the AI Catalog panel.

---

## 5. Performance budget

| Item | Target |
|---|---|
| Scan speed | 1 min of video → <10 s on mid-range CPU (frame every 2 s, TinyFaceDetector WASM) |
| Live overlay | 15 FPS detection, 60 FPS draw |
| Memory | <400 MB extra during scan |
| Index file | ~20 KB per hour of video |

Heuristics to stay in budget:
- Downscale frames to 320 px before detection.
- Skip frames with near-identical hashes (motionless segments).
- Throttle worker to single-video scan; queue the rest.

---

## 6. Privacy + UX notes

- **Explicit opt-in.** AI scan runs only when user clicks "Scan". No background scanning.
- **Local only.** No network calls. Make this visible in the panel footer.
- **Deletable.** "Clear AI catalog" button wipes index + thumbs.
- **Rename freely.** Person labels are user-owned strings; clusters merge/split on demand.

---

## 7. Open questions

1. Should clips detected as the same person across files be auto-linked, or require manual confirmation? (Recommend: auto-link above threshold, manual below.)
2. GPU acceleration — stick to WebGL backend of face-api.js, or add a native addon path for CUDA? (Start with WebGL; revisit only if users ask.)
3. Scanning policy when the user adds new files — prompt, auto-queue, or ignore? (Recommend: small badge "N files not scanned", user-triggered.)

---

## 8. Not in scope

- Cloud sync of catalog
- Emotion / age / gender inference (ethically fraught, low value)
- Real-time recognition at 60 FPS on 4K (not needed for a media player)


---

## 9. Project Status (aggiornato 2026-04-22)

Feature app totali: 35. Implementate in 3 iterazioni. Vedi README.md sezione Changelog per dettaglio feature per iterazione.

AI status:
- Scaffold UI pronto (tab Analizza)
- ai-engine.js base presente
- face-api.js integration: TODO
- coco-ssd scene tags: TODO
- whisper.cpp STT: TODO

Ultime feature (iterazione 3) non legate all'AI ma rilevanti per UX:
- Pannello File laterale (al posto di dropdown)
- Apri URL
- Drag&drop su playlist drawer per-player
- Multi-Next / Multi-Prev shortcuts
- VST2/VST3 plugin UI (hosting TODO)
- Export registrazioni multi-formato (WebM/MP4/MKV/MOV)
- Shuffle con history navigabile
- Rimozione animazioni pannelli
- Icona .exe corretta

## Help System (planned)
A togglable in-app tutorial mode: when active, hovering UI elements shows contextual tooltips and guidance balloons. Triggered from Info menu. Stores "seen" state in localStorage to avoid repeating hints.
