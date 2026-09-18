#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Smoke test per library_organizer.py e videohash.py.

Niente rete e niente ffmpeg: StashDB e impronta visiva restano spenti, si
verificano parsing dei nomi, rilevamento struttura, apprendimento tipologie e
piano di spostamento su una libreria finta creata in una cartella temporanea.

Uso:  python test_library_organizer_smoke.py [--keep]
Esce con code 0 se tutto OK, 1 altrimenti.
"""
import os, sys, json, struct, tempfile, shutil, subprocess, argparse

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import videohash  # noqa: E402
import library_organizer as lo  # noqa: E402

failed = []


def check(name, cond, detail=''):
    print(('[ok]   ' if cond else '[FAIL] ') + name + ('' if cond or detail == '' else ' → %s' % (detail,)))
    if not cond:
        failed.append(name)


def touch(root, rel, size=200 * 1024, seed=0):
    p = os.path.join(root, *rel.split('/'))
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, 'wb') as f:
        f.write(bytes(((i * 31 + seed * 7) % 251) for i in range(size)))
    return p


def test_parse():
    p = lo.parse_filename('EPORNER.COM - [pjvgTNi8MLs] Vina Sky - OF Gangbang MMMF Bukkake.mp4 (720).mp4')
    check('parse: titolo senza sito, ID e risoluzione',
          'EPORNER' not in p['title'] and 'pjvg' not in p['title'] and '720' not in p['title'], p['title'])
    check('parse: "Vina Sky" è la prima finestra-nome',
          bool(p['windows']) and p['windows'][0]['text'] == 'Vina Sky', p['windows'])
    check('parse: parole di tipologia', 'w:gangbang' in p['tokens'] and 'w:bukkake' in p['tokens'], p['tokens'])

    p = lo.parse_filename('legalporno.Veronica.Leal.Fuck.My.Big.Rose.4on1.ATM.DAP.AH243.blonde.anal.mp4')
    check('parse: codice scena AH243', p['codes'] == ['AH243'], p['codes'])
    check('parse: 4on1 riconosciuto', 'x:non1' in p['tokens'], p['tokens'])

    p = lo.parse_filename('Movie.H264.X265.DDP51.HDR10.1080p.mp4')
    check('parse: codec non scambiati per codici', p['codes'] == [], p['codes'])

    p = lo.parse_filename('wicked.24.05.17.veronica.leal.passion.canvas.mp4')
    check('parse: data aa.mm.gg', p['date'] == '2024-05-17', p['date'])

    p = lo.parse_filename('EPORNER.COM - [9afSWJVw1Im] S0fia Sm1th pregnant fuck (1080).mp4')
    check('parse: nomi offuscati tornano leggibili', 'sofia' in p['ordered'] and 'smith' in p['ordered'], p['ordered'])

    p = lo.parse_filename('68c4eb094c992ff309bd1fad (1).mp4')
    check('parse: nome esadecimale', p['hexname'] and 'shape:hex' in p['tokens'])

    check('parse: ID senza cifre', lo._is_idlike('yYzkAsUifEk') and lo._is_idlike('tXLgAnSQSGj')
          and not lo._is_idlike('DoublePenetration') and not lo._is_idlike('AnnaDeVille'))


def test_hashes(tmp):
    p = touch(tmp, 'h.bin', size=300 * 1024, seed=3)
    with open(p, 'rb') as f:
        data = f.read()
    expect = (sum(struct.unpack('<8192Q', data[:65536])) + sum(struct.unpack('<8192Q', data[-65536:]))
              + len(data)) & 0xFFFFFFFFFFFFFFFF
    check('oshash come Stash', videohash.oshash(p) == '%016x' % expect)
    check('mediana quickselect goimagehash (lunghezza pari: media dei due centrali)',
          videohash._quick_select_median([4.0, 1.0, 3.0, 2.0], 0, 3, 2) == 2.5)
    try:
        import numpy as np
        sprite = (np.indices((450, 800)).sum(axis=0)[:, :, None] * np.array([1, 2, 3])) % 256
        h = videohash.phash_from_sprite(sprite.astype(np.uint8))
        check('phash deterministico a 64 bit', 0 < h < 2 ** 64 and h == videohash.phash_from_sprite(sprite.astype(np.uint8)))
    except ImportError:
        print('[skip] numpy assente: phash non verificato')


def run_analyze(tmp, cfg):
    cfg_path = os.path.join(tmp, 'cfg-%s.json' % cfg['layout'])
    with open(cfg_path, 'w', encoding='utf-8') as f:
        json.dump(cfg, f)
    out = subprocess.run([sys.executable, os.path.join(HERE, 'library_organizer.py'), 'analyze',
                          '--config', cfg_path, '--cache', os.path.join(tmp, 'cache.sqlite')],
                         capture_output=True, text=True, encoding='utf-8')
    for line in out.stdout.splitlines():
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        if ev.get('type') == 'done':
            return ev
        if ev.get('type') == 'error':
            print(ev)
    print(out.stderr[-800:])
    return None


def test_library(tmp):
    root = os.path.join(tmp, 'lib')
    for i in range(16):
        touch(root, 'GANGBANG/Scene %d gangbang 5on1 dap anal.mp4' % i, seed=i)
        touch(root, 'GLORY HOLE/Clip %d gloryhole blowjob cum.mp4' % i, seed=100 + i)
    touch(root, 'BEST PORNSTAR/Veronica Leal/old scene.mp4', seed=200)
    touch(root, 'BEST PORNSTAR/Anna De Ville/old scene.mp4', seed=201)
    touch(root, 'PICS/HOT/me.mp4', seed=300)
    for i in range(16):
        touch(root, 'TOYS/Ksu Colt/Toy %d dildo insertion stretch.mp4' % i, seed=500 + i)
    touch(root, 'TOYS/June X/toy dildo solo.mp4', seed=520)
    a = touch(root, 'NEW/Veronica.Leal.Anal.Double.Gangbang.mp4', seed=400)
    b = touch(root, 'NEW/sub/amateur gloryhole blowjob fun.mp4', seed=401)
    c = touch(root, 'GANGBANG/Nuova cartella (2)/unknown clip.mp4', seed=402)
    d = touch(root, 'NEW/zzz qqq.mp4', seed=403)
    e = touch(root, 'NEW/huge dildo insertion stretch.mp4', seed=404)
    f = touch(root, 'TOYS/DA ORDINARE/unknown dildo clip.mp4', seed=405)
    # stesso contenuto di un file già smistato: deve risultare un doppione
    g = touch(root, 'NEW/Scene 3 gangbang 5on1 dap anal (copia).mp4', seed=3)

    lib = lo.build_library(root)
    roles = lib['roles']
    check('struttura: tipologia', roles.get('GANGBANG') == 'category', roles)
    check('struttura: tipologia con performer', roles.get('TOYS') == 'category_performers', roles)
    check('struttura: contenitore performer', roles.get('BEST PORNSTAR') == 'performers', roles)
    check('struttura: da smistare', roles.get('NEW') == 'unsorted', roles)
    check('struttura: foto ignorate', roles.get('PICS') == 'ignore', roles)
    check('struttura: nessun file da cartelle ignorate', not any(it['rel'].startswith('PICS') for it in lib['items']))

    base = {'root': root, 'scope': 'unsorted', 'useStashdb': False, 'usePhash': False, 'minPerformerFiles': 5,
            'useVisual': True, 'visualDownload': False}
    done = run_analyze(tmp, dict(base, layout='current'))
    check('analisi: evento finale', done is not None)
    if not done:
        return
    by = {it['path']: it for it in done['items']}
    check('analisi: solo i file da smistare', set(by) == {a, b, c, d, e, f, g}, sorted(by))
    ig = by.get(g) or {}
    check('doppioni: copia identica riconosciuta',
          (ig.get('dup') or {}).get('kind') == 'identico' and ig.get('dest') is None, ig)
    ia, ib, ic, idd = by.get(a, {}), by.get(b, {}), by.get(c, {}), by.get(d, {})
    check('schema attuale: tipologia con performer, senza performer → sua cartella da smistare',
          (by.get(e) or {}).get('destRel') == os.path.join('TOYS', 'DA ORDINARE'), by.get(e))
    check('schema attuale: già nella cartella da smistare della tipologia → resta',
          (by.get(f) or {}).get('dest') is None, by.get(f))
    check('schema attuale: performer con cartella → sua cartella',
          ia.get('destRel') == os.path.join('BEST PORNSTAR', 'Veronica Leal') and ia.get('conf') == 'certain', ia)
    check('schema attuale: tipologia imparata dai file smistati', ib.get('destRel') == 'GLORY HOLE', ib)
    check('schema attuale: eredita la tipologia della cartella',
          ic.get('destRel') == 'GANGBANG' and ic.get('conf') == 'certain', ic)
    check('schema attuale: file sconosciuto non si sposta', idd.get('dest') is None, idd)
    check('riepilogo: classificatore calibrato', bool((done['summary'] or {}).get('classifier')), done['summary'])
    check('immagini: senza modello l\'analisi prosegue senza bloccarsi',
          (done['summary'] or {}).get('visual') is None, (done['summary'] or {}).get('visual'))

    done = run_analyze(tmp, dict(base, layout='category_performer', minPerformerFiles=1))
    by = {it['path']: it for it in (done or {}).get('items', [])}
    check('tipologia\\performer: GANGBANG\\Veronica Leal',
          (by.get(a) or {}).get('destRel') == os.path.join('GANGBANG', 'Veronica Leal'), by.get(a))

    done = run_analyze(tmp, dict(base, layout='tags_only'))
    check('solo tag: nessuno spostamento', done is not None and all(not it['dest'] for it in done['items']))


def test_visual_training():
    """Il classificatore sulle immagini: niente modello e niente ffmpeg, si verifica
    solo che impari classi separabili e che la soglia esca calibrata."""
    try:
        import numpy as np
        import torch  # noqa: F401
    except ImportError:
        print('[skip] numpy/torch assenti: addestramento sulle immagini non verificato')
        return
    rng = np.random.RandomState(5)
    centers = rng.randn(3, 32).astype(np.float32)
    X, y = [], []
    for k in range(3):
        for _ in range(40):
            v = centers[k] + 0.4 * rng.randn(32).astype(np.float32)
            X.append(v / np.linalg.norm(v))
            y.append(k)
    m = lo.train_visual(np.stack(X).astype(np.float32), np.array(y), ['A', 'B', 'C'])
    check('immagini: impara classi separabili', m['accuracy'] > 0.9, m['accuracy'])
    check('immagini: soglia di fiducia calibrata', m['t90'] is not None and 0 < m['t90'] <= 1, m)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--keep', action='store_true', help='non cancella la cartella temporanea')
    args = ap.parse_args()
    tmp = tempfile.mkdtemp(prefix='maniac_liborg_')
    try:
        test_parse()
        test_hashes(tmp)
        test_library(tmp)
        test_visual_training()
    finally:
        if args.keep:
            print('[smoke] cartella: ' + tmp)
        else:
            shutil.rmtree(tmp, ignore_errors=True)
    print('\n%s' % ('TUTTO OK' if not failed else '%d FALLITI: %s' % (len(failed), ', '.join(failed))))
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
