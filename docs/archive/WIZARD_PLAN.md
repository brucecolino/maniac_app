# Wizard Analisi — Piano di Evoluzione

## Obiettivo
Trasformare il wizard da "analisi + griglia thumbnail" a un flusso completo:
**Analisi → Suggerimenti automatici (titoli + DB locale + API online) → Review → Tag automatici**.

Tutte le modalità (Viso, Luoghi, Oggetti, Animali, Scene, Genere, Categoria) seguiranno lo stesso pattern ma con motori diversi.

---

## 1. Ristrutturazione del flusso UI

### Stato attuale
`runWizardAnalysis` → Python → subito `renderWizardResults()` (griglia volti).

### Nuovo flusso
```
Wizard Step 1 (scelta modalità)
    ↓ Analizza
Wizard Step 2 (progress bar, invariato)
    ↓ done
Wizard Step 3 — RIEPILOGO + AZIONI (nuovo)
    • Trovati N volti unici in M file
    • Suggerimento titolo dominante: "Nome Cognome" (se rilevato)
    • 3 pulsanti:
        [🔮 Suggerimenti Automatici]  ← default, esegue title+DB+API
        [👁 Seleziona Volti Riconosciuti] ← griglia attuale
        [❌ Chiudi]
    ↓ Suggerimenti Automatici
Wizard Step 4 — REVIEW CARD (nuovo)
    • Per ogni cluster: thumbnail + nome proposto + foto online + fonte (local/TMDB/OMDB/AdultColony)
    • Slider di confidenza + bottoni [✓ Accetta] / [✎ Modifica] / [✗ Rifiuta]
    • [Applica tutto] in fondo
    ↓ Applica
Wizard Step 5 — FINE
    • Tag creati e applicati a tutti i file del cluster
    • Volti salvati nel DB locale per match futuri
    • Toast "N tag applicati su M file"
```

### File modificati
- `src/index.html`:
  - Nuove funzioni: `renderWizardSummary()`, `renderWizardReview()`, `runAutoSuggest()`
  - `runWizardAnalysis` chiama `renderWizardSummary` invece di `renderWizardResults`

---

## 2. Estrazione nomi dai titoli

### Logica (JS-side, niente dipendenze)
```js
function extractNameCandidates(files) {
  // 1. Pulizia: rimuovi estensione, [1080p], (2024), _xxx_, -dash-, tag di release
  // 2. Tokenizza su [\s\-_.\[\]()]
  // 3. Trova sequenze di 2+ token che iniziano con maiuscola, length >= 3
  // 4. Punteggio: frequenza globale × numero file in cui appare
  // 5. Applica stopwords (scene titles, risoluzioni, codec, siti)
  // 6. Ritorna lista ordinata [{name:"Nome Cognome", score, files:[...]}]
}
```

### Per ogni cluster
- Filtrare i titoli dei file che appartengono al cluster
- Trovare il nome più ricorrente in quei titoli specifici
- Normalizzare: `"john smith" → "John Smith"`, rimuovere numeri/episodi

---

## 3. Database locale volti (SQLite)

### Schema (`userData/faces.db`)
```sql
CREATE TABLE IF NOT EXISTS faces (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  embedding BLOB NOT NULL,      -- Float32Array(128) binary
  thumbnail TEXT,               -- data:image/jpeg;base64,...
  source TEXT,                  -- 'manual' | 'tmdb' | 'omdb' | 'adultcolony' | 'auto-title'
  source_id TEXT,               -- id esterno (tmdb_id, etc)
  metadata TEXT,                -- JSON: {bio, birth, nationality, photo_url, ...}
  seen_count INTEGER DEFAULT 1,
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_faces_name ON faces(name);
```

### Nuovo script Python `python/facedb.py`
Comandi:
- `--match --embedding-b64 <...>` → ritorna top-3 match con cosine sim
- `--add --name <n> --embedding-b64 <...> --thumb-b64 <...> --source <s> [--source-id <id>] [--metadata <json>]`
- `--list [--query <n>]`
- `--delete --id <id>`

Output: JSON line-delimited via stdout (stesso pattern di `analyze.py`).

### IPC bridge (main.js)
- `ai:face-db-match` (jobId, embedding)
- `ai:face-db-add` (payload)
- `ai:face-db-list` (query)
- Preload: `window.maniac.ai.faceDb.{match,add,list}`

### Integrazione con `analyze.py`
Durante `run_face`, **prima** di creare un nuovo cluster, provare il match contro il DB locale:
```python
match = facedb.query_match(emb, threshold=0.75)
if match:
    cluster.label = match['name']
    cluster.source = 'local-db'
    cluster.db_id = match['id']
```
→ L'utente vede già nomi noti senza dover cercare online.

---

## 4. API online — Servizi JS

Nuovo file helper (inline in `index.html` o modulo separato):

### TMDB Service
```js
const TMDB_KEY = window.localStorage.getItem('tmdb_key') || '';
async function tmdbSearchPerson(name) {
  const r = await fetch(`https://api.themoviedb.org/3/search/person?api_key=${TMDB_KEY}&query=${encodeURIComponent(name)}`);
  const j = await r.json();
  return (j.results||[]).slice(0,3).map(p=>({
    id:p.id, name:p.name, photo:p.profile_path?`https://image.tmdb.org/t/p/w185${p.profile_path}`:null,
    known_for:(p.known_for||[]).map(k=>k.title||k.name).slice(0,3).join(', '),
    source:'tmdb'
  }));
}
```

### OMDB Service (fallback film)
```js
const OMDB_KEY = '692eac66';  // da brief
async function omdbSearchPerson(name) { /* search?s= + type=... */ }
```

### Adult Colony Service (da brief)
```js
const ADULTCOLONY_BASE = 'https://adultcolony-api-production-8f4d.up.railway.app';
async function adultColonySearch(name) {
  const r = await fetch(`${ADULTCOLONY_BASE}/api/performers/search?q=${encodeURIComponent(name)}`);
  // gestire schema reale — per ora best-effort
}
```

### Logica di lookup combinata
```js
async function resolvePerson(candidateName, clusterThumb) {
  // 1. Local DB first (già fatto in Python)
  // 2. TMDB
  const tmdb = await tmdbSearchPerson(candidateName);
  if (tmdb.length) return { best: tmdb[0], all: tmdb };
  // 3. AdultColony (se opzione "contenuto adulto" attiva)
  if (S.enableAdultSources) {
    const ac = await adultColonySearch(candidateName);
    if (ac.length) return { best: ac[0], all: ac };
  }
  // 4. OMDB come ultimo fallback
  return null;
}
```

### Configurazione API Key
Nuova sezione in Impostazioni: input TMDB_API_KEY + toggle "Includi fonti adulti".
Persistenza: `S.tmdbKey`, `S.enableAdultSources`.

---

## 5. Applicazione tag

Già esiste `applyWizardTag`. Estenderlo per accettare il batch auto:

```js
function applyAutoSuggestions(reviewState) {
  // reviewState = [{cluster, proposedName, accepted, source}]
  for (const item of reviewState.filter(x=>x.accepted)) {
    const tagLabel = item.proposedName;
    const color = stableColorFromName(tagLabel);  // hash → palette
    ensureTagExists({label:tagLabel, color});
    for (const filePath of item.cluster.files) {
      addTagToFile(filePath, {label:tagLabel, color});
    }
    // Salva nel DB locale per match futuri
    await faceDbAdd({
      name: tagLabel,
      embedding: item.cluster.embedding,
      thumbnail: item.cluster.thumb,
      source: item.source,
      source_id: item.sourceId,
      metadata: item.metadata
    });
  }
  persistFileTags();
  saveConfig();
}
```

---

## 6. Estensione alle altre modalità

### 6.1 Luoghi (Places365)
- Python: `analyze.py` aggiunge `run_places(files)` che usa `torchvision.models.resnet50` pretrained su Places365
- Peso: `resnet50_places365.pth` (~97MB) auto-download al primo uso in `~/.maniac/weights/`
- Per ogni frame → top-5 scene label → cluster per scena dominante
- Suggerimento tag: nome della scena ("Camera da letto", "Spiaggia", "Ufficio")

### 6.2 Oggetti (YOLO v8)
- `from ultralytics import YOLO; model = YOLO('yolov8n.pt')`
- Filtro: classi `person, car, dog, ...` dal COCO dataset
- Cluster per classe (non per identità)
- Tag: nome oggetto

### 6.3 Animali
- Stesso YOLO, filtro `cat, dog, horse, sheep, cow, elephant, bear, zebra, giraffe, bird`
- Tag specifica: "🐕 Cane", "🐈 Gatto", etc.

### 6.4 Scene
- Alias di Places365 ma con label più narrativi (interno/esterno, giorno/notte)
- Euristica semplice da Places365 output

### 6.5 Genere (di film)
- Due approcci combinati:
  1. **Dai titoli**: se trovato un film in TMDB → leggi `genres[]` via `/movie/{id}`
  2. **Keyword matching**: dizionario locale `{action, comedy, horror, ...}` vs tokens titolo
- Cluster per genere dominante → tag con nome genere

### 6.6 Categoria
- Euristica file system:
  - Dimensione, durata, risoluzione
  - Pattern filename (`S01E01` → Serie TV, `2024` → Film recente, etc.)
  - Metadati MediaInfo (richiede tool esterno — opzionale)
- Tag: "Serie TV", "Film", "Clip", "Live", "Documentario", "Tutorial"

---

## 7. Modifiche file

| File | Modifica |
|---|---|
| `python/analyze.py` | Aggiunge `run_places`, `run_objects`, `run_animals`, `run_scene`, `run_genre`, `run_category`; integra `facedb` per pre-match |
| `python/facedb.py` | **Nuovo** — gestione SQLite volti |
| `main.js` | Handler `ai:face-db-*`; handler unificato già esistente per `analyze-folder` |
| `preload.js` | Esponi `ai.faceDb.{match,add,list,delete}` |
| `src/index.html` | Nuove funzioni `renderWizardSummary`, `renderWizardReview`, `runAutoSuggest`, `extractNameCandidates`, `resolvePerson`, `tmdbSearchPerson`, `adultColonySearch`, `applyAutoSuggestions`; sezione Impostazioni per API key |
| `package.json` | Nessuna nuova dipendenza Node (fetch nativo) |

---

## 8. Ordine di implementazione

1. **[Fondamenta]** `python/facedb.py` + IPC bridge + preload
2. **[Viso v2]** `analyze.py` integra facedb pre-match
3. **[UI]** Nuovo Step 3 (Summary) + Step 4 (Review)
4. **[Titoli]** `extractNameCandidates` in JS
5. **[API]** TMDB + AdultColony + OMDB + UI chiave API
6. **[Apply]** `applyAutoSuggestions` + salvataggio in facedb
7. **[Oggetti + Animali]** YOLO in `analyze.py`
8. **[Luoghi + Scene]** Places365 in `analyze.py`
9. **[Genere]** Titoli → TMDB genre lookup
10. **[Categoria]** Euristiche filename + metadati

Dopo il punto 6 il flusso principale del Viso è funzionante end-to-end. I punti 7-10 sono estensioni.

---

## 9. Rischi e mitigazioni

- **TMDB key mancante**: mostra banner nelle Impostazioni all'apertura del wizard; il suggerimento automatico funziona comunque con DB locale + titoli
- **Adult Colony endpoint sconosciuto**: best-effort con try/catch, log errore, fallback silenzioso
- **Pesi Places365 download**: al primo run, mostra progress bar separato; cache permanente
- **SQLite blocking**: tutte le operazioni in processi Python separati (non-blocking)
- **Embedding BLOB grande**: ~512 byte per volto (128 float32) → trascurabile per migliaia di volti

---

## 10. Fuori scope (rimandabili)

- ExifTool/MediaInfo integration (utile per Categoria ma non critico)
- Places365 fine-tuning
- Editing manuale del DB volti via UI (ora: solo via facedb.py CLI o tramite wizard)
- Face re-identification in tempo reale durante la riproduzione
