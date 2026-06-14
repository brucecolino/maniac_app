#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Riconoscimento e tagging musicale (gratuito, multi-layer).

Pipeline:
  Layer 1 - mutagen: legge i tag già presenti (ID3v2 / Vorbis / MP4 / FLAC)
  Layer 2 - AcoustID + Chromaprint: impronta acustica → MBID (gratis, key gratis)
  Layer 3 - MusicBrainz: metadati canonici da MBID (gratis, no key, rate-limit 1/s)
  Layer 4 - Cover Art Archive: locandine album da MBID (gratis, no key)
  Layer 5 - opzionale Shazam (shazamio) come fallback

Tag write-back:
  Modalità 'tag'      → solo DB applicazione, file non toccato
  Modalità 'metadata' → riscrive i tag fisici via mutagen, con backup .bak
  Modalità 'both'     → entrambi

CLI:
  python musicid.py --folder DIR [--acoustid-key KEY] [--write-mode tag|metadata|both]
                    [--max-files 200] [--use-shazam] [--no-musicbrainz] [--no-coverart]

Output (stdout, una riga JSON per evento):
  {type:"phase",      text}
  {type:"progress",   stage, current, total, file?}
  {type:"warn",       file, error}
  {type:"track",      file, identified, source, tags:{...}}
  {type:"done",       results:[...]}
"""
import sys, os, json, time, shutil, argparse, traceback, urllib.request, urllib.parse, base64

AUDIO_EXT = {'.mp3', '.flac', '.m4a', '.aac', '.ogg', '.opus', '.wma', '.wav', '.aiff', '.aif', '.alac', '.ape'}

MUSICBRAINZ_BASE = "https://musicbrainz.org/ws/2"
MUSICBRAINZ_UA = "ManiacApp/1.0 (https://github.com/markt/maniac-app)"
COVERART_BASE = "https://coverartarchive.org"
_LAST_MB_CALL = [0.0]  # rate limit 1 req/s

def _emit(obj):
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _phase(text):
    _emit({"type": "phase", "text": text})


def list_audio_files(folder, max_files):
    out = []
    for root, _, files in os.walk(folder):
        for f in files:
            ext = os.path.splitext(f)[1].lower()
            if ext in AUDIO_EXT:
                out.append(os.path.join(root, f))
                if max_files and len(out) >= max_files:
                    return out
    return out


# ─────────────────────────────────────────────────────────────────────
# Layer 1: mutagen (lettura tag presenti)
# ─────────────────────────────────────────────────────────────────────
def read_existing_tags(path):
    try:
        from mutagen import File
    except ImportError:
        return None
    try:
        m = File(path, easy=True)
        if not m:
            return None
        get = lambda k: (m.get(k) or [None])[0]
        return {
            'title':    get('title'),
            'artist':   get('artist'),
            'album':    get('album'),
            'date':     get('date'),
            'genre':    get('genre'),
            'tracknumber': get('tracknumber'),
            'albumartist': get('albumartist'),
            'duration': int(m.info.length) if hasattr(m, 'info') and m.info else None,
        }
    except Exception:
        return None


def has_complete_tags(tags):
    if not tags:
        return False
    return bool(tags.get('title') and tags.get('artist'))


# ─────────────────────────────────────────────────────────────────────
# Layer 2: AcoustID + Chromaprint
# ─────────────────────────────────────────────────────────────────────
def fingerprint_acoustid(path, api_key):
    try:
        import acoustid
    except ImportError:
        return None, "pyacoustid non installato"
    try:
        results = list(acoustid.match(api_key, path, parse=True))
        if not results:
            return None, "no match"
        # results = [(score, recording_id, title, artist), ...]
        best = max(results, key=lambda x: x[0] or 0)
        score, rid, title, artist = best
        return {
            'mbid': rid,
            'title': title,
            'artist': artist,
            'score': score,
        }, None
    except acoustid.NoBackendError:
        return None, "fpcalc (chromaprint) non trovato nel PATH"
    except acoustid.FingerprintGenerationError as e:
        return None, "fingerprint failed: " + str(e)[:120]
    except acoustid.WebServiceError as e:
        return None, "acoustid api: " + str(e)[:120]
    except Exception as e:
        return None, str(e)[:160]


# ─────────────────────────────────────────────────────────────────────
# Layer 3: MusicBrainz (no key, rate-limit 1/s)
# ─────────────────────────────────────────────────────────────────────
def _mb_throttle():
    elapsed = time.time() - _LAST_MB_CALL[0]
    if elapsed < 1.05:
        time.sleep(1.05 - elapsed)
    _LAST_MB_CALL[0] = time.time()


def _http_get_json(url, timeout=10):
    req = urllib.request.Request(url, headers={
        'User-Agent': MUSICBRAINZ_UA,
        'Accept': 'application/json',
    })
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode('utf-8'))


def fetch_musicbrainz(mbid):
    if not mbid:
        return None
    _mb_throttle()
    try:
        url = f"{MUSICBRAINZ_BASE}/recording/{mbid}?inc=artist-credits+releases+tags+isrcs+work-rels&fmt=json"
        data = _http_get_json(url)
        artists = data.get('artist-credit') or []
        artist_name = " ".join(a.get('name') or (a.get('artist') or {}).get('name') or '' for a in artists).strip()
        releases = data.get('releases') or []
        rel = releases[0] if releases else {}
        tags = data.get('tags') or []
        out = {
            'mbid': data.get('id'),
            'title': data.get('title'),
            'artist': artist_name or None,
            'album': rel.get('title'),
            'release_mbid': rel.get('id'),
            'date': data.get('first-release-date') or rel.get('date'),
            'length_ms': data.get('length'),
            'isrcs': data.get('isrcs') or [],
            'tags': sorted([t['name'] for t in tags if t.get('count', 0) > 0],
                           key=lambda n: -next((t['count'] for t in tags if t['name'] == n), 0))[:5],
        }
        return out
    except Exception as e:
        _emit({"type": "warn", "mode": "musicbrainz", "error": str(e)[:160]})
        return None


# ─────────────────────────────────────────────────────────────────────
# Layer 4: Cover Art Archive
# ─────────────────────────────────────────────────────────────────────
def fetch_cover_art(release_mbid, size='250'):
    if not release_mbid:
        return None
    try:
        url = f"{COVERART_BASE}/release/{release_mbid}/front-{size}"
        req = urllib.request.Request(url, headers={'User-Agent': MUSICBRAINZ_UA})
        with urllib.request.urlopen(req, timeout=10) as r:
            data = r.read()
            mime = r.headers.get('Content-Type', 'image/jpeg')
            return f"data:{mime};base64," + base64.b64encode(data).decode('ascii')
    except Exception:
        return None


# ─────────────────────────────────────────────────────────────────────
# Layer 5 (opzionale): Shazam fallback
# ─────────────────────────────────────────────────────────────────────
def fingerprint_shazam(path):
    try:
        from shazamio import Shazam  # type: ignore
        import asyncio
    except ImportError:
        return None, "shazamio non installato"
    try:
        async def _run():
            sh = Shazam()
            return await sh.recognize(path)
        loop = asyncio.new_event_loop()
        try:
            res = loop.run_until_complete(_run())
        finally:
            loop.close()
        track = (res or {}).get('track') or {}
        if not track:
            return None, "no match"
        return {
            'title': track.get('title'),
            'artist': track.get('subtitle'),
            'shazam_id': track.get('key'),
            'image': (track.get('images') or {}).get('coverart'),
        }, None
    except Exception as e:
        return None, str(e)[:160]


# ─────────────────────────────────────────────────────────────────────
# Tag write-back con mutagen
# ─────────────────────────────────────────────────────────────────────
def write_tags(path, tags, cover_data_url=None, do_backup=True):
    """Riscrive i metadati nel file fisico.

    tags: dict con keys 'title','artist','album','date','genre','tracknumber','albumartist'
    cover_data_url: 'data:image/jpeg;base64,...' opzionale
    Ritorna (ok, error_str_or_None).
    """
    try:
        from mutagen import File
        from mutagen.id3 import APIC, ID3
    except ImportError:
        return False, "mutagen non installato"

    backup = path + '.bak'
    if do_backup:
        try:
            shutil.copy2(path, backup)
        except Exception as e:
            return False, "backup failed: " + str(e)[:120]

    try:
        m = File(path, easy=True)
        if m is None:
            return False, "formato non supportato"
        for k in ('title', 'artist', 'album', 'date', 'genre', 'tracknumber', 'albumartist'):
            v = tags.get(k)
            if v:
                try:
                    m[k] = str(v)
                except Exception:
                    pass
        m.save()

        # Cover art (richiede l'interfaccia non-easy)
        if cover_data_url and cover_data_url.startswith('data:'):
            try:
                head, b64 = cover_data_url.split(',', 1)
                mime = head.split(';')[0].split(':')[1] if ':' in head else 'image/jpeg'
                img_bytes = base64.b64decode(b64)
                ext = os.path.splitext(path)[1].lower()
                if ext == '.mp3':
                    raw = ID3(path)
                    raw.delall('APIC')
                    raw.add(APIC(encoding=3, mime=mime, type=3, desc='Cover', data=img_bytes))
                    raw.save()
                elif ext in ('.m4a', '.mp4', '.alac'):
                    from mutagen.mp4 import MP4, MP4Cover
                    f = MP4(path)
                    fmt = MP4Cover.FORMAT_PNG if 'png' in mime else MP4Cover.FORMAT_JPEG
                    f.tags['covr'] = [MP4Cover(img_bytes, imageformat=fmt)]
                    f.save()
                elif ext == '.flac':
                    from mutagen.flac import FLAC, Picture
                    f = FLAC(path)
                    pic = Picture()
                    pic.data = img_bytes
                    pic.type = 3
                    pic.mime = mime
                    f.clear_pictures()
                    f.add_picture(pic)
                    f.save()
            except Exception as e:
                _emit({"type": "warn", "file": path, "error": "cover write: " + str(e)[:120]})

        if do_backup and os.path.exists(backup):
            try: os.unlink(backup)
            except: pass
        return True, None
    except Exception as e:
        # Restore da backup
        if do_backup and os.path.exists(backup):
            try: shutil.copy2(backup, path); os.unlink(backup)
            except: pass
        return False, str(e)[:160]


# ─────────────────────────────────────────────────────────────────────
# Orchestratore
# ─────────────────────────────────────────────────────────────────────
def identify_one(path, opts):
    """Ritorna dict con metadati riconosciuti + sorgente."""
    out = {
        'file': path,
        'identified': False,
        'source': None,
        'tags': {},
        'cover': None,
        'mbid': None,
        'confidence': None,
    }

    # Layer 1: tag esistenti
    existing = read_existing_tags(path) or {}
    if has_complete_tags(existing):
        out['tags'] = {k: v for k, v in existing.items() if v}
        out['source'] = 'mutagen'
        out['identified'] = True
        # Anche se i tag esistono, prova lookup MB per arricchire (cover, anno)

    # Layer 2: AcoustID
    fp = None
    if opts.get('use_acoustid', True) and opts.get('acoustid_key'):
        fp, err = fingerprint_acoustid(path, opts['acoustid_key'])
        if err and err != "no match":
            _emit({"type": "warn", "file": path, "error": "acoustid: " + err})
        if fp:
            out['mbid'] = fp.get('mbid')
            out['confidence'] = fp.get('score')
            if not out['identified']:
                out['tags']['title'] = fp.get('title')
                out['tags']['artist'] = fp.get('artist')
                out['source'] = 'acoustid'
                out['identified'] = True

    # Layer 3: MusicBrainz (se ho un MBID)
    if out.get('mbid') and opts.get('use_musicbrainz', True):
        mb = fetch_musicbrainz(out['mbid'])
        if mb:
            for k in ('title', 'artist', 'album', 'date'):
                if mb.get(k): out['tags'][k] = mb[k]
            if mb.get('tags'):
                out['tags']['genre'] = mb['tags'][0]
            out['release_mbid'] = mb.get('release_mbid')
            out['source'] = (out['source'] or '') + '+musicbrainz' if out['source'] else 'musicbrainz'

    # Layer 4: Cover Art (se ho un release_mbid)
    if out.get('release_mbid') and opts.get('use_coverart', True):
        cov = fetch_cover_art(out['release_mbid'])
        if cov:
            out['cover'] = cov

    # Layer 5: Shazam fallback
    if not out['identified'] and opts.get('use_shazam'):
        sh, err = fingerprint_shazam(path)
        if sh:
            out['tags']['title'] = sh.get('title')
            out['tags']['artist'] = sh.get('artist')
            out['source'] = 'shazam'
            out['identified'] = True
            if sh.get('image') and not out['cover']:
                out['cover'] = sh['image']  # url, non data-url
        elif err and err != "no match":
            _emit({"type": "warn", "file": path, "error": "shazam: " + err})

    return out


def run_music(files, opts):
    """Ciclo principale.

    opts keys:
      acoustid_key, use_acoustid, use_musicbrainz, use_coverart, use_shazam,
      write_mode ('tag'|'metadata'|'both'), write_backup
    """
    _phase("Avvio riconoscimento musicale…")
    write_mode = opts.get('write_mode', 'tag')
    do_write = write_mode in ('metadata', 'both')

    results = []
    total = len(files)
    for i, fp in enumerate(files, 1):
        _emit({"type": "progress", "stage": "musicid",
               "current": i, "total": total, "file": fp})
        try:
            r = identify_one(fp, opts)
            if do_write and r['identified']:
                ok, err = write_tags(fp, r['tags'], r.get('cover'),
                                     do_backup=opts.get('write_backup', True))
                r['written'] = ok
                if not ok:
                    r['write_error'] = err
                    _emit({"type": "warn", "file": fp, "error": "write: " + (err or '?')})
            _emit({"type": "track", **r})
            results.append({
                'id': 'mus_' + str(i),
                'label': (r['tags'].get('artist') or '?') + ' — ' + (r['tags'].get('title') or os.path.basename(fp)),
                'thumb': r.get('cover'),
                'count': 1,
                'files': [fp],
                'type': 'music',
                'identified': r['identified'],
                'source': r.get('source'),
                'mbid': r.get('mbid'),
                'tags': r['tags'],
                'localMatch': None,
                'apiMatch': {'name': r['tags'].get('artist'), 'photoUrl': r.get('cover'), 'source': r.get('source')} if r['identified'] else None,
                'nameCandidates': [],
                'written': r.get('written', False),
            })
        except Exception as e:
            _emit({"type": "warn", "file": fp, "error": str(e)[:160]})

    _phase(f"Riconoscimento musicale completato: {sum(1 for r in results if r['identified'])}/{total}")
    return results


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--folder', required=True)
    ap.add_argument('--max-files', type=int, default=200)
    ap.add_argument('--acoustid-key', default=None)
    ap.add_argument('--write-mode', default='tag', choices=['tag', 'metadata', 'both'])
    ap.add_argument('--no-acoustid', action='store_true')
    ap.add_argument('--no-musicbrainz', action='store_true')
    ap.add_argument('--no-coverart', action='store_true')
    ap.add_argument('--use-shazam', action='store_true')
    ap.add_argument('--no-backup', action='store_true')
    args = ap.parse_args()

    if not os.path.isdir(args.folder):
        _emit({"type": "error", "error": "cartella non valida: " + args.folder})
        return 2

    files = list_audio_files(args.folder, args.max_files)
    _emit({"type": "progress", "stage": "init", "total": len(files)})
    _phase(f"{len(files)} file audio trovati.")

    opts = {
        'acoustid_key': args.acoustid_key,
        'use_acoustid': not args.no_acoustid,
        'use_musicbrainz': not args.no_musicbrainz,
        'use_coverart': not args.no_coverart,
        'use_shazam': args.use_shazam,
        'write_mode': args.write_mode,
        'write_backup': not args.no_backup,
    }
    results = run_music(files, opts)
    _emit({"type": "done", "results": results})
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception as e:
        _emit({"type": "error", "error": str(e)[:200],
               "trace": traceback.format_exc()[:600]})
        sys.exit(1)
