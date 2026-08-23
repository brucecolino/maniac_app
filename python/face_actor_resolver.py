#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Face Actor Resolver: risolve cluster di volti contro StashDB tramite
verifica foto-vs-foto. NON usa più nomi da TMDB/AdultColony/OMDB/Wikidata.

Flusso (per cluster passato in input):
  1. Auto-merge cross-cluster (cosine ≥ merge_threshold)
  2. Early-hit: le righe simili in faces.db PROPONGONO candidati, non nomi.
     Anche un hit fortissimo deve superare la stessa verifica foto del punto 5
     (una riga di cache è un volto in una sola condizione: su DB grandi trova
     sempre qualcuno che somiglia). Unica eccezione: le voci senza external_id,
     cioè i volti nominati a mano dall'utente.
  3. Costruisce i termini di ricerca dai file del cluster (build_search_terms):
       a) full-text del filename ripulito — strategia PRIMARIA: il ranking di
          StashDB isola il performer meglio di qualsiasi euristica locale;
       b) n-gram 1-3 parole filtrati per plausibilità-nome — copre i file con
          più performer, dove il full-text restituisce solo il primo.
  4. Per ogni termine → stashdb.search_performer → per ogni performer
     confronta il volto con max_photos_per_performer foto SPARSE sulla galleria
     (scene, epoche e luci diverse) via analyze.verify_match_photo.
  5. Verdetto consensuale — tre condizioni, tutte necessarie:
       · score  = media delle 3 similarità migliori ≥ confirm_threshold
       · quorum = almeno `photo_votes` foto distinte sopra soglia da sole
       · margine dal 2° performer ≥ ACTOR_SAFE_MARGIN
     Il massimo su N foto NON basta: più foto guardi, più è probabile che una
     somigli per caso. Altrimenti → null, nessun nome esposto.
  6. L'embedding della foto confermata viene salvato in faces.db
     (entities, source='stashdb-photo') → le run successive lo riusano in early-hit.

Il nome nel filename non è mai una prova: serve solo a proporre chi cercare.
Se il volto non regge il confronto fotografico, il nome viene scartato.

CLI:
  python face_actor_resolver.py resolve \\
    --clusters <path>/clusters.json \\
    --facedb   <userData>/faces.db \\
    [--merge-threshold 0.62] [--confirm-threshold 0.55] [--photo-votes 2] \\
    [--topk 5] [--max-photos-per-performer 10] [--max-performers-per-query 5]

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
    # Dominio del sito (EPORNER.COM, www.brazzers.net…). Il lookbehind su `.`
    # è essenziale: senza, in "Riley.Reid.XXX.1080p" il pattern mangerebbe
    # "Reid.XXX" cancellando il cognome.
    re.compile(r'(?<![\w.])(?:www\.)?[a-z0-9][a-z0-9-]{1,30}'
               r'\.(?:com|net|org|tv|xxx|cc|io)(?!\w)', re.I),
]

# Estensioni video/contenitore: rimosse ovunque nel nome, non solo in coda
# (i download producono spesso doppie estensioni: "clip.mp4 (720).mp4").
_EXT_RX = re.compile(
    r'\.(?:mp4|mkv|avi|mov|wmv|flv|m4v|ts|m2ts|webm|mpg|mpeg|vob|rmvb|divx|asf|ogv)\b',
    re.I)

_VOWELS = set('aeiouyàèéìòù')


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
EARLY_STOP_SCORE  = 0.70   # oltre questo score consensuale si smette di cercare
SCREEN_BATCH      = 4      # foto del primo giro di screening
SCREEN_MIN_MAX    = 0.40   # se dopo lo screening il max è sotto → performer scartato
MAX_OPTIONS       = 3      # alternative proposte all'utente a fine processo
LOCAL_ONLY_MATCH  = 0.80   # soglia per fidarsi della sola cache locale
GENERIC_RATIO     = 0.40   # oltre questa quota di volti attratti, la voce non discrimina
FEW_PHOTOS_MATCH  = 0.70   # score richiesto quando le foto sono meno di 3
FEW_PHOTOS_LIMIT  = 3      # sotto questo numero il quorum non e' applicabile
OPTION_MARGIN     = 0.10   # quanto sotto confirm_threshold può stare un'opzione
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

def generic_entries(db_rows, threshold=0.35):
    """Nomi la cui impronta somiglia a troppi volti diversi.

    Si confronta ogni voce con tutte le altre di nome diverso: se ne attrae
    più di GENERIC_RATIO, quella voce non sta descrivendo una persona in
    particolare — di solito è nata da un ritaglio sfocato o parziale — e
    proporla significa dare sempre la stessa risposta sbagliata.

    Il calcolo è O(n²) ma il database locale conta decine di voci, non
    migliaia, e il risultato viene riusato per tutti i cluster della sessione.
    """
    rows = [r for r in (db_rows or []) if r.get('name') and r.get('embedding')]
    names = {r['name'] for r in rows}
    if len(names) < 4:
        return set()   # troppo pochi nomi perché la statistica dica qualcosa

    attracts = {}
    for i, a in enumerate(rows):
        for b in rows[i + 1:]:
            if a['name'] == b['name']:
                continue
            if len(a['embedding']) != len(b['embedding']):
                continue
            if _cosine(a['embedding'], b['embedding']) >= threshold:
                attracts[a['name']] = attracts.get(a['name'], 0) + 1
                attracts[b['name']] = attracts.get(b['name'], 0) + 1

    others = len(names) - 1
    return {n for n, hits in attracts.items() if hits / max(1, others) > GENERIC_RATIO}


def early_facedb_candidates(centroid, db_rows, threshold, top_k=5, skip_names=None):
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
                if skip_names and r.get('name') in skip_names: continue
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
            if skip_names and r.get('name') in skip_names: continue
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
def _clean_text(s):
    """Normalizza un frammento di path: lowercase, via estensioni, pattern di
    noise (siti, risoluzioni, anni, URL) e punteggiatura → parole separate."""
    s = (s or '').lower()
    s = _EXT_RX.sub(' ', s)
    for rx in _FN_NOISE_PATTERNS:
        s = rx.sub(' ', s)
    s = re.sub(r"[._\-\(\)\[\]\{\}#@!\?,;:+=&'\"/\\]+", ' ', s)
    return re.sub(r'\s+', ' ', s).strip()


def _is_hashish(tok):
    """True per token che sembrano ID/hash di sito (pjvgTNi8MLs, 5e3f9a2b…).
    Un nome umano non mescola mai cifre e lettere né vive senza vocali."""
    if len(tok) < 5:
        return False
    if any(c.isdigit() for c in tok) and any(c.isalpha() for c in tok):
        return True
    if len(tok) >= 9 and not (set(tok) & _VOWELS):
        return True
    return False


def _is_noise_token(tok):
    if not tok or len(tok) < 2: return True
    if tok.isdigit(): return True
    if tok in EXPLICIT_NOISE: return True
    if _is_hashish(tok): return True
    return False


def _looks_like_name(gram):
    """Plausibilità che un n-gram sia un nome di persona ∈ [0,1]. 0 = scarta."""
    toks = gram.split(' ')
    if not toks:
        return 0.0
    for t in toks:
        if len(t) < 2 or not t.isalpha():
            return 0.0
        if not (set(t) & _VOWELS):
            return 0.0
        if t in EXPLICIT_NOISE:
            return 0.0
    # Nome+cognome è la forma di gran lunga più probabile.
    return {1: 0.45, 2: 1.0, 3: 0.7}.get(len(toks), 0.3)


def _split_path(path):
    """(basename, [max 2 cartelle padre]) senza lettera di drive."""
    norm = (path or '').replace('\\', '/')
    parts = [p for p in norm.split('/') if p and not re.match(r'^[a-z]:$', p, re.I)]
    if not parts:
        return '', []
    return parts[-1], (parts[-3:-1] if len(parts) >= 2 else [])


def _path_streams(path):
    """Spezza un path in (token_del_filename, token_delle_2_cartelle_padre),
    già ripuliti dal rumore lessicale. Usato per la generazione n-gram.
    I token di cartella pesano meno: "Nuova cartella\\pornpad" non è un nome."""
    base, dirs = _split_path(path)
    fn_toks = [t for t in _clean_text(base).split(' ') if not _is_noise_token(t)]
    dir_toks = [t for t in _clean_text(' '.join(dirs)).split(' ') if not _is_noise_token(t)]
    return fn_toks, dir_toks


def _strip_filename(path):
    """Compat: stringa unica (cartelle + filename) ripulita."""
    fn, dirs = _path_streams(path)
    return ' '.join(dirs + fn)


def fulltext_terms(files, max_terms=2):
    """Stringa completa del solo filename ripulito, da passare tale e quale a
    `searchPerformer`. È la strategia primaria: il ranking full-text di StashDB
    isola il performer molto meglio di qualunque euristica n-gram locale
    (verificato: nome corretto in posizione 1 anche con filename molto sporchi).
    Le cartelle padre sono escluse: introducono solo rumore.

    NB: qui la EXPLICIT_NOISE NON viene applicata. Serve a ripulire gli n-gram,
    ma su un termine full-text sarebbe controproducente — contiene parole che
    sono anche cognomi reali ("white", "black", "danger"): togliendole
    "brazzers - angela white pov" diventa "angela" e il match si perde.
    Il ranking di StashDB tollera benissimo le parole di contorno."""
    out, seen = [], set()
    for fp in (files or []):
        base, _dirs = _split_path(fp)
        fn = [t for t in _clean_text(base).split(' ')
              if t and not t.isdigit() and not _is_hashish(t)]
        if not fn:
            continue
        q = ' '.join(fn)[:120].strip()
        key = q.lower()
        if not q or key in seen:
            continue
        seen.add(key)
        out.append({'name': q, 'score': 100.0, 'fileCount': 1, 'kind': 'fulltext'})
        if len(out) >= max_terms:
            break
    return out


def tokenize_filenames(files, topk=5):
    """Candidati n-gram (1-3 parole) dai path. Serve a coprire i file con più
    performer, dove il full-text restituisce solo il primo."""
    ngram_files = {}
    ngram_dironly = {}
    for fp in (files or []):
        fn_toks, dir_toks = _path_streams(fp)
        if not fn_toks and not dir_toks:
            continue
        for toks, from_dir in ((fn_toks, False), (dir_toks, True)):
            seen_in_this = set()
            for size in (1, 2, 3):
                if len(toks) < size: continue
                for i in range(len(toks) - size + 1):
                    gram = ' '.join(toks[i:i + size])
                    if gram in seen_in_this: continue
                    seen_in_this.add(gram)
                    ngram_files.setdefault(gram, set()).add(fp)
                    # Un gram è "solo cartella" finché non compare in un filename.
                    if not from_dir:
                        ngram_dironly[gram] = False
                    else:
                        ngram_dironly.setdefault(gram, True)
    if not ngram_files: return []

    # Score = frequenza × plausibilità-nome × penalità-cartella.
    # La plausibilità è il fattore decisivo: senza, con un solo file tutti i
    # 2-gram pareggiano e vince quello con la stringa più lunga (di solito un
    # hash o un nome di cartella).
    def score(g, fs):
        pl = _looks_like_name(g)
        if pl <= 0:
            return 0.0
        return len(fs) * pl * (0.35 if ngram_dironly.get(g) else 1.0)

    scored = [(g, fs, score(g, fs)) for g, fs in ngram_files.items()]
    scored = [x for x in scored if x[2] > 0]
    scored.sort(key=lambda x: (-x[2], -len(x[0])))

    out = []
    for g, fs, sc in scored[:topk * 4]:
        pretty = ' '.join(w.capitalize() for w in g.split(' '))
        out.append({'name': pretty, 'score': float(sc), 'fileCount': len(fs)})
    # Dedup: se "anna ralph" è presente, scarta i singoli "anna" / "ralph"
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


def _squash(s):
    """Riduce un nome alla sola sequenza di lettere minuscole: rende
    equivalenti "Anna De Ville", "Anna DeVille", "anna.de.ville", "ANNA-DEVILLE"."""
    return re.sub(r'[^a-z]', '', (s or '').lower())


def title_blob(files):
    """Testo compattato di tutti i filename del cluster, per il confronto nomi."""
    parts = []
    for fp in (files or []):
        base, dirs = _split_path(fp)
        parts.append(_clean_text(base))
        parts.extend(_clean_text(d) for d in dirs)
    return _squash(' '.join(parts))


def name_in_title(performer, blob):
    """True se il nome del performer (o un suo alias) compare nel titolo.

    È una evidenza INDIPENDENTE dal volto: quando concorda con la verifica
    fotografica il match è molto più solido, e serve a dirimere fra due
    performer con punteggi fotografici quasi identici. Da sola non basta mai —
    in una scena con più persone il volto inquadrato può benissimo non essere
    quello citato nel titolo."""
    if not blob:
        return False
    names = [performer.get('name')] + list(performer.get('aliases') or [])
    for nm in names:
        sq = _squash(nm)
        # Almeno 6 lettere: sotto quella soglia i falsi positivi esplodono
        # ("Mia", "Sky" comparirebbero ovunque).
        if len(sq) >= 6 and sq in blob:
            return True
    return False


def build_search_terms(files, topk=5):
    """Lista ordinata di termini da interrogare su StashDB:
    prima il full-text del filename, poi gli n-gram plausibili."""
    terms = fulltext_terms(files)
    seen = {t['name'].lower() for t in terms}
    for c in tokenize_filenames(files, topk=topk):
        k = c['name'].lower()
        if k in seen: continue
        seen.add(k)
        terms.append(c)
    return terms


# ────────────────────────────────────────────────────────────────────
# Stage E: lookup StashDB + verifica foto-vs-foto
# ────────────────────────────────────────────────────────────────────
def _wikidata_people(name, limit=4, timeout=8):
    """Wikidata: persone reali con una foto (P18). Copre attori, musicisti,
    sportivi, politici — chiunque abbia una voce. Nessuna chiave richiesta."""
    j = analyze._safe_request('GET', "https://www.wikidata.org/w/api.php",
        params={"action": "wbsearchentities", "format": "json", "language": "en",
                "uselang": "en", "type": "item", "search": name, "limit": limit},
        timeout=timeout)
    out = []
    for ent in (j or {}).get('search', [])[:limit]:
        qid = ent.get('id')
        if not qid:
            continue
        desc = (ent.get('description') or '').lower()
        # Prima si scartano le OPERE: una filmografia o una discografia ha una
        # descrizione che cita il mestiere della persona ("list of movies with
        # actor X") e supererebbe il filtro sotto.
        if any(k in desc for k in ('list of', 'filmography', 'discography',
                                   'album', 'song by', 'video game', 'episode',
                                   'film series', 'awards and nominations')):
            continue
        # Poi si tiene solo ciò che è una persona: senza questo filtro un
        # titolo di film o un comune omonimo entrerebbe fra i candidati.
        if desc and not any(k in desc for k in (
                'actor', 'actress', 'singer', 'musician', 'rapper', 'artist',
                'player', 'athlete', 'footballer', 'director', 'model',
                'presenter', 'comedian', 'dancer', 'writer', 'politician',
                'person', 'born', 'youtuber', 'streamer', 'wrestler', 'boxer',
                'driver', 'coach', 'producer', 'dj')):
            continue
        c = analyze._safe_request('GET', "https://www.wikidata.org/w/api.php",
            params={"action": "wbgetclaims", "format": "json",
                    "entity": qid, "property": "P18"}, timeout=timeout)
        imgs = []
        try:
            for claim in (((c or {}).get('claims') or {}).get('P18') or [])[:4]:
                fn = claim['mainsnak']['datavalue']['value']
                if fn:
                    imgs.append("https://commons.wikimedia.org/wiki/Special:FilePath/"
                                + fn.replace(' ', '_'))
        except Exception:
            pass
        if not imgs:
            continue
        out.append({'id': qid, 'name': ent.get('label') or name, 'images': imgs,
                    'gender': None, 'source': 'wikidata',
                    'disambiguation': ent.get('description'), 'aliases': []})
    return out


def _wikipedia_people(name, timeout=8):
    """Wikipedia: una foto dal riassunto della voce. Fallback per i nomi che
    Wikidata non indicizza con P18."""
    try:
        import urllib.parse
        slug = urllib.parse.quote(name.replace(' ', '_'), safe='')
    except Exception:
        slug = name.replace(' ', '_')
    j = analyze._safe_request('GET',
        "https://en.wikipedia.org/api/rest_v1/page/summary/" + slug, timeout=timeout)
    if not j or j.get('type') == 'disambiguation' or not j.get('title'):
        return []
    photo = None
    if j.get('originalimage'):
        photo = j['originalimage'].get('source')
    elif j.get('thumbnail'):
        photo = j['thumbnail'].get('source')
    if not photo:
        return []
    return [{'id': str(j.get('pageid')), 'name': j.get('title'), 'images': [photo],
             'gender': None, 'source': 'wikipedia',
             'disambiguation': j.get('description'), 'aliases': []}]


def _tmdb_people(name, tmdb_key, limit=4, timeout=8):
    """TMDB: cinema e TV. Richiede la chiave dell'utente. Prende più scatti
    per persona (/person/{id}/images) così il quorum resta applicabile."""
    if not tmdb_key:
        return []
    j = analyze._safe_request('GET', "https://api.themoviedb.org/3/search/person",
        params={"api_key": tmdb_key, "query": name}, timeout=timeout)
    out = []
    for hit in (j or {}).get('results', [])[:limit]:
        pid = hit.get('id')
        if not pid:
            continue
        imgs = []
        det = analyze._safe_request('GET',
            "https://api.themoviedb.org/3/person/%s/images" % pid,
            params={"api_key": tmdb_key}, timeout=timeout)
        for pr in ((det or {}).get('profiles') or [])[:8]:
            if pr.get('file_path'):
                imgs.append("https://image.tmdb.org/t/p/w342" + pr['file_path'])
        if not imgs and hit.get('profile_path'):
            imgs.append("https://image.tmdb.org/t/p/w342" + hit['profile_path'])
        if not imgs:
            continue
        g = hit.get('gender')
        out.append({'id': str(pid), 'name': hit.get('name'), 'images': imgs,
                    'gender': {1: 'FEMALE', 2: 'MALE'}.get(g),
                    'source': 'tmdb',
                    'disambiguation': hit.get('known_for_department'), 'aliases': []})
    return out


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


def spread_photos(urls, k):
    """Campiona k foto distribuite su TUTTA la galleria del performer invece
    delle prime k. Le immagini StashDB sono ordinate per scena/shooting: uno
    stride uniforme copre epoche, acconciature e luci diverse, che è proprio
    ciò che rende la conferma robusta."""
    urls = [u for u in (urls or []) if u]
    k = max(1, int(k))
    if len(urls) <= k:
        return urls
    step = len(urls) / float(k)
    out, seen = [], set()
    for i in range(k):
        u = urls[int(i * step)]
        if u not in seen:
            seen.add(u); out.append(u)
    return out


def lookup_all_sources(name, gql_cache, max_per_query, sources):
    """Cerca il nome su tutte le fonti abilitate e restituisce un'unica lista
    di candidati, ciascuno con le sue foto. StashDB per primo quando c'è: è
    l'unico con molte foto per persona, quindi il più solido da verificare."""
    key = (name or '').lower().strip()
    if not key:
        return []
    cached = gql_cache.get(('all', key))
    if cached is not None:
        return cached
    items = []
    if sources.get('stashdb'):
        items.extend(lookup_performer(name, gql_cache, max_per_query))
    if sources.get('tmdb'):
        try: items.extend(_tmdb_people(name, sources.get('tmdb_key'), max_per_query))
        except Exception: pass
    if sources.get('wikidata'):
        try: items.extend(_wikidata_people(name, max_per_query))
        except Exception: pass
    if sources.get('wikipedia'):
        try: items.extend(_wikipedia_people(name))
        except Exception: pass
    # Dedup per nome: la stessa persona può comparire su più fonti.
    seen, out = set(), []
    for it in items:
        k = (it.get('name') or '').strip().lower()
        if not k or k in seen:
            continue
        seen.add(k)
        out.append(it)
    gql_cache[('all', key)] = out
    return out


def verify_cluster_against_performer(cluster_emb, performer, photo_emb_cache,
                                     confirm_threshold, max_photos_per_performer,
                                     min_votes=2):
    """Confronta il cluster con più foto dello stesso performer, prese da scene
    diverse, e ritorna un verdetto CONSENSUALE — non il massimo singolo.

    Il massimo su N foto è una statistica fragile: più foto guardi, più è
    probabile che una somigli per caso. Serve invece corroborazione:
      · votes     = quante foto superano da sole confirm_threshold
      · score     = media delle 3 migliori (una foto fortunata non basta)
    Misurato sui dati reali: il performer giusto fa 8 voti su 12 foto e
    top3mean 0.77; i performer sbagliati fanno 0 voti e non superano 0.33.

    Screening progressivo: valuta prima SCREEN_BATCH foto; se nessuna vota e il
    massimo resta sotto SCREEN_MIN_MAX il performer è senza speranza e si
    interrompe subito — così il costo pieno lo pagano solo i candidati veri.

    Ritorna None se nessuna foto è valutabile (download/volto non rilevato)."""
    imgs = spread_photos(performer.get('images'), max_photos_per_performer)
    if not imgs:
        return None
    sims, best, best_url = [], -1.0, None

    def _eval(batch):
        nonlocal best, best_url
        for url in batch:
            sim = analyze.verify_match_photo(url, cluster_emb,
                                             photo_emb_cache=photo_emb_cache)
            if sim is None:
                continue
            sims.append(float(sim))
            if sim > best:
                best, best_url = float(sim), url

    _eval(imgs[:SCREEN_BATCH])
    promising = (best >= SCREEN_MIN_MAX) or any(s >= confirm_threshold for s in sims)
    if promising:
        _eval(imgs[SCREEN_BATCH:])

    if not sims:
        return None
    ordered = sorted(sims, reverse=True)
    top3 = ordered[:3]
    votes = sum(1 for s in sims if s >= confirm_threshold)
    # Con una galleria ampia lo score è il consenso: media delle 3 migliori,
    # così una singola somiglianza fortunata non basta. Con meno di
    # FEW_PHOTOS_LIMIT foto il consenso non è calcolabile — Wikidata e
    # Wikipedia pubblicano spesso uno o due ritratti, magari in epoche molto
    # diverse — e mediare significherebbe far affossare un riconoscimento
    # perfetto da uno scatto poco utilizzabile. Lì si guarda la prova
    # migliore, ma _passes pretende che superi FEW_PHOTOS_MATCH.
    consensus = (float(sum(top3) / len(top3)) if len(sims) >= FEW_PHOTOS_LIMIT
                 else float(ordered[0]))
    return {
        'score': consensus,
        'max': float(ordered[0]),
        'votes': int(votes),
        'photos': len(sims),
        'screened': len(sims) < len(imgs),
        'thumbUrl': best_url,
    }


def _passes(e, confirm_threshold, min_votes):
    """Il candidato è confermato?

    Con almeno FEW_PHOTOS_LIMIT foto vale il quorum: più scatti indipendenti
    devono superare la soglia. Sotto quel numero il quorum è inapplicabile —
    Wikidata e Wikipedia pubblicano spesso un solo ritratto — e allora si
    chiede una somiglianza nettamente più alta su quell'unica foto.
    """
    if e.get('score', 0) < confirm_threshold:
        return False
    photos = int(e.get('photos') or 0)
    if photos >= FEW_PHOTOS_LIMIT:
        return e.get('votes', 0) >= int(min_votes)
    return e.get('score', 0) >= FEW_PHOTOS_MATCH and e.get('votes', 0) >= 1


def _merge_options(verified_opts, local_opts):
    """Unisce le alternative verificate con le proposte dalla libreria locale.
    Le verificate vengono prima: hanno prove fotografiche, le altre no."""
    out, seen = [], set()
    for e in list(verified_opts or []) + list(local_opts or []):
        key = str(e.get('performerId') or '') or (e.get('name') or '').strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(e)
        if len(out) >= MAX_OPTIONS:
            break
    return out


def resolve_one(cluster, candidates, gql_cache, photo_emb_cache,
                confirm_threshold, max_performers_per_query,
                max_photos_per_performer, on_progress=None, min_votes=2,
                sources=None):
    """Itera candidati → performer → foto. Ritorna (match|None, termini, opzioni).

    `opzioni` sono i migliori MAX_OPTIONS performer verificati, ordinati per
    score: servono alla UI per far scegliere l'utente quando l'automatismo
    sbaglia. Il primo elemento coincide col match quando questo è accettato.
    Dedupa performer per id (uno stesso performer può tornare da candidati diversi)."""
    seen_ids = set()
    evaluated = []          # tutti i performer effettivamente confrontati
    cands_scored = []
    blob = title_blob(cluster.get('files'))
    for ci, cand in enumerate(candidates or []):
        name = cand.get('name') if isinstance(cand, dict) else cand
        if not name: continue
        # Early-stop. Due condizioni sufficienti:
        #  · uno score fotografico molto alto, già conferma robusta di per sé;
        #  · un performer citato nel titolo che supera anche il quorum foto —
        #    le due evidenze indipendenti concordano, cercare oltre è sprecato.
        #    È qui che il nome nel file fa risparmiare davvero tempo.
        if evaluated and (
                max(e['score'] for e in evaluated) >= EARLY_STOP_SCORE
                or any(e.get('inTitle') and _passes(e, confirm_threshold, min_votes)
                       for e in evaluated)):
            break
        if on_progress:
            # NB: nessun nome nel progress. Mostrare i candidati mentre vengono
            # scartati confonde — l'utente vedrebbe scorrere nomi che non sono
            # il risultato. Si comunica solo l'avanzamento.
            on_progress({'stage': 'query', 'candidate_idx': ci})
        performers = lookup_all_sources(name, gql_cache, max_performers_per_query,
                                        sources or {'stashdb': True})
        cand_best = -1.0
        for p in performers:
            pid = p.get('id')
            if pid in seen_ids: continue
            seen_ids.add(pid)
            if on_progress:
                on_progress({'stage': 'verify', 'candidate_idx': ci})
            res = verify_cluster_against_performer(
                cluster['embedding'], p, photo_emb_cache,
                confirm_threshold, max_photos_per_performer,
                min_votes=min_votes)
            if res is None: continue
            score = res['score']
            if score > cand_best: cand_best = score
            evaluated.append({
                'performerId': pid,
                'name': p.get('name'),
                'score': score,
                'source': p.get('source') or 'stashdb',
                'thumbUrl': res['thumbUrl'] or p.get('image'),
                'aliases': p.get('aliases') or [],
                'gender': p.get('gender'),
                'votes': res['votes'],
                'photos': res['photos'],
                'maxScore': res['max'],
                'inTitle': name_in_title(p, blob),
            })
        cands_scored.append({'name': name, 'score': float(cand_best) if cand_best >= 0 else None})

    if not evaluated:
        return None, cands_scored, []
    evaluated.sort(key=lambda e: -e['score'])
    best = evaluated[0]

    # ── Triangolazione volto + titolo ────────────────────────────────────
    # Due evidenze indipendenti che concordano battono una sola evidenza più
    # alta ma isolata: è il caso di due performer somiglianti in cui il
    # punteggio fotografico è quasi un pareggio, e quello di un nome pescato
    # da una parola qualsiasi del titolo che segna alto per caso.
    # Il titolo da solo NON promuove mai nessuno: senza quorum fotografico il
    # volto inquadrato potrebbe essere di un'altra persona presente in scena.
    titled = [e for e in evaluated
              if e.get('inTitle') and _passes(e, confirm_threshold, min_votes)]
    promoted = False
    if titled:
        # Chi è citato nel titolo E supera consenso e quorum fotografico ha
        # DUE evidenze indipendenti che concordano; il rivale col punteggio
        # più alto ne ha una sola. Non si confrontano numeri omogenei, quindi
        # non c'è una tolleranza da rispettare: vince la concordanza.
        #
        # Misurato su un caso reale: il titolo conteneva "Pee on each other
        # Veronica Leal", StashDB restituiva anche "Pee Player Ariel" che
        # segnava 0.787 contro 0.580 di Veronica Leal — e vinceva, pur essendo
        # emerso solo da una parola del titolo presa alla lettera.
        t = titled[0]        # evaluated è già ordinato per score
        promoted = t is not best
        best = t
        evaluated = [t] + [e for e in evaluated if e is not t]

    best_score = best['score']
    second_score = evaluated[1]['score'] if len(evaluated) > 1 else -1.0

    # ── Opzioni da proporre all'utente ───────────────────────────────────
    # Due categorie, entrambe utili, tenute ben distinte:
    #  · verificate → hanno superato consenso e quorum fotografico;
    #  · dal titolo → il nome compare nel file e corrisponde a un performer
    #    reale su StashDB, ma il volto non lo conferma. Non si accettano da
    #    sole, però vanno MOSTRATE: quando il video offre un solo volto, di
    #    profilo o mosso, il confronto fotografico non può concludere nulla e
    #    il nome nel titolo resta l'informazione migliore che abbiamo. Meglio
    #    proporlo con l'etichetta giusta che lasciare l'utente a mani vuote.
    opt_floor = max(0.0, confirm_threshold - OPTION_MARGIN)
    plausible, from_title = [], []
    for e in evaluated:
        # `verified` = ha superato consenso E quorum. È il flag che la UI usa
        # per distinguere una proposta accertata da una da controllare.
        e['verified'] = _passes(e, confirm_threshold, min_votes)
        # Un solo voto non basta per finire fra le proposte. Con gallerie
        # piccole il punteggio è il migliore di pochi tentativi, quindi un
        # valore medio-alto capita facilmente per caso: misurato, un
        # performer con 2 sole foto segna in media 0.31 contro volti
        # estranei, uno con 49 foto si ferma a 0.13. Serve o una conferma
        # ripetuta, o una somiglianza netta su quelle poche foto.
        enough = (
            e.get('votes', 0) >= 2
            or (int(e.get('photos') or 0) < FEW_PHOTOS_LIMIT
                and e['score'] >= FEW_PHOTOS_MATCH)
        )
        if e['score'] >= opt_floor and enough:
            plausible.append(e)       # evidenza fotografica, forte o debole
        elif e.get('inTitle'):
            from_title.append(e)      # solo il nome nel file lo sostiene
    verified_first = [e for e in plausible if e['verified']]
    rest = [e for e in plausible if not e['verified']]
    # fra le non confermate, prima chi e' citato nel titolo
    rest.sort(key=lambda e: (0 if e.get('inTitle') else 1, -e['score']))
    from_title.sort(key=lambda e: -e['score'])
    options = (verified_first + from_title + rest)[:MAX_OPTIONS] if not verified_first               else (verified_first + rest + from_title)[:MAX_OPTIONS]

    # Tre condizioni indipendenti, tutte necessarie:
    #  1. consenso   → la media delle 3 foto migliori supera la soglia
    #  2. quorum     → almeno min_votes foto DIVERSE lo confermano da sole
    #  3. distacco   → stacca il 2° performer, per non confondere due somiglianti
    if not _passes(best, confirm_threshold, min_votes):
        return None, cands_scored, options
    gap = (best_score - second_score) if second_score >= 0 else best_score
    # Dopo una promozione da titolo il 2° può avere score più alto: il distacco
    # fotografico non è più la metrica giusta, la decisione l'ha presa la
    # concordanza delle due evidenze. Niente margini negativi in uscita.
    best['margin'] = round(max(0.0, gap), 4)
    best['promotedByTitle'] = promoted
    # Il nome nel titolo è la seconda evidenza: con entrambe che concordano il
    # distacco fotografico dal 2° non serve più come unica garanzia.
    best['safe'] = bool(gap >= ACTOR_SAFE_MARGIN or best.get('inTitle'))
    best['confidence'] = 'confirmed' if best['safe'] else 'ambiguous'
    return best, cands_scored, options


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
    # Voci che somigliano a tutto: vanno escluse prima di iniziare, altrimenti
    # tornerebbero come suggerimento su ogni volto incerto.
    generic = generic_entries(db_rows, float(args.early_hit_threshold))
    if generic:
        _emit({'event': 'progress', 'stage': 'generic-skip', 'cluster': 0,
               'total': len(clusters), 'names': sorted(generic)})

    # API key StashDB obbligatoria per query (l'early-hit funziona comunque
    # senza key, perché legge solo cache locale).
    has_key = bool(stashdb._load_key())

    out = []
    matched = 0
    gql_cache = {}
    photo_emb_cache = {}

    confirm_thr = float(args.confirm_threshold)
    early_thr = float(args.early_hit_threshold)
    min_votes = max(1, int(args.photo_votes))
    # Fonti attive per questa esecuzione. StashDB copre l'adult; Wikidata,
    # Wikipedia e TMDB coprono attori, musicisti, sportivi e volti pubblici.
    sources = {
        'stashdb': (not args.no_stashdb) and has_key,
        'wikidata': bool(args.use_wikidata),
        'wikipedia': bool(args.use_wikipedia),
        'tmdb': bool(args.tmdb_key),
        'tmdb_key': args.tmdb_key,
    }
    _emit({'event': 'progress', 'stage': 'sources', 'cluster': 0, 'total': n,
           'detail': ','.join(k for k in ('stashdb', 'wikidata', 'wikipedia', 'tmdb')
                              if sources.get(k)) or 'nessuna'})

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
            'options': [],      # max MAX_OPTIONS alternative fra cui scegliere
        }

        # Stage C: early-hit dalla cache locale.
        #
        # La cache NON è mai una prova sufficiente. Un embedding salvato è il
        # centroide di un volto ripreso in UNA condizione: su un DB grande
        # trova sempre "qualcuno" che somiglia. Misurato: un volto otteneva
        # 0.597 contro la riga cache di un performer le cui foto reali non
        # superano 0.286 — nome sbagliato, accettato senza guardare una foto.
        #
        # Quindi: la cache serve solo a PROPORRE candidati; il nome esce
        # unicamente dopo lo stesso test consensuale su più foto/scene usato
        # nello Stage E. Unica eccezione: le voci senza external_id StashDB,
        # cioè i volti che l'utente ha nominato a mano — lì il nome è suo e
        # non c'è nulla da verificare contro StashDB.
        early_cands = early_facedb_candidates(cl['embedding'], db_rows, early_thr,
                                              top_k=5, skip_names=generic)
        early = None
        early_options = []     # alternative verificate, per la scelta manuale
        local_suggestions = [] # nomi dalla sola libreria locale, non verificati
        for cand in early_cands:
            ext_id = cand.get('performerId')
            if not ext_id or not has_key:
                # Voce locale senza controparte StashDB (volto nominato a mano
                # dall'utente): non esiste nessuna foto contro cui verificarla.
                # Diventa un nome solo con evidenza schiacciante; altrimenti
                # resta una proposta, perché su un DB grande un cosine appena
                # sopra soglia trova sempre qualcuno che somiglia.
                if not ext_id:
                    if cand['score'] >= LOCAL_ONLY_MATCH:
                        early = dict(cand)
                        early['source'] = cand.get('source') or 'facedb'
                        early['confidence'] = 'local'
                        early['safe'] = False
                        break
                    if cand['score'] >= confirm_thr and len(local_suggestions) < 2:
                        sug = dict(cand)
                        sug['source'] = cand.get('source') or 'facedb'
                        sug['verified'] = False
                        sug['fromLibrary'] = True
                        sug['votes'] = 0
                        sug['photos'] = 0
                        local_suggestions.append(sug)
                continue
            # Nessun nome nel progress: i candidati in esame non sono risultati.
            _emit({'event': 'progress', 'stage': 'verify',
                   'cluster': idx + 1, 'total': n})
            p_data, _err = stashdb._gql(stashdb.Q_PERFORMER_INFO, {'id': ext_id})
            photos = []
            p_gender = None
            if p_data and p_data.get('findPerformer'):
                _pf = p_data['findPerformer']
                photos = [i.get('url') for i in (_pf.get('images') or [])
                          if i and i.get('url')]
                # Il genere dichiarato da StashDB viaggia col match: serve al
                # wizard per non far scartare il performer dal filtro genere,
                # la cui stima locale è molto meno affidabile.
                p_gender = _pf.get('gender')
            # Fallback: se thumbUrl è già una URL http (raro), usalo.
            # NB: il thumbnail locale può essere base64 (face crop): ignoralo,
            # usa SEMPRE l'API StashDB per ottenere le foto vere.
            thumb = cand.get('thumbUrl')
            if not photos and isinstance(thumb, str) and thumb.startswith('http'):
                photos = [thumb]
            res = verify_cluster_against_performer(
                cl['embedding'], {'images': photos}, photo_emb_cache,
                confirm_thr, int(args.max_photos_per_performer),
                min_votes=min_votes)
            if res:
                # Ogni candidato con evidenza fotografica reale entra fra le
                # alternative, anche se non vince: è il materiale con cui
                # l'utente corregge un match sbagliato.
                if res['score'] >= max(0.0, confirm_thr - OPTION_MARGIN) and res['votes'] >= 1:
                    opt = dict(cand)
                    opt.update({'score': float(res['score']), 'maxScore': res['max'],
                                'votes': res['votes'], 'photos': res['photos'],
                                'thumbUrl': res['thumbUrl'] or thumb,
                                'gender': p_gender,
                                'source': (cand.get('source') or 'facedb') + '+photo'})
                    early_options.append(opt)
                if res['score'] >= confirm_thr and res['votes'] >= min_votes:
                    early = dict(early_options[-1]) if early_options else dict(cand)
                    early['confidence'] = 'confirmed'
                    early['safe'] = True
                    # Salva embedding foto come ref stabile (auto-bootstrap)
                    photo_emb = photo_emb_cache.get(res['thumbUrl']) if res['thumbUrl'] else None
                    if photo_emb and args.facedb:
                        _save_photo_ref_to_db(args.facedb, early['name'], photo_emb,
                                              ext_id, res['thumbUrl'])
                    break
            # Verifica fallita: prova prossimo candidato
        if early:
            _emit({'event': 'progress', 'stage': 'cache',
                   'cluster': idx + 1, 'total': n})
            early_options.sort(key=lambda e: -e.get('score', 0))
            cluster_obj['match'] = early
            cluster_obj['options'] = _merge_options(early_options, local_suggestions)
            out.append(cluster_obj)
            _emit(cluster_obj)
            matched += 1
            continue

        # Stage D: candidati (full-text del filename + n-gram plausibili)
        cands = build_search_terms(cl['files'], topk=int(args.topk))

        any_source = any(sources.get(k) for k in ('stashdb', 'wikidata', 'wikipedia', 'tmdb'))
        if not any_source or not cands:
            cluster_obj['candidates'] = cands
            _emit({'event': 'progress',
                   'stage': 'skipped' if not any_source else 'no-candidates',
                   'cluster': idx + 1, 'total': n})
            out.append(cluster_obj)
            _emit(cluster_obj)
            continue

        # Stage E: lookup + verify
        def on_progress(ev):
            ev = dict(ev)
            ev.update({'event': 'progress', 'cluster': idx + 1, 'total': n})
            _emit(ev)

        match, cands_scored, options = resolve_one(
            cl, cands, gql_cache, photo_emb_cache,
            confirm_thr,
            int(args.max_performers_per_query),
            int(args.max_photos_per_performer),
            on_progress=on_progress,
            min_votes=min_votes,
            sources=sources,
        )
        # Salva embedding foto del match come ref stabile per future early-hit
        if match and match.get('performerId') and match.get('thumbUrl') and args.facedb:
            photo_emb = photo_emb_cache.get(match['thumbUrl'])
            if photo_emb:
                _save_photo_ref_to_db(args.facedb, match['name'], photo_emb,
                                      match['performerId'], match['thumbUrl'])
        cluster_obj['match'] = match
        cluster_obj['candidates'] = cands_scored
        cluster_obj['options'] = _merge_options(options, local_suggestions)
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
    # 10 foto sparse sulla galleria = scene/epoche diverse. Lo screening
    # progressivo fa sì che i performer sbagliati ne costino comunque solo 4.
    p.add_argument('--max-photos-per-performer', default='10')
    p.add_argument('--max-performers-per-query', default='5')
    p.add_argument('--tmdb-key', default=None,
                   help='chiave TMDB: abilita cinema e TV')
    p.add_argument('--use-wikidata', action='store_true',
                   help='abilita Wikidata (gratis): attori, musicisti, sportivi')
    p.add_argument('--use-wikipedia', action='store_true',
                   help='abilita Wikipedia (gratis) come fallback')
    p.add_argument('--no-stashdb', action='store_true',
                   help='disabilita StashDB')
    p.add_argument('--photo-votes', default='2',
                   help='foto distinte che devono superare da sole la soglia '
                        'perché il nome venga accettato (quorum)')
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
