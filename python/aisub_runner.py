#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Wrapper di ai-sub per Maniac.

Espone una CLI compatibile con il nostro IPC `subs:generate`:

  python aisub_runner.py --video <path> --out-dir <path>
                         [--lang auto|en|it|...] [--translate-to it]
                         [--api-key <key>]

Emette progress JSONL su stdout (compatibile con `subs:progress` event):
  {"type": "phase", "text": "..."}
  {"type": "progress", "percent": 12.3, "text": "uploading segment 2/8"}
  {"type": "done", "outPath": ".../video.it.srt"}
  {"type": "error", "error": "..."}

Richiede `GOOGLE_API_KEY` (passato via --api-key o env var). Senza la key
ai-sub fallisce, e il chiamante (main.js) fa fallback a faster-whisper.
"""
import os, sys, json, argparse, time, traceback


def emit(obj):
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


HERE = os.path.dirname(os.path.abspath(__file__))
APP_ROOT = os.path.abspath(os.path.join(HERE, os.pardir))
AISUB_SRC = os.path.join(APP_ROOT, "ai-sub-main", "src")
if os.path.isdir(AISUB_SRC) and AISUB_SRC not in sys.path:
    sys.path.insert(0, AISUB_SRC)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--video", required=True, help="Path video di input")
    ap.add_argument("--out-dir", required=True, help="Cartella di output per il file SRT")
    ap.add_argument("--lang", default="auto", help="Lingua sorgente (auto = detect)")
    ap.add_argument("--translate-to", default=None,
                    help="Lingua di traduzione SRT (es. 'it'). Se assente, solo trascrizione.")
    ap.add_argument("--api-key", default=None, help="Gemini API key (override env)")
    args = ap.parse_args()

    if args.api_key:
        os.environ["GOOGLE_API_KEY"] = args.api_key

    if not os.environ.get("GOOGLE_API_KEY") and not os.environ.get("GEMINI_API_KEY"):
        emit({"type": "error", "error": "Manca Gemini API key (GOOGLE_API_KEY)"})
        return 2

    if not os.path.isfile(args.video):
        emit({"type": "error", "error": "Video non trovato: " + args.video})
        return 2

    try:
        os.makedirs(args.out_dir, exist_ok=True)
    except Exception as e:
        emit({"type": "error", "error": "out-dir non creabile: " + str(e)})
        return 2

    # Costruisci argv per ai-sub. Il pacchetto ai-sub usa pydantic-settings con
    # opzioni gerarchiche tipo --dir.out, --ai.model, ecc.
    argv = ["ai-sub", args.video, "--dir.out", args.out_dir]
    if args.translate_to:
        # ai-sub ha dual output: trascrizione in lingua originale + traduzione EN.
        # Per altre lingue, dipende dalla versione: passiamo --ai.target-lang se supportato,
        # altrimenti il flag viene ignorato e ci accontentiamo del default.
        argv += ["--ai.target-lang", args.translate_to]

    # Patch: cattura il logger di ai-sub per ri-emettere progress JSONL
    try:
        import logging
        class _ProgressHandler(logging.Handler):
            def emit(self, record):
                try:
                    msg = self.format(record)
                    if msg:
                        emit({"type": "log", "level": record.levelname.lower(),
                              "text": msg[:500]})
                except Exception:
                    pass
        logging.basicConfig(level=logging.INFO)
        logging.getLogger().addHandler(_ProgressHandler())
    except Exception:
        pass

    sys.argv = argv
    try:
        emit({"type": "phase", "text": "ai-sub avvio…"})
        from ai_sub.main import main as aisub_main  # type: ignore
        # ai-sub usa asyncio internamente — main() dovrebbe gestirlo
        if hasattr(aisub_main, "__call__"):
            import asyncio, inspect
            if inspect.iscoroutinefunction(aisub_main):
                asyncio.run(aisub_main())
            else:
                aisub_main()
        # Cerca il file SRT generato
        base = os.path.splitext(os.path.basename(args.video))[0]
        candidates = []
        for ext in (".srt", ".en.srt", ".it.srt"):
            p = os.path.join(args.out_dir, base + ext)
            if os.path.isfile(p): candidates.append(p)
        # Se ci sono entrambi (originale + traduzione), preferiamo la traduzione
        if args.translate_to:
            tgt = os.path.join(args.out_dir, base + "." + args.translate_to + ".srt")
            if os.path.isfile(tgt): candidates = [tgt] + [c for c in candidates if c != tgt]
        out_path = candidates[0] if candidates else None
        if not out_path:
            # Fallback: scan diretto della cartella per .srt più recente con stem matching
            try:
                srts = [f for f in os.listdir(args.out_dir) if f.lower().endswith(".srt") and base in f]
                srts.sort(key=lambda f: os.path.getmtime(os.path.join(args.out_dir, f)), reverse=True)
                if srts: out_path = os.path.join(args.out_dir, srts[0])
            except Exception:
                pass
        if out_path:
            emit({"type": "done", "outPath": out_path})
        else:
            emit({"type": "error", "error": "ai-sub completato ma nessun .srt trovato"})
        return 0
    except SystemExit as e:
        # ai-sub potrebbe chiamare sys.exit con error code
        if e.code and e.code != 0:
            emit({"type": "error", "error": "ai-sub uscito con codice " + str(e.code)})
            return int(e.code)
        return 0
    except Exception as e:
        emit({"type": "error", "error": str(e)[:300] + " | " + traceback.format_exc()[:500]})
        return 1


if __name__ == "__main__":
    sys.exit(main())
