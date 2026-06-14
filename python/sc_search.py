#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Helper StreamingCommunity per il wizard di Maniac.

Espone una CLI che ritorna JSON sull'ultima riga di stdout:

  search   --q "<query>"                            -> lista risultati con cover/anno/plot/seasons
  info     --id N --slug S                          -> dettaglio singolo titolo
  seasons  --id N --slug S --n 1                    -> lista episodi della stagione N
  download --id N --slug S [--season N --episode M] -> avvia download (progress JSONL su stdout)

Risolve il dominio via sc_domain.py (cache 6h). Le richieste API riusano la sessione
e restituiscono cover/anno/plot/seasons che la search "ufficiale" del repo upstream droppa.
"""
import os, sys, json, argparse, time

HERE = os.path.dirname(os.path.abspath(__file__))
APP_ROOT = os.path.abspath(os.path.join(HERE, os.pardir))
SC_ROOT = os.path.join(APP_ROOT, "StreamingCommunity_api-main")

# Importa sc_domain.py (vicino a noi)
sys.path.insert(0, HERE)
import sc_domain  # noqa: E402

# Per i download riusiamo le funzioni del repo SC. Devono essere importate con cwd = SC_ROOT
# perché Src/Lib/FFmpeg/my_m3u8 e affini scrivono path relativi (videos/, tmp/).
def _ensure_sc_imports():
    if SC_ROOT not in sys.path:
        sys.path.insert(0, SC_ROOT)

import requests  # type: ignore

DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "it-IT,it;q=0.9,en;q=0.7",
}

# StreamingCommunity ha un anti-bot challenge JS (FingerprintJS).
# Le richieste Python "dritte" tornano sempre HTML con redirect.
# Maniac (Electron) esegue prima un warm-up con un BrowserWindow nascosto e
# passa i cookie raccolti in env var SCWIZ_COOKIE (formato "k=v; k2=v2").
# Senza il warm-up le chiamate API falliscono — ritorniamo un errore chiaro.
def _attach_session_cookies(session):
    raw = os.environ.get("SCWIZ_COOKIE", "").strip()
    if not raw:
        return False
    for piece in raw.split(";"):
        if "=" in piece:
            k, v = piece.strip().split("=", 1)
            if k:
                session.cookies.set(k, v)
    return True

# ────────────────────────────────────────────────────────────
# Helpers
# ────────────────────────────────────────────────────────────

def _resolve_domain(force=False):
    rec = sc_domain.resolve(force=force)
    return rec.get("url"), rec.get("domain"), rec  # url completo, tld, full record


def _site_version(session, base_url):
    """Estrae 'version' da data-page del div#app sulla home."""
    try:
        from bs4 import BeautifulSoup  # type: ignore
        r = session.get(base_url, headers=DEFAULT_HEADERS, timeout=10, verify=False)
        soup = BeautifulSoup(r.text, "lxml")
        node = soup.find("div", {"id": "app"})
        if not node:
            return None
        meta = json.loads(node.get("data-page"))
        return meta.get("version")
    except Exception:
        return None


def _cover_url(images):
    """Estrae poster URL dall'array 'images' della response API."""
    if not isinstance(images, list):
        return None
    # Cerca poster, poi cover
    for kind in ("poster", "cover", "background", "logo"):
        for img in images:
            if img.get("type") == kind and img.get("filename"):
                return f"https://cdn.streamingcommunity.computer/images/{img['filename']}"
    if images:
        f = images[0].get("filename")
        if f:
            return f"https://cdn.streamingcommunity.computer/images/{f}"
    return None


# ────────────────────────────────────────────────────────────
# CLI commands
# ────────────────────────────────────────────────────────────

def cmd_search(args):
    base_url, tld, _rec = _resolve_domain()
    if not base_url:
        return {"ok": False, "error": "dominio non risolto"}
    sess = requests.Session()
    sess.verify = False
    _attach_session_cookies(sess)
    try:
        r = sess.get(f"{base_url}/api/search",
                     params={"q": args.q},
                     headers=DEFAULT_HEADERS, timeout=10)
        if not r.ok:
            return {"ok": False, "error": f"HTTP {r.status_code}"}
        data = r.json().get("data", []) or []
        items = []
        for t in data[:21]:
            items.append({
                "id": t.get("id"),
                "slug": t.get("slug"),
                "name": t.get("name"),
                "type": "series" if t.get("type") == "tv" else (t.get("type") or "movie"),
                "year": t.get("last_air_date", "")[:4] or t.get("release_date", "")[:4] or None,
                "score": t.get("score"),
                "plot": t.get("plot"),
                "seasons": t.get("seasons_count") if (t.get("type") == "tv") else None,
                "cover": _cover_url(t.get("images")),
                "url": f"{base_url}/titles/{t.get('id')}-{t.get('slug')}",
            })
        return {"ok": True, "domain": tld, "base_url": base_url, "results": items}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}


def cmd_info(args):
    base_url, tld, _rec = _resolve_domain()
    if not base_url:
        return {"ok": False, "error": "dominio non risolto"}
    sess = requests.Session()
    sess.verify = False
    _attach_session_cookies(sess)
    try:
        version = _site_version(sess, base_url) or ""
        r = sess.get(f"{base_url}/titles/{args.id}-{args.slug}",
                     headers={**DEFAULT_HEADERS, "X-Inertia": "true",
                              "X-Inertia-Version": version},
                     timeout=10)
        if not r.ok:
            return {"ok": False, "error": f"HTTP {r.status_code}"}
        title = r.json().get("props", {}).get("title", {}) or {}
        return {"ok": True, "domain": tld, "version": version, "title": {
            "id": title.get("id"),
            "slug": title.get("slug"),
            "name": title.get("name"),
            "plot": title.get("plot"),
            "type": "series" if title.get("type") == "tv" else (title.get("type") or "movie"),
            "year": (title.get("last_air_date") or title.get("release_date") or "")[:4] or None,
            "seasons": title.get("seasons_count"),
            "runtime": title.get("runtime"),
            "score": title.get("score"),
            "cover": _cover_url(title.get("images")),
            "genres": [g.get("name") for g in (title.get("genres") or []) if g.get("name")],
        }}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}


def cmd_seasons(args):
    base_url, tld, _rec = _resolve_domain()
    if not base_url:
        return {"ok": False, "error": "dominio non risolto"}
    sess = requests.Session()
    sess.verify = False
    _attach_session_cookies(sess)
    try:
        version = _site_version(sess, base_url) or ""
        # token via watch
        sess.get(f"{base_url}/watch/{args.id}", headers=DEFAULT_HEADERS, timeout=10)
        token = sess.cookies.get("XSRF-TOKEN", "")
        r = sess.get(f"{base_url}/titles/{args.id}-{args.slug}/stagione-{args.n}",
                     headers={**DEFAULT_HEADERS,
                              "X-Inertia": "true",
                              "X-Inertia-Version": version,
                              "X-XSRF-Token": token},
                     timeout=10)
        if not r.ok:
            return {"ok": False, "error": f"HTTP {r.status_code}"}
        loaded = r.json().get("props", {}).get("loadedSeason", {}) or {}
        eps = [{"id": ep.get("id"),
                "n": ep.get("number"),
                "name": ep.get("name"),
                "plot": ep.get("plot"),
                "duration": ep.get("duration"),
                "cover": _cover_url(ep.get("images"))} for ep in loaded.get("episodes", [])]
        return {"ok": True, "domain": tld, "season": args.n, "episodes": eps}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}


def cmd_download(args):
    """Scarica film o episodio. Stampa progress JSONL durante il download."""
    base_url, tld, _rec = _resolve_domain()
    if not base_url:
        return {"ok": False, "error": "dominio non risolto"}
    _ensure_sc_imports()
    # cwd dev'essere SC_ROOT per le librerie del repo
    old_cwd = os.getcwd()
    os.chdir(SC_ROOT)
    try:
        # Forza data.json nel formato atteso da Page.domain_version()
        try:
            with open(os.path.join(SC_ROOT, "data.json"), "w", encoding="utf-8") as f:
                json.dump({"domain": tld, "url": base_url, "cached_at": time.time()}, f)
        except Exception:
            pass
        if args.season and args.episode:
            from Src.Api.tv import get_token, get_info_season, dw_single_ep  # type: ignore
            token = get_token(args.id, tld)
            eps = get_info_season(args.id, args.slug, tld, "", token, int(args.season))
            ep_index = int(args.episode) - 1
            if not (0 <= ep_index < len(eps)):
                return {"ok": False, "error": f"episodio {args.episode} fuori range"}
            sys.stdout.write(json.dumps({"event": "start", "kind": "episode",
                                          "season": args.season, "episode": args.episode}) + "\n")
            sys.stdout.flush()
            dw_single_ep(args.id, eps, ep_index, tld, token, args.slug, int(args.season))
        else:
            from Src.Api.film import main_dw_film  # type: ignore
            sys.stdout.write(json.dumps({"event": "start", "kind": "film"}) + "\n")
            sys.stdout.flush()
            main_dw_film(args.id, args.slug, tld)
        return {"ok": True, "event": "done"}
    except Exception as e:
        return {"ok": False, "error": str(e)[:300]}
    finally:
        try:
            os.chdir(old_cwd)
        except Exception:
            pass


def cmd_resolve_domain(_args):
    rec = sc_domain.resolve(force=False)
    return {"ok": True, **{k: v for k, v in rec.items() if not k.startswith("_")}}


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd")

    p = sub.add_parser("search"); p.add_argument("--q", required=True)
    p = sub.add_parser("info"); p.add_argument("--id", required=True); p.add_argument("--slug", required=True)
    p = sub.add_parser("seasons"); p.add_argument("--id", required=True); p.add_argument("--slug", required=True); p.add_argument("--n", required=True)
    p = sub.add_parser("download"); p.add_argument("--id", required=True); p.add_argument("--slug", required=True); p.add_argument("--season"); p.add_argument("--episode")
    p = sub.add_parser("resolve_domain")

    args = ap.parse_args()
    try:
        # Disabilita warning self-signed (verify=False)
        try:
            import urllib3  # type: ignore
            urllib3.disable_warnings()
        except Exception:
            pass
        if args.cmd == "search":
            res = cmd_search(args)
        elif args.cmd == "info":
            res = cmd_info(args)
        elif args.cmd == "seasons":
            res = cmd_seasons(args)
        elif args.cmd == "download":
            res = cmd_download(args)
        elif args.cmd == "resolve_domain":
            res = cmd_resolve_domain(args)
        else:
            res = {"ok": False, "error": "comando non specificato"}
    except Exception as e:
        res = {"ok": False, "error": str(e)[:300]}
    print(json.dumps(res, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
