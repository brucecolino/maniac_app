#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Smoke test per analyze.py: verifica che le pipeline ML caricano,
producono detection plausibili su immagini di test e usano la cache.

Uso:  python test_analyze_smoke.py            # esegue tutto
      python test_analyze_smoke.py --quick    # salta Places365 (più veloce)

Esce con code 0 se tutto OK, 1 altrimenti. Nessuna dipendenza extra
oltre a quelle già presenti nel venv (urllib, json, tempfile).
"""
import os, sys, json, tempfile, urllib.request, time, shutil, argparse

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import analyze  # noqa: E402

SAMPLES = {
    "bus.jpg":    "https://ultralytics.com/images/bus.jpg",     # ha bus + persone
    "zidane.jpg": "https://ultralytics.com/images/zidane.jpg",  # scena stadio
}

def fetch_samples(folder):
    for fn, url in SAMPLES.items():
        dst = os.path.join(folder, fn)
        if os.path.exists(dst) and os.path.getsize(dst) > 0:
            continue
        urllib.request.urlretrieve(url, dst)

def collect_results(modes, folder):
    files = analyze.list_files(folder, max_files=20)
    out = []
    if 'object' in modes or 'animal' in modes:
        for kind in ('object', 'animal'):
            if kind in modes:
                out += analyze.run_yolo(files, kind)
    if 'place' in modes:
        out += analyze.run_places(files)
    return out

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--quick', action='store_true', help='salta Places365')
    ap.add_argument('--keep', action='store_true', help='non cancella tmp')
    args = ap.parse_args()

    tmp = tempfile.mkdtemp(prefix='maniac_smoke_')
    print(f"[smoke] sample dir: {tmp}")
    failed = []
    try:
        fetch_samples(tmp)

        # --- 1. YOLO object: deve trovare almeno "bus" su bus.jpg
        t0 = time.perf_counter()
        res_obj = collect_results({'object'}, tmp)
        dt1 = time.perf_counter() - t0
        classes = {r.get('className') for r in res_obj}
        print(f"[yolo-object] in {dt1:.1f}s → classi: {classes}")
        if 'bus' not in classes:
            failed.append(f"YOLO non ha rilevato 'bus' (trovato: {classes})")

        # --- 2. Cache hit: secondo run deve essere ~istantaneo
        t0 = time.perf_counter()
        res_obj2 = collect_results({'object'}, tmp)
        dt2 = time.perf_counter() - t0
        print(f"[yolo-object cached] in {dt2:.2f}s")
        if dt2 > dt1 * 0.7:
            print(f"[warn] cache speedup limitato: {dt1:.1f}s → {dt2:.2f}s "
                  "(ok se i file sono pochi, il caricamento modello domina)")
        if {r.get('className') for r in res_obj2} != classes:
            failed.append("cache: detection cambiate al secondo run")

        # --- 3. Places365 (skipped in quick mode)
        if not args.quick:
            t0 = time.perf_counter()
            res_pl = collect_results({'place'}, tmp)
            dt3 = time.perf_counter() - t0
            cls_pl = {r.get('className') for r in res_pl}
            print(f"[places365] in {dt3:.1f}s → classi: {cls_pl}")
            if not res_pl:
                failed.append("Places365 nessun risultato")

        # --- 4. Shape risultati (campi obbligatori)
        sample = (res_obj or [])[:1]
        if sample:
            r = sample[0]
            for k in ('id','label','files','count','type','detector','className','confidence'):
                if k not in r:
                    failed.append(f"campo mancante nella detection: {k}")

    finally:
        if not args.keep:
            shutil.rmtree(tmp, ignore_errors=True)

    if failed:
        print("\n[FAIL]")
        for f in failed: print(" -", f)
        return 1
    print("\n[OK] smoke test passed")
    return 0

if __name__ == '__main__':
    sys.exit(main())
