#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
subtitles.py — Sottotitoli automatici (faster-whisper) + traduzione (argostranslate).

CLI (richiamato dal bridge IPC):
    python subtitles.py generate --video <path> [--lang auto|it|en|...] [--model base]
                                  [--out <srt-path>]
    python subtitles.py translate --srt <path> --from <code> --to <code>
                                   [--out <srt-path>]
    python subtitles.py list-langs
    python subtitles.py install-lang --from <code> --to <code>

Output: una riga JSON per evento sullo stdout (prefissi "phase","progress","done","error")
così il main process può fare progress streaming come per analyze.py.

faster-whisper e argostranslate sono lazy-loaded: l'import avviene solo nel
sotto-comando che li serve, così `list-langs` non carica torch.
"""
import sys, os, json, argparse, time, traceback, re

def _emit(o):
    sys.stdout.write(json.dumps(o, ensure_ascii=False) + "\n")
    sys.stdout.flush()

def _phase(text):  _emit({"type": "phase", "text": text})
def _err(text):    _emit({"type": "error", "error": text})

# ─────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────
def _ts(seconds):
    if seconds is None or seconds < 0: seconds = 0
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int(round((seconds - int(seconds)) * 1000))
    if ms == 1000:
        s += 1; ms = 0
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

def _ml_base_dir():
    # In packaged usa MANIAC_MODELS_DIR (userData, scrivibile); in dev <app>/models/ml.
    d = os.environ.get("MANIAC_MODELS_DIR")
    if not d:
        here = os.path.dirname(os.path.abspath(__file__))
        root = os.path.abspath(os.path.join(here, os.pardir))
        d = os.path.join(root, "models", "ml")
    return d

def _whisper_models_dir():
    d = os.path.join(_ml_base_dir(), "whisper")
    os.makedirs(d, exist_ok=True)
    return d

def _argos_models_dir():
    d = os.path.join(_ml_base_dir(), "argos")
    os.makedirs(d, exist_ok=True)
    return d

# ─────────────────────────────────────────────────────────────────────
# Whisper transcribe
# ─────────────────────────────────────────────────────────────────────
def cmd_generate(args):
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        _err("faster-whisper non installato. Esegui: pip install faster-whisper")
        return 1

    model_name = args.model or os.environ.get("WHISPER_MODEL", "base")
    lang = None if (args.lang or "auto") == "auto" else args.lang

    _phase(f"Caricamento Whisper '{model_name}'…")
    try:
        model = WhisperModel(model_name, device="cpu", compute_type="int8",
                             download_root=_whisper_models_dir())
    except Exception as e:
        _err(f"caricamento whisper: {str(e)[:200]}")
        return 1

    _phase(f"Trascrizione ({os.path.basename(args.video)})…")
    try:
        segments_iter, info = model.transcribe(args.video, language=lang,
                                               vad_filter=True, beam_size=1)
    except Exception as e:
        _err(f"transcribe: {str(e)[:200]}")
        return 1

    detected = info.language
    duration = info.duration or 0
    _emit({"type": "progress", "stage": "whisper-init",
           "language": detected, "duration": float(duration)})

    srt_lines = []
    n = 0
    for seg in segments_iter:
        n += 1
        s = _ts(seg.start); e = _ts(seg.end)
        text = (seg.text or "").strip()
        srt_lines.append(f"{n}\n{s} --> {e}\n{text}\n")
        if duration > 0:
            _emit({"type": "progress", "stage": "whisper-transcribe",
                   "current": float(seg.end), "total": float(duration),
                   "lastText": text[:120]})

    srt = "\n".join(srt_lines).strip() + "\n"

    out_path = args.out
    if not out_path:
        base, _ = os.path.splitext(args.video)
        out_path = base + ".srt"
    try:
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(srt)
    except Exception as e:
        _err(f"scrittura SRT: {str(e)[:200]}")
        return 1

    _emit({"type": "done", "ok": True, "language": detected,
           "segments": n, "srt": out_path})
    return 0

# ─────────────────────────────────────────────────────────────────────
# Argos translate
# ─────────────────────────────────────────────────────────────────────
def _set_argos_home():
    # Indirizza argos a leggere/scrivere i pacchetti dentro il progetto
    os.environ.setdefault("ARGOS_PACKAGES_DIR", _argos_models_dir())
    os.environ.setdefault("ARGOS_TRANSLATE_PACKAGE_DIR", _argos_models_dir())

def _ensure_argos_pair(src, tgt):
    """Scarica on-demand il pacchetto di traduzione src->tgt se mancante.
    Se non esiste il pair diretto, installa src->en + en->tgt (argos pivota su EN).
    Ritorna (ok, error)."""
    try:
        import argostranslate.package as ap
    except ImportError:
        return False, "argostranslate non installato"
    try:
        installed = {(p.from_code, p.to_code) for p in ap.get_installed_packages()}
        def have(a, b): return (a, b) in installed
        if have(src, tgt) or (have(src, "en") and have("en", tgt)):
            return True, None
        _phase(f"Scarico pacchetto traduzione {src}→{tgt}…")
        ap.update_package_index()
        avail = ap.get_available_packages()
        def find(a, b):
            return next((p for p in avail if p.from_code == a and p.to_code == b), None)
        legs = []
        direct = find(src, tgt)
        if direct:
            legs = [direct]
        else:
            l1, l2 = find(src, "en"), find("en", tgt)
            if l1 and l2: legs = [l1, l2]
        if not legs:
            return False, f"nessun pacchetto argos per {src}->{tgt}"
        for pkg in legs:
            if (pkg.from_code, pkg.to_code) in installed:
                continue
            ap.install_from_path(pkg.download())
        return True, None
    except Exception as e:
        return False, str(e)[:200]

_SRT_TS_RE = re.compile(r"^\d{2}:\d{2}:\d{2}[,\.]\d{3}\s*-->\s*\d{2}:\d{2}:\d{2}[,\.]\d{3}\s*$")

def cmd_translate(args):
    try:
        _set_argos_home()
        import argostranslate.translate as at
    except ImportError:
        _err("argostranslate non installato.")
        return 1

    src_lang, tgt_lang = args.from_lang, args.to_lang
    ok, perr = _ensure_argos_pair(src_lang, tgt_lang)
    if not ok:
        _err(f"pacchetto traduzione {src_lang}->{tgt_lang} non disponibile: {perr}")
        return 1
    try:
        with open(args.srt, "r", encoding="utf-8") as f:
            content = f.read()
    except Exception as e:
        _err(f"lettura SRT: {str(e)[:200]}")
        return 1

    _phase(f"Traduzione {src_lang} → {tgt_lang}…")

    out_lines = []
    total = content.count("\n") or 1
    done = 0
    for line in content.split("\n"):
        done += 1
        stripped = line.strip()
        if not stripped or stripped.isdigit() or _SRT_TS_RE.match(stripped):
            out_lines.append(line)
        else:
            try:
                out_lines.append(at.translate(stripped, src_lang, tgt_lang))
            except Exception as e:
                # Se manca il pacchetto, errore chiaro
                _err(f"argos: {str(e)[:160]} — installa il pacchetto {src_lang}->{tgt_lang}")
                return 1
        if done % 50 == 0:
            _emit({"type": "progress", "stage": "argos-translate",
                   "current": done, "total": total})

    out_path = args.out
    if not out_path:
        base, ext = os.path.splitext(args.srt)
        out_path = f"{base}.{tgt_lang}{ext or '.srt'}"
    try:
        with open(out_path, "w", encoding="utf-8") as f:
            f.write("\n".join(out_lines))
    except Exception as e:
        _err(f"scrittura SRT tradotto: {str(e)[:200]}")
        return 1

    _emit({"type": "done", "ok": True, "srt": out_path,
           "from": src_lang, "to": tgt_lang})
    return 0

def cmd_list_langs(_args):
    try:
        _set_argos_home()
        import argostranslate.package as ap
    except ImportError:
        _err("argostranslate non installato.")
        return 1
    installed_out = []
    available_out = []
    try:
        installed = ap.get_installed_packages()
        installed_out = [{"from": p.from_code, "to": p.to_code,
                "from_name": p.from_name, "to_name": p.to_name}
               for p in installed]
    except Exception as e:
        _emit({"type": "warn", "error": str(e)[:200]})
    # Anche la lista dei pacchetti disponibili (online) per il picker UI.
    try:
        ap.update_package_index()
        avail = ap.get_available_packages()
        available_out = [{"from": p.from_code, "to": p.to_code,
                          "from_name": p.from_name, "to_name": p.to_name}
                         for p in avail]
    except Exception as e:
        _emit({"type": "warn", "error": "available: " + str(e)[:200]})
    _emit({"type": "done", "ok": True,
           "installed": installed_out, "available": available_out})
    return 0

def cmd_install_lang(args):
    try:
        _set_argos_home()
        import argostranslate.package as ap
    except ImportError:
        _err("argostranslate non installato.")
        return 1

    _phase(f"Aggiornamento indice argos…")
    try:
        ap.update_package_index()
    except Exception as e:
        _err(f"update index: {str(e)[:200]}")
        return 1

    available = ap.get_available_packages()
    pkg = next((p for p in available
                if p.from_code == args.from_lang and p.to_code == args.to_lang), None)
    if not pkg:
        _err(f"nessun pacchetto {args.from_lang} → {args.to_lang}")
        return 1

    _phase(f"Download pacchetto {args.from_lang} → {args.to_lang}…")
    try:
        path = pkg.download()
        ap.install_from_path(path)
    except Exception as e:
        _err(f"install: {str(e)[:200]}")
        return 1

    _emit({"type": "done", "ok": True,
           "installed": f"{args.from_lang}->{args.to_lang}"})
    return 0

# ─────────────────────────────────────────────────────────────────────
# Entrypoint
# ─────────────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)

    g = sub.add_parser("generate")
    g.add_argument("--video", required=True)
    g.add_argument("--lang", default="auto")
    g.add_argument("--model", default=None)  # tiny|base|small|medium|large
    g.add_argument("--out", default=None)

    t = sub.add_parser("translate")
    t.add_argument("--srt", required=True)
    t.add_argument("--from", dest="from_lang", required=True)
    t.add_argument("--to", dest="to_lang", required=True)
    t.add_argument("--out", default=None)

    sub.add_parser("list-langs")

    i = sub.add_parser("install-lang")
    i.add_argument("--from", dest="from_lang", required=True)
    i.add_argument("--to", dest="to_lang", required=True)

    args = ap.parse_args()
    try:
        if args.cmd == "generate":     return cmd_generate(args)
        if args.cmd == "translate":    return cmd_translate(args)
        if args.cmd == "list-langs":   return cmd_list_langs(args)
        if args.cmd == "install-lang": return cmd_install_lang(args)
    except Exception as e:
        _err(f"unhandled: {e}\n{traceback.format_exc()[:600]}")
        return 1
    return 1

if __name__ == "__main__":
    sys.exit(main())
