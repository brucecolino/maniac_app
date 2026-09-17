#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Impronte video compatibili con Stash / StashDB.

OSHASH  somma a 64 bit dei primi e degli ultimi 64 KiB del file (uint64
        little-endian) più la dimensione. Identifica copie identiche byte per
        byte: istantaneo anche su dischi lenti.

PHASH   impronta percettiva: regge ricodifiche, cambi di risoluzione e
        watermark. Replica esattamente l'algoritmo di Stash:
          · 25 fotogrammi fra il 5% e il 95% della durata, larghi 160 px
            (ffmpeg `-ss t -i file -vf scale=160:-2`, output BMP);
          · sprite 5x5;
          · riduzione 64x64 identica a nfnt/resize Bilinear (pesi interi a
            8 bit, doppia passata orizzontale → verticale, troncamenti Go);
          · scala di grigi, DCT-II separabile, 8x8 in alto a sinistra;
          · mediana calcolata col quickselect di goimagehash — non è la
            mediana statistica, e usarne un'altra sposta un bit su tre file
            su tre.
        Verificato contro le impronte PHASH pubblicate su StashDB: distanza 0.

CLI:
  python videohash.py oshash <file> [...]
  python videohash.py phash  <file> [...] [--ffmpeg PATH]
"""
import os, sys, re, json, math, struct, shutil, subprocess
from concurrent.futures import ThreadPoolExecutor

CHUNK = 64 * 1024
_NO_WINDOW = 0x08000000 if os.name == 'nt' else 0


def resolve_ffmpeg(explicit=None):
    if explicit and os.path.isfile(explicit):
        return explicit
    env = os.environ.get('MANIAC_FFMPEG')
    if env and os.path.isfile(env):
        return env
    here = os.path.dirname(os.path.abspath(__file__))
    exe = 'ffmpeg.exe' if os.name == 'nt' else 'ffmpeg'
    bundled = os.path.join(here, os.pardir, 'tools', 'ffmpeg', 'bin', exe)
    if os.path.isfile(bundled):
        return os.path.abspath(bundled)
    return shutil.which('ffmpeg')


def oshash(path):
    size = os.path.getsize(path)
    n = min(CHUNK, size)
    if n <= 0:
        return None
    with open(path, 'rb') as f:
        head = f.read(n)
        f.seek(-n, 2)
        tail = f.read(n)
    buf = head + tail
    count = len(buf) // 8
    total = sum(struct.unpack('<%dQ' % count, buf[:count * 8]))
    return '%016x' % ((total + size) & 0xFFFFFFFFFFFFFFFF)


_DUR_RX = re.compile(r'Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)')


def probe_duration(path, ffmpeg=None):
    """Durata del contenitore in secondi, arrotondata al centesimo come Stash."""
    ffmpeg = ffmpeg or resolve_ffmpeg()
    if ffmpeg:
        try:
            r = subprocess.run([ffmpeg, '-hide_banner', '-i', path], capture_output=True,
                               text=True, encoding='utf-8', errors='replace',
                               timeout=60, creationflags=_NO_WINDOW)
            m = _DUR_RX.search(r.stderr or '')
            if m:
                d = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
                if d > 0:
                    return math.floor(d * 100 + 0.5) / 100
        except Exception:
            pass
    try:
        from pymediainfo import MediaInfo
        for tr in MediaInfo.parse(path).tracks:
            if tr.track_type == 'General' and tr.duration:
                return math.floor(float(tr.duration) / 10 + 0.5) / 100
    except Exception:
        pass
    return None


def _frame(path, t, ffmpeg):
    import numpy as np, cv2
    args = [ffmpeg, '-v', 'error', '-y', '-ss', repr(t), '-i', path, '-frames:v', '1',
            '-vf', 'scale=160:-2', '-c:v', 'bmp', '-f', 'rawvideo', '-']
    try:
        r = subprocess.run(args, capture_output=True, timeout=120, creationflags=_NO_WINDOW)
    except Exception:
        return None
    if r.returncode != 0 or not r.stdout:
        return None
    img = cv2.imdecode(np.frombuffer(r.stdout, np.uint8), cv2.IMREAD_COLOR)
    return None if img is None else img[:, :, ::-1]


def _weights8(out_len, in_len):
    import numpy as np
    scale = in_len / out_len
    taps = 2 * max(int(math.ceil(scale)), 1)
    factor = min(1.0 / scale, 1.0)
    coeffs = np.zeros((out_len, taps), dtype=np.int64)
    starts = np.zeros(out_len, dtype=np.int64)
    for y in range(out_len):
        interp = scale * (y + 0.5) - 0.5
        start = math.trunc(interp) - taps // 2 + 1
        interp -= start
        starts[y] = start
        for i in range(taps):
            v = abs((interp - i) * factor)
            coeffs[y, i] = math.trunc((1.0 - v) * 256) if v <= 1 else 0
    return coeffs, starts, taps


def _resample(arr, out_len, axis):
    import numpy as np
    in_len = arr.shape[axis]
    coeffs, starts, taps = _weights8(out_len, in_len)
    idx = np.clip(starts[:, None] + np.arange(taps)[None, :], 0, in_len - 1)
    moved = np.moveaxis(arr.astype(np.int64), axis, -1)
    val = (moved[..., idx] * coeffs).sum(-1)
    out = np.clip(val // coeffs.sum(1), 0, 255)
    return np.moveaxis(out, -1, axis)


def _quick_select_median(seq, low, hi, k):
    if low == hi:
        return seq[k]
    while low < hi:
        pivot = low // 2 + hi // 2
        pv = seq[pivot]
        store = low
        seq[pivot], seq[hi] = seq[hi], seq[pivot]
        for i in range(low, hi):
            if seq[i] < pv:
                seq[store], seq[i] = seq[i], seq[store]
                store += 1
        seq[hi], seq[store] = seq[store], seq[hi]
        if k <= store:
            hi = store
        else:
            low = store + 1
    if len(seq) % 2 == 0:
        return seq[k - 1] / 2 + seq[k] / 2
    return seq[k]


def phash_from_sprite(sprite_rgb):
    import numpy as np
    from scipy.fft import dct
    small = _resample(_resample(sprite_rgb, 64, 1), 64, 0).astype(np.float64)
    gray = 0.299 * small[:, :, 0] + 0.587 * small[:, :, 1] + 0.114 * small[:, :, 2]
    coeffs = dct(dct(gray, axis=1), axis=0)
    flat = coeffs[:8, :8].reshape(-1).tolist()
    median = _quick_select_median(list(flat), 0, 63, 32)
    value = 0
    for i, p in enumerate(flat):
        if p > median:
            value |= 1 << (63 - i)
    return value


def phash(path, ffmpeg=None, duration=None, workers=4):
    """Ritorna (phash_hex | None, durata). Hex senza zeri iniziali, come Stash."""
    import numpy as np
    ffmpeg = ffmpeg or resolve_ffmpeg()
    if not ffmpeg:
        return None, duration
    duration = duration or probe_duration(path, ffmpeg)
    if not duration or duration < 1:
        return None, duration
    times = [0.05 * duration + i * ((0.9 * duration) / 25) for i in range(25)]
    with ThreadPoolExecutor(max(1, int(workers))) as ex:
        frames = list(ex.map(lambda t: _frame(path, t, ffmpeg), times))
    if any(f is None for f in frames):
        return None, duration
    h, w = frames[0].shape[:2]
    sprite = np.zeros((h * 5, w * 5, 3), np.uint8)
    for i, fr in enumerate(frames):
        x, y = w * (i % 5), h * (i // 5)
        part = fr[:h, :w]
        sprite[y:y + part.shape[0], x:x + part.shape[1]] = part
    return format(phash_from_sprite(sprite), 'x'), duration


def hamming(a, b):
    try:
        return bin(int(a, 16) ^ int(b, 16)).count('1')
    except Exception:
        return 64


def main(argv):
    if len(argv) < 2 or argv[0] not in ('oshash', 'phash'):
        print(json.dumps({'ok': False, 'error': 'uso: videohash.py oshash|phash <file>... [--ffmpeg PATH]'}))
        return 2
    ffmpeg = None
    if '--ffmpeg' in argv:
        i = argv.index('--ffmpeg')
        ffmpeg = argv[i + 1] if i + 1 < len(argv) else None
        argv = argv[:i] + argv[i + 2:]
    out = []
    for p in argv[1:]:
        try:
            if argv[0] == 'oshash':
                out.append({'path': p, 'oshash': oshash(p)})
            else:
                h, d = phash(p, ffmpeg)
                out.append({'path': p, 'phash': h, 'duration': d})
        except Exception as e:
            out.append({'path': p, 'error': str(e)[:200]})
    print(json.dumps({'ok': True, 'items': out}, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
