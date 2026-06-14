#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Diagnostica ambiente venv311 per Maniac.
Uso: python bootstrap.py --check
Stampa una riga JSON finale con lo stato di ciascun modulo e delle dipendenze native.
"""
import sys, os, json, shutil, argparse, importlib

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
]

OPTIONAL_MODULES = [
    ("mutagen",  "mutagen"),
    ("acoustid", "pyacoustid"),
]

def probe_module(mod):
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
    args = ap.parse_args()

    result = {
        "python": sys.version.split()[0],
        "executable": sys.executable,
        "platform": sys.platform,
        "modules":  {pip_name: probe_module(imp) for imp, pip_name in MODULES},
        "optional": {pip_name: probe_module(imp) for imp, pip_name in OPTIONAL_MODULES},
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
