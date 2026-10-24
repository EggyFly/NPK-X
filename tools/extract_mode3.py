#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Extract all Eggy Party NPKs using the recovered mode-3 index.

* real logical path -> output path (textures .png, WEM .flac)
* Zstd decompression and texture conversion are streamed in memory
* unsupported images/audio keep their original bytes and are reported
* unresolved path hashes go to ``_unresolved/``; script files to ``_script/``
"""

from __future__ import annotations

import argparse
import collections
import csv
import gzip
import hashlib
import json
import mmap
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
from pathlib import Path

import zstandard

import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parent))

import image_convert
import nxpk_mode3 as N

TOOLKIT_ROOT = Path(__file__).resolve().parent
if (TOOLKIT_ROOT / "web" / "index.html").is_file():
    PACKAGE_ROOT = TOOLKIT_ROOT
elif (TOOLKIT_ROOT.parent / "web" / "index.html").is_file():
    PACKAGE_ROOT = TOOLKIT_ROOT.parent
elif (TOOLKIT_ROOT / "data").is_dir():
    PACKAGE_ROOT = TOOLKIT_ROOT
elif (TOOLKIT_ROOT.parent / "data").is_dir():
    PACKAGE_ROOT = TOOLKIT_ROOT.parent
elif (TOOLKIT_ROOT.parent / "bin").is_dir():
    PACKAGE_ROOT = TOOLKIT_ROOT.parent
else:
    PACKAGE_ROOT = TOOLKIT_ROOT
DEFAULT_GAME_ROOTS = [
    Path(r"D:\FeverApps\party_pc"),
    Path.cwd(),
    TOOLKIT_ROOT.parent / "party_pc",
    TOOLKIT_ROOT / "party_pc",
]
DEFAULT_OUT = Path.cwd() / "export_mode3"
WORK = Path(tempfile.gettempdir()) / "eggy_party_npk_toolkit"
VGMSTREAM_CANDIDATES = [
    PACKAGE_ROOT / "bin" / "vgmstream" / "vgmstream-cli.exe",
    TOOLKIT_ROOT / "_tools" / "vgmstream" / "vgmstream-cli.exe",
    Path("vgmstream-cli.exe"),
]
FFMPEG_CANDIDATES = [
    PACKAGE_ROOT / "bin" / "ffmpeg" / "ffmpeg.exe",
    Path(r"C:\ffmpeg\bin\ffmpeg.exe"),
    Path("ffmpeg.exe"),
]
try:
    import soundfile as _soundfile
except Exception:  # optional; ffmpeg is used when unavailable
    _soundfile = None

SCRIPT_PACKAGES = {"script.npk", "script_patch.npk"}
IMAGE_SUFFIXES = {".kta", ".dds", ".ktx", ".tga", ".png", ".jpg", ".jpeg", ".bmp", ".webp", ".gif"}
PNG_SOURCE_SUFFIXES = {".kta", ".dds", ".ktx", ".tga", ".bmp", ".webp", ".gif"}
ILLEGAL = re.compile(r'[<>:"|?*\x00-\x1f]')
RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


def find_ffmpeg() -> str | None:
    for candidate in FFMPEG_CANDIDATES:
        if candidate.is_file():
            return str(candidate)
    return shutil.which("ffmpeg")


def find_vgmstream() -> str | None:
    for candidate in VGMSTREAM_CANDIDATES:
        if candidate.is_file():
            return str(candidate)
    return shutil.which("vgmstream-cli")


def find_game_root(explicit: str | Path | None) -> Path:
    candidates = [Path(explicit)] if explicit else []
    env = os.environ.get("EGGY_GAME_ROOT")
    if env:
        candidates.insert(0, Path(env))
    candidates.extend(DEFAULT_GAME_ROOTS)
    for candidate in candidates:
        try:
            if candidate.is_dir() and any(candidate.glob("*.npk")):
                return candidate.resolve()
        except OSError:
            continue
    raise SystemExit("game directory not found; pass --game-root <dir containing *.npk>")


def find_path_dict(explicit: str | Path | None) -> Path | None:
    candidates = [Path(explicit)] if explicit else []
    candidates += [
        PACKAGE_ROOT / "data" / "pathdict.txt.gz",
        PACKAGE_ROOT / "data" / "pathdict.txt",
        TOOLKIT_ROOT / "data" / "pathdict.txt.gz",
        TOOLKIT_ROOT / "data" / "pathdict.txt",
        TOOLKIT_ROOT / "_pathdict.txt",
        Path("_pathdict.txt"),
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def sanitize_rel(rel: str) -> str:
    parts = []
    for part in rel.replace("\\", "/").split("/"):
        if part in ("", ".", ".."):
            continue
        part = ILLEGAL.sub("_", part).rstrip(" .")
        if not part:
            part = "_"
        stem = part.split(".", 1)[0].upper()
        if stem in RESERVED:
            part = "_" + part
        if len(part) > 180:
            digest = hashlib.sha1(part.encode("utf-8", "ignore")).hexdigest()[:12]
            part = part[:150] + "__" + digest + part[-10:]
        parts.append(part)
    return "/".join(parts)


def abs_path(root: Path, rel: str) -> str:
    value = str(root / rel.replace("/", os.sep))
    if len(value) > 240 and not value.startswith("\\\\?\\"):
        value = "\\\\?\\" + value
    return value


def ensure_parent(root: Path, rel: str) -> str:
    target = abs_path(root, rel)
    parent = os.path.dirname(target)
    os.makedirs(parent, exist_ok=True)
    return target


def write_bytes(root: Path, rel: str, data: bytes) -> int:
    target = ensure_parent(root, rel)
    with open(target, "wb") as handle:
        handle.write(data)
    return len(data)


def planned_rel(real_path: str | None, signature: int, package: str) -> tuple[str, str | None, bool]:
    """Return (planned output relative path, source suffix, is_script)."""
    if real_path:
        rel = sanitize_rel(real_path.lstrip("/"))
        suffix = Path(rel).suffix.lower()
        if suffix in PNG_SOURCE_SUFFIXES or suffix in (".jpg", ".jpeg"):
            rel = rel[: -len(suffix)] + ".png"
        elif suffix == ".wem":
            rel = rel[: -len(suffix)] + ".flac"
        return rel, suffix, False
    stem = Path(package).stem
    if package in SCRIPT_PACKAGES:
        return f"_script/{stem}/{signature:08x}.xn", ".xn", True
    return f"_unresolved/{stem}/{signature:08x}", None, False


def fallback_rel(real_path: str | None, planned: str, signature: int, package: str, suffix: str) -> str:
    if real_path:
        original = sanitize_rel(real_path.lstrip("/"))
        if original:
            return original
    path = Path(planned)
    if path.suffix:
        return str(path.with_suffix(suffix))
    return planned + suffix


def png_rel(job: dict) -> str:
    planned = job["planned"]
    if not job["real_path"]:
        return planned + ".png"
    path = Path(planned)
    return planned if path.suffix.lower() == ".png" else str(path.with_suffix(".png"))


def flac_rel(job: dict) -> str:
    planned = job["planned"]
    if not job["real_path"]:
        return planned + ".flac"
    path = Path(planned)
    return planned if path.suffix.lower() == ".flac" else str(path.with_suffix(".flac"))


def wem_to_flac(payload: bytes, out_abs: str, tmp_dir: Path, vgmstream: str | None, ffmpeg: str | None) -> bool:
    if not vgmstream or (not ffmpeg and _soundfile is None):
        return False
    tmp_dir.mkdir(parents=True, exist_ok=True)
    token = f"{os.getpid()}_{uuid.uuid4().hex}"
    wem_path = tmp_dir / f"{token}.wem"
    wav_path = tmp_dir / f"{token}.wav"
    try:
        wem_path.write_bytes(payload)
        result = subprocess.run(
            [vgmstream, "-o", str(wav_path), str(wem_path)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            timeout=120,
        )
        if result.returncode != 0 or not wav_path.exists() or wav_path.stat().st_size == 0:
            return False
        if _soundfile is not None:
            samples, samplerate = _soundfile.read(str(wav_path), dtype="int16", always_2d=True)
            _soundfile.write(out_abs, samples, samplerate, format="FLAC", subtype="PCM_16")
            return os.path.exists(out_abs) and os.path.getsize(out_abs) > 0
        result = subprocess.run(
            [ffmpeg, "-y", "-loglevel", "error", "-i", str(wav_path), "-c:a", "flac", "-compression_level", "8", out_abs],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            timeout=120,
        )
        return result.returncode == 0 and os.path.exists(out_abs) and os.path.getsize(out_abs) > 0
    except Exception:
        return False
    finally:
        for path in (wem_path, wav_path):
            try:
                path.unlink()
            except FileNotFoundError:
                pass


def process_task(task: tuple[list[dict], dict]) -> list[tuple]:
    jobs, cfg = task
    root = Path(cfg["root"])
    tmp_dir = Path(cfg["tmp"])
    vgmstream = cfg["vgmstream"]
    ffmpeg = cfg["ffmpeg"]
    dctx = zstandard.ZstdDecompressor()
    results = []
    package = jobs[0]["package"] if jobs else ""
    with open(package, "rb") as handle, mmap.mmap(handle.fileno(), 0, access=mmap.ACCESS_READ) as mm:
        for job in jobs:
            row = job["row"]
            planned = job["planned"]
            real_path = job["real_path"]
            suffix = job["src_ext"] or ""
            status = "failed"
            final_rel = planned
            written = 0
            note = ""
            try:
                if job["packed"] == 0 and job["unpacked"] == 0:
                    final_rel = planned + ".empty"
                    ensure_parent(root, final_rel)
                    open(abs_path(root, final_rel), "wb").close()
                    status = "empty"
                    results.append((row, status, final_rel, 0, ""))
                    continue
                payload = mm[job["offset"]:job["offset"] + job["packed"]]
                if job["compression"] == 3:
                    obj = zstandard.ZstdDecompressor().decompressobj()
                    payload = obj.decompress(payload)
                    if not obj.eof:
                        raise ValueError("zstd frame truncated")
                    if obj.unused_data and any(obj.unused_data):
                        raise ValueError("unexpected trailing zstd bytes")
                elif job["compression"] != 0:
                    raise ValueError(f"compression {job['compression']}")
                if len(payload) != job["unpacked"]:
                    raise ValueError("unpacked size mismatch")

                kind = image_convert.classify(payload, suffix)
                if job["is_script"]:
                    written = write_bytes(root, planned, payload)
                    status = "copied"
                elif kind == "wem" or suffix == ".wem":
                    final_rel = flac_rel(job)
                    target = ensure_parent(root, final_rel)
                    if wem_to_flac(payload, target, tmp_dir, vgmstream, ffmpeg):
                        written = os.path.getsize(target)
                        status = "converted_flac"
                    else:
                        final_rel = fallback_rel(real_path, planned, job["signature"], job["package"], ".wem")
                        written = write_bytes(root, final_rel, payload)
                        status = "fallback_wem"
                        note = "vgmstream/ffmpeg failed"
                elif kind in ("ktx1", "ktx2", "dds", "png", "jpeg", "tga", "bmp", "webp", "gif"):
                    if kind in ("ktx2",):
                        raise ValueError("KTX2 not supported")
                    final_rel = png_rel(job)
                    target = ensure_parent(root, final_rel)
                    try:
                        conv_status, _ = image_convert.convert_image(payload, suffix, Path(target))
                        written = os.path.getsize(target)
                        status = conv_status
                    except Exception as exc:
                        final_rel = fallback_rel(real_path, planned, job["signature"], job["package"], suffix or image_convert.image_extension(payload, suffix))
                        written = write_bytes(root, final_rel, payload)
                        status = "fallback_original"
                        note = str(exc)[:120]
                elif kind == "other" and not real_path:
                    # unnamed payload: choose a content extension and keep it
                    ext = image_convert.image_extension(payload, suffix)
                    final_rel = planned + ext
                    written = write_bytes(root, final_rel, payload)
                    status = "copied"
                else:
                    if not real_path:
                        ext = image_convert.image_extension(payload, suffix)
                        final_rel = planned + ext
                    written = write_bytes(root, final_rel, payload)
                    status = "copied"
            except Exception as exc:
                status = "failed"
                note = str(exc)[:200]
            results.append((row, status, final_rel, written, note))
    return results


def build_jobs(packages: list[Path], resolver: N.PathResolver, key: bytes = N.MODE3_KEY) -> list[dict]:
    jobs: list[dict] = []
    row = 0
    for package in packages:
        header, entries = N.read_index(package, key, validate=True, resolver=resolver)
        for entry in entries:
            real = resolver.resolve(entry.signature)[0]
            if package.name in SCRIPT_PACKAGES:
                real = None
            planned, suffix, is_script = planned_rel(real, entry.signature, package.name)
            jobs.append(
                {
                    "row": row,
                    "package": str(package),
                    "package_name": package.name,
                    "real_path": real or "",
                    "signature": entry.signature,
                    "offset": entry.offset,
                    "packed": entry.packed_size,
                    "unpacked": entry.unpacked_size,
                    "compression": entry.compression,
                    "flags": entry.flags,
                    "src_ext": suffix or "",
                    "planned": planned,
                    "is_script": is_script,
                }
            )
            row += 1
    return jobs


def dedupe_outputs(jobs: list[dict]) -> int:
    """Make output paths case-insensitively unique; return collision count."""
    seen: dict[str, int] = {}
    collisions = 0
    for job in jobs:
        key = job["planned"].lower()
        if key in seen:
            collisions += 1
            path = Path(job["planned"])
            suffix = f"__{job['package_name'].split('.')[0]}_{job['signature']:08x}"
            job["planned"] = str(path.with_name(path.stem + suffix + path.suffix)) if path.suffix else str(path.with_name(path.name + suffix))
            key = job["planned"].lower()
        seen[key] = job["row"]
    return collisions


def main() -> int:
    parser = argparse.ArgumentParser(description="Extract Eggy Party NPKs by real index paths")
    parser.add_argument("--game-root", type=Path, default=None,
                        help="directory containing *.npk (default: auto-detect)")
    parser.add_argument("--root", "--out", dest="root", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--path-dict", type=Path, default=None)
    parser.add_argument("--profile", type=Path, default=None)
    parser.add_argument("--key-hex", type=str, default=None,
                        help="override the 16-byte mode-3 key (32 hex chars)")
    parser.add_argument("--tables", type=Path, default=None,
                        help="optional mode3_tables.py replacement")
    parser.add_argument("--tmp", type=Path, default=WORK / "tmp")
    parser.add_argument("--jobs", type=int, default=6)
    parser.add_argument("--limit", type=int, default=0, help="limit total entries for a test run")
    parser.add_argument("--packages", nargs="*", default=None)
    parser.add_argument("--no-script", action="store_true")
    parser.add_argument("--force-empty", action="store_true")
    args = parser.parse_args()

    root = args.root
    if root.exists() and any(root.iterdir()) and not args.force_empty:
        print(f"output directory is not empty: {root}", file=sys.stderr)
        return 2
    root.mkdir(parents=True, exist_ok=True)
    args.tmp.mkdir(parents=True, exist_ok=True)

    profile_path = args.profile
    if profile_path is None:
        candidate = PACKAGE_ROOT / "data" / "mode3_profile.json"
        if candidate.is_file():
            profile_path = candidate
    profile = N.load_profile(profile_path)
    key = N.profile_key(profile, args.key_hex)
    seed = N.profile_seed(profile)
    tables = args.tables or profile.get("tables_file")
    if tables:
        tables_path = Path(tables)
        if not tables_path.is_absolute() and profile_path:
            tables_path = Path(profile_path).parent / tables_path
        N.install_tables(tables_path)

    ffmpeg = find_ffmpeg()
    vgmstream = find_vgmstream()
    if not vgmstream:
        print("warning: vgmstream-cli not found; WEM will be kept as .wem", file=sys.stderr)
    print(f"profile={profile.get('name', 'custom')} key={key.hex()} hash_seed=0x{seed:08x}", flush=True)

    game_root = find_game_root(args.game_root)
    packages = sorted(game_root.glob("*.npk"))
    if args.packages:
        wanted = set(args.packages)
        packages = [p for p in packages if p.name in wanted]
    if args.no_script:
        packages = [p for p in packages if p.name not in SCRIPT_PACKAGES]
    if not packages:
        print(f"no .npk files found under {game_root}", file=sys.stderr)
        return 4

    path_dict = find_path_dict(args.path_dict)
    if path_dict:
        resolver = N.PathResolver.from_file(path_dict, seed)
        print(f"path dictionary={path_dict}", flush=True)
    else:
        resolver = N.PathResolver([], seed)
        print("warning: path dictionary not found; every entry will use hash names", file=sys.stderr)

    print("reading/decrypting indexes...", flush=True)
    started = time.time()
    try:
        jobs = build_jobs(packages, resolver, key)
    except N.Mode3FormatError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 5
    if args.limit:
        jobs = jobs[: args.limit]
    collisions = dedupe_outputs(jobs)
    free = shutil.disk_usage(root).free
    print(
        f"entries={len(jobs)} planned_path_collisions={collisions} "
        f"free={free / 1e9:.1f}GB workers={args.jobs}",
        flush=True,
    )
    if free < 12 * 1024**3:
        print("not enough free disk space (need at least 12 GB headroom)", file=sys.stderr)
        return 3

    chunks = []
    chunk_size = 1000
    start = 0
    while start < len(jobs):
        package = jobs[start]["package"]
        end = start
        while end < len(jobs) and jobs[end]["package"] == package:
            end += 1
        for offset in range(start, end, chunk_size):
            chunks.append(jobs[offset:min(offset + chunk_size, end)])
        start = end
    config = {
        "root": str(root),
        "tmp": str(args.tmp),
        "vgmstream": vgmstream,
        "ffmpeg": ffmpeg,
    }
    tasks = [(chunk, config) for chunk in chunks]

    results: dict[int, tuple] = {}
    status_counts: collections.Counter = collections.Counter()
    package_bytes: collections.Counter = collections.Counter()
    package_counts: collections.Counter = collections.Counter()
    failures = []
    completed_tasks = 0
    cancelled = False
    t0 = time.time()

    with ProcessPoolExecutor(max_workers=args.jobs) as pool:
        pending = set()
        iterator = iter(tasks)
        while True:
            while len(pending) < args.jobs * 2:
                try:
                    nxt = next(iterator)
                except StopIteration:
                    break
                pending.add(pool.submit(process_task, nxt))
            if not pending:
                break
            done, pending = wait(pending, return_when=FIRST_COMPLETED)
            for future in done:
                try:
                    task_results = future.result()
                except Exception as exc:
                    completed_tasks += 1
                    failures.append({"task_error": repr(exc)})
                    continue
                completed_tasks += 1
                for row, status, final_rel, written, note in task_results:
                    job = jobs[row]
                    results[row] = (status, final_rel, written, note)
                    status_counts[status] += 1
                    package_counts[job["package_name"]] += 1
                    package_bytes[job["package_name"]] += written
                    if status in ("failed", "fallback_original", "fallback_wem"):
                        if len(failures) < 5000:
                            failures.append(
                                {
                                    "package": job["package_name"],
                                    "path": job["real_path"],
                                    "signature": f"{job['signature']:08x}",
                                    "status": status,
                                    "output": final_rel,
                                    "note": note,
                                }
                            )
                if completed_tasks % 20 == 0:
                    free_now = shutil.disk_usage(root).free
                    written_gb = sum(package_bytes.values()) / 1e9
                    elapsed = time.time() - t0
                    print(
                        f"tasks {completed_tasks}/{len(tasks)} entries {len(results)}/{len(jobs)} "
                        f"written {written_gb:.2f}GB free {free_now/1e9:.1f}GB elapsed {elapsed/60:.1f}m",
                        flush=True,
                    )
                    if free_now < 6 * 1024**3:
                        print("low disk space: cancelling remaining work", file=sys.stderr, flush=True)
                        cancelled = True
            if cancelled:
                for fut in pending:
                    fut.cancel()
                break

    # Fill missing results (cancelled/failed task) as failed.
    for job in jobs:
        if job["row"] not in results:
            results[job["row"]] = ("cancelled" if cancelled else "failed", job["planned"], 0, "not processed")
            status_counts[results[job["row"]][0]] += 1

    manifest_path = root / "_manifest.csv.gz"
    with gzip.open(manifest_path, "wt", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "package", "real_path", "output_path", "signature", "offset",
                "packed_size", "unpacked_size", "compression", "flags", "status", "output_size", "note",
            ]
        )
        for job in jobs:
            status, final_rel, written, note = results[job["row"]]
            writer.writerow(
                [
                    job["package_name"],
                    job["real_path"],
                    final_rel,
                    f"{job['signature']:08x}",
                    job["offset"],
                    job["packed"],
                    job["unpacked"],
                    job["compression"],
                    f"{job['flags']:#06x}",
                    status,
                    written,
                    note,
                ]
            )

    summary = {
        "started": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(started)),
        "elapsed_seconds": round(time.time() - t0, 2),
        "output_root": str(root),
        "entries": len(jobs),
        "status_counts": dict(status_counts),
        "written_bytes": sum(package_bytes.values()),
        "written_gb": round(sum(package_bytes.values()) / 1e9, 3),
        "packages": {
            name: {"entries": package_counts[name], "written_bytes": package_bytes[name]}
            for name in sorted(package_counts)
        },
        "wem_tools": {"vgmstream": vgmstream, "ffmpeg": ffmpeg},
        "cancelled_low_disk": cancelled,
        "failure_count": len(failures),
        "free_bytes_end": shutil.disk_usage(root).free,
    }
    (root / "_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    with open(root / "_failures.jsonl", "w", encoding="utf-8") as handle:
        for item in failures:
            handle.write(json.dumps(item, ensure_ascii=False) + "\n")
    print(json.dumps({k: v for k, v in summary.items() if k != "packages"}, ensure_ascii=False, indent=2))
    return 0 if not cancelled else 1


if __name__ == "__main__":
    raise SystemExit(main())
