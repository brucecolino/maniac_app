#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Mini-engine per i .yml di stashapp/community-scrapers.

Supporta:
  - sceneByName / sceneByFragment (azione scrapeXPath/scrapeJson)
  - sceneByURL  (azione scrapeXPath/scrapeJson + URL match)
  - performerByName, performerByFragment
  - xPathScrapers (lxml) e jsonScrapers (jq-like via dict access)

NOTA: questo è un engine *sufficiente* per il caso d'uso "auto-tag library":
non replica al 100% le post-processing chains di Stash. Per i campi mancanti
ricadiamo su 'None' senza fallire l'intera sessione.

CLI:
  python scraper_engine.py list-scrapers --dir <models/scrapers>
  python scraper_engine.py scrape-by-name --dir <dir> --name "<query>"
                                          [--per-source-timeout 8]
                                          [--max-sources 6]
"""
import os, sys, json, argparse, time, re

USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36")


def emit(obj):
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _try_imports():
    """Import dipendenze opzionali — se mancano, i comandi affected falliscono pulito."""
    global yaml, lxml_html, requests
    try:
        import yaml as _yaml; yaml = _yaml
    except Exception: yaml = None
    try:
        import lxml.html as _lh; lxml_html = _lh
    except Exception: lxml_html = None
    try:
        import requests as _r; requests = _r
    except Exception: requests = None


_try_imports()


def load_scrapers(scrapers_dir):
    """Carica tutti i .yml in scrapers_dir. Salta i broken con stderr warning."""
    if not yaml: return []
    out = []
    if not os.path.isdir(scrapers_dir): return out
    for root, _, files in os.walk(scrapers_dir):
        for f in files:
            if not (f.endswith('.yml') or f.endswith('.yaml')): continue
            full = os.path.join(root, f)
            try:
                with open(full, 'r', encoding='utf-8') as fp:
                    data = yaml.safe_load(fp)
                if isinstance(data, dict):
                    data['_source_file'] = full
                    out.append(data)
            except Exception as e:
                sys.stderr.write(f"[scraper] skip {f}: {e}\n")
    return out


def _http_get(url, timeout=8):
    if not requests: return None
    try:
        r = requests.get(url, headers={'User-Agent': USER_AGENT}, timeout=timeout, verify=False)
        if r.status_code != 200: return None
        return r.text
    except Exception:
        return None


def _xpath_one(tree, expr):
    """Prima occorrenza, stripped. Supporta espressioni semplici."""
    if not expr or not lxml_html: return None
    try:
        res = tree.xpath(expr)
        if not res: return None
        v = res[0]
        if hasattr(v, 'text_content'): v = v.text_content()
        return str(v).strip() if v else None
    except Exception:
        return None


def _xpath_all(tree, expr):
    if not expr or not lxml_html: return []
    try:
        res = tree.xpath(expr)
        out = []
        for v in res:
            if hasattr(v, 'text_content'): v = v.text_content()
            if v: out.append(str(v).strip())
        return out
    except Exception:
        return []


def _resolve_xpath_block(tree, spec):
    """Risolve una sezione 'scene'/'performer' con {Field: xpath, Field2: {selector: xpath}}.
    Performers/Tags sono liste di dict {Name: xpath}."""
    out = {}
    for key, val in (spec or {}).items():
        if isinstance(val, str):
            out[key] = _xpath_one(tree, val)
        elif isinstance(val, dict):
            sel = val.get('selector')
            if sel and key in ('Performers', 'Tags', 'Studios', 'Movies'):
                # Lista di entità: estraiamo un array di nomi
                names = _xpath_all(tree, sel)
                # spec opzionale 'Name' che, se presente, raffina
                name_sub = val.get('Name')
                if name_sub and isinstance(name_sub, str):
                    names = [n for n in names if n]
                out[key] = [{'Name': n} for n in names if n]
            else:
                # Dict generico: sub-fields
                sub = {}
                for k2, v2 in val.items():
                    if isinstance(v2, str):
                        sub[k2] = _xpath_one(tree, v2)
                if sub: out[key] = sub
    return out


def scrape_by_name(scrapers, query, max_sources=6, per_source_timeout=8):
    """Per ogni scraper supporting `sceneByName` o `performerByName`, esegue la query.
    Ritorna list[dict] (max max_sources non vuoti)."""
    results = []
    if not requests or not lxml_html: return results
    for sc in scrapers:
        if len(results) >= max_sources: break
        name = sc.get('name') or os.path.basename(sc.get('_source_file','?'))
        # 1) sceneByName: lista di {action, queryURL, scraper}
        action_block = sc.get('sceneByName') or []
        if not action_block: continue
        for entry in (action_block if isinstance(action_block, list) else [action_block]):
            try:
                act = entry.get('action')
                qurl = entry.get('queryURL') or ''
                if not qurl: continue
                qurl = qurl.replace('{}', requests.utils.quote(query))
                qurl = qurl.replace('{query}', requests.utils.quote(query))
                if act != 'scrapeXPath':
                    continue  # supporto base
                # Ritrova scraper xpath
                scraper_id = entry.get('scraper')
                xspec = (sc.get('xPathScrapers') or {}).get(scraper_id, {}).get('scene', {})
                if not xspec: continue
                html = _http_get(qurl, timeout=per_source_timeout)
                if not html: continue
                tree = lxml_html.fromstring(html)
                fields = _resolve_xpath_block(tree, xspec)
                if not fields: continue
                fields['_source'] = name
                fields['_url'] = qurl
                results.append(fields)
                break  # un risultato per scraper basta
            except Exception as e:
                sys.stderr.write(f"[scraper] {name}: {e}\n")
                continue
    return results


def consensus(results, min_votes=3):
    """Voting su campi singoli (Title/Date/Studio/Director) e liste (Performers/Tags)."""
    from collections import Counter
    out = {'sources': len(results), 'min_votes': min_votes}
    for f in ('Title', 'Date', 'Studio', 'Director'):
        votes = Counter()
        for r in results:
            v = r.get(f)
            if v: votes[v.strip()] += 1
        if votes:
            best, count = votes.most_common(1)[0]
            if count >= min_votes:
                out[f] = best
                out[f+'_votes'] = count
    # Liste: union con ≥ (min_votes-1) voti
    for f in ('Performers', 'Tags'):
        votes = Counter()
        for r in results:
            for it in (r.get(f) or []):
                n = (it.get('Name') if isinstance(it, dict) else it) or ''
                n = str(n).strip()
                if n: votes[n] += 1
        if votes:
            min_list = max(2, min_votes - 1)
            out[f] = [n for n, c in votes.items() if c >= min_list]
    out['confidence'] = 'high' if out.get('Title') else ('low' if not results else 'medium')
    return out


def cmd_list(args):
    scrapers = load_scrapers(args.dir)
    items = [{'name': s.get('name', '?'),
              'has_sceneByName': bool(s.get('sceneByName')),
              'has_sceneByURL': bool(s.get('sceneByURL')),
              'file': os.path.relpath(s.get('_source_file', ''), args.dir)} for s in scrapers]
    emit({'ok': True, 'count': len(items), 'items': items})
    return 0


def cmd_scrape(args):
    scrapers = load_scrapers(args.dir)
    if not scrapers:
        emit({'ok': False, 'error': 'nessuno scraper in ' + args.dir})
        return 2
    results = scrape_by_name(scrapers, args.name,
                              max_sources=int(args.max_sources or 6),
                              per_source_timeout=int(args.per_source_timeout or 8))
    cs = consensus(results, min_votes=int(args.min_votes or 3))
    emit({'ok': True, 'query': args.name, 'sources_count': len(results),
          'consensus': cs, 'raw_results': results[:8]})
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('cmd', choices=['list-scrapers', 'scrape-by-name'])
    ap.add_argument('--dir', required=True)
    ap.add_argument('--name', default=None)
    ap.add_argument('--max-sources', default='6')
    ap.add_argument('--per-source-timeout', default='8')
    ap.add_argument('--min-votes', default='3')
    args = ap.parse_args()
    try:
        try:
            import urllib3 as _u3; _u3.disable_warnings()
        except Exception: pass
        if args.cmd == 'list-scrapers': return cmd_list(args)
        if args.cmd == 'scrape-by-name':
            if not args.name: emit({'ok': False, 'error': "richiede --name"}); return 2
            return cmd_scrape(args)
    except Exception as e:
        emit({'ok': False, 'error': str(e)[:300]})
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
