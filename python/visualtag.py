#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""visualtag.py — la tipologia letta dalle immagini.

Prende qualche fotogramma del video, lo passa all'encoder immagini di CLIP
(ViT-B/32, ONNX) e restituisce un vettore di 512 numeri. Da solo non decide
niente: sopra questi vettori `library_organizer.py` addestra un classificatore
sulle cartelle che l'utente ha già smistato, quindi impara le SUE tipologie e
non una tassonomia decisa altrove.

Misurato sulla libreria di prova (12 tipologie, 45 esempi per classe, 4
fotogrammi): 67% di risposte esatte in generale, ma il dato che conta è la
calibrazione — sopra 0,7 di fiducia copre un terzo dei file col 97% di
precisione. Per questo il risultato viene usato solo oltre una soglia.

Il modello (335 MB) viene scaricato al primo uso in models/ml/clip/, come i pesi
di YOLO e Places365.

CLI:
  python visualtag.py embed <file> [...] [--ffmpeg PATH] [--frames 8]
  python visualtag.py model            # scarica il modello e stampa il percorso
"""
import os, sys, json, math, subprocess, urllib.request

MODEL_NAME = 'clip_vision_b32.onnx'
MODEL_URL = ('https://huggingface.co/Xenova/clip-vit-base-patch32/resolve/main/'
             'onnx/vision_model.onnx')
MODEL_MIN_BYTES = 200 * 1024 * 1024
DIM = 512
_NO_WINDOW = 0x08000000 if os.name == 'nt' else 0
_MEAN = (0.48145466, 0.4578275, 0.40821073)
_STD = (0.26862954, 0.26130258, 0.27577711)
_SESSION = None


def models_dir():
    """models/ml/clip: in packaged sta in userData (scrivibile), come per YOLO."""
    env = os.environ.get('MANIAC_MODELS_DIR')
    if env:
        return os.path.join(env, 'clip')
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.abspath(os.path.join(here, os.pardir, 'models', 'ml', 'clip'))


def model_path():
    return os.path.join(models_dir(), MODEL_NAME)


def have_model():
    p = model_path()
    try:
        return os.path.getsize(p) >= MODEL_MIN_BYTES
    except OSError:
        return False


def ensure_model(on_progress=None):
    """Scarica il modello se manca. on_progress(scaricati, totale) per la UI."""
    p = model_path()
    if have_model():
        return p
    os.makedirs(models_dir(), exist_ok=True)
    tmp = p + '.part'
    req = urllib.request.Request(MODEL_URL, headers={'User-Agent': 'Maniac/1.0'})
    with urllib.request.urlopen(req, timeout=60) as r, open(tmp, 'wb') as f:
        total = int(r.headers.get('Content-Length') or 0)
        done = 0
        while True:
            chunk = r.read(1024 * 256)
            if not chunk:
                break
            f.write(chunk)
            done += len(chunk)
            if on_progress:
                on_progress(done, total)
    os.replace(tmp, p)
    if not have_model():
        raise RuntimeError('modello scaricato incompleto')
    return p


def session():
    global _SESSION
    if _SESSION is None:
        import onnxruntime as ort
        opts = ort.SessionOptions()
        opts.intra_op_num_threads = min(8, (os.cpu_count() or 4))
        _SESSION = ort.InferenceSession(ensure_model(), opts, providers=['CPUExecutionProvider'])
    return _SESSION


def _frame(path, t, ffmpeg):
    """Un fotogramma già tagliato a 224x224 come vuole CLIP (copre e centra)."""
    import numpy as np, cv2
    args = [ffmpeg, '-v', 'error', '-ss', repr(t), '-i', path, '-frames:v', '1',
            '-vf', 'scale=224:224:force_original_aspect_ratio=increase,crop=224:224',
            '-c:v', 'bmp', '-f', 'rawvideo', '-']
    try:
        r = subprocess.run(args, capture_output=True, timeout=120, creationflags=_NO_WINDOW)
    except Exception:
        return None
    if r.returncode != 0 or not r.stdout:
        return None
    img = cv2.imdecode(np.frombuffer(r.stdout, np.uint8), cv2.IMREAD_COLOR)
    if img is None or img.shape[:2] != (224, 224):
        return None
    rgb = img[:, :, ::-1].astype(np.float32) / 255.0
    return ((rgb - np.array(_MEAN, dtype=np.float32)) / np.array(_STD, dtype=np.float32)).transpose(2, 0, 1)


def embed(path, ffmpeg=None, duration=None, frames=8):
    """Vettore medio dei fotogrammi, normalizzato. None se il video non si legge."""
    import numpy as np
    import videohash
    ffmpeg = ffmpeg or videohash.resolve_ffmpeg()
    if not ffmpeg:
        return None
    duration = duration or videohash.probe_duration(path, ffmpeg)
    if not duration or duration < 2:
        return None
    n = max(2, int(frames))
    # Dal 10% al 90%: sigle e titoli di coda non dicono nulla sulla scena.
    times = [duration * (0.1 + 0.8 * i / (n - 1)) for i in range(n)]
    batch = [f for f in (_frame(path, t, ffmpeg) for t in times) if f is not None]
    if len(batch) < 2:
        return None
    sess = session()
    name = sess.get_inputs()[0].name
    out = sess.run(None, {name: np.stack(batch)})[0]
    out = out / (np.linalg.norm(out, axis=1, keepdims=True) + 1e-8)
    v = out.mean(0)
    return (v / (np.linalg.norm(v) + 1e-8)).astype(np.float32)


def pack(vec):
    import numpy as np
    return np.asarray(vec, dtype=np.float32).tobytes()


def unpack(blob):
    import numpy as np
    return np.frombuffer(blob, dtype=np.float32)


def main(argv):
    if not argv:
        print(json.dumps({'ok': False, 'error': 'uso: visualtag.py embed <file>… | model'}))
        return 2
    ffmpeg = None
    if '--ffmpeg' in argv:
        i = argv.index('--ffmpeg')
        ffmpeg = argv[i + 1] if i + 1 < len(argv) else None
        argv = argv[:i] + argv[i + 2:]
    frames = 8
    if '--frames' in argv:
        i = argv.index('--frames')
        frames = int(argv[i + 1]) if i + 1 < len(argv) else 8
        argv = argv[:i] + argv[i + 2:]
    if argv[0] == 'model':
        print(json.dumps({'ok': True, 'path': ensure_model(), 'bytes': os.path.getsize(model_path())}))
        return 0
    out = []
    for p in argv[1:]:
        v = embed(p, ffmpeg, frames=frames)
        out.append({'path': p, 'dim': int(v.shape[0]) if v is not None else 0,
                    'ok': v is not None})
    print(json.dumps({'ok': True, 'items': out}, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
