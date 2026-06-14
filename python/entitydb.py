#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""DB locale unificato per tutte le entità (faces, objects, places, animals, genres, categories).
Schema SQLite — tabella `entities`:
  id, kind, name, embedding BLOB (float32, opzionale per non-face),
  thumbnail, source, external_id, metadata JSON, created_at, updated_at.

CLI:
  python entitydb.py --db <path> match  <payload.json>     # match per kind: face=embedding, altri=nome
  python entitydb.py --db <path> add    <payload.json>     # payload deve contenere kind
  python entitydb.py --db <path> list   [<kind>]           # lista (opzionale filtro per kind)
  python entitydb.py --db <path> delete <id>
  python entitydb.py --db <path> search <kind> <query>     # ricerca testuale per nome
  python entitydb.py --db <path> migrate                   # migra da tabella legacy `faces`

Output: una riga JSON finale su stdout.
"""
import sys, os, json, argparse, sqlite3, time, struct, math

MATCH_THRESHOLD = 0.62  # cosine similarity per faces (era 0.75 — troppo restrittivo)
MERGE_THRESHOLD = 0.62  # auto-merge cluster simili
SAFE_MARGIN = 0.06      # stacco minimo dalla 2ª identità per un match "sicuro"
KINDS = ("face", "object", "place", "animal", "genre", "category")

try:
    import numpy as np
    _HAVE_NP = True
except Exception:
    _HAVE_NP = False


def emit(obj):
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def pack_embedding(vec):
    return struct.pack('<%sf' % len(vec), *[float(x) for x in vec])


def unpack_embedding(blob):
    if blob is None:
        return None
    n = len(blob) // 4
    return list(struct.unpack('<%sf' % n, blob))


def cosine(a, b):
    if not a or not b or len(a) != len(b):
        return -1.0
    s = na = nb = 0.0
    for x, y in zip(a, b):
        s += x * y; na += x * x; nb += y * y
    if na <= 0 or nb <= 0:
        return -1.0
    return s / (math.sqrt(na) * math.sqrt(nb))


def connect(db_path):
    if db_path:
        d = os.path.dirname(db_path)
        if d:
            os.makedirs(d, exist_ok=True)
    con = sqlite3.connect(db_path)
    con.execute("""CREATE TABLE IF NOT EXISTS entities (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        kind TEXT NOT NULL,
        name TEXT NOT NULL,
        embedding BLOB,
        thumbnail TEXT,
        source TEXT,
        external_id TEXT,
        metadata TEXT,
        created_at INTEGER,
        updated_at INTEGER
    )""")
    con.execute("CREATE INDEX IF NOT EXISTS idx_entities_kind_name ON entities(kind, name COLLATE NOCASE)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_entities_kind ON entities(kind)")
    # Mantiene tabella legacy `faces` se esiste (gestita da facedb.py)
    con.commit()
    return con


def _row_to_entry(row):
    rid, kind, name, blob, thumb, source, ext_id, meta, created, updated = row
    out = {
        "id": rid, "kind": kind, "name": name,
        "thumbnail": thumb, "source": source, "external_id": ext_id,
        "created_at": created, "updated_at": updated,
    }
    if meta:
        try: out["metadata"] = json.loads(meta)
        except Exception: out["metadata"] = None
    if blob is not None:
        out["embedding"] = unpack_embedding(blob)
    return out


def cmd_match(con, payload_path, threshold=MATCH_THRESHOLD):
    with open(payload_path, 'r', encoding='utf-8') as f:
        p = json.load(f)
    kind = (p.get('kind') or 'face').lower()
    if kind not in KINDS:
        emit({"ok": False, "error": "kind sconosciuto: " + kind}); return 2
    thr = float(p.get('threshold', threshold))

    if kind == 'face':
        emb = p.get('embedding')
        if not emb:
            emit({"ok": False, "error": "embedding mancante per kind=face"}); return 2
        margin = float(p.get('margin', SAFE_MARGIN))
        rows = list(con.execute("SELECT id, kind, name, embedding, thumbnail, source, external_id, metadata, created_at, updated_at FROM entities WHERE kind='face'"))
        scored = []  # (score, entry)
        if _HAVE_NP and rows:
            mats, entries = [], []
            for row in rows:
                if row[3] is None:
                    continue
                v = np.frombuffer(row[3], dtype='<f4')
                mats.append(v); entries.append(_row_to_entry(row))
            if mats:
                dim = max(v.shape[0] for v in mats)
                q = np.asarray(emb, dtype=np.float32)
                if q.shape[0] == dim:
                    M = np.zeros((len(mats), dim), dtype=np.float32)
                    ok = np.zeros(len(mats), dtype=bool)
                    for i, v in enumerate(mats):
                        if v.shape[0] == dim:
                            M[i] = v; ok[i] = True
                    norms = np.linalg.norm(M, axis=1, keepdims=True); norms[norms == 0] = 1.0
                    sims = (M / norms) @ (q / (np.linalg.norm(q) or 1.0))
                    for i, e in enumerate(entries):
                        if ok[i]:
                            scored.append((float(sims[i]), e))
        if not scored:  # fallback puro-Python
            for row in rows:
                entry = _row_to_entry(row)
                ev = entry.get('embedding')
                if not ev: continue
                scored.append((cosine(emb, ev), entry))
        if not scored:
            emit({"ok": True, "match": None, "bestScore": None, "safe": False, "confidence": "none"})
            return 0
        # Aggrega per identità + margine sulla 2ª (match sicuro).
        best_per = {}
        for score, e in scored:
            key = e.get('external_id') or ('name:' + (e.get('name') or str(e.get('id'))))
            if key not in best_per or score > best_per[key][0]:
                best_per[key] = (score, e)
        ranked = sorted(best_per.values(), key=lambda t: t[0], reverse=True)
        best_score, best = ranked[0]
        second = ranked[1][0] if len(ranked) > 1 else -1.0
        gap = (best_score - second) if second >= 0 else best_score
        safe = bool(best_score >= thr and gap >= margin)
        confidence = ("confirmed" if safe else "ambiguous") if best_score >= thr else \
                     ("candidate" if best_score >= thr - 0.07 else "none")
        best = dict(best); best.pop('embedding', None)
        best["score"] = round(best_score, 4); best["second"] = round(second, 4) if second >= 0 else None
        best["margin"] = round(gap, 4); best["safe"] = safe; best["confidence"] = confidence
        if best_score >= thr:
            emit({"ok": True, "match": best, "safe": safe, "confidence": confidence})
        else:
            emit({"ok": True, "match": None, "bestScore": round(best_score, 4),
                  "candidate": best if confidence == "candidate" else None,
                  "safe": False, "confidence": confidence})
        return 0

    # Match per nome (objects/places/animals/genres/categories)
    name = (p.get('name') or '').strip()
    if not name:
        emit({"ok": False, "error": "name mancante"}); return 2
    rows = con.execute(
        "SELECT id, kind, name, embedding, thumbnail, source, external_id, metadata, created_at, updated_at "
        "FROM entities WHERE kind=? AND name LIKE ? COLLATE NOCASE LIMIT 5",
        (kind, '%' + name + '%')
    ).fetchall()
    items = [_row_to_entry(r) for r in rows]
    for it in items: it.pop('embedding', None)
    # Esatto se trovato, altrimenti il primo "like"
    exact = next((it for it in items if (it['name'] or '').lower() == name.lower()), None)
    emit({"ok": True, "match": exact, "candidates": items})
    return 0


def cmd_add(con, payload_path):
    with open(payload_path, 'r', encoding='utf-8') as f:
        p = json.load(f)
    kind = (p.get('kind') or 'face').lower()
    name = (p.get('name') or '').strip()
    if kind not in KINDS:
        emit({"ok": False, "error": "kind sconosciuto: " + kind}); return 2
    if not name:
        emit({"ok": False, "error": "name richiesto"}); return 2
    emb = p.get('embedding')
    if kind == 'face' and not emb:
        emit({"ok": False, "error": "embedding richiesto per kind=face"}); return 2
    blob = pack_embedding(emb) if emb else None
    meta = p.get('metadata')
    meta_json = json.dumps(meta, ensure_ascii=False) if meta is not None else None
    now = int(time.time())
    cur = con.execute(
        "INSERT INTO entities(kind, name, embedding, thumbnail, source, external_id, metadata, created_at, updated_at) "
        "VALUES(?,?,?,?,?,?,?,?,?)",
        (kind, name, blob, p.get('thumbnail'), p.get('source'),
         str(p.get('external_id') or p.get('tmdb_id') or '') or None,
         meta_json, now, now)
    )
    con.commit()
    emit({"ok": True, "id": cur.lastrowid})
    return 0


def cmd_list(con, kind=None):
    if kind:
        rows = con.execute(
            "SELECT id, kind, name, embedding, thumbnail, source, external_id, metadata, created_at, updated_at "
            "FROM entities WHERE kind=? ORDER BY name COLLATE NOCASE", (kind,)
        ).fetchall()
    else:
        rows = con.execute(
            "SELECT id, kind, name, embedding, thumbnail, source, external_id, metadata, created_at, updated_at "
            "FROM entities ORDER BY kind, name COLLATE NOCASE"
        ).fetchall()
    items = [_row_to_entry(r) for r in rows]
    # rimuovi embedding pesante dalla lista
    for it in items: it.pop('embedding', None)
    emit({"ok": True, "items": items})
    return 0


def cmd_delete(con, eid):
    con.execute("DELETE FROM entities WHERE id=?", (int(eid),))
    con.commit()
    emit({"ok": True})
    return 0


def cmd_search(con, kind, query):
    rows = con.execute(
        "SELECT id, kind, name, embedding, thumbnail, source, external_id, metadata, created_at, updated_at "
        "FROM entities WHERE kind=? AND name LIKE ? COLLATE NOCASE ORDER BY name LIMIT 50",
        (kind, '%' + query + '%')
    ).fetchall()
    items = [_row_to_entry(r) for r in rows]
    for it in items: it.pop('embedding', None)
    emit({"ok": True, "items": items})
    return 0


def _entity_completeness(row):
    """row: (id, kind, name, embedding, thumbnail, source, external_id, metadata, ...).
    Score per scegliere il record canonico in un cluster di duplicati."""
    score = 0
    _id, kind, name, blob, thumb, source, ext_id, metadata = row[0:8]
    if ext_id:                  score += 4
    if thumb:                   score += 2
    if source == 'tmdb':        score += 2
    if source == 'wikipedia':   score += 1
    if metadata:                score += 1
    if name:                    score += 1
    return score


def cmd_merge_similar(con, kind='face', threshold=MERGE_THRESHOLD):
    """Auto-merge cluster simili (cosine >= threshold) per il kind specificato."""
    rows = list(con.execute(
        "SELECT id, kind, name, embedding, thumbnail, source, external_id, metadata "
        "FROM entities WHERE kind=?", (kind,)))
    if len(rows) < 2:
        emit({"ok": True, "kind": kind, "merged_groups": 0,
              "removed_records": 0, "kept_records": len(rows)})
        return 0
    parent = list(range(len(rows)))
    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb: parent[rb] = ra
    embs = []
    for r in rows:
        try: embs.append(unpack_embedding(r[3]) if r[3] else None)
        except Exception: embs.append(None)
    for i in range(len(rows)):
        if embs[i] is None: continue
        for j in range(i + 1, len(rows)):
            if embs[j] is None: continue
            if cosine(embs[i], embs[j]) >= threshold:
                union(i, j)
    groups = {}
    for i in range(len(rows)):
        r = find(i)
        groups.setdefault(r, []).append(i)
    merged_groups = 0
    removed = 0
    for grp in groups.values():
        if len(grp) < 2: continue
        merged_groups += 1
        scored = sorted(grp, key=lambda i: _entity_completeness(rows[i]), reverse=True)
        keep_idx = scored[0]
        for i in grp:
            if i == keep_idx: continue
            con.execute("DELETE FROM entities WHERE id=?", (rows[i][0],))
            removed += 1
    con.commit()
    emit({"ok": True, "kind": kind, "merged_groups": merged_groups,
          "removed_records": removed,
          "kept_records": len(rows) - removed,
          "threshold": threshold})
    return 0


def cmd_migrate(con):
    """Migra le entry da tabella legacy `faces` (vecchio facedb.py) verso `entities`."""
    moved = 0
    try:
        rows = con.execute("SELECT id, name, embedding, thumbnail, source, tmdb_id, metadata FROM faces").fetchall()
    except sqlite3.OperationalError:
        emit({"ok": True, "migrated": 0, "note": "nessuna tabella faces legacy"})
        return 0
    for r in rows:
        rid, name, blob, thumb, source, tmdb_id, meta = r
        existing = con.execute(
            "SELECT 1 FROM entities WHERE kind='face' AND name=? AND created_at IS NOT NULL", (name,)
        ).fetchone()
        if existing:
            continue
        now = int(time.time())
        con.execute(
            "INSERT INTO entities(kind, name, embedding, thumbnail, source, external_id, metadata, created_at, updated_at) "
            "VALUES('face',?,?,?,?,?,?,?,?)",
            (name, blob, thumb, source, str(tmdb_id) if tmdb_id else None, meta, now, now)
        )
        moved += 1
    con.commit()
    emit({"ok": True, "migrated": moved})
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--db', required=True)
    ap.add_argument('cmd', choices=['match', 'add', 'list', 'delete', 'search', 'migrate', 'merge_similar'])
    ap.add_argument('args', nargs='*')
    a = ap.parse_args()
    try:
        con = connect(a.db)
    except Exception as e:
        emit({"ok": False, "error": "db: " + str(e)})
        return 1
    try:
        if a.cmd == 'match':
            if not a.args: emit({"ok": False, "error": "match richiede payload JSON"}); return 2
            return cmd_match(con, a.args[0])
        if a.cmd == 'add':
            if not a.args: emit({"ok": False, "error": "add richiede payload JSON"}); return 2
            return cmd_add(con, a.args[0])
        if a.cmd == 'list':
            return cmd_list(con, a.args[0] if a.args else None)
        if a.cmd == 'delete':
            if not a.args: emit({"ok": False, "error": "delete richiede id"}); return 2
            return cmd_delete(con, a.args[0])
        if a.cmd == 'search':
            if len(a.args) < 2: emit({"ok": False, "error": "search richiede <kind> <query>"}); return 2
            return cmd_search(con, a.args[0], a.args[1])
        if a.cmd == 'migrate':
            return cmd_migrate(con)
        if a.cmd == 'merge_similar':
            kind = a.args[0] if a.args else 'face'
            thr = float(a.args[1]) if len(a.args) > 1 else MERGE_THRESHOLD
            return cmd_merge_similar(con, kind, thr)
    finally:
        con.close()
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception as e:
        emit({"ok": False, "error": str(e)})
        sys.exit(1)
