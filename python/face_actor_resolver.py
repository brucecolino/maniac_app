#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Face Actor Resolver: risolve cluster di volti contro StashDB tramite
verifica foto-vs-foto. NON usa più nomi da TMDB/AdultColony/OMDB/Wikidata.

Flusso (per cluster passato in input):
  1. Auto-merge cross-cluster (cosine ≥ merge_threshold)
  2. Early-hit: se il centroide matcha già una riga in faces.db con
     source='stashdb' AND external_id presente → riusa il nome senza chiamate.
  3. Tokenizza i basename dei file del cluster → top-K candidati nome
     (1-/2-/3-gram puliti da noise codec/site/explicit).
  4. Per ogni candidato → stashdb.search_performer → per ogni performer
     scarica fino a max_photos_per_performer immagini → calcola
     cosine(cluster_centroid, photo_embedding) tramite analyze.verify_match_photo.
  5. Ritiene il match con score più alto se ≥ confirm_threshold; altrimenti null.

CLI:
  python face_actor_resolver.py resolve \\
    --clusters <path>/clusters.json \\
    --facedb   <userData>/faces.db \\
    [--merge-threshold 0.62] [--confirm-threshold 0.62] \\
    [--topk 5] [--max-photos-per-performer 4] [--max-performers-per-query 5]

Output: JSONL su stdout
  {"event":"start","clusters":N}
  {"event":"progress","stage":"merge|cache|query|verify","cluster":i,"total":N,"detail":"..."}
  {"event":"cluster","id":i,"match":{"performerId","name","score","source","thumbUrl"}|null,
   "candidates":[{"name","score"}],"files":[...],"count":n,"thumb":"...",
   "avgEmbedding":[...]}
  {"event":"result","clusters":[...],"matched":k,"unmatched":u}
"""
import os, sys, json, argparse, time, math, re
import struct

# Reuso primitive esistenti (no duplicazione)
HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import facedb  # connect, unpack_embedding, cosine
import stashdb  # _gql, Q_SEARCH_PERFORMER, _normalize_performer
import analyze  # verify_match_photo


# ────────────────────────────────────────────────────────────────────
# Noise wordlist per la tokenizzazione filename → nomi candidati.
# Se compare uno di questi token, viene rimosso prima dell'estrazione n-gram.
# ────────────────────────────────────────────────────────────────────
EXPLICIT_NOISE = {
    # codec / risoluzione / formato
    '1080p','720p','480p','2160p','4k','x264','x265','h264','h265','hevc','av1',
    'aac','ac3','mp3','opus','dts','bluray','webrip','web','dl','hdrip','dvdrip',
    'brrip','hdtv','remux','hdr','sdr','uhd','vr','cam','ts',
    # site / studio / brand
    'onlyfans','pornhub','xvideos','xhamster','brazzers','bangbros','realitykings',
    'mofos','naughtyamerica','teamskeet','blacked','tushy','deeper','evilangel',
    'kink','digitalplayground','mylf','milfed','bbcpie','familystrokes','bratty','sis',
    # esplicito / atti
    'xxx','anal','oral','dp','dap','tap','tp','bbc','ir','teen','petite','tiny','small',
    'big','tits','boobs','ass','milf','gilf','bbw','ssbbw','pov','solo','threesome',
    'foursome','gangbang','bukkake','creampie','facial','cumshot','cuckold','hotwife',
    'squirt','squirting','lesbian','gay','bisex','trans','tranny','shemale','mature',
    'fisting','bdsm','bondage','hardcore','softcore','amateur','cosplay','casting',
    'audition','black','white','asian','latina','italian','italiana','italiano',
    'french','german','russian','japanese','korean','czech','ebony','blonde','brunette',
    'redhead','hairy','shaved','natural','fake','pussy','cock','dick','double','triple',
    # generico
    'scene','part','vol','volume','episode','eps','full','hd','premium','exclusive',
    'trailer','compilation','pack','collection','watch','download','changing','room',
    'video','movie','official','final','release','rip','encode','encoded',
    # nomi cartelle comuni (introdotti dal tokenizer di path)
    'videos','movies','films','downloads','download','torrents','torrent','tmp','temp',
    'desktop','documents','library','media','archive','archives','old','new','backup',
    'misc','various','other','stuff','content','contents','folder','dir','data',
    'porn','xxx','adult','hentai','content','samples','sample','complete','done',
    'users','user','public','desktop','onedrive','dropbox','gdrive','icloud',
}

# Pattern da rimuovere su filename (regex)
_FN_NOISE_PATTERNS = [
    re.compile(r'\bs\d{1,2}e\d{1,2}\b', re.I),       # S01E04
    re.compile(r'\b(19|20)\d{2}\b'),                  # anni
    re.compile(r'\bhttps?://\S+', re.I),              # URL
    re.compile(r'\b\d{2,4}p\b', re.I),                # 1080p ecc.
]


def _emit(obj):
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _cosine(a, b):
    return facedb.cosine(a, b)


# ────────────────────────────────────────────────────────────────────
# Stage A: load clusters da JSON di input
# ────────────────────────────────────────────────────────────────────
def load_clusters(path):
    with open(path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    raw = data.get('clusters') if isinstance(data, dict) else data
    out = []
    for c in (raw or []):
        emb = c.get('avgEmbedding') or c.get('embedding')
        if not emb: continue
        out.append({
            'id': c.get('id') or len(out) + 1,
            'embedding': [float(x) for x in emb],
            'count': int(c.get('count') or 0) or 1,
            'files': list(c.get('files') or []),
            'thumb': c.get('thumb') or '',
        })
    return out


# ────────────────────────────────────────────────────────────────────
# Stage B: agglomerative-merge cross-cluster (cosine ≥ threshold)
# ────────────────────────────────────────────────────────────────────
def merge_clusters(clusters, threshold):
    if len(clusters) < 2:
        return clusters
    n = len(clusters)
    parent = list(range(n))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb: parent[rb] = ra

    for i in range(n):
        for j in range(i + 1, n):
            if _cosine(clusters[i]['embedding'], clusters[j]['embedding']) >= threshold:
                union(i, j)

    groups = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(i)

    merged = []
    for grp in groups.values():
        if len(grp) == 1:
            merged.append(clusters[grp[0]])
            continue
        # Centroide pesato sui count, files e thumb dal più popoloso
        total = sum(clusters[i]['count'] for i in grp)
        dim = len(clusters[grp[0]]['embedding'])
        centroid = [0.0] * dim
        for i in grp:
            w = clusters[i]['count'] / total
            for k in range(dim):
                centroid[k] += clusters[i]['embedding'][k] * w
        files = []
        for i in grp:
            for fp in clusters[i]['files']:
                if fp not in files: files.append(fp)
        biggest = max(grp, key=lambda i: clusters[i]['count'])
        merged.append({
            'id': clusters[biggest]['id'],
            'embedding': centroid,
            'count': total,
            'files': files,
            'thumb': clusters[biggest]['thumb'],
            '_mergedFrom': [clusters[i]['id'] for i in grp if i != biggest],
        })
    merged.sort(key=lambda c: -c['count'])
    return merged


# ────────────────────────────────────────────────────────────────────
# Stage C: early-hit faces.db (riusa nomi da run precedenti)
# ────────────────────────────────────────────────────────────────────
def load_facedb_rows(facedb_path):
    """Carica righe volti dal DB locale. Legge BOTH tabelle (legacy `faces` +
    moderna `entities` kind='face') e dedupa per (external_id|name) dando
    priorità a `entities` (più recenti, possono includere refs foto stabili)."""
    if not facedb_path or not os.path.exists(facedb_path):
        return []
    try:
        con = facedb.connect(facedb_path)
    except Exception:
        return []
    by_key = {}  # key=(external_id or name.lower()) → row con score di priorità
    try:
        # Modern entities table FIRST (priorità alta: ha refs foto, dati recenti)
        try:
            for r in con.execute(
                "SELECT id, name, embedding, thumbnail, source, external_id "
                "FROM entities WHERE kind='face' AND embedding IS NOT NULL"
            ):
                key = str(r[5]) if r[5] else (r[1] or '').strip().lower()
                if not key: continue
                # Più refs per stesso performer (es. centroide video + foto): tieni tutte
                # ma evita duplicati esatti (stessa source+external_id già in lista).
                row = {
                    'id': r[0], 'name': r[1],
                    'embedding': facedb.unpack_embedding(r[2]),
                    'thumbnail': r[3], 'source': r[4], 'external_id': r[5],
                }
                by_key.setdefault(key, []).append(row)
        except Exception:
            pass
        # Legacy faces table (fallback: includi solo se non già coperto da entities)
        try:
            for r in con.execute("SELECT id, name, embedding, thumbnail, source, external_id FROM faces"):
                if r[2] is None: continue
                key = str(r[5]) if r[5] else (r[1] or '').strip().lower()
                if not key: continue
                if key in by_key:
                    continue  # entities ha già questo performer
                row = {
                    'id': 'legacy_' + str(r[0]), 'name': r[1],
                    'embedding': facedb.unpack_embedding(r[2]),
                    'thumbnail': r[3], 'source': r[4], 'external_id': r[5],
                }
                by_key.setdefault(key, []).append(row)
        except Exception:
            pass
    finally:
        con.close()
    rows = []
    for v in by_key.values():
        rows.extend(v)
    return rows


try:
    import numpy as _np
    _HAVE_NP_RES = True
except Exception:
    _HAVE_NP_RES = False

ACTOR_SAFE_MARGIN = 0.05   # stacco minimo dal 2° performer per un match "sicuro"
_DB_MATRIX_CACHE = {}

def _db_matrix(db_rows):
    """Matrice L2-normalizzata (cache) delle facce DB per match vettoriale."""
    key = id(db_rows)
    cached = _DB_MATRIX_CACHE.get(key)
    if cached is not None and cached[0] is db_rows:
        return cached[1]
    idxs = [i for i, r in enumerate(db_rows) if r.get('name') and r.get('embedding')]
    res = None
    if idxs:
        vecs = [_np.asarray(db_rows[i]['embedding'], dtype=_np.float32) for i in idxs]
        dim = max(v.shape[0] for v in vecs)
        M = _np.zeros((len(vecs), dim), dtype=_np.float32); ok = []
        for k, v in enumerate(vecs):
            if v.shape[0] == dim:
                M[len(ok)] = v; ok.append(idxs[k])
        M = M[:len(ok)]
        norms = _np.linalg.norm(M, axis=1, keepdims=True); norms[norms == 0] = 1.0
        res = (M / norms, ok, dim)
    _DB_MATRIX_CACHE.clear(); _DB_MATRIX_CACHE[key] = (db_rows, res)
    return res

def early_facedb_candidates(centroid, db_rows, threshold, top_k=5):
    """Restituisce i top-K performer unici nel DB locale ordinati per similarità.
    Dedup per (external_id|name). Match vettoriale numpy (fallback puro-Python)."""
    by_perf = {}  # key=(external_id|name) → (best_score, row)
    packed = _db_matrix(db_rows) if _HAVE_NP_RES else None
    if packed is not None:
        M, ok, dim = packed
        q = _np.asarray(centroid, dtype=_np.float32)
        if q.shape[0] == dim:
            sims = M @ (q / (_np.linalg.norm(q) or 1.0))
            for k, ri in enumerate(ok):
                s = float(sims[k])
                if s < threshold: continue
                r = db_rows[ri]
                key = str(r.get('external_id')) if r.get('external_id') else (r['name'] or '').strip().lower()
                if not key: continue
                prev = by_perf.get(key)
                if prev is None or s > prev[0]:
                    by_perf[key] = (s, r)
        else:
            packed = None
    if packed is None:
        for r in db_rows:
            if not r.get('name') or not r.get('embedding'):
                continue
            s = _cosine(centroid, r['embedding'])
            if s < threshold: continue
            key = str(r.get('external_id')) if r.get('external_id') else (r['name'] or '').strip().lower()
            if not key: continue
            prev = by_perf.get(key)
            if prev is None or s > prev[0]:
                by_perf[key] = (s, r)
    ranked = sorted(by_perf.values(), key=lambda x: -x[0])
    out = []
    for s, r in ranked[:top_k]:
        out.append({
            'performerId': r.get('external_id'),
            'name': r['name'],
            'score': float(s),
            'source': r.get('source') or 'facedb-cache',
            'thumbUrl': r.get('thumbnail'),
        })
    return out


def early_facedb_hit(centroid, db_rows, threshold):
    """Compat: restituisce il top match (None se nessuno supera threshold)."""
    cands = early_facedb_candidates(centroid, db_rows, threshold, top_k=1)
    return cands[0] if cands else None


def _save_photo_ref_to_db(facedb_path, name, photo_emb, external_id, photo_url):
    """Salva embedding di una foto StashDB confermata come riferimento stabile
    nel DB locale (entities table, source='stashdb-photo'). Le early-hit
    successive matchano contro questa foto invariante (non solo contro centroidi
    di video, che variano per condizione di ripresa) → riconoscimento più affidabile.

    Idempotente: se ref foto già presente per lo stesso performer, no-op."""
    if not facedb_path or not photo_emb or not external_id or not name:
        return
    try:
        import sqlite3 as _sql
        con = facedb.connect(facedb_path)
    except Exception:
        return
    try:
        try:
            existing = con.execute(
                "SELECT id FROM entities WHERE kind='face' AND source='stashdb-photo' "
                "AND external_id=?",
                (str(external_id),)
            ).fetchone()
        except Exception:
            existing = None
        if not existing:
            blob = struct.pack('<%sf' % len(photo_emb), *[float(x) for x in photo_emb])
            now = int(time.time())
            try:
                con.execute(
                    "INSERT INTO entities(kind, name, embedding, thumbnail, source, "
                    "external_id, created_at, updated_at) "
                    "VALUES('face',?,?,?,'stashdb-photo',?,?,?)",
                    (name, blob, photo_url, str(external_id), now, now)
                )
                con.commit()
            except Exception:
                pass
    finally:
        try: con.close()
        except Exception: pass


# ────────────────────────────────────────────────────────────────────
# Stage D: top-K candidati nome dai filename
# ────────────────────────────────────────────────────────────────────
def _strip_filename(path):
    # Include up to 2 parent dir components so paths like /performers/Summer Vixen/video.mp4
    # contribute "summer vixen" as a candidate even if the filename has no name.
    norm = (path or '').replace('\\', '/')
    parts = [p for p in norm.split('/') if p]
    # Take last 3 parts: [parent2, parent1, filename]
    chunk = parts[-3:] if len(parts) >= 3 else parts
    text_parts = []
    for i, p in enumerate(chunk):
        text_parts.append(os.path.splitext(p)[0] if i == len(chunk) - 1 else p)
    s = ' '.join(text_parts).lower()
    for rx in _FN_NOISE_PATTERNS:
        s = rx.sub(' ', s)
    s = re.sub(r"[._\-\(\)\[\]\{\}#@!\?,;:+=]+", ' ', s)
    s = re.sub(r'\s+', ' ', s).strip()
    return s


def _is_noise_token(tok):
    if not tok or len(tok) < 2: return True
    if tok.isdigit(): return True
    if tok in EXPLICIT_NOISE: return True
    return False


def tokenize_filenames(files, topk=5):
    # Frequenza n-gram (1-3 parole) su file_count distinti
    ngram_files = {}
    for fp in (files or []):
        s = _strip_filename(fp)
        if not s: continue
        toks = [t for t in s.split(' ') if not _is_noise_token(t)]
        seen_in_this = set()
        for size in (1, 2, 3):
            if len(toks) < size: continue
            for i in range(len(toks) - size + 1):
                gram = ' '.join(toks[i:i + size])
                if gram in seen_in_this: continue
                seen_in_this.add(gram)
                ngram_files.setdefault(gram, set()).add(fp)
    if not ngram_files: return []

    # Score: file-frequency × bonus lunghezza (preferisci 2-3 parole, plausibili nomi-cognome)
    def score(g, fs):
        words = g.count(' ') + 1
        length_bonus = {1: 0.6, 2: 1.4, 3: 1.0}[words]
        return len(fs) * length_bonus

    ranked = sorted(
        ngram_files.items(),
        key=lambda kv: (-score(kv[0], kv[1]), -len(kv[0]))
    )
    out = []
    for g, fs in ranked[:topk * 4]:  # filtra ulteriormente per evitare grossi sovrapposti
        # capitalizza: "anna ralph" → "Anna Ralph"
        pretty = ' '.join(w.capitalize() for w in g.split(' '))
        out.append({'name': pretty, 'score': float(score(g, fs)), 'fileCount': len(fs)})
    # Dedup: se "anna ralph" presente, scarta "anna" e "ralph" sub-string
    final = []
    taken_words = set()
    for c in out:
        words = c['name'].lower().split(' ')
        if len(words) == 1 and any(words[0] in tw for tw in taken_words):
            continue
        final.append(c)
        taken_words.add(' '.join(words))
        if len(final) >= topk:
            break
    return final


# ────────────────────────────────────────────────────────────────────
# Stage E: lookup StashDB + verifica foto-vs-foto
# ────────────────────────────────────────────────────────────────────
def lookup_performer(name, gql_cache, max_performers_per_query):
    key = (name or '').lower().strip()
    if not key: return []
    if key in gql_cache:
        return gql_cache[key]
    data, err = stashdb._gql(stashdb.Q_SEARCH_PERFORMER,
                              {"term": name, "limit": int(max_performers_per_query)})
    items = []
    if data:
        for p in (data.get("searchPerformer") or []):
            if not p: continue
            np_ = stashdb._normalize_performer(p)
            # immagini come lista di URL (non solo prima foto)
            imgs = [i.get('url') for i in (p.get('images') or []) if i and i.get('url')]
            np_['images'] = imgs
            items.append(np_)
    gql_cache[key] = items
    time.sleep(0.25)  # rate-limit gentile
    return items


def verify_cluster_against_performer(cluster_emb, performer, photo_emb_cache,
                                     confirm_threshold, max_photos_per_performer):
    best = -1.0
    best_url = None
    imgs = (performer.get('images') or [])[:int(max_photos_per_performer)]
    for url in imgs:
        sim = analyze.verify_match_photo(url, cluster_emb,
                                          photo_emb_cache=photo_emb_cache)
        if sim is None:
            continue
        if sim > best:
            best = sim; best_url = url
        # Early-exit se ben sopra soglia
        if sim >= max(confirm_threshold + 0.10, 0.85):
            break
    if best < 0:
        return None
    return {'score': float(best), 'thumbUrl': best_url}


def resolve_one(cluster, candidates, gql_cache, photo_emb_cache,
                confirm_threshold, max_performers_per_query,
                max_photos_per_performer, on_progress=None):
    """Itera candidati → performer → foto. Ritorna il match migliore o None.
    Dedupa performer per id (uno stesso performer può tornare da candidati diversi)."""
    seen_ids = set()
    best = None; best_score = -1.0; second_score = -1.0
    cands_scored = []
    for ci, cand in enumerate(candidates or []):
        name = cand.get('name') if isinstance(cand, dict) else cand
        if not name: continue
        if on_progress:
            on_progress({'stage': 'query', 'detail': name, 'candidate_idx': ci})
        performers = lookup_performer(name, gql_cache, max_performers_per_query)
        cand_best = -1.0
        for p in performers:
            pid = p.get('id')
            if pid in seen_ids: continue
            seen_ids.add(pid)
            if on_progress:
                on_progress({'stage': 'verify', 'detail': p.get('name'),
                             'candidate_idx': ci})
            res = verify_cluster_against_performer(
                cluster['embedding'], p, photo_emb_cache,
                confirm_threshold, max_photos_per_performer)
            if res is None: continue
            score = res['score']
            if score > cand_best: cand_best = score
            if score > best_score:
                second_score = best_score        # il vecchio migliore diventa secondo
                best_score = score
                best = {
                    'performerId': pid,
                    'name': p.get('name'),
                    'score': score,
                    'source': 'stashdb',
                    'thumbUrl': res['thumbUrl'] or p.get('image'),
                    'aliases': p.get('aliases') or [],
                    'gender': p.get('gender'),
                }
            elif score > second_score:
                second_score = score
        cands_scored.append({'name': name, 'score': float(cand_best) if cand_best >= 0 else None})

    if best and best_score >= confirm_threshold:
        # Match SICURO solo se stacca il 2° performer di ACTOR_SAFE_MARGIN: evita
        # conferme ambigue fra due performer somiglianti.
        gap = (best_score - second_score) if second_score >= 0 else best_score
        best['margin'] = round(gap, 4)
        best['safe'] = bool(gap >= ACTOR_SAFE_MARGIN)
        best['confidence'] = 'confirmed' if best['safe'] else 'ambiguous'
        return best, cands_scored
    return None, cands_scored


# ────────────────────────────────────────────────────────────────────
# CLI
# ────────────────────────────────────────────────────────────────────
def cmd_resolve(args):
    clusters = load_clusters(args.clusters)
    n_in = len(clusters)
    if n_in == 0:
        _emit({'event': 'result', 'clusters': [], 'matched': 0, 'unmatched': 0})
        return 0

    _emit({'event': 'start', 'clusters': n_in})

    # Stage B: merge cross-cluster
    _emit({'event': 'progress', 'stage': 'merge', 'cluster': 0, 'total': n_in})
    clusters = merge_clusters(clusters, float(args.merge_threshold))
    n = len(clusters)

    # Stage C: precarica righe facedb per early-hit
    db_rows = load_facedb_rows(args.facedb) if args.facedb else []

    # API key StashDB obbligatoria per query (l'early-hit funziona comunque
    # senza key, perché legge solo cache locale).
    has_key = bool(stashdb._load_key())

    out = []
    matched = 0
    gql_cache = {}
    photo_emb_cache = {}

    confirm_thr = float(args.confirm_threshold)
    early_thr = float(args.early_hit_threshold)

    for idx, cl in enumerate(clusters):
        cluster_obj = {
            'event': 'cluster',
            'id': cl['id'],
            'thumb': cl.get('thumb') or '',
            'files': cl['files'],
            'count': cl['count'],
            'avgEmbedding': cl['embedding'],
            'mergedFrom': cl.get('_mergedFrom') or [],
            'match': None,
            'candidates': [],
        }

        # Stage C: early-hit con verifica foto per borderline.
        # Top-K candidati dal DB locale: i match forti (≥ confirm_thr) auto-confermano,
        # i borderline (< confirm_thr) richiedono verifica foto-vs-foto contro StashDB
        # → evita falsi positivi a soglia 0.35 con DB grandi.
        early_cands = early_facedb_candidates(cl['embedding'], db_rows, early_thr, top_k=5)
        early = None
        # Soglia foto-verifica più permissiva del confirm globale: l'evidenza locale
        # del DB è già un primo segnale, basta confermare con cosine foto >= 0.45.
        photo_verify_thr = max(early_thr, confirm_thr - 0.10)
        for cand in early_cands:
            # Match forte: auto-confirm senza verifica
            if cand['score'] >= confirm_thr:
                early = cand
                break
            # Borderline: verifica via foto StashDB se abbiamo ext_id + key.
            # NB: il thumbnail locale può essere base64 (face crop): ignoralo,
            # usa SEMPRE l'API StashDB per ottenere le foto vere.
            ext_id = cand.get('performerId')
            if ext_id and has_key:
                _emit({'event': 'progress', 'stage': 'verify',
                       'cluster': idx + 1, 'total': n,
                       'detail': cand.get('name')})
                p_data, _err = stashdb._gql(stashdb.Q_PERFORMER_INFO, {'id': ext_id})
                photos = []
                if p_data and p_data.get('findPerformer'):
                    photos = [i.get('url') for i in
                              ((p_data['findPerformer'].get('images')) or [])
                              if i and i.get('url')]
                # Fallback: se thumbUrl è già una URL http (raro), usalo
                thumb = cand.get('thumbUrl')
                if not photos and isinstance(thumb, str) and thumb.startswith('http'):
                    photos = [thumb]
                best_sim = -1.0
                best_url = None
                for url in photos[:int(args.max_photos_per_performer)]:
                    sim = analyze.verify_match_photo(url, cl['embedding'],
                                                     photo_emb_cache=photo_emb_cache)
                    if sim is None: continue
                    if sim > best_sim:
                        best_sim, best_url = sim, url
                    if sim >= max(confirm_thr + 0.10, 0.85):
                        break
                if best_sim >= photo_verify_thr:
                    early = dict(cand)
                    early['score'] = float(best_sim)
                    early['thumbUrl'] = best_url or thumb
                    early['source'] = (cand.get('source') or 'facedb') + '+photo'
                    # Salva embedding foto come ref stabile (auto-bootstrap)
                    photo_emb = photo_emb_cache.get(best_url) if best_url else None
                    if photo_emb and args.facedb:
                        _save_photo_ref_to_db(args.facedb, early['name'], photo_emb,
                                              ext_id, best_url)
                    break
                # Verifica fallita: prova prossimo candidato
        if early:
            _emit({'event': 'progress', 'stage': 'cache',
                   'cluster': idx + 1, 'total': n, 'detail': early.get('name')})
            cluster_obj['match'] = early
            out.append(cluster_obj)
            _emit(cluster_obj)
            matched += 1
            continue

        # Stage D: candidati
        cands = tokenize_filenames(cl['files'], topk=int(args.topk))

        if not has_key or not cands:
            cluster_obj['candidates'] = cands
            _emit({'event': 'progress',
                   'stage': 'skipped' if not has_key else 'no-candidates',
                   'cluster': idx + 1, 'total': n,
                   'detail': 'no_api_key' if not has_key else 'no_candidates'})
            out.append(cluster_obj)
            _emit(cluster_obj)
            continue

        # Stage E: lookup + verify
        def on_progress(ev):
            ev = dict(ev)
            ev.update({'event': 'progress', 'cluster': idx + 1, 'total': n})
            _emit(ev)

        match, cands_scored = resolve_one(
            cl, cands, gql_cache, photo_emb_cache,
            confirm_thr,
            int(args.max_performers_per_query),
            int(args.max_photos_per_performer),
            on_progress=on_progress,
        )
        # Salva embedding foto del match come ref stabile per future early-hit
        if match and match.get('performerId') and match.get('thumbUrl') and args.facedb:
            photo_emb = photo_emb_cache.get(match['thumbUrl'])
            if photo_emb:
                _save_photo_ref_to_db(args.facedb, match['name'], photo_emb,
                                      match['performerId'], match['thumbUrl'])
        cluster_obj['match'] = match
        cluster_obj['candidates'] = cands_scored
        if match: matched += 1
        out.append(cluster_obj)
        _emit(cluster_obj)

    # Output finale (lista clusters senza il flag event)
    final_clusters = []
    for c in out:
        c2 = dict(c)
        c2.pop('event', None)
        final_clusters.append(c2)
    _emit({'event': 'result',
           'clusters': final_clusters,
           'matched': matched,
           'unmatched': n - matched,
           'total': n,
           'inputCount': n_in,
           'hasKey': has_key})
    return 0


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest='cmd')
    p = sub.add_parser('resolve')
    p.add_argument('--clusters', required=True,
                   help='Path a JSON con array clusters [{id,avgEmbedding,files,thumb,count}]')
    p.add_argument('--facedb', required=False, default=None)
    p.add_argument('--merge-threshold', default='0.62')
    p.add_argument('--confirm-threshold', default='0.55')
    p.add_argument('--early-hit-threshold', default='0.35')
    p.add_argument('--topk', default='5')
    p.add_argument('--max-photos-per-performer', default='4')
    p.add_argument('--max-performers-per-query', default='5')
    args = ap.parse_args()
    if args.cmd == 'resolve':
        return cmd_resolve(args)
    ap.print_help()
    return 2


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception as e:
        _emit({'event': 'error', 'error': str(e)[:300]})
        sys.exit(1)
