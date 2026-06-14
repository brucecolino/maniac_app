#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""DB locale volti per Maniac.
Schema SQLite: faces(id, name, embedding BLOB float32, thumbnail, source, tmdb_id, metadata JSON, created_at, updated_at).
CLI:
  python facedb.py --db <path> match <embedding.json>
  python facedb.py --db <path> add <payload.json>
  python facedb.py --db <path> list
  python facedb.py --db <path> delete <id>
Le coordinate JSON sono su stdout (una riga JSON finale).
"""
import sys, os, json, argparse, sqlite3, time, struct, math

MATCH_THRESHOLD = 0.62  # cosine similarity (0.55–0.65 tipici per ArcFace/Facenet)
MERGE_THRESHOLD = 0.62  # soglia per auto-merge cluster simili
SAFE_MARGIN = 0.06      # il migliore deve battere la 2ª identità diversa di questo margine

try:
    import numpy as np            # match vettoriale (ordini di grandezza più veloce)
    _HAVE_NP = True
except Exception:
    _HAVE_NP = False


def emit(obj):
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def pack_embedding(vec):
    return struct.pack('<%sf' % len(vec), *[float(x) for x in vec])


def unpack_embedding(blob):
    n = len(blob) // 4
    return list(struct.unpack('<%sf' % n, blob))


def cosine(a, b):
    if len(a) != len(b):
        return -1.0
    s = 0.0; na = 0.0; nb = 0.0
    for x, y in zip(a, b):
        s += x * y
        na += x * x
        nb += y * y
    if na <= 0 or nb <= 0:
        return -1.0
    return s / (math.sqrt(na) * math.sqrt(nb))


def connect(db_path):
    os.makedirs(os.path.dirname(db_path), exist_ok=True) if os.path.dirname(db_path) else None
    con = sqlite3.connect(db_path)
    con.execute("""CREATE TABLE IF NOT EXISTS faces (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        embedding BLOB NOT NULL,
        thumbnail TEXT,
        source TEXT,
        tmdb_id INTEGER,
        metadata TEXT,
        created_at INTEGER,
        updated_at INTEGER
    )""")
    con.execute("CREATE INDEX IF NOT EXISTS idx_faces_name ON faces(name)")
    # Migrazione: external_id (UUID stringa, es. StashDB performer.id)
    cols = {row[1] for row in con.execute("PRAGMA table_info(faces)")}
    if 'external_id' not in cols:
        con.execute("ALTER TABLE faces ADD COLUMN external_id TEXT")
    con.execute("CREATE INDEX IF NOT EXISTS idx_faces_external_id ON faces(external_id)")
    con.commit()
    return con


def _load_face_matrix(con):
    """Tutte le facce come matrice L2-normalizzata per il match vettoriale."""
    rows = list(con.execute(
        "SELECT id, name, embedding, thumbnail, source, tmdb_id, external_id FROM faces"))
    if not rows or not _HAVE_NP:
        return rows, None
    vecs = [np.frombuffer(r[2], dtype='<f4') for r in rows]
    dim = max((v.shape[0] for v in vecs), default=0)
    if dim == 0:
        return rows, None
    M = np.zeros((len(vecs), dim), dtype=np.float32)
    valid = np.zeros(len(vecs), dtype=bool)
    for i, v in enumerate(vecs):
        if v.shape[0] == dim:
            M[i] = v; valid[i] = True
    norms = np.linalg.norm(M, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    M = M / norms
    return rows, (M, valid, dim)


def cmd_match(con, payload_path, threshold=MATCH_THRESHOLD):
    with open(payload_path, 'r', encoding='utf-8') as f:
        payload = json.load(f)
    emb = payload.get('embedding') if isinstance(payload, dict) else payload
    if not emb:
        emit({"ok": False, "error": "embedding mancante"})
        return 2
    thr = float(payload.get('threshold', threshold)) if isinstance(payload, dict) else threshold
    margin = float(payload.get('margin', SAFE_MARGIN)) if isinstance(payload, dict) else SAFE_MARGIN

    rows, packed = _load_face_matrix(con)
    if not rows:
        emit({"ok": True, "match": None, "bestScore": None, "safe": False, "confidence": "none"})
        return 0

    # 1) Punteggi cosine: vettoriale con numpy (M·q), fallback puro-Python.
    scored = []  # (score, row)
    if packed is not None:
        M, valid, dim = packed
        q = np.asarray(emb, dtype=np.float32)
        if q.shape[0] == dim:
            qn = np.linalg.norm(q) or 1.0
            sims = M @ (q / qn)
            for i, row in enumerate(rows):
                if valid[i]:
                    scored.append((float(sims[i]), row))
        else:
            packed = None
    if packed is None:
        for row in rows:
            scored.append((cosine(emb, unpack_embedding(row[2])), row))
    if not scored:
        emit({"ok": True, "match": None, "bestScore": None, "safe": False, "confidence": "none"})
        return 0

    # 2) Aggrega per IDENTITÀ (external_id o nome): miglior punteggio per identità,
    #    così un'identità con molte foto non falsa il confronto.
    best_per_ident = {}
    for score, row in scored:
        rid, name, blob, thumb, source, tmdb_id, external_id = row
        key = external_id or ('name:' + (name or str(rid)))
        cur = best_per_ident.get(key)
        if cur is None or score > cur[0]:
            best_per_ident[key] = (score, row)
    ranked = sorted(best_per_ident.values(), key=lambda t: t[0], reverse=True)

    best_score, best_row = ranked[0]
    second_score = ranked[1][0] if len(ranked) > 1 else -1.0
    rid, name, blob, thumb, source, tmdb_id, external_id = best_row
    gap = (best_score - second_score) if second_score >= 0 else best_score
    # 3) Match SICURO: supera la soglia E stacca la 2ª identità diversa di `margin`.
    safe = bool(best_score >= thr and gap >= margin)
    if best_score >= thr and safe:
        confidence = "confirmed"
    elif best_score >= thr:
        confidence = "ambiguous"           # sopra soglia ma troppo vicino a un'altra identità
    elif best_score >= (thr - 0.07):
        confidence = "candidate"
    else:
        confidence = "none"

    best = {"id": rid, "name": name, "score": round(best_score, 4),
            "thumbnail": thumb, "source": source, "tmdb_id": tmdb_id,
            "external_id": external_id,
            "second": round(second_score, 4) if second_score >= 0 else None,
            "margin": round(gap, 4), "safe": safe, "confidence": confidence}

    if best_score >= thr:
        emit({"ok": True, "match": best, "safe": safe, "confidence": confidence})
    else:
        emit({"ok": True, "match": None, "bestScore": round(best_score, 4),
              "candidate": best if confidence == "candidate" else None,
              "safe": False, "confidence": confidence})
    return 0


def cmd_add(con, payload_path):
    with open(payload_path, 'r', encoding='utf-8') as f:
        p = json.load(f)
    name = (p.get('name') or '').strip()
    emb = p.get('embedding')
    if not name or not emb:
        emit({"ok": False, "error": "name/embedding richiesti"})
        return 2
    now = int(time.time())
    blob = pack_embedding(emb)
    metadata = p.get('metadata')
    meta_json = json.dumps(metadata, ensure_ascii=False) if metadata is not None else None
    cur = con.execute(
        "INSERT INTO faces(name, embedding, thumbnail, source, tmdb_id, external_id, metadata, created_at, updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
        (name, blob, p.get('thumbnail'), p.get('source'), p.get('tmdb_id'),
         p.get('external_id'), meta_json, now, now)
    )
    con.commit()
    emit({"ok": True, "id": cur.lastrowid})
    return 0


def cmd_list(con):
    rows = []
    for r in con.execute("SELECT id, name, thumbnail, source, tmdb_id, external_id, created_at FROM faces ORDER BY name COLLATE NOCASE"):
        rows.append({"id": r[0], "name": r[1], "thumbnail": r[2], "source": r[3],
                     "tmdb_id": r[4], "external_id": r[5], "created_at": r[6]})
    emit({"ok": True, "items": rows})
    return 0


def cmd_delete(con, face_id):
    con.execute("DELETE FROM faces WHERE id=?", (int(face_id),))
    con.commit()
    emit({"ok": True})
    return 0


def _record_completeness(row):
    """Score per scegliere il "record canonico" in un cluster: chi ha più info vince."""
    score = 0
    rid, name, blob, thumb, source, tmdb_id, metadata = row
    if tmdb_id:           score += 4
    if thumb:             score += 2
    if source == 'tmdb':  score += 2
    if source == 'wikipedia': score += 1
    if metadata:          score += 1
    if name:              score += 1
    return score


def cmd_merge_similar(con, threshold=MERGE_THRESHOLD):
    """Scansiona tutto il DB e fonde i cluster simili (cosine >= threshold).
    Per ogni cluster, sceglie il record più completo come canonico e cancella i duplicati.
    Ritorna stats {merged_groups, removed_records, kept_records}."""
    rows = list(con.execute(
        "SELECT id, name, embedding, thumbnail, source, tmdb_id, metadata FROM faces"))
    if len(rows) < 2:
        emit({"ok": True, "merged_groups": 0, "removed_records": 0, "kept_records": len(rows)})
        return 0
    # Disjoint-set union su indici
    parent = list(range(len(rows)))
    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb: parent[rb] = ra
    n = len(rows)
    if _HAVE_NP:
        # Similarità a blocchi via prodotto matriciale normalizzato (rapido, O(n²)
        # ma in C; chunk per limitare la memoria su DB grandi).
        vecs = [np.frombuffer(r[2], dtype='<f4') for r in rows]
        dim = max((v.shape[0] for v in vecs), default=0)
        M = np.zeros((n, dim), dtype=np.float32); valid = np.zeros(n, dtype=bool)
        for i, v in enumerate(vecs):
            if v.shape[0] == dim and dim > 0:
                M[i] = v; valid[i] = True
        norms = np.linalg.norm(M, axis=1, keepdims=True); norms[norms == 0] = 1.0
        M = M / norms
        CHUNK = 512
        for start in range(0, n, CHUNK):
            block = M[start:start + CHUNK] @ M.T
            for bi in range(block.shape[0]):
                i = start + bi
                if not valid[i]:
                    continue
                for j in np.where(block[bi] >= threshold)[0]:
                    j = int(j)
                    if j > i and valid[j]:
                        union(i, j)
    else:
        embs = [unpack_embedding(r[2]) for r in rows]
        for i in range(n):
            for j in range(i + 1, n):
                if cosine(embs[i], embs[j]) >= threshold:
                    union(i, j)
    # Raggruppa
    groups = {}
    for i in range(len(rows)):
        r = find(i)
        groups.setdefault(r, []).append(i)
    merged_groups = 0
    removed_records = 0
    for grp in groups.values():
        if len(grp) < 2: continue
        merged_groups += 1
        # canonico = più completo
        scored = sorted(grp, key=lambda i: _record_completeness(rows[i]), reverse=True)
        canonical_idx = scored[0]
        keep_id = rows[canonical_idx][0]
        # Cancella tutti gli altri
        for i in grp:
            if i == canonical_idx: continue
            con.execute("DELETE FROM faces WHERE id=?", (rows[i][0],))
            removed_records += 1
    con.commit()
    emit({"ok": True, "merged_groups": merged_groups,
          "removed_records": removed_records,
          "kept_records": len(rows) - removed_records,
          "threshold": threshold})
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--db', required=True)
    ap.add_argument('cmd', choices=['match', 'add', 'list', 'delete', 'merge_similar'])
    ap.add_argument('arg', nargs='?')
    args = ap.parse_args()
    try:
        con = connect(args.db)
    except Exception as e:
        emit({"ok": False, "error": "db: " + str(e)})
        return 1
    try:
        if args.cmd == 'match':
            if not args.arg:
                emit({"ok": False, "error": "match richiede path JSON payload"})
                return 2
            return cmd_match(con, args.arg)
        if args.cmd == 'add':
            if not args.arg:
                emit({"ok": False, "error": "add richiede path JSON payload"})
                return 2
            return cmd_add(con, args.arg)
        if args.cmd == 'list':
            return cmd_list(con)
        if args.cmd == 'delete':
            if not args.arg:
                emit({"ok": False, "error": "delete richiede id"})
                return 2
            return cmd_delete(con, args.arg)
        if args.cmd == 'merge_similar':
            thr = float(args.arg) if args.arg else MERGE_THRESHOLD
            return cmd_merge_similar(con, thr)
    finally:
        con.close()
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception as e:
        emit({"ok": False, "error": str(e)})
        sys.exit(1)
