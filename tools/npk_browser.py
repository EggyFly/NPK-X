#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Local, read-only web browser for Eggy Party NPK packages.

Reads NPK indexes through the recovered mode-3 decoder, builds a searchable
virtual filesystem, and serves images/audio/text on demand.  No bulk export is
required.  Images are converted to PNG on first view (small disk cache); WEM/BNK
audio is decoded on demand and streamed as FLAC without creating a permanent
FLAC library.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import ipaddress
import json
import glob
import mimetypes
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import uuid
import webbrowser
import zstandard
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse

from PIL import Image, ImageDraw

import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parent))

import bnk_parse
import rebuild_data
import resource_inspect
import image_convert
import gmp_parse
import xn_crypto
import xn_parse
import xn_decompile
import xn_bootstrap
import nxpk_mode3 as N
import extract_mode3 as E

try:
    import soundfile as _soundfile
except Exception:  # optional
    _soundfile = None

try:
    import numpy as _np
except Exception:  # optional
    _np = None

SCHEMA_VERSION = "1"
TOOLKIT_VERSION = "1.5-browser"
SORT_EXPRESSIONS = {
    "name_asc": "name COLLATE NOCASE ASC, id ASC",
    "name_desc": "name COLLATE NOCASE DESC, id ASC",
    "size_asc": "unpacked ASC, id ASC",
    "size_desc": "unpacked DESC, id ASC",
    "type": "kind ASC, name COLLATE NOCASE ASC, id ASC",
    "package": "package COLLATE NOCASE ASC, name COLLATE NOCASE ASC, id ASC",
}
KINDS = {"image", "audio", "bank", "video", "text", "script", "model", "binary"}
TOOLKIT_ROOT = Path(__file__).resolve().parent
PACKAGE_ROOT = E.PACKAGE_ROOT
WEB_DIR = PACKAGE_ROOT / "web"

IMAGE_EXT = {".kta", ".dds", ".ktx", ".png", ".jpg", ".jpeg", ".tga", ".bmp", ".webp", ".gif"}
AUDIO_EXT = {".wem", ".bnk", ".wav", ".flac", ".ogg", ".mp3", ".m4a", ".aac", ".opus"}
VIDEO_EXT = {".mp4", ".webm", ".mkv", ".avi", ".mov"}
TEXT_EXT = {
    ".json", ".xml", ".txt", ".conf", ".plist", ".atlas", ".mtg", ".gim", ".lod",
    ".ini", ".csv", ".yaml", ".yml", ".html", ".css", ".js", ".lua", ".py", ".md",
}
MODEL_EXT = {".mesh", ".skel", ".animgraph", ".animation", ".rawanimation", ".blendspace1d", ".trackgroup"}
MIME = {
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".flac": "audio/flac", ".wav": "audio/wav", ".ogg": "audio/ogg",
    ".mp3": "audio/mpeg", ".m4a": "audio/mp4", ".opus": "audio/ogg",
    ".mp4": "video/mp4", ".webm": "video/webm", ".mkv": "video/x-matroska",
    ".txt": "text/plain; charset=utf-8", ".json": "application/json",
}


def default_cache_dir() -> Path:
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA") or str(Path.home())
        return Path(base) / "EggyNpkBrowser"
    return Path.home() / ".cache" / "eggy_npk_browser"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_entry_bytes(package_path: str, row) -> bytes:
    if hasattr(row, "packed_size"):
        offset, packed, unpacked, compression = row.offset, row.packed_size, row.unpacked_size, row.compression
    else:
        offset, packed, unpacked, compression = row["offset"], row["packed"], row["unpacked"], row["compression"]
    offset, packed, unpacked, compression = int(offset), int(packed), int(unpacked), int(compression)
    with open(package_path, "rb") as handle:
        handle.seek(offset)
        raw = handle.read(packed)
    if len(raw) != packed:
        raise ValueError("short read from package")
    if compression == 3:
        obj = zstandard.ZstdDecompressor().decompressobj()
        data = obj.decompress(raw)
        if not obj.eof:
            raise ValueError("truncated Zstd frame")
        if obj.unused_data and any(obj.unused_data):
            raise ValueError("unexpected trailing Zstd data")
    elif compression == 0:
        data = raw
    else:
        raise ValueError(f"unsupported compression {compression}")
    if len(data) != unpacked:
        raise ValueError("unpacked size mismatch")
    return data


def kind_for_ext(ext: str) -> str:
    ext = (ext or "").lower()
    if ext in IMAGE_EXT:
        return "image"
    if ext == ".bnk":
        return "bank"
    if ext in AUDIO_EXT:
        return "audio"
    if ext in VIDEO_EXT:
        return "video"
    if ext in TEXT_EXT:
        return "text"
    if ext == ".xn":
        return "script"
    if ext in MODEL_EXT:
        return "model"
    return "binary"


def detect_unresolved(package_path: str, entry) -> tuple[str, str]:
    """Return (extension, kind) for an entry without a logical path."""
    payload = read_entry_bytes(package_path, entry)
    kind = image_convert.classify(payload, "")
    ext = image_convert.image_extension(payload, "")
    if kind in ("ktx1", "ktx2", "dds", "png", "jpeg", "tga", "bmp", "webp", "gif"):
        return ext, "image"
    if ext == ".wem":
        return ".wem", "audio"
    if ext == ".bnk":
        return ".bnk", "bank"
    if ext == ".mesh":
        return ".mesh", "model"
    return ext or ".bin", "binary"


def fingerprint(game_root: Path, profile: dict, key: bytes, seed: int, path_dict: Path | None, packages: list[Path]) -> dict:
    path_dict_info = None
    if path_dict and path_dict.is_file():
        path_dict_info = {"name": path_dict.name, "size": path_dict.stat().st_size, "sha256": sha256_file(path_dict)}
    tables_hash = None
    try:
        tables_file = Path(N._t.__file__)  # type: ignore[attr-defined]
        tables_hash = sha256_file(tables_file)
    except Exception:
        pass
    return {
        "schema": SCHEMA_VERSION,
        "game_root": str(game_root.resolve()),
        "profile": profile.get("name"),
        "key": key.hex(),
        "path_hash_seed": f"0x{seed:08x}",
        "tables_sha256": tables_hash,
        "path_dict": path_dict_info,
        "packages": [
            {"name": p.name, "size": p.stat().st_size, "mtime_ns": p.stat().st_mtime_ns}
            for p in packages
        ],
    }


def build_catalog(
    db_path: Path,
    game_root: Path,
    profile: dict,
    key: bytes,
    seed: int,
    path_dict: Path | None,
    packages: list[Path],
    resolver: N.PathResolver,
) -> dict:
    meta = fingerprint(game_root, profile, key, seed, path_dict, packages)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = db_path.with_suffix(".tmp.sqlite")
    for path in (tmp, Path(str(tmp) + "-wal"), Path(str(tmp) + "-shm")):
        try:
            path.unlink()
        except FileNotFoundError:
            pass
    connection = sqlite3.connect(tmp)
    connection.executescript(
        """
        PRAGMA journal_mode=OFF;
        PRAGMA synchronous=OFF;
        CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT);
        CREATE TABLE packages(name TEXT PRIMARY KEY, path TEXT, entries INTEGER, bytes INTEGER);
        CREATE TABLE files(
            id INTEGER PRIMARY KEY, package TEXT, path TEXT UNIQUE, dir TEXT, name TEXT,
            signature INTEGER, offset INTEGER, packed INTEGER, unpacked INTEGER,
            compression INTEGER, flags INTEGER, packed_hash INTEGER, unpacked_hash INTEGER,
            kind TEXT, ext TEXT
        );
        CREATE TABLE dirs(path TEXT PRIMARY KEY, parent TEXT, file_count INTEGER, bytes INTEGER);
        CREATE INDEX idx_files_dir ON files(dir);
        CREATE INDEX idx_files_path ON files(path);
        CREATE INDEX idx_dirs_parent ON dirs(parent);
        """
    )
    dir_stats: dict[str, list[int]] = {}
    seen: set[str] = set()
    total = 0
    for package in packages:
        header, entries = N.read_index(package, key, validate=True, resolver=resolver)
        package_name = package.name
        package_bytes = 0
        rows = []
        for entry in entries:
            real = resolver.resolve(entry.signature)[0]
            if package_name in E.SCRIPT_PACKAGES:
                real = None
            if real:
                rel = real.replace("\\", "/").lstrip("/")
                suffix = Path(rel).suffix.lower()
                kind = kind_for_ext(suffix)
                if suffix in (".jpg", ".jpeg"):
                    kind = "image"
                if suffix == ".wem":
                    kind = "audio"
            elif package_name in E.SCRIPT_PACKAGES:
                rel = f"_script/{Path(package_name).stem}/{entry.signature:08x}.xn"
                suffix = ".xn"
                kind = "script"
            else:
                try:
                    suffix, kind = detect_unresolved(str(package), entry)
                except Exception:
                    suffix, kind = ".bin", "binary"
                rel = f"_unresolved/{Path(package_name).stem}/{entry.signature:08x}{suffix}"
            if rel in seen:
                stem, dot, ext = rel.rpartition(".")
                rel = (stem + "~" + Path(package_name).stem + (dot + ext if dot else ""))
            seen.add(rel)
            parent = rel.rsplit("/", 1)[0] if "/" in rel else ""
            name = rel.rsplit("/", 1)[-1]
            rows.append(
                (
                    package_name, rel, parent, name, entry.signature, entry.offset,
                    entry.packed_size, entry.unpacked_size, entry.compression, entry.flags,
                    entry.packed_hash, entry.unpacked_hash, kind, suffix,
                )
            )
            package_bytes += entry.unpacked_size
            total += 1
            ancestor = parent
            while ancestor:
                stat = dir_stats.setdefault(ancestor, [0, 0])
                stat[0] += 1
                stat[1] += entry.unpacked_size
                ancestor = ancestor.rsplit("/", 1)[0] if "/" in ancestor else ""
            if len(rows) >= 5000:
                connection.executemany(
                    "INSERT INTO files(package,path,dir,name,signature,offset,packed,unpacked,compression,flags,packed_hash,unpacked_hash,kind,ext) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    rows,
                )
                rows.clear()
        if rows:
            connection.executemany(
                "INSERT INTO files(package,path,dir,name,signature,offset,packed,unpacked,compression,flags,packed_hash,unpacked_hash,kind,ext) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                rows,
            )
        connection.execute(
            "INSERT INTO packages(name,path,entries,bytes) VALUES (?,?,?,?)",
            (package_name, str(package), len(entries), package_bytes),
        )
        connection.commit()
        print(f"catalog: {package_name} {len(entries)} entries", flush=True)
    connection.executemany(
        "INSERT INTO dirs(path,parent,file_count,bytes) VALUES (?,?,?,?)",
        [(path, path.rsplit("/", 1)[0] if "/" in path else "", value[0], value[1]) for path, value in dir_stats.items()],
    )
    connection.execute("INSERT INTO meta(key,value) VALUES ('schema',?)", (SCHEMA_VERSION,))
    connection.execute("INSERT INTO meta(key,value) VALUES ('fingerprint',?)", (json.dumps(meta, ensure_ascii=False, sort_keys=True),))
    connection.execute("INSERT INTO meta(key,value) VALUES ('created',?)", (time.strftime("%Y-%m-%d %H:%M:%S"),))
    connection.execute("INSERT INTO meta(key,value) VALUES ('toolkit',?)", (TOOLKIT_VERSION,))
    connection.commit()
    connection.execute("PRAGMA optimize")
    connection.close()
    os.replace(tmp, db_path)
    print(f"catalog ready: {db_path} ({total} files)", flush=True)
    return meta


def ensure_catalog(
    db_path: Path,
    game_root: Path,
    profile: dict,
    key: bytes,
    seed: int,
    path_dict: Path | None,
    packages: list[Path],
    resolver: N.PathResolver,
    rebuild: bool,
) -> dict:
    wanted = fingerprint(game_root, profile, key, seed, path_dict, packages)
    if not rebuild and db_path.is_file():
        try:
            connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
            row = connection.execute("SELECT value FROM meta WHERE key='fingerprint'").fetchone()
            connection.close()
            if row:
                stored = json.loads(row[0])
                # Older catalogs also stored a toolkit version; ignore extra keys.
                if all(stored.get(key) == value for key, value in wanted.items()):
                    print(f"catalog reused: {db_path}", flush=True)
                    return wanted
        except Exception:
            pass
    if db_path.exists():
        db_path.unlink()
    return build_catalog(db_path, game_root, profile, key, seed, path_dict, packages, resolver)


class BrowserServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    db_path: Path
    cache_root: Path
    artifacts: Path
    cache_limit: int
    vgmstream: str | None
    fingerprint: dict
    _cache_lock = threading.Lock()
    _thumb_sem = threading.Semaphore(3)
    _gmp_sem = threading.Semaphore(2)
    _gmp_cache_lock = threading.Lock()
    _gmp_cache: dict = {}
    _xn_sem = threading.Semaphore(2)
    _xn_cache_lock = threading.Lock()
    _xn_cache: dict = {}
    _xn_source_lock = threading.Lock()
    _xn_source_cache: dict = {}
    _xn_decompile_sem = threading.Semaphore(1)
    _xn_names_lock = threading.Lock()
    _xn_names = None
    _xn_names_mtime = 0.0
    xn_names_path = None
    xn_opmap_path = None
    _last_prune = 0.0

    def read_artifact(self, path: Path) -> bytes | None:
        with self._cache_lock:
            try:
                return path.read_bytes()
            except FileNotFoundError:
                return None

    def prune_cache(self) -> None:
        if self.cache_limit <= 0:
            return
        now = time.monotonic()
        with self._cache_lock:
            if now - self._last_prune < 15:
                return
            self._last_prune = now
        threading.Thread(target=self._prune_cache_worker, daemon=True).start()

    def _prune_cache_worker(self) -> None:
        files = []
        for path in self.artifacts.rglob("*"):
            if not path.is_file() or path.name.endswith(".tmp"):
                continue
            try:
                stat = path.stat()
            except OSError:
                continue
            files.append((stat.st_mtime, stat.st_size, path))
        total = sum(item[1] for item in files)
        if total <= self.cache_limit:
            return
        files.sort(key=lambda item: item[0])
        target = int(self.cache_limit * 0.8)
        for _, size, path in files:
            if total <= target:
                break
            try:
                path.unlink()
                total -= size
            except OSError:
                pass

    def artifact(self, row, suffix: str, variant: str = "") -> Path:
        raw = f"{row['package']}|{row['path']}|{row['signature']}|{variant}".encode("utf-8")
        name = hashlib.sha256(raw).hexdigest()[:32] + suffix
        target = self.artifacts / name
        target.parent.mkdir(parents=True, exist_ok=True)
        return target


def db_connect(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def find_vscode() -> str | None:
    for name in ("code", "code.cmd", "Code.exe"):
        found = shutil.which(name)
        if found:
            return found
    candidates = [
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Microsoft VS Code" / "Code.exe",
        Path(os.environ.get("ProgramFiles", "")) / "Microsoft VS Code" / "Code.exe",
        Path("/Applications/Visual Studio Code.app/Contents/Resources/app/bin/code"),
        Path.home() / "Applications" / "Visual Studio Code.app" / "Contents" / "Resources" / "app" / "bin" / "code",
        Path("/usr/share/code/code"),
        Path("/snap/bin/code"),
        Path.home() / ".local" / "bin" / "code",
    ]
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    return None


def find_idea() -> str | None:
    for name in ("idea64.exe", "idea.exe", "idea.bat", "idea"):
        found = shutil.which(name)
        if found:
            return found
    patterns = []
    local = os.environ.get("LOCALAPPDATA", "")
    programs = os.environ.get("ProgramFiles", "")
    if local:
        patterns.extend(
            [
                str(Path(local) / "Programs" / "IntelliJ IDEA*" / "bin" / "idea64.exe"),
                str(Path(local) / "JetBrains" / "Toolbox" / "apps" / "IDEA-U" / "ch-*" / "bin" / "idea64.exe"),
                str(Path(local) / "JetBrains" / "Toolbox" / "apps" / "IDEA-C" / "ch-*" / "bin" / "idea64.exe"),
            ]
        )
    if programs:
        patterns.append(str(Path(programs) / "JetBrains" / "IntelliJ IDEA*" / "bin" / "idea64.exe"))
    patterns.extend(
        [
            "/Applications/IntelliJ IDEA.app/Contents/MacOS/idea",
            "/Applications/IntelliJ IDEA CE.app/Contents/MacOS/idea",
            str(Path.home() / "Applications" / "IntelliJ IDEA.app" / "Contents" / "MacOS" / "idea"),
            "/opt/idea/bin/idea.sh",
            "/snap/bin/intellij-idea-ultimate",
            "/snap/bin/intellij-idea-community",
            str(Path.home() / ".local" / "share" / "JetBrains" / "Toolbox" / "apps" / "IDEA-U" / "ch-*" / "bin" / "idea.sh"),
            str(Path.home() / ".local" / "share" / "JetBrains" / "Toolbox" / "apps" / "IDEA-C" / "ch-*" / "bin" / "idea.sh"),
        ]
    )
    for pattern in patterns:
        matches = glob.glob(pattern)
        if matches:
            return matches[0]
    return None


def launch_app(app: str, target: Path) -> tuple[bool, str]:
    target_s = str(target)
    if app == "explorer":
        if sys.platform != "win32":
            return False, "资源管理器仅支持 Windows"
        subprocess.Popen(["explorer", f"/select,{target_s}"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True)
        return True, "已在资源管理器中定位文件"
    if app == "finder":
        if sys.platform != "darwin":
            return False, "访达仅支持 macOS"
        subprocess.Popen(["open", "-R", target_s], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True)
        return True, "已在访达中定位文件"
    if app == "vscode":
        executable = find_vscode()
        if not executable:
            return False, "未找到 VS Code，请先安装或把 code 加入 PATH"
        command = [executable, target_s]
        if executable.lower().endswith((".cmd", ".bat")):
            command = ["cmd", "/c", executable, target_s]
        elif executable.lower().endswith(".sh"):
            command = ["bash", executable, target_s]
        subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True)
        return True, "已用 VS Code 打开临时副本"
    if app == "idea":
        executable = find_idea()
        if not executable:
            return False, "未找到 IntelliJ IDEA，请先安装或把 idea64.exe 加入 PATH"
        command = [executable, target_s]
        if executable.lower().endswith(".bat"):
            command = ["cmd", "/c", executable, target_s]
        elif executable.lower().endswith(".sh"):
            command = ["bash", executable, target_s]
        subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True)
        return True, "已用 IntelliJ IDEA 打开临时副本"
    return False, "不支持的程序"


class Handler(BaseHTTPRequestHandler):
    server: BrowserServer
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        return

    def handle(self):
        try:
            super().handle()
        except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError):
            pass

    def _query(self):
        parsed = urlparse(self.path)
        return parsed.path, parse_qs(parsed.query)

    def _row(self, path: str):
        connection = db_connect(self.server.db_path)
        try:
            return connection.execute(
                "SELECT f.*, p.path AS package_path FROM files f JOIN packages p ON f.package=p.name WHERE f.path=?",
                (path,),
            ).fetchone()
        finally:
            connection.close()

    def _send(self, data: bytes, content_type: str, status: int = 200, filename: str | None = None, download: bool = False, cache: str | None = None):
        total_size = len(data)
        start = 0
        end = len(data) - 1
        range_header = self.headers.get("Range")
        if range_header and len(data) > 0:
            match = re.match(r"bytes=(\d*)-(\d*)$", range_header.strip())
            if match:
                if match.group(1):
                    start = int(match.group(1))
                    end = int(match.group(2)) if match.group(2) else len(data) - 1
                elif match.group(2):
                    start = max(0, len(data) - int(match.group(2)))
                end = min(end, len(data) - 1)
                if start <= end:
                    status = 206
                    data = data[start:end + 1]
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Accept-Ranges", "bytes")
        no_store = content_type.startswith("application/json") or content_type.startswith("audio/")
        self.send_header("Cache-Control", cache or ("no-store" if no_store else "private, max-age=3600"))
        if status == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{total_size}")
        if filename:
            disposition = "attachment" if download else "inline"
            self.send_header("Content-Disposition", f"{disposition}; filename*=UTF-8''{quote(filename)}")
        self.end_headers()
        try:
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _json(self, value, status: int = 200):
        self._send(json.dumps(value, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8", status)

    def _error(self, message: str, status: int = 400):
        self._json({"error": message}, status)

    def _entry_payload(self, row) -> bytes:
        return read_entry_bytes(row["package_path"], row)

    def _ensure_png(self, row) -> tuple[bytes, str]:
        payload = self._entry_payload(row)
        kind = image_convert.classify(payload, row["ext"] or "")
        if kind == "png":
            return payload, "image/png"
        if kind == "jpeg":
            return payload, "image/jpeg"
        if kind not in ("ktx1", "ktx2", "dds", "jpeg", "tga", "bmp", "webp", "gif"):
            raise ValueError("not a supported image")
        if self.server.cache_limit <= 0:
            tmp = self.server.cache_root / "tmp" / f"{uuid.uuid4().hex}.png"
            tmp.parent.mkdir(parents=True, exist_ok=True)
            try:
                image_convert.convert_image(payload, row["ext"] or "", tmp)
                return tmp.read_bytes(), "image/png"
            finally:
                try:
                    tmp.unlink()
                except FileNotFoundError:
                    pass
        cache = self.server.artifact(row, ".png", "image")
        cached = self.server.read_artifact(cache)
        if cached is not None:
            return cached, "image/png"
        tmp = cache.with_suffix(f".{uuid.uuid4().hex}.tmp")
        try:
            image_convert.convert_image(payload, row["ext"] or "", tmp)
            data = tmp.read_bytes()
            os.replace(tmp, cache)
        finally:
            try:
                tmp.unlink()
            except FileNotFoundError:
                pass
        self.server.prune_cache()
        return data, "image/png"

    def _decode_wwise(self, payload: bytes, ext: str) -> tuple[bytes, str, str]:
        if not self.server.vgmstream:
            raise ValueError("vgmstream-cli not available")
        tmpdir = self.server.cache_root / "tmp"
        tmpdir.mkdir(parents=True, exist_ok=True)
        token = uuid.uuid4().hex
        src = tmpdir / f"{token}{ext}"
        wav = tmpdir / f"{token}.wav"
        try:
            src.write_bytes(payload)
            result = subprocess.run(
                [self.server.vgmstream, "-o", str(wav), str(src)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                timeout=180,
            )
            if result.returncode != 0 or not wav.is_file() or wav.stat().st_size < 44:
                raise ValueError("audio decode failed")
            if wav.stat().st_size > 500 * 1024 * 1024:
                raise ValueError("audio too large for preview")
            if _soundfile is not None:
                samples, rate = _soundfile.read(str(wav), dtype="int16", always_2d=True)
                buffer = io.BytesIO()
                _soundfile.write(buffer, samples, rate, format="FLAC", subtype="PCM_16")
                return buffer.getvalue(), "audio/flac", "flac"
            data = wav.read_bytes()
            return data, "audio/wav", "wav"
        finally:
            for path in (src, wav):
                try:
                    path.unlink()
                except FileNotFoundError:
                    pass

    def _audio(self, row, media: str | None = None) -> tuple[bytes, str, str]:
        ext = (row["ext"] or "").lower()
        payload = self._entry_payload(row)
        if ext in (".wav", ".flac", ".ogg", ".mp3", ".m4a", ".aac", ".opus"):
            return payload, MIME.get(ext, "application/octet-stream"), ext[1:]
        if ext not in (".wem", ".bnk"):
            raise ValueError("not an audio entry")
        if ext == ".bnk":
            try:
                bank = bnk_parse.parse_bank(payload)
            except bnk_parse.BankFormatError:
                bank = None
            if bank is not None and bank.media:
                if media:
                    try:
                        media_id = int(media, 0)
                    except ValueError:
                        raise ValueError("invalid embedded media id") from None
                    _, payload = bank.media_payload(media_id=media_id)
                else:
                    _, payload = bank.media_payload(index=0)
                return self._decode_wwise(payload, ".wem")
            if media:
                raise ValueError("no embedded media in this bank")
        return self._decode_wwise(payload, ".bnk" if ext == ".bnk" else ".wem")

    def _materialize(self, row) -> tuple[Path, bool]:
        payload = self._entry_payload(row)
        converted = False
        name = Path(row["path"]).name
        if row["kind"] == "image":
            try:
                data, ctype = self._ensure_png(row)
                payload = data
                if ctype == "image/png":
                    name = Path(row["path"]).stem + ".png"
                    converted = (row["ext"] or "").lower() != ".png"
                elif ctype == "image/jpeg":
                    name = Path(row["path"]).stem + ".jpg"
                else:
                    name = Path(row["path"]).name
            except Exception:
                pass
        name = re.sub(r'[<>:"/\|?*\x00-]', "_", name).strip(" .")
        if not name:
            name = f"entry_{row['signature']:08x}.bin"
        key = hashlib.sha1(row["path"].encode("utf-8", "ignore")).hexdigest()[:20]
        target_dir = self.server.cache_root / "open" / hashlib.sha1(
            json.dumps(self.server.fingerprint, sort_keys=True).encode()
        ).hexdigest()[:12] / key
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / name
        if not target.is_file() or target.stat().st_size != len(payload):
            tmp = target.with_suffix(target.suffix + f".{uuid.uuid4().hex}.tmp")
            try:
                tmp.write_bytes(payload)
                os.replace(tmp, target)
            finally:
                try:
                    tmp.unlink()
                except FileNotFoundError:
                    pass
        return target, converted

    def _image_thumb(self, row, width: int) -> bytes:
        cache = self.server.artifact(row, f"_img_w{width}.png", "thumb")
        thumb = self.server.read_artifact(cache)
        if thumb is not None:
            return thumb
        data, _ = self._ensure_png(row)
        image = Image.open(io.BytesIO(data))
        image.load()
        image.thumbnail((width, width))
        if image.mode not in ("RGB", "RGBA"):
            image = image.convert("RGBA")
        tmp = cache.with_suffix(f".{uuid.uuid4().hex}.tmp")
        try:
            image.save(tmp, "PNG")
            thumb = tmp.read_bytes()
            os.replace(tmp, cache)
        finally:
            try:
                tmp.unlink()
            except FileNotFoundError:
                pass
        self.server.prune_cache()
        return thumb

    def _decode_wav_file(self, payload: bytes, ext: str) -> Path:
        if not self.server.vgmstream:
            raise ValueError("vgmstream-cli not available")
        tmpdir = self.server.cache_root / "tmp"
        tmpdir.mkdir(parents=True, exist_ok=True)
        token = uuid.uuid4().hex
        src = tmpdir / f"{token}{ext}"
        wav = tmpdir / f"{token}.wav"
        try:
            src.write_bytes(payload)
            result = subprocess.run(
                [self.server.vgmstream, "-o", str(wav), str(src)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                timeout=180,
            )
            if result.returncode != 0 or not wav.is_file() or wav.stat().st_size < 44:
                raise ValueError("audio decode failed")
            return wav
        except Exception:
            try:
                wav.unlink()
            except FileNotFoundError:
                pass
            raise
        finally:
            try:
                src.unlink()
            except FileNotFoundError:
                pass

    def _waveform_samples(self, row, max_seconds: int = 60):
        """Decode just enough audio for a waveform; no intermediate FLAC."""
        ext = (row["ext"] or "").lower()
        payload = self._entry_payload(row)
        source = None
        try:
            if ext == ".bnk":
                try:
                    bank = bnk_parse.parse_bank(payload)
                except bnk_parse.BankFormatError:
                    raise ValueError("bank has no embedded media") from None
                if not bank.media:
                    raise ValueError("bank has no embedded media")
                _, payload = bank.media_payload(index=0)
                source = self._decode_wav_file(payload, ".wem")
            elif ext in (".wav", ".flac", ".ogg", ".mp3", ".m4a", ".aac", ".opus"):
                info = _soundfile.info(io.BytesIO(payload))
                frames = min(int(info.frames), int(info.samplerate * max_seconds))
                samples, rate = _soundfile.read(
                    io.BytesIO(payload), dtype="float32", always_2d=True, frames=max(frames, 1)
                )
                return samples.mean(axis=1), rate
            else:
                source = self._decode_wav_file(payload, ext if ext.startswith(".") else ".wem")
            info = _soundfile.info(str(source))
            frames = min(int(info.frames), int(info.samplerate * max_seconds))
            samples, rate = _soundfile.read(
                str(source), dtype="float32", always_2d=True, frames=max(frames, 1)
            )
            return samples.mean(axis=1), rate
        finally:
            if source is not None:
                try:
                    source.unlink()
                except FileNotFoundError:
                    pass

    def _waveform_thumb(self, row, width: int) -> bytes:
        if _soundfile is None or _np is None:
            raise ValueError("waveform preview unavailable")
        cache = self.server.artifact(row, f"_wave_w{width}.png", "thumb")
        thumb = self.server.read_artifact(cache)
        if thumb is not None:
            return thumb
        with BrowserServer._thumb_sem:
            thumb = self.server.read_artifact(cache)
            if thumb is not None:
                return thumb
            try:
                mono, _rate = self._waveform_samples(row)
            except ValueError:
                raise
            except Exception as exc:
                raise ValueError(f"cannot decode audio: {exc}") from None
            if not mono.size:
                raise ValueError("audio has no samples")
            height = max(48, int(round(width * 0.625)))
            bins = _np.array_split(_np.abs(mono), width)
            peaks = _np.array(
                [float(part.max()) if part.size else 0.0 for part in bins], dtype="float32"
            )
            peak = float(peaks.max())
            peaks = _np.sqrt(peaks / peak) if peak > 0 else _np.zeros(width, dtype="float32")
            highlight = float(_np.percentile(peaks, 99)) if peak > 0 else 1.0
            if highlight >= 1.0:
                # Fully saturated tracks would otherwise paint every lane amber.
                highlight = 1.01
            image = Image.new("RGB", (width, height), (12, 19, 28))
            draw = ImageDraw.Draw(image)
            for y in (height // 4, (height * 3) // 4):
                draw.line((0, y, width - 1, y), fill=(17, 26, 37))
            mid = height / 2
            for x, value in enumerate(peaks):
                amplitude = max(1.0, float(value) * height * 0.44)
                color = (255, 209, 102) if value >= highlight else (110, 231, 216)
                draw.line((x, mid - amplitude, x, mid + amplitude), fill=color)
            draw.line((0, int(mid), width - 1, int(mid)), fill=(34, 48, 67))
            tmp = cache.with_suffix(f".{uuid.uuid4().hex}.tmp")
            try:
                image.save(tmp, "PNG")
                thumb = tmp.read_bytes()
                os.replace(tmp, cache)
            finally:
                try:
                    tmp.unlink()
                except FileNotFoundError:
                    pass
        self.server.prune_cache()
        return thumb

    def _thumbnail(self, row, width: int) -> bytes:
        ext = (row["ext"] or "").lower()
        kind = row["kind"] or ""
        if kind == "image" or ext in IMAGE_EXT:
            return self._image_thumb(row, width)
        if kind in ("audio", "bank") or ext in AUDIO_EXT:
            return self._waveform_thumb(row, width)
        raise ValueError("thumbnail not available for this type")

    def _gmp_data(self, row) -> dict:
        key = (row["path"], row["signature"], row["packed_hash"])
        with BrowserServer._gmp_cache_lock:
            data = BrowserServer._gmp_cache.get(key)
        if data is not None:
            return data
        with BrowserServer._gmp_sem:
            data = gmp_parse.parse(self._entry_payload(row))
        data["path"] = row["path"]
        if not data.get("map_name"):
            data["map_name"] = Path(row["path"]).stem
        with BrowserServer._gmp_cache_lock:
            BrowserServer._gmp_cache[key] = data
            while len(BrowserServer._gmp_cache) > 8:
                BrowserServer._gmp_cache.pop(next(iter(BrowserServer._gmp_cache)))
        return data

    def _xn_name_map(self) -> dict:
        path = Path(self.server.xn_names_path) if self.server.xn_names_path else Path(__file__).resolve().parent.parent / "data" / "xn_names.json"
        try:
            mtime = path.stat().st_mtime if path.is_file() else 0.0
        except OSError:
            mtime = 0.0
        with BrowserServer._xn_names_lock:
            if BrowserServer._xn_names is None or mtime != BrowserServer._xn_names_mtime:
                mapping = {}
                try:
                    payload = json.loads(path.read_text("utf-8"))
                    mapping = {int(key): value for key, value in payload.get("map", {}).items()}
                except (OSError, ValueError, TypeError):
                    mapping = {}
                BrowserServer._xn_names = mapping
                BrowserServer._xn_names_mtime = mtime
            return BrowserServer._xn_names

    def _xn_data(self, row) -> dict:
        signature = int(row["signature"])
        salt = self._xn_name_map().get(signature)
        result = {"path": row["path"], "signature": f"{signature:08x}", "module": salt}
        payload = self._entry_payload(row)
        sizes = xn_crypto.header(payload)
        if not sizes:
            result.update({"decrypted": False, "reason": "不是有效的 XN 容器"})
            return result
        result["compressed"], result["original"] = sizes
        if not salt:
            result.update({"decrypted": False, "reason": "模块名尚未索引，无法解密"})
            return result
        key = (signature, salt, row["packed_hash"])
        with BrowserServer._xn_cache_lock:
            cached = BrowserServer._xn_cache.get(key)
        if cached is not None:
            return cached
        with BrowserServer._xn_sem:
            plain = xn_crypto.decrypt(payload, salt)
        if plain is None:
            result.update({"decrypted": False, "reason": "解密失败"})
            return result
        try:
            preview = xn_parse.preview(plain)
        except Exception as exc:
            result.update({"decrypted": False, "reason": f"解析失败: {exc}"})
            return result
        result["decrypted"] = True
        result["preview"] = preview
        with BrowserServer._xn_cache_lock:
            BrowserServer._xn_cache[key] = result
            while len(BrowserServer._xn_cache) > 32:
                BrowserServer._xn_cache.pop(next(iter(BrowserServer._xn_cache)))
        return result

    def _xn_disasm(self, row, func: str = "") -> dict:
        signature = int(row["signature"])
        salt = self._xn_name_map().get(signature)
        if not salt:
            return {"error": "模块名尚未索引", "signature": f"{signature:08x}"}
        payload = self._entry_payload(row)
        plain = xn_crypto.decrypt(payload, salt)
        if plain is None:
            return {"error": "解密失败", "signature": f"{signature:08x}"}
        try:
            text = xn_parse.disasm_text(plain, func, 1600)
        except Exception as exc:
            return {"error": f"反汇编失败: {exc}", "signature": f"{signature:08x}"}
        return {
            "path": row["path"],
            "signature": f"{signature:08x}",
            "module": salt,
            "function": func or "<module>",
            "disassembly": text,
        }

    def _uncompyle_exe(self) -> str | None:
        candidates = [shutil.which("uncompyle6")]
        for base in (os.environ.get("APPDATA"), os.environ.get("LOCALAPPDATA")):
            if not base:
                continue
            candidates.extend(glob.glob(str(Path(base) / "Python" / "Python*" / "Scripts" / "uncompyle6.exe")))
            candidates.extend(glob.glob(str(Path(base) / "Programs" / "Python" / "Python*" / "Scripts" / "uncompyle6.exe")))
        candidates.append(r"C:\Python314\Scripts\uncompyle6.exe")
        for candidate in candidates:
            if candidate and Path(candidate).is_file():
                return candidate
        return None

    def _xn_source(self, row) -> dict:
        signature = int(row["signature"])
        salt = self._xn_name_map().get(signature)
        if not salt:
            return {"error": "模块名尚未索引", "signature": f"{signature:08x}"}
        key = (signature, salt, row["packed_hash"])
        with BrowserServer._xn_source_lock:
            cached = BrowserServer._xn_source_cache.get(key)
        if cached is not None:
            return cached
        payload = self._entry_payload(row)
        with BrowserServer._xn_sem:
            plain = xn_crypto.decrypt(payload, salt)
        if plain is None:
            return {"error": "解密失败", "signature": f"{signature:08x}"}
        try:
            pyc = xn_decompile.to_pyc(plain)
        except Exception as exc:
            return {"error": f"字节码转换失败: {exc}", "signature": f"{signature:08x}"}
        exe = self._uncompyle_exe()
        if not exe:
            return {"error": "未找到 uncompyle6, 无法反编译", "signature": f"{signature:08x}"}
        tmp = self.server.cache_root / "tmp" / f"{uuid.uuid4().hex}.pyc"
        tmp.parent.mkdir(parents=True, exist_ok=True)
        try:
            tmp.write_bytes(pyc)
            with BrowserServer._xn_decompile_sem:
                proc = subprocess.run(
                    [exe, str(tmp)],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=60,
                    cwd=str(tmp.parent),
                )
        except subprocess.TimeoutExpired:
            return {"error": "反编译超时", "signature": f"{signature:08x}"}
        finally:
            try:
                tmp.unlink()
            except FileNotFoundError:
                pass
        source = proc.stdout or ""
        if not source.strip():
            message = (proc.stderr or "uncompyle6 没有输出").strip().splitlines()[-1]
            return {"error": f"反编译失败: {message}", "signature": f"{signature:08x}"}
        result = {
            "path": row["path"],
            "signature": f"{signature:08x}",
            "module": salt,
            "source": source,
            "source_bytes": len(source.encode("utf-8")),
        }
        with BrowserServer._xn_source_lock:
            BrowserServer._xn_source_cache[key] = result
            while len(BrowserServer._xn_source_cache) > 16:
                BrowserServer._xn_source_cache.pop(next(iter(BrowserServer._xn_source_cache)))
        return result

    def _text_snippet(self, row, max_chars: int, max_lines: int) -> tuple[str, bool]:
        payload = self._entry_payload(row)
        head = payload[: 128 * 1024]
        if b"\x00" in head[:4096]:
            raise ValueError("binary data; no text preview")
        text = None
        for encoding in ("utf-8-sig", "utf-8", "gb18030"):
            try:
                text = head.decode(encoding)
                break
            except UnicodeDecodeError:
                continue
        if text is None:
            text = head.decode("latin-1")
        sample = text[:4096]
        printable = sum(1 for char in sample if char.isprintable() or char in "\r\n\t")
        if sample and printable < len(sample) * 0.9:
            raise ValueError("binary data; no text preview")
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        lines = text.split("\n")
        out: list[str] = []
        used = 0
        for line in lines[:max_lines]:
            line = line.rstrip()
            if used >= max_chars:
                break
            room = max_chars - used
            if len(line) > room:
                out.append(line[:room])
                used = max_chars
                break
            out.append(line)
            used += len(line) + 1
        snippet = "\n".join(out)
        return snippet, len(text) > len(snippet)

    def do_POST(self):
        route, _ = self._query()
        if route != "/api/open":
            self._error("not found", 404)
            return
        origin = self.headers.get("Origin")
        if origin:
            try:
                if urlparse(origin).hostname not in ("127.0.0.1", "localhost", "::1"):
                    self._error("origin not allowed", 403)
                    return
            except Exception:
                self._error("bad origin", 403)
                return
        if "application/json" not in (self.headers.get("Content-Type") or ""):
            self._error("expected application/json", 415)
            return
        try:
            length = int(self.headers.get("Content-Length", "0") or 0)
            if length <= 0 or length > 65536:
                raise ValueError("invalid body length")
            body = json.loads(self.rfile.read(length).decode("utf-8"))
            path = str(body.get("path", "")).strip("/")
            app = str(body.get("app", "")).strip().lower()
            row = self._row(path)
            if not row:
                self._error("not found", 404)
                return
            target, converted = self._materialize(row)
            ok, message = launch_app(app, target)
            if not ok:
                self._error(message, 400)
                return
            self._json({
                "ok": True, "message": message, "app": app,
                "file": str(target), "converted": converted,
            })
        except Exception as exc:
            self._error(f"open failed: {exc}", 500)

    def do_GET(self):
        route, query = self._query()
        try:
            if route == "/" or route == "/index.html":
                self._send((WEB_DIR / "index.html").read_bytes(), "text/html; charset=utf-8", cache="no-store")
                return
            if route.startswith("/static/"):
                name = route[len("/static/"):]
                if name not in {"app.js", "style.css"}:
                    self._error("not found", 404)
                    return
                ctype = "application/javascript; charset=utf-8" if name.endswith(".js") else "text/css; charset=utf-8"
                self._send((WEB_DIR / name).read_bytes(), ctype, cache="no-store")
                return
            if route == "/favicon.ico":
                self.send_response(204)
                self.end_headers()
                return
            if route == "/api/stats":
                connection = db_connect(self.server.db_path)
                try:
                    total, total_bytes = connection.execute("SELECT COUNT(*), COALESCE(SUM(unpacked),0) FROM files").fetchone()
                    dirs = connection.execute("SELECT COUNT(*) FROM dirs").fetchone()[0]
                    unresolved = connection.execute("SELECT COUNT(*) FROM files WHERE substr(path,1,12)='_unresolved/'").fetchone()[0]
                    scripts = connection.execute("SELECT COUNT(*) FROM files WHERE substr(path,1,8)='_script/'").fetchone()[0]
                    packages = [dict(row) for row in connection.execute("SELECT * FROM packages ORDER BY name")]
                    roots = [dict(row) for row in connection.execute("SELECT path,parent,file_count,bytes FROM dirs WHERE parent='' ORDER BY path")]
                    kinds = {row[0]: row[1] for row in connection.execute("SELECT kind,COUNT(*) FROM files GROUP BY kind")}
                finally:
                    connection.close()
                meta = self.server.fingerprint
                self._json(
                    {
                        "files": total,
                        "bytes": total_bytes,
                        "dirs": dirs,
                        "unresolved": unresolved,
                        "scripts": scripts,
                        "packages": packages,
                        "roots": roots,
                        "kinds": kinds,
                        "profile": meta.get("profile"),
                        "platform": sys.platform,
                        "cache_mb": round(self.server.cache_limit / 1024 / 1024, 1),
                    }
                )
                return
            if route == "/api/list":
                path = (query.get("path", [""])[0] or "").strip("/").replace("\\", "/")
                sort_key = query.get("sort", ["name_asc"])[0]
                sort_expr = SORT_EXPRESSIONS.get(sort_key, SORT_EXPRESSIONS["name_asc"])
                kind_filter = (query.get("kind", [""])[0] or "").strip()
                if kind_filter not in KINDS:
                    kind_filter = ""
                offset = max(int(query.get("offset", ["0"])[0] or 0), 0)
                limit = min(max(int(query.get("limit", ["1500"])[0] or 1500), 1), 3000)
                connection = db_connect(self.server.db_path)
                try:
                    dirs = [
                        {"name": row["path"].rsplit("/", 1)[-1], "path": row["path"], "count": row["file_count"], "bytes": row["bytes"], "kind": "dir"}
                        for row in connection.execute("SELECT * FROM dirs WHERE parent=? ORDER BY path COLLATE NOCASE", (path,))
                    ]
                    if sort_key == "name_desc":
                        dirs.reverse()
                    elif sort_key in ("size_asc", "size_desc"):
                        dirs.sort(key=lambda item: (item["bytes"], item["path"]), reverse=sort_key == "size_desc")
                    where = "dir=?"
                    params = [path]
                    if kind_filter:
                        where += " AND kind=?"
                        params.append(kind_filter)
                    total_files = connection.execute(f"SELECT COUNT(*) FROM files WHERE {where}", params).fetchone()[0]
                    file_rows = connection.execute(
                        f"SELECT * FROM files WHERE {where} ORDER BY {sort_expr} LIMIT ? OFFSET ?",
                        params + [limit, offset],
                    ).fetchall()
                    kind_counts = {
                        row[0]: row[1]
                        for row in connection.execute("SELECT kind,COUNT(*) FROM files WHERE dir=? GROUP BY kind", (path,))
                    }
                    if path:
                        summary_row = connection.execute("SELECT file_count,bytes FROM dirs WHERE path=?", (path,)).fetchone()
                        dir_summary = {"files": summary_row[0], "bytes": summary_row[1]} if summary_row else {"files": 0, "bytes": 0}
                    else:
                        summary_row = connection.execute("SELECT COUNT(*),COALESCE(SUM(unpacked),0) FROM files").fetchone()
                        dir_summary = {"files": summary_row[0], "bytes": summary_row[1]}
                    files = [
                        {
                            "name": row["name"], "path": row["path"], "size": row["unpacked"],
                            "kind": row["kind"], "ext": row["ext"], "package": row["package"],
                            "signature": f"{row['signature']:08x}",
                        }
                        for row in file_rows
                    ]
                finally:
                    connection.close()
                crumbs = []
                current = ""
                for part in [p for p in path.split("/") if p]:
                    current = f"{current}/{part}" if current else part
                    crumbs.append({"name": part, "path": current})
                self._json({
                    "path": path, "dirs": dirs, "files": files, "breadcrumbs": crumbs,
                    "file_total": total_files, "offset": offset, "limit": limit,
                    "truncated": offset + len(files) < total_files,
                    "kind_counts": kind_counts, "kind_filter": kind_filter,
                    "dir_summary": dir_summary, "sort": sort_key,
                })
                return
            if route == "/api/search":
                query_text = (query.get("q", [""])[0] or "").strip()
                if not query_text:
                    self._error("empty query", 400)
                    return
                limit = min(max(int(query.get("limit", ["1500"])[0] or 1500), 1), 3000)
                offset = max(int(query.get("offset", ["0"])[0] or 0), 0)
                sort_key = query.get("sort", ["name_asc"])[0]
                sort_expr = SORT_EXPRESSIONS.get(sort_key, SORT_EXPRESSIONS["name_asc"])
                pattern = "%" + query_text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
                connection = db_connect(self.server.db_path)
                try:
                    kind_counts = {
                        row[0]: row[1]
                        for row in connection.execute(
                            "SELECT kind,COUNT(*) FROM files WHERE lower(path) LIKE lower(?) ESCAPE '\\' GROUP BY kind",
                            (pattern,),
                        )
                    }
                    total_matches = sum(kind_counts.values())
                    rows = connection.execute(
                        f"SELECT name,path,unpacked,kind,ext,package,signature FROM files WHERE lower(path) LIKE lower(?) ESCAPE '\\' ORDER BY {sort_expr} LIMIT ? OFFSET ?",
                        (pattern, limit, offset),
                    )
                    files = [
                        {
                            "name": row["name"], "path": row["path"], "size": row["unpacked"],
                            "kind": row["kind"], "ext": row["ext"], "package": row["package"],
                            "signature": f"{row['signature']:08x}",
                        }
                        for row in rows
                    ]
                finally:
                    connection.close()
                self._json({
                    "query": query_text, "files": files, "sort": sort_key,
                    "total": total_matches, "offset": offset, "limit": limit,
                    "truncated": offset + len(files) < total_matches,
                    "kind_counts": kind_counts,
                })
                return
            if route == "/api/info":
                path = (query.get("path", [""])[0] or "").strip("/")
                row = self._row(path)
                if not row:
                    self._error("not found", 404)
                    return
                self._json(
                    {
                        "path": row["path"], "name": row["name"], "package": row["package"],
                        "package_path": row["package_path"], "signature": f"{row['signature']:08x}",
                        "offset": row["offset"], "packed": row["packed"], "unpacked": row["unpacked"],
                        "compression": row["compression"], "flags": f"{row['flags']:#06x}",
                        "packed_hash": f"{row['packed_hash']:08x}", "unpacked_hash": f"{row['unpacked_hash']:08x}",
                        "kind": row["kind"], "ext": row["ext"],
                    }
                )
                return
            if route == "/api/gmp":
                path = (query.get("path", [""])[0] or "").strip("/")
                row = self._row(path)
                if not row:
                    self._error("not found", 404)
                    return
                if (row["ext"] or "").lower() != ".gmp":
                    self._error("not a GMP file", 415)
                    return
                try:
                    data = self._gmp_data(row)
                except Exception as exc:
                    self._error(f"GMP parse failed: {exc}", 415)
                    return
                self._json(data)
                return
            if route == "/api/xn":
                client = (self.client_address[0] or "").lower()
                try:
                    address = ipaddress.ip_address(client)
                    mapped = getattr(address, "ipv4_mapped", None)
                    allowed = address.is_loopback or (mapped is not None and mapped.is_loopback)
                except ValueError:
                    allowed = client == "localhost"
                if not allowed:
                    self._error("XN preview is loopback-only", 403)
                    return
                path = (query.get("path", [""])[0] or "").strip("/")
                row = self._row(path)
                if not row:
                    self._error("not found", 404)
                    return
                if (row["ext"] or "").lower() != ".xn":
                    self._error("not an XN file", 415)
                    return
                if (query.get("decompile", ["0"])[0] or "") == "1":
                    self._json(self._xn_source(row))
                    return
                if (query.get("disasm", ["0"])[0] or "") == "1":
                    self._json(self._xn_disasm(row, (query.get("func", [""])[0] or "")))
                    return
                try:
                    data = self._xn_data(row)
                except Exception as exc:
                    self._error(f"XN decrypt failed: {exc}", 415)
                    return
                self._json(data)
                return
            if route == "/api/inspect":
                path = (query.get("path", [""])[0] or "").strip("/")
                row = self._row(path)
                if not row:
                    self._error("not found", 404)
                    return
                payload = self._entry_payload(row)
                data = resource_inspect.inspect(payload, row["ext"] or "", row["name"] or "")
                data.update(
                    {
                        "path": row["path"], "package": row["package"],
                        "kind": row["kind"], "ext": row["ext"],
                    }
                )
                self._json(data)
                return
            if route == "/api/bank":
                path = (query.get("path", [""])[0] or "").strip("/")
                row = self._row(path)
                if not row:
                    self._error("not found", 404)
                    return
                if (row["ext"] or "").lower() != ".bnk":
                    self._error("not a BNK file", 415)
                    return
                payload = self._entry_payload(row)
                try:
                    bank = bnk_parse.parse_bank(payload)
                except bnk_parse.BankFormatError as exc:
                    self._error(str(exc), 415)
                    return
                data = bank.to_dict()
                data.update(
                    {
                        "path": row["path"], "name": row["name"], "package": row["package"],
                        "size": row["unpacked"],
                    }
                )
                self._json(data)
                return
            if route == "/api/image":
                path = (query.get("path", [""])[0] or "").strip("/")
                row = self._row(path)
                if not row:
                    self._error("not found", 404)
                    return
                try:
                    data, ctype = self._ensure_png(row)
                except ValueError as exc:
                    self._error(str(exc), 415)
                    return
                suffix = ".png" if ctype == "image/png" else ".jpg" if ctype == "image/jpeg" else Path(row["path"]).suffix
                self._send(data, ctype, filename=Path(row["path"]).with_suffix(suffix).name)
                return
            if route == "/api/thumb":
                path = (query.get("path", [""])[0] or "").strip("/")
                row = self._row(path)
                if not row:
                    self._error("not found", 404)
                    return
                width = min(max(int(query.get("w", ["256"])[0] or 256), 32), 1024)
                try:
                    thumb = self._thumbnail(row, width)
                except ValueError as exc:
                    self._error(str(exc), 415)
                    return
                self._send(thumb, "image/png")
                return
            if route == "/api/snippet":
                path = (query.get("path", [""])[0] or "").strip("/")
                row = self._row(path)
                if not row:
                    self._error("not found", 404)
                    return
                chars = min(max(int(query.get("chars", ["480"])[0] or 480), 80), 2000)
                lines = min(max(int(query.get("lines", ["9"])[0] or 9), 1), 30)
                try:
                    text, truncated = self._text_snippet(row, chars, lines)
                except ValueError as exc:
                    self._error(str(exc), 415)
                    return
                self._json({"path": row["path"], "text": text, "truncated": truncated})
                return
            if route == "/api/audio":
                path = (query.get("path", [""])[0] or "").strip("/")
                row = self._row(path)
                if not row:
                    self._error("not found", 404)
                    return
                try:
                    data, ctype, ext = self._audio(row, query.get("media", [None])[0])
                except ValueError as exc:
                    self._error(str(exc), 415)
                    return
                filename = f"{Path(row['path']).stem}.{ext}"
                if query.get("media", [None])[0]:
                    filename = f"{Path(row['path']).stem}_{query['media'][0]}.{ext}"
                self._send(data, ctype, filename=filename, download=query.get("download", ["0"])[0] == "1")
                return
            if route == "/api/text":
                path = (query.get("path", [""])[0] or "").strip("/")
                row = self._row(path)
                if not row:
                    self._error("not found", 404)
                    return
                payload = self._entry_payload(row)
                head = payload[: 2 * 1024 * 1024]
                if b"\x00" in head[:4096]:
                    self._error("binary data; use hex/download", 415)
                    return
                text = None
                for encoding in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
                    try:
                        text = head.decode(encoding)
                        break
                    except UnicodeDecodeError:
                        continue
                if text is None:
                    self._error("cannot decode text", 415)
                    return
                self._send(text.encode("utf-8"), "text/plain; charset=utf-8")
                return
            if route == "/api/hex":
                path = (query.get("path", [""])[0] or "").strip("/")
                row = self._row(path)
                if not row:
                    self._error("not found", 404)
                    return
                payload = self._entry_payload(row)
                head = payload[:4096]
                lines = []
                for offset in range(0, len(head), 16):
                    chunk = head[offset:offset + 16]
                    hexpart = " ".join(f"{b:02x}" for b in chunk)
                    asciipart = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
                    lines.append(f"{offset:08x}  {hexpart:<47}  {asciipart}")
                self._json({"size": len(payload), "shown": len(head), "lines": lines, "truncated": len(payload) > len(head)})
                return
            if route == "/api/video":
                path = (query.get("path", [""])[0] or "").strip("/")
                row = self._row(path)
                if not row:
                    self._error("not found", 404)
                    return
                payload = self._entry_payload(row)
                ext = (row["ext"] or "").lower()
                self._send(payload, MIME.get(ext, "video/mp4"), filename=row["name"])
                return
            if route == "/api/download":
                path = (query.get("path", [""])[0] or "").strip("/")
                row = self._row(path)
                if not row:
                    self._error("not found", 404)
                    return
                payload = self._entry_payload(row)
                self._send(payload, "application/octet-stream", filename=row["name"], download=True)
                return
            self._error("not found", 404)
        except BrokenPipeError:
            pass
        except (ConnectionAbortedError, ConnectionResetError):
            pass
        except Exception as exc:
            import traceback

            traceback.print_exc()
            try:
                self._error(f"internal error: {exc}", 500)
            except Exception:
                pass


def main() -> int:
    parser = argparse.ArgumentParser(description="Local Eggy Party NPK browser")
    parser.add_argument("--game-root", type=Path, default=None)
    parser.add_argument("--profile", type=Path, default=None)
    parser.add_argument("--path-dict", "--paths", dest="path_dict", type=Path, default=None)
    parser.add_argument("--key-hex", type=str, default=None)
    parser.add_argument("--tables", type=Path, default=None)
    parser.add_argument("--cache-dir", type=Path, default=None)
    parser.add_argument("--cache-limit-mb", type=int, default=2048)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--rebuild-catalog", action="store_true")
    parser.add_argument("--refresh-pathdict", action="store_true")
    parser.add_argument("--no-open", action="store_true")
    args = parser.parse_args()

    game_root = E.find_game_root(args.game_root)
    packages = sorted(game_root.glob("*.npk"))
    if not packages:
        print(f"no NPK files under {game_root}", file=sys.stderr)
        return 4
    cache_root = args.cache_dir or default_cache_dir()
    cache_root.mkdir(parents=True, exist_ok=True)

    profile_path = args.profile
    if profile_path is not None and not Path(profile_path).is_file():
        print(f"warning: profile not found: {profile_path}; generating from local client", file=sys.stderr)
        profile_path = None
    if profile_path is None:
        candidate = E.PACKAGE_ROOT / "data" / "mode3_profile.json"
        if candidate.is_file():
            profile_path = candidate
    if profile_path is None:
        generated_profile = cache_root / "mode3_profile.json"
        if not generated_profile.is_file():
            try:
                generated = rebuild_data.build_profile(game_root, "pathdict.txt.gz")
                generated_profile.write_text(json.dumps(generated, ensure_ascii=False, indent=2) + "\n", "utf-8")
                print(f"generated profile={generated_profile}")
            except (OSError, ValueError) as exc:
                print(f"warning: could not generate profile: {exc}", file=sys.stderr)
        if generated_profile.is_file():
            profile_path = generated_profile
    if profile_path is not None and not Path(profile_path).is_file():
        print("warning: generated profile unavailable; using built-in defaults", file=sys.stderr)
        profile_path = None
    profile = N.load_profile(profile_path)
    key = N.profile_key(profile, args.key_hex)
    seed = N.profile_seed(profile)
    tables = args.tables or profile.get("tables_file")
    if tables:
        tables_path = Path(tables)
        if not tables_path.is_absolute() and profile_path:
            tables_path = Path(profile_path).parent / tables_path
        N.install_tables(tables_path)
    path_dict = None if args.refresh_pathdict else E.find_path_dict(args.path_dict)
    if path_dict is None:
        generated = cache_root / "pathdict.generated.txt.gz"
        try:
            sources = list(game_root.glob("*.npk")) + [
                path for path in (game_root / "ppk").glob("*") if path.is_file()
            ]
            newest = max((path.stat().st_mtime for path in sources), default=0)
            if generated.is_file() and generated.stat().st_mtime >= newest:
                path_dict = generated
                print(f"path dictionary={generated} (generated cache)")
            else:
                print("path dictionary not found; rebuilding it from the game's PPK shards...", flush=True)
                data = rebuild_data.find_path_dictionary(game_root, log=print)
                path_dict = rebuild_data.write_path_dictionary(data, generated, log=print)
        except (OSError, FileNotFoundError) as exc:
            print(f"warning: could not rebuild the path dictionary: {exc}", file=sys.stderr)
            print("warning: entries will use hash names", file=sys.stderr)
    resolver = N.PathResolver.from_file(path_dict, seed) if path_dict else N.PathResolver([], seed)
    db_path = cache_root / "catalog.sqlite"
    print(f"game={game_root}")
    print(f"profile={profile.get('name', 'custom')} key={key.hex()}")
    print(f"cache={cache_root}")
    try:
        meta = ensure_catalog(db_path, game_root, profile, key, seed, path_dict, packages, resolver, args.rebuild_catalog)
    except (N.Mode3FormatError, ValueError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 5
    data_seed = E.PACKAGE_ROOT / "data" / "xn_names.json"
    opmap_path, names_path = xn_bootstrap.ensure(
        db_path,
        cache_root,
        data_seed if data_seed.is_file() else None,
        240.0,
        print,
    )
    xn_parse.OPMAP_PATH = str(opmap_path)
    xn_parse._OPMAP_CACHE = None
    artifacts_root = cache_root / "artifacts"
    artifacts = artifacts_root / hashlib.sha256(json.dumps(meta, sort_keys=True).encode()).hexdigest()[:16]
    artifacts.mkdir(parents=True, exist_ok=True)
    for old in artifacts_root.iterdir() if artifacts_root.exists() else []:
        if old.is_dir() and old.name != artifacts.name:
            shutil.rmtree(old, ignore_errors=True)
    server_class = type(
        "ConfiguredBrowserServer",
        (BrowserServer,),
        {
            "db_path": db_path,
            "cache_root": cache_root,
            "artifacts": artifacts,
            "cache_limit": args.cache_limit_mb * 1024 * 1024,
            "vgmstream": E.find_vgmstream(),
            "fingerprint": meta,
            "xn_names_path": str(names_path),
            "xn_opmap_path": str(opmap_path),
        },
    )
    server = None
    for port in range(args.port, args.port + 20):
        try:
            server = server_class((args.host, port), Handler)
            break
        except OSError:
            continue
    if server is None:
        print("no free port", file=sys.stderr)
        return 6
    url = f"http://{args.host}:{server.server_port}/"
    print(f"NPK Browser: {url}")
    print("Press Ctrl+C to stop.")
    if args.host not in ("127.0.0.1", "localhost", "::1"):
        print("WARNING: server is listening on a non-loopback address", file=sys.stderr)
    if not args.no_open:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopping...")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
