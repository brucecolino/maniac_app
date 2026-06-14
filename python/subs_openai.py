#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
subs_openai.py — Sottotitoli via OpenAI Audio API (whisper-1 / gpt-4o-transcribe)
                 e traduzione via Chat Completions (gpt-4o-mini).

CLI:
    python subs_openai.py generate --video <path> --api-key <KEY>
                                   [--lang it|auto] [--model whisper-1]
                                   [--out <srt-path>] [--ffmpeg <path>]
    python subs_openai.py translate --srt <path> --to <code> --api-key <KEY>
                                    [--out <srt-path>] [--model gpt-4o-mini]

Output JSONL su stdout — compatibile con _streamWorker (main.js).
"""
import sys, os, json, argparse, time, traceback, subprocess, tempfile

def _emit(o):
    sys.stdout.write(json.dumps(o, ensure_ascii=False) + "\n")
    sys.stdout.flush()

def _phase(text):  _emit({"type": "phase", "text": text})
def _err(text):    _emit({"type": "error", "error": text})
def _progress(stage, **extra): _emit({"type": "progress", "stage": stage, **extra})

OPENAI_BASE = "https://api.openai.com/v1"
MAX_UPLOAD_BYTES = 24 * 1024 * 1024  # margine sotto i 25MB del limite OpenAI

def _extract_audio(video_path, ffmpeg_path, bitrate="64k"):
    """Estrae traccia audio mono 16kHz mp3 in un file temporaneo."""
    fd, out = tempfile.mkstemp(suffix=".mp3", prefix="maniac_oa_")
    os.close(fd)
    cmd = [ffmpeg_path or "ffmpeg", "-y", "-loglevel", "error",
           "-i", video_path, "-vn",
           "-acodec", "libmp3lame", "-ab", bitrate,
           "-ar", "16000", "-ac", "1", out]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True)
    except FileNotFoundError:
        return None, "ffmpeg non trovato"
    if proc.returncode != 0:
        try: os.remove(out)
        except: pass
        return None, f"ffmpeg exit {proc.returncode}: {(proc.stderr or '')[:200]}"
    return out, None

def _ffprobe_duration(media_path, ffmpeg_path):
    """Restituisce la durata in secondi via ffmpeg (-i) parsando lo stderr."""
    cmd = [ffmpeg_path or "ffmpeg", "-i", media_path]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True)
    except FileNotFoundError:
        return None
    err = proc.stderr or ""
    import re as _re
    m = _re.search(r"Duration:\s*(\d+):(\d+):(\d+\.?\d*)", err)
    if not m: return None
    h, mn, s = int(m.group(1)), int(m.group(2)), float(m.group(3))
    return h*3600 + mn*60 + s

def _split_audio(audio_path, ffmpeg_path, segment_seconds):
    """Spezza un mp3 in segmenti di N secondi. Ritorna lista di tuple (path, offset_seconds)."""
    base, ext = os.path.splitext(audio_path)
    pattern = base + "_chunk%03d" + ext
    cmd = [ffmpeg_path or "ffmpeg", "-y", "-loglevel", "error",
           "-i", audio_path, "-c", "copy",
           "-f", "segment", "-segment_time", str(int(segment_seconds)),
           "-reset_timestamps", "1", pattern]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True)
    except FileNotFoundError:
        return None, "ffmpeg non trovato"
    if proc.returncode != 0:
        return None, f"ffmpeg split exit {proc.returncode}: {(proc.stderr or '')[:200]}"
    # Raccogli i chunk in ordine
    out = []
    i = 0
    while True:
        p = base + f"_chunk{i:03d}" + ext
        if not os.path.exists(p): break
        out.append((p, i*segment_seconds))
        i += 1
    return out, None

_SRT_TS_RE = __import__("re").compile(
    r"^(\d{2}):(\d{2}):(\d{2})[,.](\d{3})\s*-->\s*(\d{2}):(\d{2}):(\d{2})[,.](\d{3})\s*$")

def _shift_srt(srt_text, offset_seconds):
    """Aggiunge un offset (secondi) a tutti i timestamp del SRT."""
    def _fmt(s):
        if s < 0: s = 0
        h = int(s // 3600); m = int((s % 3600) // 60); sec = int(s % 60)
        ms = int(round((s - int(s)) * 1000))
        if ms == 1000: sec += 1; ms = 0
        return f"{h:02d}:{m:02d}:{sec:02d},{ms:03d}"
    out = []
    for line in srt_text.splitlines():
        m = _SRT_TS_RE.match(line.strip())
        if m:
            start = int(m.group(1))*3600 + int(m.group(2))*60 + int(m.group(3)) + int(m.group(4))/1000.0
            end   = int(m.group(5))*3600 + int(m.group(6))*60 + int(m.group(7)) + int(m.group(8))/1000.0
            out.append(f"{_fmt(start+offset_seconds)} --> {_fmt(end+offset_seconds)}")
        else:
            out.append(line)
    return "\n".join(out)

def _renumber_srt(srt_text):
    """Rinumera progressivamente i blocchi di un SRT (1, 2, 3, …)."""
    blocks = [b.strip() for b in srt_text.split("\n\n") if b.strip()]
    out = []
    n = 0
    for b in blocks:
        lines = b.split("\n")
        # Se la prima riga è un numero, sostituiscila; altrimenti prepend
        if lines and lines[0].strip().isdigit():
            lines = lines[1:]
        # Trova la riga di timestamp
        if not lines or not _SRT_TS_RE.match(lines[0].strip()):
            continue
        n += 1
        out.append(str(n) + "\n" + "\n".join(lines))
    return "\n\n".join(out) + "\n"

def _transcribe_one(work_file, api_key, model, lang, requests_mod):
    data = {"model": model, "response_format": "srt"}
    if lang and lang != "auto":
        data["language"] = lang
    with open(work_file, "rb") as fh:
        files = {"file": (os.path.basename(work_file), fh, "application/octet-stream")}
        r = requests_mod.post(
            f"{OPENAI_BASE}/audio/transcriptions",
            headers={"Authorization": f"Bearer {api_key}"},
            files=files, data=data, timeout=900
        )
    return r

def cmd_generate(args):
    api_key = args.api_key or os.environ.get("OPENAI_API_KEY")
    if not api_key:
        _err("OPENAI_API_KEY mancante. Imposta la chiave nelle Impostazioni.")
        return 1
    if not args.video or not os.path.exists(args.video):
        _err("video non valido")
        return 1
    try:
        import requests
    except ImportError:
        _err("requests non installato (pip install requests)")
        return 1

    src = args.video
    sz = os.path.getsize(src)
    work_file = src
    tmp_audio = None
    ext = os.path.splitext(src)[1].lower()

    # whisper-1 supporta fino a 25MB upload. Se troppo grande o non-audio diretto,
    # estrai l'audio compresso con ffmpeg.
    audio_exts = {".mp3", ".wav", ".m4a", ".ogg", ".flac", ".webm"}
    needs_extract = sz > MAX_UPLOAD_BYTES or ext not in audio_exts
    if needs_extract:
        _phase("Estrazione traccia audio…")
        tmp_audio, err = _extract_audio(src, args.ffmpeg, bitrate="64k")
        if err:
            _err(f"estrazione: {err}")
            return 1
        work_file = tmp_audio
        new_sz = os.path.getsize(work_file)
        _progress("audio-extracted", bytes=new_sz)
        # Se anche dopo l'estrazione siamo > 24MB ⇒ chunking
        if new_sz > MAX_UPLOAD_BYTES:
            duration = _ffprobe_duration(work_file, args.ffmpeg) or 0
            if duration <= 0:
                try: os.remove(tmp_audio)
                except: pass
                _err("impossibile determinare durata audio per chunking")
                return 1
            # Calcola N chunk in modo che ognuno sia < 24MB
            bytes_per_sec = new_sz / duration if duration > 0 else 8000
            # margine: usa 22 MB per chunk
            target_chunk_bytes = 22 * 1024 * 1024
            chunk_seconds = max(60, int(target_chunk_bytes / max(bytes_per_sec, 1)))
            _phase(f"File grande ({new_sz//1024//1024} MB, {int(duration)}s) — split in chunk da ~{chunk_seconds}s…")
            chunks, sperr = _split_audio(work_file, args.ffmpeg, chunk_seconds)
            if sperr or not chunks:
                try: os.remove(tmp_audio)
                except: pass
                _err(f"split audio: {sperr or 'nessun chunk prodotto'}")
                return 1
            model = args.model or "whisper-1"
            if model != "whisper-1":
                _emit({"type": "warn", "warn": f"modello {model} non supporta SRT, fallback a whisper-1"})
                model = "whisper-1"
            lang = args.lang or "auto"
            srt_parts = []
            try:
                for i, (cp, off) in enumerate(chunks):
                    _phase(f"Trascrizione chunk {i+1}/{len(chunks)} ({int(off)}s+) …")
                    try:
                        r = _transcribe_one(cp, api_key, model, lang, requests)
                    except Exception as e:
                        _err(f"richiesta chunk {i+1}: {str(e)[:200]}")
                        return 1
                    if r.status_code != 200:
                        _err(f"OpenAI HTTP {r.status_code} (chunk {i+1}): {(r.text or '')[:300]}")
                        return 1
                    shifted = _shift_srt(r.text, off)
                    srt_parts.append(shifted.strip())
                    _progress("transcribe", current=i+1, total=len(chunks))
            finally:
                # Pulisci i chunk
                for cp, _off in chunks:
                    try: os.remove(cp)
                    except: pass
                try: os.remove(tmp_audio)
                except: pass
            srt_text = _renumber_srt("\n\n".join(srt_parts))
            out_path = args.out
            if not out_path:
                base, _ext = os.path.splitext(args.video)
                out_path = base + ".srt"
            try:
                with open(out_path, "w", encoding="utf-8") as fh:
                    fh.write(srt_text)
            except Exception as e:
                _err(f"scrittura SRT: {str(e)[:200]}")
                return 1
            _emit({"type": "done", "ok": True, "srt": out_path,
                   "language": (lang if lang and lang != "auto" else None),
                   "chunks": len(chunks), "provider": "openai"})
            return 0

    model = args.model or "whisper-1"
    # whisper-1 supporta srt/vtt; gpt-4o-transcribe NO (solo json — niente timestamp).
    # Forziamo whisper-1 quando l'utente vuole un SRT con timestamp.
    if model not in ("whisper-1",):
        _emit({"type": "warn", "warn": f"modello {model} non supporta SRT, fallback a whisper-1"})
        model = "whisper-1"

    _phase(f"Invio a OpenAI ({model})…")
    lang = args.lang or "auto"

    try:
        r = _transcribe_one(work_file, api_key, model, lang, requests)
    except Exception as e:
        if tmp_audio:
            try: os.remove(tmp_audio)
            except: pass
        _err(f"richiesta: {str(e)[:200]}")
        return 1

    if tmp_audio:
        try: os.remove(tmp_audio)
        except: pass

    if r.status_code != 200:
        body = (r.text or "")[:300]
        _err(f"OpenAI HTTP {r.status_code}: {body}")
        return 1

    srt_text = r.text
    out_path = args.out
    if not out_path:
        base, _ext = os.path.splitext(args.video)
        out_path = base + ".srt"
    try:
        with open(out_path, "w", encoding="utf-8") as fh:
            fh.write(srt_text)
    except Exception as e:
        _err(f"scrittura SRT: {str(e)[:200]}")
        return 1

    _emit({"type": "done", "ok": True, "srt": out_path,
           "language": (lang if lang and lang != "auto" else None),
           "provider": "openai"})
    return 0

def cmd_translate(args):
    api_key = args.api_key or os.environ.get("OPENAI_API_KEY")
    if not api_key:
        _err("OPENAI_API_KEY mancante.")
        return 1
    if not args.srt or not os.path.exists(args.srt):
        _err("SRT non valido")
        return 1
    try:
        import requests
    except ImportError:
        _err("requests non installato")
        return 1

    try:
        with open(args.srt, "r", encoding="utf-8") as fh:
            content = fh.read()
    except Exception as e:
        _err(f"lettura SRT: {str(e)[:200]}")
        return 1

    target = args.to_lang
    model = args.model or "gpt-4o-mini"

    # Per file lunghi spezziamo in chunk di ~120 blocchi SRT.
    blocks = [b for b in content.split("\n\n") if b.strip()]
    chunks = []
    cur = []
    for b in blocks:
        cur.append(b)
        if len(cur) >= 120:
            chunks.append("\n\n".join(cur)); cur = []
    if cur: chunks.append("\n\n".join(cur))

    _phase(f"Traduzione → {target} ({model}, {len(chunks)} chunk)…")

    out_chunks = []
    for i, ck in enumerate(chunks):
        sysprompt = (
            f"You translate SRT subtitle blocks into {target}. "
            "Preserve the exact block structure: keep numeric IDs, timestamp lines "
            "(HH:MM:SS,mmm --> HH:MM:SS,mmm), and blank-line separators unchanged. "
            "Translate ONLY the subtitle text lines. Output the SRT verbatim, "
            "no commentary, no markdown fences."
        )
        try:
            r = requests.post(
                f"{OPENAI_BASE}/chat/completions",
                headers={"Authorization": f"Bearer {api_key}",
                         "Content-Type": "application/json"},
                json={
                    "model": model,
                    "messages": [
                        {"role": "system", "content": sysprompt},
                        {"role": "user",   "content": ck}
                    ],
                    "temperature": 0.2
                },
                timeout=600
            )
        except Exception as e:
            _err(f"richiesta chunk {i+1}: {str(e)[:200]}")
            return 1
        if r.status_code != 200:
            body = (r.text or "")[:300]
            _err(f"OpenAI HTTP {r.status_code} (chunk {i+1}): {body}")
            return 1
        try:
            data = r.json()
            translated = data["choices"][0]["message"]["content"]
        except Exception as e:
            _err(f"parse chunk {i+1}: {str(e)[:200]}")
            return 1
        # Rimuovi eventuali fence ```srt ... ```
        t = translated.strip()
        if t.startswith("```"):
            t = t.split("\n", 1)[1] if "\n" in t else t
            if t.endswith("```"):
                t = t[:-3]
        out_chunks.append(t.strip())
        _progress("translate", current=i+1, total=len(chunks))

    final_srt = "\n\n".join(out_chunks).strip() + "\n"

    out_path = args.out
    if not out_path:
        base, ext = os.path.splitext(args.srt)
        out_path = f"{base}.{target}{ext or '.srt'}"
    try:
        with open(out_path, "w", encoding="utf-8") as fh:
            fh.write(final_srt)
    except Exception as e:
        _err(f"scrittura SRT: {str(e)[:200]}")
        return 1

    _emit({"type": "done", "ok": True, "srt": out_path,
           "to": target, "provider": "openai"})
    return 0

def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)

    g = sub.add_parser("generate")
    g.add_argument("--video", required=True)
    g.add_argument("--api-key", default=None)
    g.add_argument("--ffmpeg", default=None)
    g.add_argument("--lang", default="auto")
    g.add_argument("--model", default="whisper-1")
    g.add_argument("--out", default=None)

    t = sub.add_parser("translate")
    t.add_argument("--srt", required=True)
    t.add_argument("--to", dest="to_lang", required=True)
    t.add_argument("--api-key", default=None)
    t.add_argument("--model", default="gpt-4o-mini")
    t.add_argument("--out", default=None)

    args = ap.parse_args()
    try:
        if args.cmd == "generate":  return cmd_generate(args)
        if args.cmd == "translate": return cmd_translate(args)
    except Exception as e:
        _err(f"unhandled: {e}\n{traceback.format_exc()[:600]}")
        return 1
    return 1

if __name__ == "__main__":
    sys.exit(main())
