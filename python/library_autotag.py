#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Auto-tag library: itera files, per ognuno chiama scraper_engine multi-fonte
ed emette JSONL progress + risultato consensus per file.

CLI:
  python library_autotag.py --scrapers-dir <models/scrapers>
                            --files-list <files.json>
                            [--min-votes 3] [--max-sources 6]

`files-list` è un JSON con una lista di paths.

Output progressivo (una riga JSON per evento):
  {"event":"start","total":N}
  {"event":"file","i":I,"path":P,"name":NAME}
  {"event":"result","i":I,"path":P,"consensus":{...},"raw":[...]}
  {"event":"done","matched":M,"uncertain":U}
"""
import os, sys, json, argparse, time, re

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from scraper_engine import load_scrapers, scrape_by_name, consensus  # noqa: E402


def emit(obj):
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _clean_name(path):
    """Estrae un nome utile dal filename: rimuove estensione + tag tecnici (1080p, x264, ecc.)."""
    base = os.path.splitext(os.path.basename(path))[0]
    # Rimuove pattern release tipici
    cleaners = [
        r'\b(1080p|720p|2160p|4k|480p|360p|240p|144p)\b',
        r'\b(x264|x265|h264|h265|hevc|av1|avc)\b',
        r'\b(bluray|blu-ray|webrip|webdl|web-dl|hdrip|dvdrip|brrip|cam|hdtv)\b',
        r'\b(yify|yts|rarbg|ettv|ettv-?tv|nf|amzn)\b',
        r'\b(aac|ac3|dts|opus|mp3)\b',
        r'\.(mkv|mp4|avi|mov|wmv|flv|webm|m4v|ts|mpg|mpeg)$',
        r'[\.\-_\[\]\(\)]+',
    ]
    out = base
    for rx in cleaners:
        out = re.sub(rx, ' ', out, flags=re.IGNORECASE)
    out = ' '.join(out.split())
    return out.strip() or base


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--scrapers-dir', required=True)
    ap.add_argument('--files-list', required=True)
    ap.add_argument('--min-votes', type=int, default=3)
    ap.add_argument('--max-sources', type=int, default=6)
    args = ap.parse_args()

    if not os.path.isfile(args.files_list):
        emit({'event': 'error', 'error': 'files-list non trovato'})
        return 2
    try:
        with open(args.files_list, 'r', encoding='utf-8') as f:
            files = json.load(f) or []
    except Exception as e:
        emit({'event': 'error', 'error': 'parse files-list: ' + str(e)})
        return 2

    scrapers = load_scrapers(args.scrapers_dir)
    if not scrapers:
        emit({'event': 'error', 'error': 'nessuno scraper in ' + args.scrapers_dir})
        return 2

    emit({'event': 'start', 'total': len(files), 'scrapers': len(scrapers)})

    matched = 0
    uncertain = 0
    for i, p in enumerate(files):
        name = _clean_name(p)
        emit({'event': 'file', 'i': i, 'path': p, 'name': name})
        try:
            results = scrape_by_name(scrapers, name,
                                      max_sources=args.max_sources,
                                      per_source_timeout=8)
            cs = consensus(results, min_votes=args.min_votes)
            status = 'matched' if cs.get('Title') else 'uncertain'
            if status == 'matched': matched += 1
            else: uncertain += 1
            emit({'event': 'result', 'i': i, 'path': p,
                  'status': status, 'consensus': cs,
                  'sources': len(results)})
        except Exception as e:
            uncertain += 1
            emit({'event': 'result', 'i': i, 'path': p,
                  'status': 'error', 'error': str(e)[:200]})

    emit({'event': 'done', 'matched': matched, 'uncertain': uncertain,
          'total': len(files)})
    return 0


if __name__ == '__main__':
    try: sys.exit(main())
    except Exception as e:
        emit({'event': 'error', 'error': str(e)})
        sys.exit(1)
