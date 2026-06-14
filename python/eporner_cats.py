#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""DB locale categorie + ricerca contenuti via Eporner API v2.

Tassonomia in due liste curate:
- SFW_CATEGORIES: macro-genere cinema/serie (action, drama, sci-fi, …)
- NSFW_CATEGORIES: dominio adult (eporner) — tag macro più frequenti

CLI:
  python eporner_cats.py --db <path> seed              # popola le categorie nel DB
  python eporner_cats.py --db <path> list [--kind X]   # lista categorie ordinate
  python eporner_cats.py --db <path> search --q TXT    # cerca via API
  python eporner_cats.py --db <path> by-cat --c NAME   # video di una categoria

Output: ultima riga JSON `{ok, ...}`. Senza network il `seed` funziona.
"""
import os, sys, json, sqlite3, argparse, time
import urllib.request, urllib.parse, urllib.error, ssl


SFW_CATEGORIES = [
    "action","adventure","animation","anime","biography","comedy","crime",
    "documentary","drama","family","fantasy","film-noir","history","horror",
    "music","musical","mystery","romance","sci-fi","short","sport","thriller",
    "war","western","reality-tv","talk-show","game-show","news","superhero",
    "indie","cult","classic","silent","experimental","martial-arts","spy",
    "heist","disaster","slasher","supernatural","psychological","period-drama",
    "coming-of-age","political","sports-drama","romcom","dark-comedy","satire",
    "epic","mockumentary","found-footage","road-movie","buddy","ensemble",
    "biographical","historical","stop-motion","cgi","2d-animation","short-film",
    "feature-length","mini-series","docu-series","web-series","procedural",
    "anthology","sitcom","drama-series","tragedy","tragicomedy","sword-and-sandal",
    "neo-noir","gothic","gothic-horror","steampunk","cyberpunk","post-apocalyptic",
    "dystopian","utopian","time-travel","alien","monster","kaiju",
]


NSFW_CATEGORIES = [
    # Macro-categorie più frequenti su tube generici (eporner-like).
    # Ridotte volutamente — la lista viene ampliata via "search" runtime.
    "amateur","anal","asian","babe","bbw","bdsm","big-ass","big-tits","blonde",
    "blowjob","brunette","cartoon","casting","celebrity","creampie","cumshot",
    "deepthroat","ebony","european","facial","feet","fetish","fingering",
    "fisting","gangbang","gay","group","handjob","hardcore","hd","hentai",
    "indian","interracial","japanese","latina","lesbian","massage","masturbation",
    "mature","milf","old-and-young","oral","orgy","outdoor","pornstar","pov",
    "public","redhead","russian","squirt","stockings","strapon","teen","threesome",
    "toys","uniform","vintage","webcam",
]


def emit(obj):
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def connect(db_path):
    if os.path.dirname(db_path):
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
    con = sqlite3.connect(db_path)
    con.execute("""CREATE TABLE IF NOT EXISTS categories (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL UNIQUE,
        kind TEXT NOT NULL,
        description TEXT,
        parent_id INTEGER,
        seeded_at INTEGER
    )""")
    con.execute("""CREATE TABLE IF NOT EXISTS videos (
        id TEXT PRIMARY KEY,
        title TEXT,
        thumbnail TEXT,
        length INTEGER,
        rate REAL,
        category TEXT,
        url TEXT,
        added_at INTEGER
    )""")
    con.execute("""CREATE INDEX IF NOT EXISTS idx_videos_cat ON videos(category)""")
    con.commit()
    return con


def cmd_seed(con):
    now = int(time.time())
    inserted = 0
    for name in SFW_CATEGORIES:
        try:
            con.execute("INSERT OR IGNORE INTO categories(name, kind, seeded_at) VALUES(?,?,?)",
                        (name, "sfw", now))
            if con.total_changes > 0: inserted += 1
        except Exception: pass
    for name in NSFW_CATEGORIES:
        try:
            con.execute("INSERT OR IGNORE INTO categories(name, kind, seeded_at) VALUES(?,?,?)",
                        (name, "nsfw", now))
        except Exception: pass
    con.commit()
    sfw_n = con.execute("SELECT COUNT(*) FROM categories WHERE kind='sfw'").fetchone()[0]
    nsfw_n = con.execute("SELECT COUNT(*) FROM categories WHERE kind='nsfw'").fetchone()[0]
    emit({"ok": True, "sfw": sfw_n, "nsfw": nsfw_n, "total": sfw_n + nsfw_n})
    return 0


def cmd_list(con, kind=None):
    rows = []
    sql = "SELECT id, name, kind FROM categories"
    args = []
    if kind:
        sql += " WHERE kind=?"; args.append(kind)
    sql += " ORDER BY name COLLATE NOCASE"
    for r in con.execute(sql, args):
        rows.append({"id": r[0], "name": r[1], "kind": r[2]})
    emit({"ok": True, "items": rows, "total": len(rows)})
    return 0


def _api_search(query, per_page=30, page=1):
    """GET https://www.eporner.com/api/v2/video/search/. Ritorna dict o None."""
    url = "https://www.eporner.com/api/v2/video/search/"
    params = {"query": query or "", "per_page": str(per_page), "page": str(page),
              "format": "json", "lq": "0", "thumbsize": "medium"}
    full = url + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(full, headers={
        "User-Agent": "Mozilla/5.0 (Maniac/1.0) AppleWebKit/537.36"
    })
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    try:
        with urllib.request.urlopen(req, timeout=12, context=ctx) as r:
            if r.status != 200: return None
            return json.loads(r.read().decode("utf-8", errors="replace"))
    except Exception:
        return None


def cmd_search(con, query):
    data = _api_search(query, per_page=30)
    if not data:
        emit({"ok": False, "error": "richiesta API fallita"})
        return 1
    videos = data.get("videos", []) or []
    out = []
    now = int(time.time())
    for v in videos[:30]:
        vid = (v.get("id") or "").strip()
        if not vid: continue
        item = {
            "id": vid,
            "title": v.get("title") or "",
            "thumbnail": (v.get("default_thumb", {}) or {}).get("src") or "",
            "length": int(v.get("length_min", 0) or 0),
            "rate": float(v.get("rate", 0) or 0),
            "category": (query or "").lower().strip(),
            "url": v.get("url") or "",
            "added_at": now
        }
        try:
            con.execute("""INSERT OR REPLACE INTO videos
                (id,title,thumbnail,length,rate,category,url,added_at) VALUES(?,?,?,?,?,?,?,?)""",
                (item["id"], item["title"], item["thumbnail"], item["length"],
                 item["rate"], item["category"], item["url"], item["added_at"]))
        except Exception: pass
        out.append(item)
    con.commit()
    emit({"ok": True, "query": query, "results": out, "total": len(out)})
    return 0


def cmd_by_cat(con, cat):
    rows = con.execute("SELECT id,title,thumbnail,length,rate,url FROM videos WHERE category=? ORDER BY rate DESC LIMIT 50",
                       ((cat or "").lower().strip(),)).fetchall()
    items = [{"id": r[0], "title": r[1], "thumbnail": r[2], "length": r[3],
              "rate": r[4], "url": r[5]} for r in rows]
    emit({"ok": True, "category": cat, "items": items, "total": len(items)})
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("cmd", choices=["seed", "list", "search", "by-cat"])
    ap.add_argument("--kind", default=None)
    ap.add_argument("--q", default=None)
    ap.add_argument("--c", default=None)
    a = ap.parse_args()
    try:
        con = connect(a.db)
    except Exception as e:
        emit({"ok": False, "error": "db: " + str(e)})
        return 1
    try:
        if a.cmd == "seed":      return cmd_seed(con)
        if a.cmd == "list":      return cmd_list(con, a.kind)
        if a.cmd == "search":
            if not a.q: emit({"ok": False, "error": "search richiede --q"}); return 2
            return cmd_search(con, a.q)
        if a.cmd == "by-cat":
            if not a.c: emit({"ok": False, "error": "by-cat richiede --c"}); return 2
            return cmd_by_cat(con, a.c)
    finally:
        con.close()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        emit({"ok": False, "error": str(e)})
        sys.exit(1)
