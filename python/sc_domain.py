#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Risolutore dominio StreamingCommunity per Maniac.

Strategia:
  1. Pull `data.json` dal repo upstream `ghost6446/StreamingCommunity_api`
     (raw.githubusercontent.com). Cache locale 6h.
  2. Se la rete fallisce o il dominio non risponde, probing TLD su
     `https://streamingcommunityz.<tld>` finché HEAD ritorna 200/30x.
  3. Aggiorna `StreamingCommunity_api-main/data.json` col dominio risolto
     (formato: {"domain": "<tld>", "url": "<full url>"}).

Output JSON sull'ultima riga di stdout:
  {"ok": true, "domain": "ooo", "url": "https://streamingcommunityz.ooo",
   "source": "github" | "probe" | "cache" | "manual",
   "cached_at": "2026-04-26T..."}
"""
import os, sys, json, time, argparse, urllib.request, urllib.error, ssl, socket

UPSTREAM_RAW = "https://raw.githubusercontent.com/ghost6446/StreamingCommunity_api/main/data.json"
USER_AGENT = "Maniac/1.0 (sc-domain-resolver)"
TIMEOUT = 6
CACHE_TTL_SEC = 60 * 60  # 1 ora — il dominio cambia spesso, meglio ri-controllare presto

# TLDs noti per StreamingCommunity / StreamingCommunityZ.
# NB: l'ordine è importante — i TLD più recentemente usati dal sito vanno prima.
PROBE_TLDS = [
    "organic", "computer", "city", "net", "world", "vc",
    "ooo", "boats", "blue", "broker", "buzz", "cyou", "best",
    "cam", "africa", "fund", "today", "art", "win", "casa",
    "monster", "lat", "blog", "click", "click",
]
# Prefissi: il sito è migrato spesso fra "streamingcommunity" e "streamingcommunityz".
# Mettiamo "streamingcommunity" PRIMA perché è quello che espone le API JSON
# (`/api/search` ecc). I mirror "streamingcommunityz.*" sono solo browsing.
PROBE_PREFIXES = ["streamingcommunity", "streamingcommunityz"]


def _here():
    return os.path.dirname(os.path.abspath(__file__))


def _cache_path():
    # In packaged usa SC_DATA_JSON (userData, scrivibile); in dev il data.json vendored.
    env = os.environ.get("SC_DATA_JSON")
    if env:
        return env
    root = os.path.abspath(os.path.join(_here(), os.pardir))
    return os.path.join(root, "StreamingCommunity_api-main", "data.json")


def _load_cache():
    try:
        with open(_cache_path(), "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    return None


def _save_cache(rec):
    try:
        with open(_cache_path(), "w", encoding="utf-8") as f:
            json.dump(rec, f, ensure_ascii=False)
    except Exception:
        pass


def _probe(url, api_check=False):
    """HEAD su `url`; ritorna True se status < 400.
    Se api_check=True, esegue invece GET su {url}/api/search?q=test e accetta
    solo 200 (così filtriamo i mirror che servono solo HTML)."""
    target = url.rstrip("/") + ("/api/search?q=test" if api_check else "")
    method = "GET" if api_check else "HEAD"
    req = urllib.request.Request(target, method=method,
                                 headers={"User-Agent": USER_AGENT,
                                          "Accept": "application/json, */*"})
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT, context=ctx) as r:
            if api_check:
                if r.status != 200:
                    return False
                ct = (r.headers.get("Content-Type") or "").lower()
                return "json" in ct
            return r.status < 400
    except urllib.error.HTTPError as e:
        # 403/429 spesso indicano sito attivo dietro WAF: lo consideriamo vivo
        # (ma non per api_check: l'API deve rispondere 200 con JSON).
        return False if api_check else e.code in (403, 405, 429, 503)
    except (urllib.error.URLError, socket.timeout, ssl.SSLError, ConnectionError):
        return False


def _try_upstream():
    """Pull data.json dal repo. Ritorna dict {domain, url, source:'github'} o None."""
    req = urllib.request.Request(UPSTREAM_RAW, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            raw = r.read().decode("utf-8", errors="replace").strip()
        data = json.loads(raw)
        domain = (data or {}).get("domain", "").strip()
        if not domain:
            return None
        # Il file upstream a volte ha solo TLD ("ooo"), a volte URL completo.
        if domain.startswith("http"):
            url = domain.rstrip("/")
            tld = url.split(".")[-1]
        else:
            tld = domain.lstrip(".")
            url = f"https://streamingcommunityz.{tld}"
        if _probe(url, api_check=True):
            return {"domain": tld, "url": url, "source": "github"}
        # se non risponde, almeno restituiamo l'hint per il probing
        return {"_hint_tld": tld}
    except Exception:
        return None


def _probe_all_tlds(extra_first=None):
    """Probe sequenziale prefissi+tld. Cerca un URL la cui /api/search risponda con JSON 200.
    Solo questi servono al wizard (i mirror che servono solo HTML browsing sono inutili).
    """
    seen = set()
    tlds = list(PROBE_TLDS)
    if extra_first and extra_first not in tlds:
        tlds.insert(0, extra_first)
    for prefix in PROBE_PREFIXES:
        for tld in tlds:
            url = f"https://{prefix}.{tld}"
            if url in seen:
                continue
            seen.add(url)
            if _probe(url, api_check=True):
                return {"domain": tld, "url": url, "source": "probe", "prefix": prefix}
    return None


def resolve(force=False):
    cache = _load_cache() or {}
    cached_at = cache.get("cached_at", 0)
    cached_url = cache.get("url")
    # Cache valida e ancora viva → riusa (verifichiamo che l'API sia tuttora attiva)
    if not force and cached_url and (time.time() - float(cached_at or 0)) < CACHE_TTL_SEC:
        if _probe(cached_url, api_check=True):
            cache["source"] = "cache"
            return cache

    # 1) Upstream GitHub
    up = _try_upstream()
    hint = (up or {}).get("_hint_tld")
    if up and "url" in up:
        rec = {**up, "cached_at": time.time()}
        _save_cache(rec)
        return rec

    # 2) Probing TLD
    pr = _probe_all_tlds(extra_first=hint or (cache.get("domain") if cached_url else None))
    if pr:
        rec = {**pr, "cached_at": time.time()}
        _save_cache(rec)
        return rec

    # 3) Fallback: cache stantia se almeno presente
    if cached_url:
        return {**cache, "source": "cache_stale"}

    # 4) Default ultimissimo tentativo
    return {"domain": "ooo", "url": "https://streamingcommunityz.ooo", "source": "default"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="ignora cache 6h")
    args = ap.parse_args()
    try:
        rec = resolve(force=args.force)
        out = {"ok": True, **{k: v for k, v in rec.items() if not k.startswith("_")}}
    except Exception as e:
        out = {"ok": False, "error": str(e)[:200]}
    print(json.dumps(out, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
