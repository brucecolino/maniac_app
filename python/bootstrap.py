#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Diagnostica ambiente venv311 per Maniac.
Uso: python bootstrap.py --check [--deep]
Stampa una riga JSON finale con lo stato di ciascun modulo e delle dipendenze native.

--check da solo guarda se i pacchetti sono installati e con che versione SENZA importarli:
pochi millisecondi. Importare davvero TensorFlow, PyTorch, DeepFace & co. costava 8-9 s e
~600 MB di RAM a ogni avvio dell'app. --deep li importa (pulsante "Ricontrolla").
"""
import sys, os, json, shutil, argparse, importlib, importlib.util, importlib.metadata

def _ml_base_dir():
    # In packaged usa MANIAC_MODELS_DIR (userData, scrivibile); in dev <app>/models/ml.
    d = os.environ.get("MANIAC_MODELS_DIR")
    if not d:
        here = os.path.dirname(os.path.abspath(__file__))
        root = os.path.abspath(os.path.join(here, os.pardir))
        d = os.path.join(root, "models", "ml")
    return d

MODULES = [
    ("deepface",       "deepface"),
    ("tf_keras",       "tf-keras"),
    ("cv2",            "opencv-python"),
    ("torch",          "torch"),
    ("torchvision",    "torchvision"),
    ("faster_whisper", "faster-whisper"),
    ("argostranslate", "argos-translate"),
    ("ultralytics",    "ultralytics"),
    ("pymediainfo",    "pymediainfo"),
    ("yt_dlp",         "yt-dlp"),
    ("onnxruntime",    "onnxruntime"),
]

OPTIONAL_MODULES = [
    ("mutagen",  "mutagen"),
    ("acoustid", "pyacoustid"),
]

DEEP = False


def probe_module(mod, dist=None):
    if not DEEP:
        try:
            if importlib.util.find_spec(mod) is None:
                return {"ok": False, "error": "non installato"}
        except Exception as e:
            return {"ok": False, "error": str(e)[:200]}
        ver = None
        for name in (dist, mod):
            if not name:
                continue
            try:
                ver = importlib.metadata.version(name)
                break
            except Exception:
                pass
        return {"ok": True, "version": ver}
    try:
        m = importlib.import_module(mod)
        ver = getattr(m, "__version__", None)
        return {"ok": True, "version": ver}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}

def probe_ffmpeg():
    path = shutil.which("ffmpeg")
    return {"ok": bool(path), "path": path}

def probe_fpcalc():
    """Chromaprint binary, richiesto da pyacoustid per il fingerprinting."""
    path = shutil.which("fpcalc")
    return {"ok": bool(path), "path": path,
            "hint": None if path else "Scarica da https://acoustid.org/chromaprint e aggiungi al PATH"}

def probe_argos_packs():
    try:
        # Stessa cartella usata da subtitles.py (MANIAC_MODELS_DIR se presente).
        argos_dir = os.path.join(_ml_base_dir(), "argos")
        os.environ.setdefault("ARGOS_PACKAGES_DIR", argos_dir)
        os.environ.setdefault("ARGOS_TRANSLATE_PACKAGE_DIR", argos_dir)
        if not DEEP:
            # Senza importare argostranslate: ogni pacchetto è una cartella con metadata.json.
            pairs = []
            for d in (os.listdir(argos_dir) if os.path.isdir(argos_dir) else []):
                meta = os.path.join(argos_dir, d, "metadata.json")
                try:
                    with open(meta, encoding="utf-8") as f:
                        m = json.load(f)
                    pairs.append(f"{m.get('from_code')}->{m.get('to_code')}")
                except Exception:
                    pass
            return {"ok": True, "count": len(pairs), "pairs": pairs, "dir": argos_dir}
        from argostranslate import package
        installed = package.get_installed_packages()
        return {"ok": True, "count": len(installed),
                "pairs": [f"{p.from_code}->{p.to_code}" for p in installed],
                "dir": argos_dir}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}

def _project_tools_dir():
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(os.path.abspath(os.path.join(here, os.pardir)), "tools")

def probe_external_tools():
    """Cerca ExifTool / MediaInfo CLI / ffmpeg prima nel /tools locale, poi sul PATH."""
    import shutil, subprocess
    tools = {}
    tools_dir = _project_tools_dir()
    locals_ = {
        "exiftool":  os.path.join(tools_dir, "exiftool", "exiftool.exe"),
        "mediainfo": os.path.join(tools_dir, "mediainfo", "MediaInfo.exe"),
        "ffmpeg":    os.path.join(tools_dir, "ffmpeg", "bin", "ffmpeg.exe"),
        "ffprobe":   os.path.join(tools_dir, "ffmpeg", "bin", "ffprobe.exe"),
    }
    for name in ("exiftool", "mediainfo", "ffmpeg", "ffprobe"):
        local = locals_[name]
        which = local if os.path.exists(local) else shutil.which(name)
        ver = None
        if which:
            try:
                args = [which, "-ver"] if name == "exiftool" \
                    else [which, "--Version"] if name == "mediainfo" \
                    else [which, "-version"]
                r = subprocess.run(args, capture_output=True, text=True, timeout=5)
                first = (r.stdout or r.stderr or "").splitlines()
                ver = first[0].strip()[:120] if first else None
            except Exception:
                pass
        tools[name] = {"present": bool(which), "path": which, "version": ver,
                       "bundled": which == local and os.path.exists(local)}
    return tools

def probe_ml_weights():
    """Stato dei pesi ML usati da analyze.py (yolov8n, places365)."""
    ml = _ml_base_dir()
    items = {
        "yolov8n":          os.path.join(ml, "yolov8n.pt"),
        "places365_resnet": os.path.join(ml, "resnet18_places365.pth.tar"),
        "places365_cats":   os.path.join(ml, "categories_places365.txt"),
    }
    out = {}
    for k, p in items.items():
        try:
            sz = os.path.getsize(p) if os.path.exists(p) else 0
        except OSError:
            sz = 0
        out[k] = {"present": sz > 0, "path": p, "bytes": sz}
    out["dir"] = ml
    return out

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--deep", action="store_true", help="importa davvero i moduli (lento)")
    args = ap.parse_args()
    global DEEP
    DEEP = args.deep

    result = {
        "python": sys.version.split()[0],
        "executable": sys.executable,
        "platform": sys.platform,
        "deep": DEEP,
        "modules":  {pip_name: probe_module(imp, pip_name) for imp, pip_name in MODULES},
        "optional": {pip_name: probe_module(imp, pip_name) for imp, pip_name in OPTIONAL_MODULES},
        "ffmpeg": probe_ffmpeg(),
        "fpcalc": probe_fpcalc(),
        "argos_packs": probe_argos_packs(),
        "ml_weights": probe_ml_weights(),
        "external_tools": probe_external_tools(),
    }
    # all_ok: i moduli "core" (deepface/cv2/torch/yolo) devono esserci.
    # whisper/argos/pymediainfo sono opzionali per le feature avanzate.
    core = ("deepface", "tf-keras", "opencv-python", "torch",
            "torchvision", "ultralytics", "yt-dlp")
    result["all_ok"] = (
        all(result["modules"][k]["ok"] for k in core)
        and result["ffmpeg"]["ok"]
    )
    print(json.dumps(result))
    return 0

if __name__ == "__main__":
    sys.exit(main())
