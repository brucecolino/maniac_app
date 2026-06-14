#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Analisi video/immagini per Maniac (pipeline multi-modalità).

Modalità:
 - face     : clustering volti (DeepFace + Facenet) + match DB locale + lookup API esterne
 - object   : YOLOv8 (COCO) per oggetti reali + merge con entities kind=object
 - place    : Places365 ResNet18 per scene reali + merge con entities kind=place
 - animal   : YOLOv8 filtrato sulle classi animali COCO + merge entities kind=animal
 - genre    : keyword filename + match entities kind=genre
 - category : keyword filename + match entities kind=category

Se i modelli ML non sono disponibili (errore di import o download fallito),
le modalità object/place/animal effettuano fallback al keyword-match sul filename.

Eventi su stdout (uno per riga JSON):
  {type:"phase",     text:"…"}                    # status human-readable della fase corrente
  {type:"progress",  stage:"…", current,total,file}
  {type:"warn",      file/mode, error}
  {type:"error",     error, trace?}
  {type:"done",      results:[…]}
  {type:"exit"}                                    # implicito a chiusura processo

CLI:
  python analyze.py --folder DIR --modes face[,...]
                    [--max-files 200]
                    [--gender male|female|both]
                    [--face-db PATH] [--match-threshold 0.75]
                    [--entity-db PATH]
                    [--tmdb-key K] [--omdb-key K] [--adultcolony BASE_URL]
"""
import sys, os, json, argparse, base64, math, time, re, traceback, sqlite3, struct, hashlib

# ─────────────────────────────────────────────────────────────────────
# Util
# ─────────────────────────────────────────────────────────────────────
def _emit(obj):
    try:
        sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
        sys.stdout.flush()
    except Exception:
        pass

def _phase(text):
    _emit({"type": "phase", "text": text})

VIDEO_EXT = {'.mp4','.mkv','.webm','.mov','.avi','.m4v','.flv','.wmv','.mpg','.mpeg','.ts','.m2ts','.ogv','.3gp'}
IMAGE_EXT = {'.jpg','.jpeg','.png','.bmp','.webp','.gif','.tif','.tiff'}
AUDIO_EXT = {'.mp3','.flac','.m4a','.aac','.ogg','.opus','.wma','.wav','.aiff','.aif','.alac','.ape'}

def list_files(folder, max_files, include_audio=False):
    out = []
    for root, dirs, files in os.walk(folder):
        for f in files:
            ext = os.path.splitext(f)[1].lower()
            ok = ext in VIDEO_EXT or ext in IMAGE_EXT
            if include_audio and ext in AUDIO_EXT:
                ok = True
            if ok:
                out.append(os.path.join(root, f))
                if max_files and len(out) >= max_files:
                    return out
    return out

def thumb_b64(frame_bgr, max_w=180):
    import cv2
    h, w = frame_bgr.shape[:2]
    if w > max_w:
        nh = int(h * (max_w / w))
        frame_bgr = cv2.resize(frame_bgr, (max_w, nh))
    ok, buf = cv2.imencode('.jpg', frame_bgr, [int(cv2.IMWRITE_JPEG_QUALITY), 78])
    if not ok:
        return None
    return "data:image/jpeg;base64," + base64.b64encode(buf.tobytes()).decode('ascii')

def _adaptive_sampling(duration_sec, every_sec, max_frames):
    """Restituisce (every_sec, max_frames) adattati alla durata.
    Priorità velocità: campiona al massimo max_frames frame distribuiti
    uniformemente, indipendentemente dalla durata del video."""
    if duration_sec <= 0:
        return every_sec, max_frames
    if duration_sec < 15:
        # Video brevissimo: campiona ogni 2s, max 4 frame
        return 2.0, min(max_frames, 4)
    # Per qualsiasi durata: distribuisci esattamente max_frames frame
    # sull'intera durata → spacing = duration / max_frames
    spacing = duration_sec / max(1, max_frames)
    return max(every_sec, spacing), max_frames

def iter_video_frames(path, every_sec=2.0, max_frames=20, on_frame=None, adaptive=True):
    """Yields (timestamp, frame_bgr). Se adaptive=True, scala in base alla durata.
    `on_frame(grabbed_index, planned_total)` chiamato dopo ogni frame letto."""
    import cv2
    cap = cv2.VideoCapture(path)
    if not cap.isOpened(): return
    try:
        fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
        total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        duration = (total / fps) if fps > 0 else 0
        if adaptive:
            every_sec, max_frames = _adaptive_sampling(duration, every_sec, max_frames)
        step = max(1, int(fps * every_sec))
        planned = max_frames if not total else min(max_frames, max(1, total // step))
        grabbed = 0; idx = 0
        while grabbed < max_frames:
            cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
            ok, fr = cap.read()
            if not ok: break
            yield idx / fps, fr
            grabbed += 1; idx += step
            if on_frame:
                try: on_frame(grabbed, planned)
                except Exception: pass
            if total and idx >= total: break
    finally:
        cap.release()

def cosine(a, b):
    na = math.sqrt(sum(x*x for x in a)) or 1.0
    nb = math.sqrt(sum(x*x for x in b)) or 1.0
    return sum(x*y for x,y in zip(a,b)) / (na*nb)

# ─────────────────────────────────────────────────────────────────────
# Entity DB (legge entities + legacy faces in fallback)
# ─────────────────────────────────────────────────────────────────────
def _load_face_db_entries(db_path):
    """Restituisce lista volti dal DB. Preferisce tabella `entities` (kind=face),
    fallback a tabella legacy `faces`."""
    if not db_path or not os.path.exists(db_path):
        return []
    out = []
    try:
        con = sqlite3.connect(db_path)
        # Prima `entities`
        try:
            for r in con.execute(
                "SELECT id,name,embedding,thumbnail,source,external_id "
                "FROM entities WHERE kind='face' AND embedding IS NOT NULL"):
                blob = r[2]
                if not blob: continue
                n = len(blob) // 4
                vec = list(struct.unpack('<%sf' % n, blob))
                out.append({"id": r[0], "name": r[1], "embedding": vec,
                            "thumbnail": r[3], "source": r[4], "tmdb_id": r[5]})
        except sqlite3.OperationalError:
            pass
        # Poi legacy `faces`
        try:
            for r in con.execute("SELECT id,name,embedding,thumbnail,source,tmdb_id FROM faces"):
                blob = r[2]
                if not blob: continue
                n = len(blob) // 4
                vec = list(struct.unpack('<%sf' % n, blob))
                out.append({"id": "legacy_" + str(r[0]), "name": r[1], "embedding": vec,
                            "thumbnail": r[3], "source": r[4], "tmdb_id": r[5]})
        except sqlite3.OperationalError:
            pass
        con.close()
    except Exception:
        pass
    return out

def _load_entity_names(db_path, kind):
    """Carica entry di kind specificato dalla tabella entities (per match testuale)."""
    if not db_path or not os.path.exists(db_path):
        return []
    out = []
    try:
        con = sqlite3.connect(db_path)
        try:
            for r in con.execute(
                "SELECT id, name, thumbnail, source, external_id, metadata "
                "FROM entities WHERE kind=?", (kind,)):
                meta = None
                if r[5]:
                    try: meta = json.loads(r[5])
                    except Exception: meta = None
                out.append({"id": r[0], "name": r[1], "thumbnail": r[2],
                            "source": r[3], "external_id": r[4], "metadata": meta})
        except sqlite3.OperationalError:
            pass
        con.close()
    except Exception:
        pass
    return out

# ─────────────────────────────────────────────────────────────────────
# Estrazione candidati nomi da filename (pulizia tags release)
# ─────────────────────────────────────────────────────────────────────
NAME_RE = re.compile(r"\b[A-ZÀ-Ý][a-zà-ÿ]{1,}(?:\s+[A-ZÀ-Ý][a-zà-ÿ]{1,}){1,2}\b")
RELEASE_RE = re.compile(
    r"\b(1080p|720p|480p|2160p|4k|x264|x265|h264|h265|hevc|aac|mp3|web-dl|webrip|"
    r"bluray|brrip|hdrip|dvdrip|uncut|remux|hdr|sdr|ita|eng|sub)\b", re.IGNORECASE)
SEASON_RE = re.compile(r"\bS\d{1,2}E\d{1,2}\b", re.IGNORECASE)

def clean_name_for_search(filename):
    base = os.path.splitext(os.path.basename(filename))[0]
    s = RELEASE_RE.sub(' ', base)
    s = SEASON_RE.sub(' ', s)
    s = re.sub(r'\b(19|20)\d{2}\b', ' ', s)
    s = re.sub(r'[._\-\[\](){}]', ' ', s)
    return re.sub(r'\s+', ' ', s).strip()

def extract_name_candidates(files, blocklist=None, top=3):
    bl = set(n.lower() for n in (blocklist or []))
    counts = {}
    for p in files:
        cleaned = clean_name_for_search(p)
        seen = set()
        for m in NAME_RE.finditer(cleaned):
            name = m.group(0).strip()
            if name.lower() in bl: continue
            if name in seen: continue
            seen.add(name)
            if name.split()[0] in ('Season','Episode','Episodio','Volume','Part','Chapter'):
                continue
            counts[name] = counts.get(name, 0) + 1
    ranked = sorted(counts.items(), key=lambda x: -x[1])
    return [{"name": n, "score": s} for n, s in ranked[:top]]

# ─────────────────────────────────────────────────────────────────────
# Lookup API esterne — TMDB / StashDB / AdultColony / Wikidata / Wikipedia / OMDB
# ─────────────────────────────────────────────────────────────────────
def _safe_request(method, url, *, params=None, json_payload=None, headers=None, timeout=8):
    try:
        import requests
        if method == 'GET':
            r = requests.get(url, params=params, headers=headers, timeout=timeout)
        else:
            r = requests.post(url, json=json_payload, headers=headers, timeout=timeout)
        if r.status_code != 200: return None
        return r.json()
    except Exception:
        return None

def _provider_tmdb(name, tmdb_key, timeout=8):
    if not tmdb_key: return []
    j = _safe_request('GET',
        "https://api.themoviedb.org/3/search/person",
        params={"api_key": tmdb_key, "query": name},
        timeout=timeout)
    out = []
    for hit in (j or {}).get('results', [])[:5]:
        out.append({
            "name": hit.get('name'),
            "photoUrl": ("https://image.tmdb.org/t/p/w185" + hit['profile_path'])
                        if hit.get('profile_path') else None,
            "source": "tmdb",
            "external_id": hit.get('id'),
            "metadata": {"known_for": hit.get('known_for') or [],
                         "popularity": hit.get('popularity')},
        })
    return out

def _provider_stashdb(name, stashdb_key, timeout=8):
    """StashDB GraphQL: cerca performer per nome. Richiede API key (https://stashdb.org)."""
    if not stashdb_key: return []
    query = ('query($name:String!){'
             'queryPerformers(input:{names:$name,per_page:5}){'
             'performers{id name images{url}}'
             '}}')
    j = _safe_request('POST',
        "https://stashdb.org/graphql",
        json_payload={"query": query, "variables": {"name": name}},
        headers={"ApiKey": stashdb_key, "Content-Type": "application/json"},
        timeout=timeout)
    perfs = (((j or {}).get('data') or {}).get('queryPerformers') or {}).get('performers') or []
    out = []
    for p in perfs[:5]:
        photo = None
        imgs = p.get('images') or []
        if imgs: photo = imgs[0].get('url')
        out.append({
            "name": p.get('name'),
            "photoUrl": photo,
            "source": "stashdb",
            "external_id": p.get('id'),
            "metadata": {"images": [i.get('url') for i in imgs[:3]]},
        })
    return out

def _provider_adultcolony(name, base, timeout=8):
    if not base: return []
    j = _safe_request('GET', base.rstrip('/') + "/search",
                       params={"q": name}, timeout=timeout)
    out = []
    items = []
    if isinstance(j, list): items = j
    elif isinstance(j, dict): items = j.get('results') or j.get('data') or []
    for hit in items[:5]:
        out.append({
            "name": hit.get('name') or hit.get('title') or name,
            "photoUrl": hit.get('image') or hit.get('photo') or hit.get('thumbnail'),
            "source": "adultcolony",
            "external_id": hit.get('id') or hit.get('slug'),
            "metadata": hit,
        })
    return out

def _provider_wikidata(name, timeout=8):
    """Wikidata: cerca entità per label, prendi P18 (image) come photoUrl.
    Niente API key richiesta. Usa wbsearchentities + wbgetclaims."""
    j = _safe_request('GET',
        "https://www.wikidata.org/w/api.php",
        params={"action":"wbsearchentities","format":"json","language":"en",
                "type":"item","search":name,"limit":5},
        timeout=timeout)
    out = []
    for entity in (j or {}).get('search', [])[:5]:
        qid = entity.get('id')
        if not qid: continue
        # Recupera P18 (immagine). Solo una richiesta extra ogni 5 entità.
        c = _safe_request('GET',
            "https://www.wikidata.org/w/api.php",
            params={"action":"wbgetclaims","format":"json",
                    "entity":qid,"property":"P18"},
            timeout=timeout)
        photo = None
        try:
            claim = (((c or {}).get('claims') or {}).get('P18') or [None])[0]
            fname = claim['mainsnak']['datavalue']['value'] if claim else None
            if fname:
                photo = "https://commons.wikimedia.org/wiki/Special:FilePath/" + fname
        except Exception:
            photo = None
        out.append({
            "name": entity.get('label') or name,
            "photoUrl": photo,
            "source": "wikidata",
            "external_id": qid,
            "metadata": {"description": entity.get('description')},
        })
    return out

def _provider_wikipedia(name, timeout=8):
    """Wikipedia REST API: page summary → originalimage / thumbnail. No key."""
    try:
        import urllib.parse
        slug = urllib.parse.quote(name.replace(' ', '_'), safe='')
    except Exception:
        slug = name.replace(' ', '_')
    j = _safe_request('GET',
        "https://en.wikipedia.org/api/rest_v1/page/summary/" + slug,
        timeout=timeout)
    if not j or j.get('type') == 'disambiguation' or not j.get('title'):
        return []
    photo = None
    if j.get('originalimage'): photo = j['originalimage'].get('source')
    elif j.get('thumbnail'): photo = j['thumbnail'].get('source')
    return [{
        "name": j.get('title') or name,
        "photoUrl": photo,
        "source": "wikipedia",
        "external_id": j.get('pageid'),
        "metadata": {"description": j.get('description'),
                     "extract": (j.get('extract') or '')[:240]},
    }]

def _provider_omdb(name, omdb_key, timeout=8):
    if not omdb_key: return []
    j = _safe_request('GET', "https://www.omdbapi.com/",
                       params={"apikey": omdb_key, "s": name}, timeout=timeout)
    hits = (j or {}).get('Search') or []
    out = []
    for hit in hits[:3]:
        poster = hit.get('Poster')
        out.append({
            "name": name,
            "photoUrl": poster if poster and poster != 'N/A' else None,
            "source": "omdb",
            "external_id": hit.get('imdbID'),
            "metadata": hit,
        })
    return out

def lookup_external_all(name, tmdb_key=None, omdb_key=None, adultcolony_base=None,
                         stashdb_key=None, use_wikidata=False, use_wikipedia=False,
                         timeout=8):
    """Aggrega candidati da TUTTI i provider abilitati. Ritorna lista (max ~20),
    ordinata per affidabilità del provider (TMDB/StashDB > AdultColony > Wikidata > Wikipedia > OMDB).
    Se `requests` non è disponibile ritorna [] con flag interno."""
    try:
        import requests  # noqa
    except ImportError:
        return [{"error": "requests_missing"}]
    out = []
    out.extend(_provider_tmdb(name, tmdb_key, timeout))
    out.extend(_provider_stashdb(name, stashdb_key, timeout))
    out.extend(_provider_adultcolony(name, adultcolony_base, timeout))
    if use_wikidata: out.extend(_provider_wikidata(name, timeout))
    if use_wikipedia: out.extend(_provider_wikipedia(name, timeout))
    out.extend(_provider_omdb(name, omdb_key, timeout))
    # dedup per (source, external_id)
    seen = set(); dedup = []
    for c in out:
        k = (c.get('source'), c.get('external_id'))
        if k in seen: continue
        seen.add(k); dedup.append(c)
    return dedup

def lookup_external(name, tmdb_key=None, omdb_key=None, adultcolony_base=None, timeout=8):
    """Compat: ritorna primo candidato (vecchia API)."""
    cands = lookup_external_all(name, tmdb_key=tmdb_key, omdb_key=omdb_key,
                                  adultcolony_base=adultcolony_base, timeout=timeout)
    if cands and cands[0].get('error'): return cands[0]
    return cands[0] if cands else None

# ─────────────────────────────────────────────────────────────────────
# Verifica seconda passata: scarica foto candidato → embedding → cosine
# vs cluster_embedding. Restituisce float [0..1] (similarity).
# ─────────────────────────────────────────────────────────────────────
def verify_match_photo(photo_url, cluster_embedding, timeout=8, photo_emb_cache=None):
    """Scarica una foto, estrae il primo volto, calcola embedding FaceNet e
    confronta con cluster_embedding. Ritorna similarity ∈ [0,1] oppure None
    se il match non è valutabile (download/decode/face-detect falliti).

    Se `photo_emb_cache` è un dict, gli embedding già calcolati per uno stesso
    URL vengono riutilizzati (chiave: photo_url, valore: list[float] | None).
    Lo stesso dict riceve il risultato per richiami successivi.
    """
    if not photo_url or not cluster_embedding:
        return None
    # Cache hit: salta download + face detect + embedding.
    if photo_emb_cache is not None and photo_url in photo_emb_cache:
        emb = photo_emb_cache.get(photo_url)
        if not emb:
            return None
        sim = cosine(cluster_embedding, emb)
        return float(max(0.0, min(1.0, sim)))
    try:
        import requests
        r = requests.get(photo_url, timeout=timeout, stream=True)
        if r.status_code != 200:
            if photo_emb_cache is not None: photo_emb_cache[photo_url] = None
            return None
        data = r.content
        if not data or len(data) < 1024:
            if photo_emb_cache is not None: photo_emb_cache[photo_url] = None
            return None
    except Exception:
        if photo_emb_cache is not None: photo_emb_cache[photo_url] = None
        return None
    try:
        import cv2, numpy as np
        arr = np.frombuffer(data, dtype=np.uint8)
        img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if img is None:
            if photo_emb_cache is not None: photo_emb_cache[photo_url] = None
            return None
    except Exception:
        if photo_emb_cache is not None: photo_emb_cache[photo_url] = None
        return None
    try:
        from deepface import DeepFace
        faces = DeepFace.extract_faces(img_path=img, detector_backend='opencv',
                                       enforce_detection=False, align=True)
        if not faces:
            if photo_emb_cache is not None: photo_emb_cache[photo_url] = None
            return None
        face_img = faces[0].get('face')
        if face_img is None:
            if photo_emb_cache is not None: photo_emb_cache[photo_url] = None
            return None
        import numpy as np2, cv2 as cv2b
        fa = (face_img * 255).astype('uint8') if face_img.dtype != 'uint8' else face_img
        fa_bgr = cv2b.cvtColor(fa, cv2b.COLOR_RGB2BGR)
        rep = DeepFace.represent(img_path=fa_bgr, model_name='Facenet',
                                 detector_backend='skip', enforce_detection=False)
        emb = rep[0]['embedding'] if rep else None
        if not emb:
            if photo_emb_cache is not None: photo_emb_cache[photo_url] = None
            return None
        if photo_emb_cache is not None:
            photo_emb_cache[photo_url] = list(emb)
        sim = cosine(cluster_embedding, emb)
        # cosine ∈ [-1,1]; volti simili tipicamente 0.4-0.95. Clippa a [0,1].
        return float(max(0.0, min(1.0, sim)))
    except Exception:
        if photo_emb_cache is not None: photo_emb_cache[photo_url] = None
        return None

# ─────────────────────────────────────────────────────────────────────
# Face mode
# ─────────────────────────────────────────────────────────────────────
def _face_variance(face_bgr):
    try:
        import cv2
        g = cv2.cvtColor(face_bgr, cv2.COLOR_BGR2GRAY)
        return float(g.var())
    except Exception:
        return 0.0

def run_face(files, face_db_path=None, match_threshold=0.50,
             gender='both', tmdb_key=None, omdb_key=None, adultcolony_base=None,
             stashdb_key=None, use_wikidata=False, use_wikipedia=False,
             verify_photo=True, confirm_threshold=0.80, candidate_threshold=0.60):
    import cv2, time
    import numpy as np
    _phase("Caricamento modelli DeepFace…")
    from deepface import DeepFace

    # Detector: 'opencv' (Haar) ha recall pessimo su volti non frontali/piccoli/in
    # penombra → "nessun volto trovato" anche quando ci sono. Scegliamo il miglior
    # backend DISPONIBILE in ordine di accuratezza, con probe a runtime così se i
    # pesi non sono installati/scaricabili si degrada con grazia su opencv.
    def _pick_detector():
        probe = (np.random.rand(96, 96, 3) * 255).astype('uint8')
        for cand in ('retinaface', 'ssd', 'mtcnn', 'opencv'):
            try:
                DeepFace.extract_faces(img_path=probe, detector_backend=cand,
                                       enforce_detection=False, align=False)
                return cand
            except Exception:
                continue
        return 'opencv'
    DETECTOR  = _pick_detector()
    _emit({"type": "info", "detector": DETECTOR})
    # 960px (era 640): più recall sui volti piccoli (4K/gruppi). retinaface/ssd
    # reggono comunque < 1s/frame su questa dimensione.
    MAX_DIM   = 960 if DETECTOR != 'opencv' else 640
    FILE_TIMEOUT = 18.0 if DETECTOR != 'opencv' else 12.0
    THRESH    = 0.35
    MIN_VAR   = 30.0

    # Pre-carica il modello Facenet una volta sola per evitare 2-3s di latenza al primo file
    try:
        _dummy_face = np.zeros((160, 160, 3), dtype=np.uint8)
        DeepFace.represent(img_path=_dummy_face, model_name='Facenet',
                           detector_backend='skip', enforce_detection=False)
    except Exception:
        pass

    clusters = []
    next_id = 1

    def assign(emb, face_crop, src_path, gender_hint=None):
        nonlocal next_id
        best = None; best_sim = -1.0
        for c in clusters:
            sim = cosine(emb, c['embedding'])
            if sim > best_sim:
                best_sim = sim; best = c
        fv = _face_variance(face_crop)
        if best is not None and best_sim >= (1.0 - THRESH):
            best['count'] += 1
            best['files'].add(src_path)
            n = best['count']
            best['embedding'] = [(best['embedding'][i]*(n-1) + emb[i]) / n for i in range(len(emb))]
            if fv >= MIN_VAR and fv > best.get('thumb_var', 0.0):
                t = thumb_b64(face_crop, max_w=160)
                if t:
                    best['thumb'] = t; best['thumb_var'] = fv
            if gender_hint:
                best.setdefault('genders', {})
                best['genders'][gender_hint] = best['genders'].get(gender_hint, 0) + 1
            return best
        thumb_ok = fv >= MIN_VAR
        c = {
            'id': next_id,
            'embedding': list(emb),
            'count': 1,
            'thumb': thumb_b64(face_crop, max_w=160),
            'thumb_var': fv if thumb_ok else 0.0,
            'files': {src_path},
            'label': f"Persona {next_id}",
            'genders': {gender_hint: 1} if gender_hint else {},
        }
        next_id += 1
        clusters.append(c)
        return c

    total = len(files)
    _phase(f"Scansione {total} file per volti…")
    do_gender = (gender or 'both').lower() != 'both'

    # Pre-carico DB locale per early-exit: se un file matcha già un performer
    # confermato (source='stashdb' + external_id), processiamo poche frame.
    _early_db = _load_face_db_entries(face_db_path) if face_db_path else []
    _early_confirmed = [e for e in _early_db if e.get('name')]

    def _file_matches_known(emb, threshold=0.55):
        for e in _early_confirmed:
            if cosine(emb, e['embedding']) >= threshold:
                return True
        return False

    for i, path in enumerate(files):
        _emit({"type": "progress", "stage": "scan-faces",
               "current": i + 1, "total": total,
               "file": os.path.basename(path)})
        try:
            ext = os.path.splitext(path)[1].lower()
            frames = []
            if ext in IMAGE_EXT:
                img = cv2.imread(path)
                if img is not None: frames.append((0.0, img))
            else:
                # 4s spacing × 6 frame max: ~24s di copertura tipica (performer visibile
                # quasi sempre nei primi 20-30s). L'adaptive_sampling distribuisce i frame
                # uniformemente sulla durata reale del video.
                for t, fr in iter_video_frames(path, every_sec=4.0, max_frames=6):
                    frames.append((t, fr))
            _matched_known_in_file = False
            _file_t0 = time.time()
            for t, fr in frames:
                if time.time() - _file_t0 > FILE_TIMEOUT:
                    break  # Supera timeout → salta frame restanti, vai al file successivo
                if _matched_known_in_file:
                    break
                # Ridimensiona frame a max MAX_DIM px: 4-9x speedup su video HD/4K
                _h, _w = fr.shape[:2]
                if max(_h, _w) > MAX_DIM:
                    _scale = MAX_DIM / max(_h, _w)
                    fr = cv2.resize(fr, (int(_w * _scale), int(_h * _scale)),
                                    interpolation=cv2.INTER_AREA)
                try:
                    faces = DeepFace.extract_faces(
                        img_path=fr, detector_backend=DETECTOR,
                        enforce_detection=False, align=True)
                except Exception:
                    faces = []
                for f in (faces or []):
                    face_img = f.get('face')
                    if face_img is None: continue
                    import numpy as np
                    fa = (face_img * 255).astype('uint8') if face_img.dtype != 'uint8' else face_img
                    fa_bgr = cv2.cvtColor(fa, cv2.COLOR_RGB2BGR)
                    # Embedding
                    try:
                        rep = DeepFace.represent(
                            img_path=fa_bgr, model_name='Facenet',
                            detector_backend='skip', enforce_detection=False)
                        emb = rep[0]['embedding'] if rep else None
                    except Exception:
                        emb = None
                    if not emb: continue
                    # Gender (best-effort)
                    g_hint = None
                    if do_gender:
                        try:
                            an = DeepFace.analyze(
                                img_path=fa_bgr, actions=['gender'],
                                detector_backend='skip', enforce_detection=False, silent=True)
                            an = an[0] if isinstance(an, list) else an
                            g_hint = (an.get('dominant_gender') or '').lower()
                            if g_hint == 'man': g_hint = 'male'
                            elif g_hint == 'woman': g_hint = 'female'
                        except Exception:
                            g_hint = None
                    assign(emb, fa_bgr, path, gender_hint=g_hint)
                    # Speed-up: se l'embedding matcha un performer già confermato
                    # in faces.db, segnamo il file come "noto" → terminiamo l'analisi
                    # delle frame restanti (2-3 conferme bastano).
                    if _early_confirmed and _file_matches_known(emb):
                        _matched_known_in_file = True
                        break
        except Exception as e:
            _emit({"type": "warn", "file": os.path.basename(path), "error": str(e)[:160]})

    # Match DB locale
    _phase("Confronto con DB locale volti…")
    db_entries = _load_face_db_entries(face_db_path) if face_db_path else []

    def match_local(emb):
        best = None; best_score = -1.0
        for e in db_entries:
            s = cosine(emb, e['embedding'])
            if s > best_score:
                best_score = s; best = e
        if best and best_score >= match_threshold:
            return {"id": best['id'], "name": best['name'],
                    "score": round(float(best_score), 4),
                    "source": best.get('source'),
                    "tmdb_id": best.get('tmdb_id'),
                    "thumbnail": best.get('thumbnail')}
        return None

    # Filtra per genere
    def cluster_gender(c):
        gs = c.get('genders') or {}
        if not gs: return None
        return max(gs.items(), key=lambda kv: kv[1])[0]

    # Costruisci risultati ordinati. Quando l'utente ha richiesto un genere
    # specifico (male/female), scartiamo i cluster del genere opposto: non
    # vanno mostrati nemmeno tra i "Non riconosciuti".
    results = []
    requested_gender = (gender or 'both').lower()
    skipped_wrong_gender = 0
    for c in sorted(clusters, key=lambda x: -x['count']):
        cg = cluster_gender(c)
        if requested_gender != 'both' and cg is not None and cg != requested_gender:
            skipped_wrong_gender += 1
            continue
        local = match_local(c['embedding']) if db_entries else None
        results.append({
            "id": c['id'],
            "label": c['label'] + f" ({c['count']} volti)",
            "thumb": c['thumb'],
            "files": sorted(list(c['files'])),
            "count": c['count'],
            "type": "face",
            "gender": cg,
            "genderMatch": True,
            "avgEmbedding": [round(float(x), 6) for x in c['embedding']],
            "localMatch": local,
            "apiMatch": None,
            "nameCandidates": [],
        })
    if skipped_wrong_gender:
        _phase(f"Esclusi {skipped_wrong_gender} cluster per filtro genere ({requested_gender}).")

    # Estrazione candidati nome dai filename per cluster non già matchati localmente
    _phase("Analisi titoli file per nomi…")
    for r in results:
        if not r['localMatch']:
            r['nameCandidates'] = extract_name_candidates(r['files'])

    # Lookup API esterne (per chi ha un nome ma non match locale).
    # NUOVO: multi-provider (TMDB/StashDB/AdultColony/Wikidata/Wikipedia/OMDB) +
    # verifica seconda passata immagine-vs-immagine. Per ogni cluster scarichiamo
    # la foto del candidato top, calcoliamo embedding e confrontiamo col cluster.
    # Confidence:
    #   ≥ confirm_threshold (def 0.85) → 'confirmed'
    #   ≥ candidate_threshold (def 0.65) → 'candidate'
    #   < candidate_threshold            → 'rejected' (passa al provider successivo)
    any_provider = any([tmdb_key, omdb_key, adultcolony_base, stashdb_key,
                        use_wikidata, use_wikipedia])
    targets = [r for r in results if not r['localMatch'] and r.get('nameCandidates')]
    if targets and any_provider:
        verify_str = " + verifica foto" if verify_photo else ""
        _phase(f"Ricerca su API esterne ({len(targets)} candidati){verify_str}…")
        for idx, r in enumerate(targets):
            cand = r['nameCandidates'][0]['name']
            _emit({"type": "progress", "stage": "lookup-external",
                   "current": idx + 1, "total": len(targets), "file": cand})
            try:
                cands = lookup_external_all(
                    cand,
                    tmdb_key=tmdb_key, omdb_key=omdb_key,
                    adultcolony_base=adultcolony_base,
                    stashdb_key=stashdb_key,
                    use_wikidata=use_wikidata, use_wikipedia=use_wikipedia)
                if cands and cands[0].get('error'):
                    continue  # requests missing
                # Verifica seconda passata: scegli il candidato con cosine più alto
                # vs il centroide del cluster. Se nessuno supera la soglia → 'rejected'.
                best = None; best_sim = -1.0
                if not verify_photo:
                    # senza verifica → primo candidato disponibile come oggi
                    if cands:
                        cands[0]['confidence'] = 'unverified'
                        best = cands[0]
                else:
                    cluster_emb = r.get('avgEmbedding') or []
                    for c in cands[:6]:  # limite max 6 download per cluster
                        sim = verify_match_photo(c.get('photoUrl'), cluster_emb)
                        if sim is None:
                            c['verifiedSim'] = None
                            c['confidence'] = 'unverifiable'
                            continue
                        c['verifiedSim'] = round(sim, 4)
                        if sim >= confirm_threshold:    c['confidence'] = 'confirmed'
                        elif sim >= candidate_threshold: c['confidence'] = 'candidate'
                        else:                            c['confidence'] = 'rejected'
                        if sim > best_sim:
                            best_sim = sim; best = c
                        # Early-exit se confermato sopra soglia alta
                        if sim >= confirm_threshold:
                            break
                if best:
                    r['apiMatch'] = best
                    others = [c for c in cands if c is not best][:4]
                    r['apiCandidates'] = others
            except Exception as e:
                _emit({"type": "warn", "mode": "lookup", "error": str(e)[:160]})
    else:
        if not any_provider:
            _phase("Lookup esterno saltato (nessuna API key o provider abilitato).")
        else:
            _phase("Lookup esterno saltato (nessun candidato senza match locale).")

    return results

# ─────────────────────────────────────────────────────────────────────
# Cache modelli ML (YOLO / Places365)
# ─────────────────────────────────────────────────────────────────────
def _ml_cache_dir():
    """Cartella pesi ML. In packaged usa MANIAC_MODELS_DIR (userData, scrivibile);
    in dev ripiega su <app>/models/ml/."""
    d = os.environ.get('MANIAC_MODELS_DIR')
    if not d:
        here = os.path.dirname(os.path.abspath(__file__))
        root = os.path.abspath(os.path.join(here, os.pardir))
        d = os.path.join(root, 'models', 'ml')
    try: os.makedirs(d, exist_ok=True)
    except Exception: pass
    return d

# ─────────────────────────────────────────────────────────────────────
# Cache risultati per-file (per detector). Chiave: sha1(path)+mtime+size+detector.
# Memorizza una lista di detection: [{name, conf, bbox?}, ...] + thumb opzionale.
# ─────────────────────────────────────────────────────────────────────
_DETECT_CACHE_VER = 1

def _detect_cache_path():
    d = _ml_cache_dir()
    return os.path.join(d, 'detect_cache.sqlite')

_detect_cache_conn = None
def _detect_cache_conn_get():
    global _detect_cache_conn
    if _detect_cache_conn is not None:
        return _detect_cache_conn
    try:
        con = sqlite3.connect(_detect_cache_path())
        con.execute("""CREATE TABLE IF NOT EXISTS det_cache(
            key TEXT PRIMARY KEY,
            detector TEXT NOT NULL,
            ver INTEGER NOT NULL,
            mtime REAL NOT NULL,
            size INTEGER NOT NULL,
            payload TEXT NOT NULL,
            ts INTEGER NOT NULL
        )""")
        con.commit()
        _detect_cache_conn = con
        return con
    except Exception:
        return None

def _file_key(path, detector):
    try:
        st = os.stat(path)
        h = hashlib.sha1((os.path.abspath(path) + '|' + detector).encode('utf-8')).hexdigest()
        return h, st.st_mtime, st.st_size
    except Exception:
        return None, None, None

def detect_cache_get(path, detector):
    con = _detect_cache_conn_get()
    if not con: return None
    key, mtime, size = _file_key(path, detector)
    if not key: return None
    try:
        cur = con.execute(
            "SELECT mtime,size,ver,payload FROM det_cache WHERE key=? AND detector=?",
            (key, detector))
        row = cur.fetchone()
        if not row: return None
        c_mtime, c_size, c_ver, payload = row
        if c_ver != _DETECT_CACHE_VER: return None
        if abs(c_mtime - mtime) > 1e-3 or c_size != size: return None
        return json.loads(payload)
    except Exception:
        return None

def detect_cache_put(path, detector, payload):
    con = _detect_cache_conn_get()
    if not con: return
    key, mtime, size = _file_key(path, detector)
    if not key: return
    try:
        con.execute(
            "INSERT OR REPLACE INTO det_cache(key,detector,ver,mtime,size,payload,ts) "
            "VALUES (?,?,?,?,?,?,?)",
            (key, detector, _DETECT_CACHE_VER, mtime, size,
             json.dumps(payload, ensure_ascii=False), int(time.time())))
        con.commit()
    except Exception:
        pass

def _download(url, dest, progress_label=None):
    """Scarica un file con urllib, con fallback silenzioso. Ritorna True se OK."""
    if os.path.exists(dest) and os.path.getsize(dest) > 0:
        return True
    try:
        import urllib.request
        tmp = dest + '.part'
        if progress_label:
            _phase(f"Download {progress_label}…")
        with urllib.request.urlopen(url, timeout=60) as r, open(tmp, 'wb') as f:
            while True:
                chunk = r.read(64 * 1024)
                if not chunk: break
                f.write(chunk)
        os.replace(tmp, dest)
        return True
    except Exception as e:
        try: os.unlink(dest + '.part')
        except Exception: pass
        _emit({"type": "warn", "mode": "ml-download",
               "error": f"{progress_label or url}: {str(e)[:160]}"})
        return False

# ─────────────────────────────────────────────────────────────────────
# YOLOv8 (oggetti / animali su classi COCO)
# ─────────────────────────────────────────────────────────────────────
COCO_ANIMAL_CLASSES = {
    'bird', 'cat', 'dog', 'horse', 'sheep', 'cow',
    'elephant', 'bear', 'zebra', 'giraffe',
}
# Esclusi da "object" perché coperti da altre modalità o non d'interesse:
COCO_OBJECT_EXCLUDE = COCO_ANIMAL_CLASSES | {'person'}

_yolo_cache = {"model": None, "tried": False, "error": None}

def _load_yolo():
    if _yolo_cache["model"] is not None:
        return _yolo_cache["model"]
    if _yolo_cache["tried"]:
        return None
    _yolo_cache["tried"] = True
    try:
        from ultralytics import YOLO
        cache = _ml_cache_dir()
        weights = os.path.join(cache, 'yolov8n.pt')
        if not os.path.exists(weights):
            # Mirror ufficiale ultralytics (rilascio github)
            url = "https://github.com/ultralytics/assets/releases/download/v8.3.0/yolov8n.pt"
            if not _download(url, weights, progress_label="YOLOv8n weights (~6 MB)"):
                _yolo_cache["error"] = "weights download failed"
                return None
        _phase("Caricamento YOLOv8n…")
        _yolo_cache["model"] = YOLO(weights)
        return _yolo_cache["model"]
    except Exception as e:
        _yolo_cache["error"] = str(e)[:200]
        _emit({"type": "warn", "mode": "yolo",
               "error": f"YOLO non disponibile: {_yolo_cache['error']}"})
        return None

def _yolo_detect_file(path, model, conf_thresh, max_frames, every_sec, on_frame_progress=None):
    """Esegue YOLO su un singolo file (immagine o video). Ritorna lista di detection:
    [{name, conf, bbox:[x1,y1,x2,y2], thumb:dataurl}]
    Solo per cache: la lista è per-detection, l'aggregazione avviene dopo.
    """
    import cv2
    detections = []
    ext = os.path.splitext(path)[1].lower()
    frames = []
    if ext in IMAGE_EXT:
        img = cv2.imread(path)
        if img is not None: frames.append(img)
        if on_frame_progress: on_frame_progress(1, 1)
    else:
        cb = on_frame_progress if on_frame_progress else None
        for _, fr in iter_video_frames(path, every_sec=every_sec,
                                       max_frames=max_frames, on_frame=cb):
            frames.append(fr)

    names = getattr(model, 'names', None) or {}
    if isinstance(names, list):
        names = {i: n for i, n in enumerate(names)}

    for fr in frames:
        try:
            res = model.predict(fr, conf=conf_thresh, verbose=False, imgsz=640)
        except Exception:
            continue
        if not res: continue
        r0 = res[0]
        boxes = getattr(r0, 'boxes', None)
        if boxes is None or len(boxes) == 0: continue
        try:
            cls_arr = boxes.cls.cpu().numpy().astype(int)
            conf_arr = boxes.conf.cpu().numpy()
            xyxy_arr = boxes.xyxy.cpu().numpy().astype(int)
        except Exception:
            continue
        for cid, cf, xy in zip(cls_arr, conf_arr, xyxy_arr):
            cname = names.get(int(cid))
            if not cname: continue
            x1, y1, x2, y2 = [int(v) for v in xy]
            h, w = fr.shape[:2]
            x1 = max(0, x1); y1 = max(0, y1)
            x2 = min(w, x2); y2 = min(h, y2)
            thumb = None
            if x2 > x1 and y2 > y1:
                crop = fr[y1:y2, x1:x2]
                thumb = thumb_b64(crop, max_w=180)
            detections.append({
                "name": cname,
                "conf": float(cf),
                "bbox": [x1, y1, x2, y2],
                "thumb": thumb,
            })
    return detections

def run_yolo(files, kind, entity_db_path=None, conf_thresh=0.35, max_frames=10, every_sec=2.5):
    """Detection YOLO per kind in {'object','animal'}."""
    model = _load_yolo()
    if model is None:
        _emit({"type": "warn", "mode": kind,
               "error": "modello YOLO non caricato; fallback a keyword."})
        return run_keyword_kind(files, kind, entity_db_path=entity_db_path)

    names = getattr(model, 'names', None) or {}
    if isinstance(names, list):
        names = {i: n for i, n in enumerate(names)}

    if kind == 'animal':
        allowed = COCO_ANIMAL_CLASSES
    else:
        allowed = set(n for n in names.values() if n not in COCO_OBJECT_EXCLUDE)

    clusters = {}
    total = len(files)
    _phase(f"YOLO scan ({kind}) su {total} file…")
    detector_id = "yolov8n"

    for i, path in enumerate(files):
        base = os.path.basename(path)
        _emit({"type": "progress", "stage": f"yolo-{kind}",
               "current": i + 1, "total": total, "file": base})

        cached = detect_cache_get(path, detector_id)
        if cached is not None:
            detections = cached
        else:
            def _fp(g, t, _b=base, _i=i, _tot=total):
                _emit({"type": "progress", "stage": f"yolo-{kind}-frames",
                       "current": _i + 1, "total": _tot, "file": _b,
                       "frame": g, "frameTotal": t})
            try:
                detections = _yolo_detect_file(
                    path, model, conf_thresh, max_frames, every_sec,
                    on_frame_progress=_fp)
                detect_cache_put(path, detector_id, detections)
            except Exception as e:
                _emit({"type": "warn", "file": base, "error": f"yolo: {str(e)[:120]}"})
                detections = []

        # Aggrega le detection di questo file nei cluster, filtrando per kind
        for d in detections:
            cname = d.get("name")
            if not cname or cname not in allowed: continue
            cf = float(d.get("conf") or 0.0)
            cur = clusters.setdefault(cname, {
                'count': 0, 'files': set(),
                'best_conf': 0.0, 'thumb': None,
            })
            cur['count'] += 1
            cur['files'].add(path)
            if cf > cur['best_conf']:
                cur['best_conf'] = cf
                if d.get("thumb"): cur['thumb'] = d["thumb"]

    # Merge con entityDB (se l'utente ha già voci con stesso nome)
    db_by_name = {}
    if entity_db_path:
        for e in _load_entity_names(entity_db_path, kind):
            n = (e.get('name') or '').strip().lower()
            if n: db_by_name.setdefault(n, e)

    results = []
    for cname, c in sorted(clusters.items(), key=lambda kv: -kv[1]['count']):
        local = None
        e = db_by_name.get(cname.lower())
        if e:
            local = {
                "id": e['id'], "name": e['name'],
                "source": e.get('source'),
                "external_id": e.get('external_id'),
                "thumbnail": e.get('thumbnail'),
                "metadata": e.get('metadata'),
            }
        label_h = (e['name'] if e else cname.capitalize()) + f" ({c['count']} detection)"
        results.append({
            "id": f"yolo_{kind}_{cname}",
            "label": label_h,
            "thumb": c['thumb'] or (e.get('thumbnail') if e else None),
            "files": sorted(list(c['files'])),
            "count": c['count'],
            "type": kind,
            "detector": "yolov8n",
            "className": cname,
            "confidence": round(c['best_conf'], 3),
            "localMatch": local,
            "apiMatch": None,
            "nameCandidates": [],
        })
    return results

# ─────────────────────────────────────────────────────────────────────
# Places365 ResNet18 (riconoscimento scena)
# ─────────────────────────────────────────────────────────────────────
_places_cache = {"model": None, "categories": None, "transform": None,
                 "tried": False, "error": None}

def _load_places365():
    if _places_cache["model"] is not None:
        return _places_cache["model"], _places_cache["categories"], _places_cache["transform"]
    if _places_cache["tried"]:
        return None, None, None
    _places_cache["tried"] = True
    try:
        import torch
        from torchvision import transforms, models
        cache = _ml_cache_dir()
        w_path = os.path.join(cache, 'resnet18_places365.pth.tar')
        c_path = os.path.join(cache, 'categories_places365.txt')

        ok1 = _download(
            "http://places2.csail.mit.edu/models_places365/resnet18_places365.pth.tar",
            w_path, progress_label="Places365 ResNet18 (~46 MB)")
        ok2 = _download(
            "https://raw.githubusercontent.com/csailvision/places365/master/categories_places365.txt",
            c_path, progress_label="Places365 categories")
        if not (ok1 and ok2):
            _places_cache["error"] = "download failed"
            return None, None, None

        _phase("Caricamento Places365 ResNet18…")
        model = models.resnet18(weights=None, num_classes=365)
        sd = torch.load(w_path, map_location='cpu', weights_only=False)
        state = sd.get('state_dict', sd) if isinstance(sd, dict) else sd
        # rimuovi prefisso "module." (DataParallel)
        state = {k.replace('module.', ''): v for k, v in state.items()}
        model.load_state_dict(state, strict=False)
        model.eval()

        cats = []
        with open(c_path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line: continue
                # formato: "/a/airfield 0"  → "airfield"
                parts = line.split()
                raw = parts[0]
                name = raw.lstrip('/').split('/', 1)[-1].replace('_', ' ')
                cats.append(name)

        tf = transforms.Compose([
            transforms.Resize((256, 256)),
            transforms.CenterCrop(224),
            transforms.ToTensor(),
            transforms.Normalize(mean=[0.485, 0.456, 0.406],
                                 std=[0.229, 0.224, 0.225]),
        ])
        _places_cache.update({"model": model, "categories": cats, "transform": tf})
        return model, cats, tf
    except Exception as e:
        _places_cache["error"] = str(e)[:200]
        _emit({"type": "warn", "mode": "places365",
               "error": f"Places365 non disponibile: {_places_cache['error']}"})
        return None, None, None

def _places_detect_file(path, model, cats, tf, top_k, conf_thresh,
                        max_frames, every_sec, on_frame_progress=None):
    """Inferenza Places365 su un singolo file. Ritorna [{name, conf, thumb}]."""
    import cv2, torch
    from PIL import Image
    detections = []
    ext = os.path.splitext(path)[1].lower()
    frames = []
    if ext in IMAGE_EXT:
        img = cv2.imread(path)
        if img is not None: frames.append(img)
        if on_frame_progress: on_frame_progress(1, 1)
    else:
        cb = on_frame_progress if on_frame_progress else None
        for _, fr in iter_video_frames(path, every_sec=every_sec,
                                       max_frames=max_frames, on_frame=cb):
            frames.append(fr)

    for fr in frames:
        try:
            rgb = cv2.cvtColor(fr, cv2.COLOR_BGR2RGB)
            pil = Image.fromarray(rgb)
            x = tf(pil).unsqueeze(0)
            with torch.no_grad():
                logits = model(x)
                probs = torch.softmax(logits, dim=1)[0]
                top = torch.topk(probs, k=max(1, top_k))
                confs = top.values.tolist()
                idxs = top.indices.tolist()
        except Exception:
            continue
        thumb = thumb_b64(fr, max_w=200)
        for cf, idx in zip(confs, idxs):
            if cf < conf_thresh: continue
            name = cats[idx] if 0 <= idx < len(cats) else f"class_{idx}"
            detections.append({
                "name": name,
                "conf": float(cf),
                "thumb": thumb,
            })
    return detections

def run_places(files, entity_db_path=None, top_k=1, conf_thresh=0.20,
               max_frames=8, every_sec=3.0):
    """Riconoscimento scene Places365. Aggrega per categoria con file list e thumb."""
    model, cats, tf = _load_places365()
    if model is None:
        _emit({"type": "warn", "mode": "place",
               "error": "Places365 non caricato; fallback a keyword."})
        return run_keyword_kind(files, "place", entity_db_path=entity_db_path)

    clusters = {}
    total = len(files)
    _phase(f"Places365 scan su {total} file…")
    detector_id = f"places365-resnet18-top{top_k}-c{int(conf_thresh*100)}"

    for i, path in enumerate(files):
        base = os.path.basename(path)
        _emit({"type": "progress", "stage": "places-scan",
               "current": i + 1, "total": total, "file": base})

        cached = detect_cache_get(path, detector_id)
        if cached is not None:
            detections = cached
        else:
            def _fp(g, t, _b=base, _i=i, _tot=total):
                _emit({"type": "progress", "stage": "places-scan-frames",
                       "current": _i + 1, "total": _tot, "file": _b,
                       "frame": g, "frameTotal": t})
            try:
                detections = _places_detect_file(
                    path, model, cats, tf, top_k, conf_thresh,
                    max_frames, every_sec, on_frame_progress=_fp)
                detect_cache_put(path, detector_id, detections)
            except Exception as e:
                _emit({"type": "warn", "file": base, "error": f"places: {str(e)[:120]}"})
                detections = []

        for d in detections:
            cname = d.get("name")
            if not cname: continue
            cf = float(d.get("conf") or 0.0)
            cur = clusters.setdefault(cname, {
                'count': 0, 'files': set(),
                'best_conf': 0.0, 'thumb': None,
            })
            cur['count'] += 1
            cur['files'].add(path)
            if cf > cur['best_conf']:
                cur['best_conf'] = cf
                if d.get("thumb"): cur['thumb'] = d["thumb"]

    db_by_name = {}
    if entity_db_path:
        for e in _load_entity_names(entity_db_path, "place"):
            n = (e.get('name') or '').strip().lower()
            if n: db_by_name.setdefault(n, e)

    results = []
    for cname, c in sorted(clusters.items(), key=lambda kv: -kv[1]['count']):
        local = None
        e = db_by_name.get(cname.lower())
        if e:
            local = {
                "id": e['id'], "name": e['name'],
                "source": e.get('source'),
                "external_id": e.get('external_id'),
                "thumbnail": e.get('thumbnail'),
                "metadata": e.get('metadata'),
            }
        label_h = (e['name'] if e else cname.title()) + f" ({c['count']} frame)"
        results.append({
            "id": f"places_{cname.replace(' ', '_')}",
            "label": label_h,
            "thumb": c['thumb'] or (e.get('thumbnail') if e else None),
            "files": sorted(list(c['files'])),
            "count": c['count'],
            "type": "place",
            "detector": "places365-resnet18",
            "className": cname,
            "confidence": round(c['best_conf'], 3),
            "localMatch": local,
            "apiMatch": None,
            "nameCandidates": [],
        })
    return results

# ─────────────────────────────────────────────────────────────────────
# Modi non-face: keyword filename + match entityDB
# ─────────────────────────────────────────────────────────────────────
KIND_LABELS = {
    "object":   "oggetti",
    "place":    "luoghi",
    "animal":   "animali",
    "genre":    "generi",
    "category": "categorie",
}

def run_keyword_kind(files, kind, entity_db_path=None):
    """Modalità leggera: cerca nei filename i nomi presenti nel DB locale per quel kind.
    Quando l'utente avrà popolato il DB, i match diventano significativi."""
    label_h = KIND_LABELS.get(kind, kind)
    _phase(f"Scansione filename per {label_h}…")
    db = _load_entity_names(entity_db_path, kind)
    if not db:
        _emit({"type": "warn", "mode": kind,
               "error": f"DB locale '{kind}' vuoto: aggiungi voci dal pannello Analizza per ottenere match."})
        return []

    # match: per ogni file controlla se contiene il nome di un'entry (case-insensitive)
    by_id = {e['id']: {"entry": e, "files": set()} for e in db}
    for fp in files:
        s = clean_name_for_search(fp).lower()
        for e in db:
            n = (e['name'] or '').lower()
            if not n or len(n) < 3: continue
            if n in s:
                by_id[e['id']]['files'].add(fp)

    results = []
    for eid, agg in by_id.items():
        if not agg['files']: continue
        e = agg['entry']
        results.append({
            "id": "ent_" + str(eid),
            "label": (e['name'] or '?') + f" ({len(agg['files'])} file)",
            "thumb": e.get('thumbnail') or None,
            "files": sorted(list(agg['files'])),
            "count": len(agg['files']),
            "type": kind,
            "localMatch": {
                "id": eid, "name": e['name'],
                "source": e.get('source'),
                "external_id": e.get('external_id'),
                "thumbnail": e.get('thumbnail'),
                "metadata": e.get('metadata'),
            },
            "apiMatch": None,
            "nameCandidates": [],
        })
    return results

# ─────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--folder', default=None,
                    help='Cartella da scansionare (mutuamente esclusiva con --files).')
    ap.add_argument('--files', action='append', default=None,
                    help='File specifico da analizzare. Ripetibile. Quando presente, '
                         '--folder viene ignorato e --max-files è ignorato.')
    ap.add_argument('--modes', default='face')
    ap.add_argument('--max-files', type=int, default=200)
    ap.add_argument('--gender', default='both', choices=['male', 'female', 'both'])
    ap.add_argument('--face-db', default=None)
    ap.add_argument('--entity-db', default=None,
                    help='Stesso file di --face-db se non specificato (tabella entities).')
    ap.add_argument('--match-threshold', type=float, default=0.78)
    ap.add_argument('--tmdb-key', default=None)
    ap.add_argument('--omdb-key', default=None)
    ap.add_argument('--adultcolony', default=None)
    ap.add_argument('--stashdb-key', default=None,
                    help='API key di StashDB (https://stashdb.org). Provider adult community.')
    ap.add_argument('--use-wikidata', action='store_true',
                    help='Abilita lookup su Wikidata (no API key, mainstream + niche).')
    ap.add_argument('--use-wikipedia', action='store_true',
                    help='Abilita lookup su Wikipedia REST API (no API key).')
    ap.add_argument('--no-verify-photo', action='store_true',
                    help='Disabilita verifica seconda passata immagine-vs-immagine.')
    ap.add_argument('--confirm-threshold', type=float, default=0.80)
    ap.add_argument('--candidate-threshold', type=float, default=0.60)
    ap.add_argument('--acoustid-key', default=None)
    ap.add_argument('--music-write-mode', default='tag', choices=['tag', 'metadata', 'both'])
    ap.add_argument('--music-no-shazam', action='store_true')
    ap.add_argument('--music-no-backup', action='store_true')
    args = ap.parse_args()

    entity_db = args.entity_db or args.face_db
    modes = [m.strip() for m in args.modes.split(',') if m.strip()]
    include_audio = 'music' in modes
    if args.files:
        # Single-file (o multi-file) scope: nessuna scansione folder.
        files = [f for f in args.files if os.path.isfile(f)]
        if not files:
            _emit({"type": "error", "error": "nessun file valido in --files"})
            return 2
    else:
        if not args.folder or not os.path.isdir(args.folder):
            _emit({"type": "error", "error": "cartella non valida: " + str(args.folder)})
            return 2
        files = list_files(args.folder, args.max_files, include_audio=include_audio)
    _emit({"type": "progress", "stage": "init",
           "total": len(files), "modes": modes})
    _phase(f"{len(files)} file trovati. Avvio modalità: {', '.join(modes)}.")

    all_results = []

    if 'face' in modes:
        try:
            r = run_face(files,
                         face_db_path=args.face_db,
                         match_threshold=args.match_threshold,
                         gender=args.gender,
                         tmdb_key=args.tmdb_key,
                         omdb_key=args.omdb_key,
                         adultcolony_base=args.adultcolony,
                         stashdb_key=args.stashdb_key,
                         use_wikidata=args.use_wikidata,
                         use_wikipedia=args.use_wikipedia,
                         verify_photo=not args.no_verify_photo,
                         confirm_threshold=args.confirm_threshold,
                         candidate_threshold=args.candidate_threshold)
            all_results.extend(r)
        except Exception as e:
            _emit({"type": "error", "error": "face: " + str(e)[:200],
                   "trace": traceback.format_exc()[:600]})

    for m in modes:
        if m == 'face': continue
        if m == 'music':
            try:
                import musicid
                audio_files = [f for f in files if os.path.splitext(f)[1].lower() in AUDIO_EXT]
                if not audio_files:
                    _emit({"type": "warn", "mode": "music",
                           "error": "nessun file audio trovato in cartella"})
                    continue
                r = musicid.run_music(audio_files, {
                    'acoustid_key': args.acoustid_key,
                    'use_acoustid': bool(args.acoustid_key),
                    'use_musicbrainz': True,
                    'use_coverart': True,
                    'use_shazam': not args.music_no_shazam and False,
                    'write_mode': args.music_write_mode,
                    'write_backup': not args.music_no_backup,
                })
                all_results.extend(r)
            except Exception as e:
                _emit({"type": "error", "error": "music: " + str(e)[:200],
                       "trace": traceback.format_exc()[:600]})
            continue
        if m not in KIND_LABELS:
            _emit({"type": "warn", "mode": m, "error": "kind sconosciuto"})
            continue
        try:
            if m in ('object', 'animal'):
                r = run_yolo(files, m, entity_db_path=entity_db)
            elif m == 'place':
                r = run_places(files, entity_db_path=entity_db)
            else:
                r = run_keyword_kind(files, m, entity_db_path=entity_db)
            all_results.extend(r)
        except Exception as e:
            _emit({"type": "warn", "mode": m, "error": str(e)[:160],
                   "trace": traceback.format_exc()[:600]})

    _phase(f"Completato: {len(all_results)} risultati.")
    _emit({"type": "done", "results": all_results})
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception as e:
        _emit({"type": "error", "error": str(e)[:200],
               "trace": traceback.format_exc()[:600]})
        sys.exit(1)
