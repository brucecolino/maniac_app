#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
organizer.py — Wizard organizer file (scan, anteprima, snapshot, restore).

CLI sub-comandi:
    python organizer.py scan --folders <f1>[,<f2>,...] [--max <N>]
        Scansiona ricorsivamente, estrae metadata (mediainfo / mtime / size),
        emette righe di progresso e un evento "done" con la lista file.

    python organizer.py build-ops --files-json <path-to-json> --root <dest>
                                  --by year|genre|actor|type [--by ...]
        Calcola operazioni di MOVE proposte (no I/O su disco).

    python organizer.py execute --ops-json <path> --snapshots-dir <dir>
        Crea snapshot, esegue i move e ritorna lo snapshot path.

    python organizer.py snapshots --snapshots-dir <dir>
        Lista snapshot disponibili.

    python organizer.py restore --snapshot <path>
        Inverte le operazioni dello snapshot.

I comandi `scan`, `execute`, `restore` emettono progress streaming JSON-line.
Gli altri ritornano un singolo "done".

Dipendenze opzionali:
    - pymediainfo (preferito per durata/codec/bitrate)
    - exiftool CLI (fallback per metadata)

In assenza, ricade su os.stat (size/mtime).
"""
import sys, os, json, argparse, time, shutil, traceback, hashlib
from datetime import datetime, timezone

MEDIA_EXTS = {'.mp4', '.mkv', '.avi', '.mov', '.wmv', '.m4v', '.flv',
              '.webm', '.ts', '.mpg', '.mpeg', '.3gp', '.ogv'}

def _emit(o):
    sys.stdout.write(json.dumps(o, ensure_ascii=False) + "\n")
    sys.stdout.flush()

def _phase(text): _emit({"type": "phase", "text": text})
def _err(text):   _emit({"type": "error", "error": text})

# ─────────────────────────────────────────────────────────────────────
# Metadata extraction
# ─────────────────────────────────────────────────────────────────────
_MEDIAINFO_AVAILABLE = None
def _has_mediainfo():
    global _MEDIAINFO_AVAILABLE
    if _MEDIAINFO_AVAILABLE is not None: return _MEDIAINFO_AVAILABLE
    try:
        import pymediainfo  # noqa: F401
        _MEDIAINFO_AVAILABLE = True
    except Exception:
        _MEDIAINFO_AVAILABLE = False
    return _MEDIAINFO_AVAILABLE

def _exiftool_path():
    """Cerca exiftool prima nel /tools del progetto, poi sul PATH."""
    here = os.path.dirname(os.path.abspath(__file__))
    root = os.path.abspath(os.path.join(here, os.pardir))
    local = os.path.join(root, "tools", "exiftool", "exiftool.exe")
    if os.path.exists(local):
        return local
    import shutil
    return shutil.which("exiftool")

_EXIFTOOL_CACHED = None
def _get_exiftool():
    global _EXIFTOOL_CACHED
    if _EXIFTOOL_CACHED is None:
        _EXIFTOOL_CACHED = _exiftool_path() or False
    return _EXIFTOOL_CACHED or None

def _extract_metadata(path):
    out = {"size": 0, "mtime": 0, "duration": None,
           "width": None, "height": None, "codec": None, "bitrate": None}
    try:
        st = os.stat(path)
        out["size"] = int(st.st_size)
        out["mtime"] = float(st.st_mtime)
    except OSError:
        pass

    if _has_mediainfo():
        try:
            from pymediainfo import MediaInfo
            mi = MediaInfo.parse(path)
            for tr in mi.tracks:
                if tr.track_type == "General":
                    if tr.duration: out["duration"] = float(tr.duration) / 1000.0
                    if tr.overall_bit_rate: out["bitrate"] = int(tr.overall_bit_rate)
                elif tr.track_type == "Video" and out["width"] is None:
                    if tr.width:  out["width"]  = int(tr.width)
                    if tr.height: out["height"] = int(tr.height)
                    if tr.codec_id: out["codec"] = str(tr.codec_id)
        except Exception:
            pass

    # Exiftool come fallback / arricchimento (titolo embedded, codec, anno)
    et = _get_exiftool()
    if et:
        try:
            import subprocess, json as _json
            r = subprocess.run([et, "-json", "-q", "-fast", path],
                               capture_output=True, text=True, timeout=8)
            if r.returncode == 0 and r.stdout:
                data = _json.loads(r.stdout)
                if data and isinstance(data, list):
                    d = data[0]
                    if out["duration"] is None and d.get("Duration"):
                        try:
                            # Duration può essere "0:01:23" o "83.456 s"
                            v = str(d["Duration"]).strip().rstrip("s").strip()
                            if ":" in v:
                                parts = [float(x) for x in v.split(":")]
                                while len(parts) < 3: parts.insert(0, 0)
                                out["duration"] = parts[0]*3600 + parts[1]*60 + parts[2]
                            else:
                                out["duration"] = float(v)
                        except Exception: pass
                    if out["codec"] is None and d.get("VideoCodec"):
                        out["codec"] = str(d["VideoCodec"])
                    if d.get("Title"):
                        out["title_embedded"] = str(d["Title"])
                    if d.get("ReleaseDate"):
                        out["release_date"] = str(d["ReleaseDate"])
        except Exception:
            pass
    return out

# ─────────────────────────────────────────────────────────────────────
# Filename heuristics (year, season-episode, kind)
# ─────────────────────────────────────────────────────────────────────
import re
_YEAR_RE   = re.compile(r"\b(19[5-9]\d|20[0-3]\d)\b")
_SE_RE     = re.compile(r"[Ss](\d{1,2})[\s._-]*[Ee](\d{1,2})|(\d{1,2})x(\d{2})")
_ANIME_HINT= re.compile(r"\[(?:Erai|Ohys|HorribleSubs|SubsPlease|Anime|RAW)\]|\bep(?:isod[eo])?\s*\d+", re.I)

def _guess_kind(filename):
    base = os.path.basename(filename)
    if _SE_RE.search(base):
        if _ANIME_HINT.search(base): return "anime"
        return "series"
    if _ANIME_HINT.search(base): return "anime"
    return "movie"

def _guess_year(filename):
    m = _YEAR_RE.search(os.path.basename(filename))
    return m.group(1) if m else None

def _clean_title(filename):
    base = os.path.splitext(os.path.basename(filename))[0]
    base = _SE_RE.sub(" ", base)
    base = _YEAR_RE.sub(" ", base)
    base = re.sub(r"\b(1080p|720p|480p|2160p|4k|x264|x265|h264|h265|hevc|aac|mp3|web-dl|webrip|bluray|brrip|hdrip|dvdrip|uncut|remux|hdr|sdr|10bit|HEVC|AAC)\b",
                  " ", base, flags=re.I)
    base = re.sub(r"[._\-\[\]\(\){}]", " ", base)
    base = re.sub(r"\s+", " ", base).strip()
    return base or os.path.splitext(os.path.basename(filename))[0]

# ─────────────────────────────────────────────────────────────────────
# Commands
# ─────────────────────────────────────────────────────────────────────
def cmd_scan(args):
    folders = [f.strip() for f in args.folders.split(",") if f.strip()]
    max_files = int(args.max or 0)
    files = []
    _phase(f"Scansione {len(folders)} cartella/e…")
    seen = 0
    for folder in folders:
        if not os.path.isdir(folder):
            _emit({"type": "warn", "error": f"non è una cartella: {folder}"}); continue
        for root, _, fnames in os.walk(folder):
            for fn in fnames:
                ext = os.path.splitext(fn)[1].lower()
                if ext not in MEDIA_EXTS: continue
                p = os.path.join(root, fn)
                seen += 1
                if max_files and seen > max_files: break
                meta = _extract_metadata(p)
                files.append({
                    "path": p, "name": fn, "ext": ext,
                    "title_guess": _clean_title(p),
                    "year_guess":  _guess_year(p),
                    "kind_guess":  _guess_kind(p),
                    **meta,
                })
                if seen % 25 == 0:
                    _emit({"type": "progress", "stage": "scan",
                           "current": seen, "file": fn})
            if max_files and seen > max_files: break
        if max_files and seen > max_files: break

    _emit({"type": "done", "ok": True, "count": len(files), "files": files})
    return 0

def cmd_build_ops(args):
    try:
        with open(args.files_json, "r", encoding="utf-8") as f:
            files = json.load(f)
    except Exception as e:
        _err(f"lettura files-json: {e}"); return 1

    by = [b.strip() for b in args.by.split(",") if b.strip()]
    root = os.path.abspath(args.root)
    ops = []
    for f in files:
        parts = [root]
        kind = f.get("kind_guess", "movie")
        if "type" in by:
            parts.append({"movie": "Movies", "series": "Series",
                          "anime": "Anime"}.get(kind, "Other"))
        if "year" in by:
            parts.append(f.get("year_guess") or "Unknown Year")
        if "genre" in by:
            parts.append(f.get("genre") or "Unknown Genre")
        if "actor" in by:
            actors = f.get("actors") or []
            parts.append((actors[0] if actors else "Unknown Actor"))
        # Per series + anime mantieni titolo come sotto-cartella
        if kind in ("series", "anime"):
            parts.append(f.get("title_guess") or "Unknown")
        new_dir = os.path.join(*parts)
        new_path = os.path.join(new_dir, f.get("name") or os.path.basename(f["path"]))
        if os.path.abspath(new_path) != os.path.abspath(f["path"]):
            ops.append({"from": f["path"], "to": new_path})

    _emit({"type": "done", "ok": True, "count": len(ops), "ops": ops})
    return 0

def _make_snapshot_dir(d):
    os.makedirs(d, exist_ok=True)

def _snapshot_path(snapshots_dir):
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    h = hashlib.sha1(str(time.time()).encode()).hexdigest()[:6]
    return os.path.join(snapshots_dir, f"snapshot_{ts}_{h}.json")

def cmd_execute(args):
    try:
        with open(args.ops_json, "r", encoding="utf-8") as f:
            ops = json.load(f)
    except Exception as e:
        _err(f"lettura ops-json: {e}"); return 1

    _make_snapshot_dir(args.snapshots_dir)
    snap_path = _snapshot_path(args.snapshots_dir)
    snap = {"version": "1.0",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "operations": []}

    total = len(ops)
    moved = 0; failed = 0
    _phase(f"Esecuzione {total} operazioni…")
    for i, op in enumerate(ops, 1):
        src, dst = op.get("from"), op.get("to")
        try:
            if not src or not os.path.exists(src):
                failed += 1
                _emit({"type": "warn", "error": f"sorgente mancante: {src}"})
                continue
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            if os.path.exists(dst):
                failed += 1
                _emit({"type": "warn", "error": f"destinazione esiste: {dst}"})
                continue
            shutil.move(src, dst)
            snap["operations"].append({"from": src, "to": dst})
            moved += 1
        except PermissionError as e:
            failed += 1
            _emit({"type": "warn", "error": f"permesso negato: {src} ({e})"})
        except Exception as e:
            failed += 1
            _emit({"type": "warn", "error": f"move {src}: {str(e)[:160]}"})
        _emit({"type": "progress", "stage": "execute",
               "current": i, "total": total,
               "file": os.path.basename(src or "")})

    try:
        with open(snap_path, "w", encoding="utf-8") as f:
            json.dump(snap, f, ensure_ascii=False, indent=2)
    except Exception as e:
        _err(f"scrittura snapshot: {e}"); return 1

    _emit({"type": "done", "ok": True, "moved": moved, "failed": failed,
           "snapshot": snap_path})
    return 0

def cmd_snapshots(args):
    d = args.snapshots_dir
    out = []
    if os.path.isdir(d):
        for fn in sorted(os.listdir(d)):
            if not fn.endswith(".json"): continue
            p = os.path.join(d, fn)
            try:
                with open(p, "r", encoding="utf-8") as f:
                    data = json.load(f)
                out.append({"path": p, "name": fn,
                            "created_at": data.get("created_at"),
                            "ops": len(data.get("operations") or [])})
            except Exception:
                pass
    _emit({"type": "done", "ok": True, "snapshots": out})
    return 0

def cmd_restore(args):
    try:
        with open(args.snapshot, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        _err(f"lettura snapshot: {e}"); return 1
    ops = data.get("operations") or []
    total = len(ops)
    restored = 0; failed = 0
    _phase(f"Ripristino {total} operazioni…")
    for i, op in enumerate(reversed(ops), 1):
        src, dst = op["to"], op["from"]   # inverso
        try:
            if not os.path.exists(src):
                failed += 1
                _emit({"type": "warn", "error": f"non trovato: {src}"})
                continue
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.move(src, dst)
            restored += 1
        except Exception as e:
            failed += 1
            _emit({"type": "warn", "error": f"restore {src}: {str(e)[:160]}"})
        _emit({"type": "progress", "stage": "restore",
               "current": i, "total": total,
               "file": os.path.basename(src)})
    _emit({"type": "done", "ok": True, "restored": restored, "failed": failed})
    return 0

# ─────────────────────────────────────────────────────────────────────
# Entrypoint
# ─────────────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("scan")
    s.add_argument("--folders", required=True, help="comma-separated paths")
    s.add_argument("--max", default=0)

    b = sub.add_parser("build-ops")
    b.add_argument("--files-json", required=True)
    b.add_argument("--root", required=True)
    b.add_argument("--by", default="type,year")

    e = sub.add_parser("execute")
    e.add_argument("--ops-json", required=True)
    e.add_argument("--snapshots-dir", required=True)

    sl = sub.add_parser("snapshots")
    sl.add_argument("--snapshots-dir", required=True)

    r = sub.add_parser("restore")
    r.add_argument("--snapshot", required=True)

    args = ap.parse_args()
    try:
        return {
            "scan": cmd_scan, "build-ops": cmd_build_ops,
            "execute": cmd_execute, "snapshots": cmd_snapshots,
            "restore": cmd_restore,
        }[args.cmd](args)
    except Exception as e:
        _err(f"unhandled: {e}\n{traceback.format_exc()[:600]}")
        return 1

if __name__ == "__main__":
    sys.exit(main())
