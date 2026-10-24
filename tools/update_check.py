"""Check the latest NPK-X release on GitHub without blocking startup."""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import quote, urlparse

DEFAULT_REPO = "EggyFly/NPK-X"
CACHE_NAME = "update_check.json"
CACHE_TTL = 24 * 60 * 60
REPO_RE = re.compile(r"^[0-9A-Za-z_.-]+/[0-9A-Za-z_.-]+$")
PRERELEASE_RE = re.compile(r"[-._](alpha|beta|rc|pre|preview|dev|nightly|canary|snapshot)\d*", re.I)


def normalize_repo(value: str | None) -> str | None:
    text = (value or "").strip()
    text = re.sub(r"^https?://(?:www\.)?github\.com/", "", text, flags=re.I)
    text = text.strip("/")
    if text.endswith(".git"):
        text = text[:-4]
    if not text:
        return None
    return text if REPO_RE.fullmatch(text) else None


def project_url(repo: str = DEFAULT_REPO) -> str:
    return f"https://github.com/{repo}"


def releases_url(repo: str = DEFAULT_REPO) -> str:
    return f"{project_url(repo)}/releases"


def api_url(repo: str = DEFAULT_REPO) -> str:
    return f"https://api.github.com/repos/{repo}/releases/latest"


def read_version(root: Path) -> str | None:
    try:
        text = (Path(root) / "VERSION").read_text("utf-8-sig").strip()
    except OSError:
        return None
    if not text or not re.fullmatch(r"[0-9A-Za-z._-]+", text):
        return None
    return text


def is_prerelease(value: str | None) -> bool:
    return bool(value and PRERELEASE_RE.search(value))


def base_version(value: str) -> str:
    match = PRERELEASE_RE.search(value or "")
    return value[:match.start()] if match else value


def version_key(value: str) -> tuple:
    parts = re.split(r"[^0-9A-Za-z]+", (value or "").strip().lstrip("vV"))
    key = []
    for part in parts:
        if not part:
            continue
        key.append((0, int(part), "") if part.isdigit() else (1, 0, part.lower()))
    return tuple(key)


def is_newer(current: str, latest: str) -> bool:
    if not current or not latest or is_prerelease(latest):
        return False
    current_base = version_key(base_version(current))
    latest_base = version_key(base_version(latest))
    if latest_base > current_base:
        return True
    return bool(current_base and latest_base == current_base and is_prerelease(current))


def release_url(value: str | None, tag: str, repo: str = DEFAULT_REPO) -> str:
    if value:
        try:
            parsed = urlparse(value)
            valid = (
                parsed.scheme == "https"
                and parsed.hostname == "github.com"
                and not parsed.username
                and not parsed.password
                and parsed.port in (None, 443)
                and (parsed.path == f"/{repo}/releases" or parsed.path.startswith(f"/{repo}/releases/"))
            )
        except ValueError:
            valid = False
        if valid:
            return value
    if tag:
        return f"{releases_url(repo)}/tag/{quote(tag, safe='')}"
    return releases_url(repo)


def load_cache(path: Path) -> dict:
    try:
        data = json.loads(Path(path).read_text("utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def save_cache(path: Path, data: dict) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", "utf-8")
    tmp.replace(path)


def initial_state(current: str | None, repo: str = DEFAULT_REPO, checking: bool = False, source: str = "disabled") -> dict:
    return {
        "current": current,
        "latest": None,
        "url": releases_url(repo),
        "available": False,
        "checked": False,
        "checking": checking,
        "source": source,
        "repo": repo,
        "project_url": project_url(repo),
        "releases_url": releases_url(repo),
    }


def cache_result(cache: dict, current: str | None, repo: str, source: str) -> dict:
    latest = str(cache.get("latest") or "") or None
    url = str(cache.get("url") or "") or (release_url(None, latest, repo) if latest else releases_url(repo))
    return {
        "current": current,
        "latest": latest,
        "url": url,
        "available": bool(current and latest and is_newer(current, latest)),
        "checked": bool(cache.get("checked_at")),
        "checking": False,
        "source": source,
        "repo": repo,
        "project_url": project_url(repo),
        "releases_url": releases_url(repo),
    }


def check(cache_path: Path, current: str | None, repo: str = DEFAULT_REPO, timeout: float = 3.0, proxy: str | None = None, force: bool = False) -> dict:
    repo = normalize_repo(repo) or DEFAULT_REPO
    now = int(time.time())
    cache = load_cache(cache_path)
    if cache.get("repo") != repo:
        cache = {}
    try:
        checked_at = int(cache.get("checked_at") or 0)
    except (TypeError, ValueError):
        checked_at = 0
    if not force and checked_at and now - checked_at < CACHE_TTL:
        return cache_result(cache, current, repo, "cache")
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": f"NPK-X/{current or 'dev'}",
    }
    if cache.get("etag"):
        headers["If-None-Match"] = str(cache["etag"])
    request = urllib.request.Request(api_url(repo), headers=headers)
    if proxy and "://" not in proxy:
        proxy = "http://" + proxy
    try:
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({"http": proxy, "https": proxy}) if proxy else urllib.request.ProxyHandler()
        )
        with opener.open(request, timeout=timeout) as response:
            payload = json.loads(response.read(512 * 1024).decode("utf-8"))
            etag = response.headers.get("ETag")
        if payload.get("prerelease") or is_prerelease(str(payload.get("tag_name") or "")):
            cache["checked_at"] = now
            cache["repo"] = repo
            save_cache(cache_path, cache)
            return cache_result(cache, current, repo, "cache")
        tag = str(payload.get("tag_name") or "").strip()
        if not tag:
            raise ValueError("release has no tag")
        cache = {
            "checked_at": now,
            "latest": tag,
            "url": release_url(payload.get("html_url"), tag, repo),
            "etag": etag or cache.get("etag"),
            "repo": repo,
        }
        save_cache(cache_path, cache)
        return cache_result(cache, current, repo, "network")
    except urllib.error.HTTPError as exc:
        if exc.code == 304 and cache.get("latest"):
            cache["checked_at"] = now
            cache["repo"] = repo
            save_cache(cache_path, cache)
            return cache_result(cache, current, repo, "cache")
        return cache_result(cache, current, repo, "cache")
    except Exception:
        return cache_result(cache, current, repo, "cache")
