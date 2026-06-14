#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""StashDB GraphQL client per Maniac.

Endpoint: https://stashdb.org/graphql
Auth:     header `ApiKey: <token>`

CLI:
  python stashdb.py set-key --key <token>
  python stashdb.py search-performer --q "Margot Robbie" [--limit 10]
  python stashdb.py search-scene --q "Inception" [--limit 10]
  python stashdb.py performer-info --id <uuid>
  python stashdb.py scene-by-filename --files-list <files.json> [--min-confidence 0.6]

Output: ultima riga JSON `{ok, ...}` o JSONL progress per scene-by-filename.
La key viene letta da `userData/stashdb_key.txt` (o env STASHDB_API_KEY).
"""
import os, sys, json, argparse, time, re
import urllib.request, urllib.error, ssl


GRAPHQL_URL = "https://stashdb.org/graphql"


def emit(obj):
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _key_file():
    # In packaged usa STASHDB_KEY_FILE (userData, scrivibile); in dev <app>/_stashdb_key.txt.
    env = os.environ.get("STASHDB_KEY_FILE")
    if env:
        return env
    here = os.path.dirname(os.path.abspath(__file__))
    root = os.path.abspath(os.path.join(here, os.pardir))
    return os.path.join(root, "_stashdb_key.txt")


def _load_key():
    k = (os.environ.get("STASHDB_API_KEY") or "").strip()
    if k: return k
    try:
        with open(_key_file(), "r", encoding="utf-8") as f:
            return (f.read() or "").strip()
    except Exception:
        return ""


def _save_key(k):
    try:
        with open(_key_file(), "w", encoding="utf-8") as f:
            f.write((k or "").strip())
        return True
    except Exception:
        return False


def _gql(query, variables=None, timeout=15):
    """POST GraphQL. Ritorna (data_dict, error_str)."""
    key = _load_key()
    if not key:
        return None, "Manca API key StashDB (set-key prima)"
    payload = json.dumps({"query": query, "variables": variables or {}}).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "ApiKey": key,
        "Accept": "application/json",
        "User-Agent": "Maniac/1.0 (StashDB-Client)"
    }
    req = urllib.request.Request(GRAPHQL_URL, data=payload, headers=headers, method="POST")
    ctx = ssl.create_default_context()
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
            body = r.read().decode("utf-8", errors="replace")
            j = json.loads(body)
            if j.get("errors"):
                msgs = [str(e.get("message", e)) for e in (j.get("errors") or [])]
                return j.get("data"), "; ".join(msgs)[:300]
            return j.get("data"), None
    except urllib.error.HTTPError as e:
        body = ""
        try: body = e.read().decode("utf-8", "replace")[:300]
        except Exception: pass
        return None, f"HTTP {e.code}: {body}"
    except Exception as e:
        return None, str(e)[:200]


# ────────── Queries (GraphQL strings) ──────────

Q_SEARCH_PERFORMER = """
query SearchPerformer($term: String!, $limit: Int!) {
  searchPerformer(term: $term, limit: $limit) {
    id name disambiguation aliases gender
    images { url width height }
    birthdate { date }
    country ethnicity hair_color eye_color
  }
}
"""

Q_SEARCH_SCENE = """
query SearchScene($term: String!, $limit: Int!) {
  searchScene(term: $term, limit: $limit) {
    id title release_date duration
    studio { id name }
    performers { performer { id name } as }
    tags { id name }
    images { url width height }
    urls { url site { name } }
  }
}
"""

Q_PERFORMER_INFO = """
query PerformerInfo($id: ID!) {
  findPerformer(id: $id) {
    id name disambiguation aliases gender
    images { url width height }
    birthdate { date }
    country ethnicity hair_color eye_color height measurements { cup_size waist hip }
    career_start_year career_end_year
    urls { url site { name } }
    scene_count
  }
}
"""


def _normalize_performer(p):
    if not p: return None
    img = (p.get("images") or [{}])[0] or {}
    return {
        "id": p.get("id"),
        "name": p.get("name"),
        "disambiguation": p.get("disambiguation"),
        "aliases": p.get("aliases") or [],
        "gender": p.get("gender"),
        "image": img.get("url"),
        "birthdate": (p.get("birthdate") or {}).get("date"),
        "country": p.get("country"),
        "ethnicity": p.get("ethnicity"),
        "hair_color": p.get("hair_color"),
        "eye_color": p.get("eye_color"),
        "height": p.get("height"),
        "measurements": p.get("measurements"),
        "career_start_year": p.get("career_start_year"),
        "career_end_year": p.get("career_end_year"),
        "urls": [u.get("url") for u in (p.get("urls") or []) if u.get("url")],
        "scene_count": p.get("scene_count"),
    }


def _normalize_scene(s):
    if not s: return None
    img = (s.get("images") or [{}])[0] or {}
    studio = (s.get("studio") or {})
    return {
        "id": s.get("id"),
        "title": s.get("title"),
        "release_date": s.get("release_date"),
        "duration": s.get("duration"),
        "studio": {"id": studio.get("id"), "name": studio.get("name")},
        "performers": [
            {"id": (pp.get("performer") or {}).get("id"),
             "name": (pp.get("performer") or {}).get("name"),
             "as": pp.get("as")}
            for pp in (s.get("performers") or [])
        ],
        "tags": [t.get("name") for t in (s.get("tags") or []) if t.get("name")],
        "image": img.get("url"),
        "urls": [u.get("url") for u in (s.get("urls") or []) if u.get("url")],
    }


# ────────── CLI commands ──────────

def cmd_set_key(args):
    if not args.key:
        emit({"ok": False, "error": "richiede --key"})
        return 2
    if _save_key(args.key):
        emit({"ok": True, "saved_to": _key_file()})
    else:
        emit({"ok": False, "error": "impossibile salvare la key"})
    return 0


def cmd_search_performer(args):
    data, err = _gql(Q_SEARCH_PERFORMER, {"term": args.q, "limit": int(args.limit or 10)})
    if err and not data:
        emit({"ok": False, "error": err})
        return 1
    items = [_normalize_performer(p) for p in (data.get("searchPerformer") or []) if p]
    emit({"ok": True, "items": items, "total": len(items),
          "warning": err if err else None})
    return 0


def cmd_search_scene(args):
    data, err = _gql(Q_SEARCH_SCENE, {"term": args.q, "limit": int(args.limit or 10)})
    if err and not data:
        emit({"ok": False, "error": err})
        return 1
    items = [_normalize_scene(s) for s in (data.get("searchScene") or []) if s]
    emit({"ok": True, "items": items, "total": len(items),
          "warning": err if err else None})
    return 0


def cmd_performer_info(args):
    data, err = _gql(Q_PERFORMER_INFO, {"id": args.id})
    if err and not data:
        emit({"ok": False, "error": err})
        return 1
    p = _normalize_performer((data or {}).get("findPerformer"))
    if not p:
        emit({"ok": False, "error": "performer non trovato"})
        return 1
    emit({"ok": True, "performer": p})
    return 0


def _clean_filename(path):
    base = os.path.splitext(os.path.basename(path))[0]
    cleaners = [
        r'\b(1080p|720p|2160p|4k|480p|360p)\b',
        r'\b(x264|x265|h264|h265|hevc|av1|avc)\b',
        r'\b(bluray|webrip|web-?dl|hdrip|dvdrip|brrip|hdtv)\b',
        r'\b(yify|yts|rarbg|ettv|nf|amzn)\b',
        r'\b(aac|ac3|dts|opus|mp3)\b',
        r'[\.\-_\[\]\(\)]+',
    ]
    out = base
    for rx in cleaners:
        out = re.sub(rx, ' ', out, flags=re.IGNORECASE)
    return ' '.join(out.split()).strip() or base


def cmd_scene_by_filename(args):
    """Auto-tag library: scansiona files-list, per ogni file ricerca scena su StashDB.
    Output JSONL: start, file, result, done."""
    try:
        with open(args.files_list, "r", encoding="utf-8") as f:
            files = json.load(f) or []
    except Exception as e:
        emit({"event": "error", "error": "files-list: " + str(e)})
        return 2

    emit({"event": "start", "total": len(files)})
    matched = 0
    uncertain = 0
    for i, p in enumerate(files):
        name = _clean_filename(p)
        emit({"event": "file", "i": i, "path": p, "name": name})
        data, err = _gql(Q_SEARCH_SCENE, {"term": name, "limit": 5})
        if err and not data:
            uncertain += 1
            emit({"event": "result", "i": i, "path": p,
                  "status": "error", "error": err[:200]})
            continue
        scenes = [_normalize_scene(s) for s in (data.get("searchScene") or []) if s]
        if not scenes:
            uncertain += 1
            emit({"event": "result", "i": i, "path": p, "status": "uncertain",
                  "consensus": {"sources": 0}})
            continue
        # 1° risultato è il match con score più alto.
        top = scenes[0]
        # Filename → unione attori: se filename contiene il nome di un performer,
        # aggiungi quel performer anche se mancante dal top.
        lower_name = name.lower()
        forced = []
        for s in scenes[:5]:
            for pp in (s.get("performers") or []):
                pn = (pp.get("name") or "").strip()
                if not pn: continue
                if pn.lower() in lower_name and pn not in [x.get("name") for x in (top.get("performers") or [])]:
                    forced.append({"id": pp.get("id"), "name": pn, "as": None, "_source": "filename"})
        if forced:
            top["performers"] = (top.get("performers") or []) + forced
        matched += 1
        emit({"event": "result", "i": i, "path": p, "status": "matched",
              "consensus": {
                  "Title": top.get("title"),
                  "Date": top.get("release_date"),
                  "Studio": (top.get("studio") or {}).get("name"),
                  "Performers": [pp.get("name") for pp in (top.get("performers") or []) if pp.get("name")],
                  "Tags": top.get("tags") or [],
                  "stashdb_id": top.get("id"),
                  "image": top.get("image"),
                  "sources": 1,
              }})

    emit({"event": "done", "matched": matched, "uncertain": uncertain, "total": len(files)})
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["set-key", "search-performer", "search-scene",
                                     "performer-info", "scene-by-filename"])
    ap.add_argument("--key")
    ap.add_argument("--q")
    ap.add_argument("--limit", default="10")
    ap.add_argument("--id")
    ap.add_argument("--files-list")
    ap.add_argument("--min-confidence", default="0.6")
    args = ap.parse_args()
    try:
        if args.cmd == "set-key": return cmd_set_key(args)
        if args.cmd == "search-performer":
            if not args.q: emit({"ok": False, "error": "richiede --q"}); return 2
            return cmd_search_performer(args)
        if args.cmd == "search-scene":
            if not args.q: emit({"ok": False, "error": "richiede --q"}); return 2
            return cmd_search_scene(args)
        if args.cmd == "performer-info":
            if not args.id: emit({"ok": False, "error": "richiede --id"}); return 2
            return cmd_performer_info(args)
        if args.cmd == "scene-by-filename":
            if not args.files_list: emit({"event": "error", "error": "richiede --files-list"}); return 2
            return cmd_scene_by_filename(args)
    except Exception as e:
        emit({"ok": False, "error": str(e)[:300]})
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
