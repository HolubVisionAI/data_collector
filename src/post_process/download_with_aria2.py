# python
import os
import glob
import subprocess
import logging
import pandas as pd
import json
import sys
import tempfile
import contextlib
import csv
import importlib.util
from datetime import datetime
from pathlib import Path

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows fallback
    fcntl = None

try:
    import msvcrt
except ImportError:  # pragma: no cover - POSIX fallback
    msvcrt = None

# Ensure the repository root is on sys.path for direct execution of this script.
# This allows `python src/post_process/download_with_aria2.py` to import `src.*`.
project_root = Path(__file__).resolve().parents[2]
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from src.utils.utils import load_config

# ── LOCK HELPERS ─────────────────────────────────────────────────────────
def _lock_file(lock_f):
    if fcntl is not None:
        fcntl.flock(lock_f.fileno(), fcntl.LOCK_EX)
        return
    if msvcrt is not None:
        lock_f.seek(0, os.SEEK_END)
        if lock_f.tell() == 0:
            lock_f.write(b"\0")
            lock_f.flush()
        lock_f.seek(0)
        msvcrt.locking(lock_f.fileno(), msvcrt.LK_LOCK, 1)


def _unlock_file(lock_f):
    if fcntl is not None:
        fcntl.flock(lock_f.fileno(), fcntl.LOCK_UN)
        return
    if msvcrt is not None:
        lock_f.seek(0)
        msvcrt.locking(lock_f.fileno(), msvcrt.LK_UNLCK, 1)


@contextlib.contextmanager
def _locked_file(path: str):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "a+b") as lock_f:
        _lock_file(lock_f)
        try:
            yield
        finally:
            _unlock_file(lock_f)


@contextlib.contextmanager
def _locked_state_file(path: str):
    with _locked_file(path + ".lock"):
        yield

@contextlib.contextmanager
def _locked_download_dir(download_dir: str):
    os.makedirs(download_dir, exist_ok=True)
    lock_path = os.path.join(download_dir, ".aria2_dir.lock")
    with _locked_file(lock_path):
        yield

# ── CONFIG ────────────────────────────────────────────────────────────────
cfg = load_config()["aria2_download"]
INPUT_DIR = cfg["INPUT_DIR"][0]
OUTPUT_DIR = cfg["OUTPUT_DIR"][0]


def _get_config_flag(key: str, default: bool = False) -> bool:
    raw_value = cfg.get(key, [default])
    value = raw_value[0] if isinstance(raw_value, list) and raw_value else raw_value
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"true", "1", "yes", "on"}


def _get_config_value(key: str, default):
    value = cfg.get(key, [default])
    if isinstance(value, list):
        return value[0] if value else default
    return value


ENABLE_PLAYWRIGHT_FALLBACK = _get_config_flag("ENABLE_PLAYWRIGHT_FALLBACK", False)
PLAYWRIGHT_FALLBACK_TIMEOUT_MS = int(cfg.get("PLAYWRIGHT_FALLBACK_TIMEOUT_MS", [60 * 1000])[0])
DEDUPE_AFTER_DOWNLOAD = _get_config_flag("DEDUPE_AFTER_DOWNLOAD", True)
DEDUPE_REPORT_NAME = str(_get_config_value("DEDUPE_REPORT_NAME", "duplicate_files_by_name_size.csv"))
DOWNLOAD_MANIFEST_NAME = ".download_manifest.json"
MHTML_MIN_VALID_BYTES = int(_get_config_value("MHTML_MIN_VALID_BYTES", 250 * 1024))
MHTML_CAPTURE_TIMEOUT_MS = int(_get_config_value("MHTML_CAPTURE_TIMEOUT_MS", 6000))
MHTML_CAPTURE_SETTLE_MS = int(_get_config_value("MHTML_CAPTURE_SETTLE_MS", 300))


BLOCK_PAGE_STRONG_MARKERS = [
    b"google.com/recaptcha",
    b"g-recaptcha",
    b"recaptcha/api",
    b"/sorry/index",
    b"unusual traffic",
    b"our systems have detected unusual traffic",
    b"verify you are human",
    b"prove you are not a robot",
    b"robot check",
    b"cf-challenge",
    b"checking if the site connection is secure",
    b"access denied",
]

BLOCK_PAGE_WEAK_MARKERS = [
    b"captcha",
    b"blocked",
]


def _looks_like_block_page_bytes(content: bytes) -> bool:
    haystack = bytes(content or b"").lower()
    if any(marker in haystack for marker in BLOCK_PAGE_STRONG_MARKERS):
        return True
    return (
        any(marker in haystack for marker in BLOCK_PAGE_WEAK_MARKERS)
        and any(
            phrase in haystack
            for phrase in (
                b"verify you are human",
                b"prove you are not a robot",
                b"access denied",
                b"unusual traffic",
                b"security check",
            )
        )
    )


def _get_config_list(key: str, default: list[str]) -> list[str]:
    value = cfg.get(key, default)
    if isinstance(value, list):
        items = value
    else:
        items = str(value).split(",")
    return [str(item).strip() for item in items if str(item).strip()]


DEDUPE_EXTENSIONS = {
    ext.lower() if str(ext).startswith(".") else f".{str(ext).lower()}"
    for ext in _get_config_list("DEDUPE_EXTENSIONS", [".pdf", ".mhtml"])
}

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)

ARIA2_COMMON_FLAGS = [
    "--continue=true",
    "--auto-file-renaming=false",
    "--file-allocation=trunc",
    "--summary-interval=30",
    "--timeout=60",
    "--retry-wait=10",
    "--max-tries=5",
    "--split=4",
    "--min-split-size=1M",
    "--max-connection-per-server=4",
    f"--user-agent={USER_AGENT}",
    "--header=Accept: application/pdf,*/*",
    "--header=Accept-Language: en-US,en;q=0.9,zh-CN;q=0.8",
    "--header=Connection: keep-alive",
]

# ── LOGGING ───────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


# ── STATE HELPERS ────────────────────────────────────────────────────────
def _atomic_write(path: str, data: dict):
    tmp = None
    with _locked_state_file(path):
        try:
            dirn = os.path.dirname(path) or "."
            fd, tmp = tempfile.mkstemp(dir=dirn)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            os.replace(tmp, path)
        finally:
            if tmp and os.path.exists(tmp):
                os.remove(tmp)


def load_state(path: str) -> dict | None:
    if not os.path.exists(path):
        return None
    try:
        with _locked_state_file(path):
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        return None


def remove_state(path: str):
    try:
        with _locked_state_file(path):
            if os.path.exists(path):
                os.remove(path)
    except Exception:
        logger.debug("Could not remove state file: %s", path)


# ── UTILS ──────────────────────────────────────────────────────────────────
def _human_bytes(value: int) -> str:
    units = ["B", "KB", "MB", "GB", "TB"]
    amount = float(value)
    for unit in units:
        if amount < 1024 or unit == units[-1]:
            return f"{int(amount)} {unit}" if unit == "B" else f"{amount:.1f} {unit}"
        amount /= 1024
    return f"{value} B"


def _dedupe_keep_sort_key(path: str):
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        mtime = 0
    return (mtime, len(path), path.casefold())


def _write_dedupe_report(root: str, rows: list[dict]):
    report_path = os.path.join(root, DEDUPE_REPORT_NAME)
    tmp = None
    try:
        fd, tmp = tempfile.mkstemp(dir=root, suffix=".tmp")
        with os.fdopen(fd, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(
                f,
                fieldnames=[
                    "filename",
                    "capacity_bytes",
                    "capacity_human",
                    "kept_path",
                    "removed_path",
                ],
            )
            writer.writeheader()
            writer.writerows(rows)
        os.replace(tmp, report_path)
        logger.info("Duplicate report written: %s", report_path)
    except Exception as e:
        logger.warning("Could not write duplicate report: %s", e)
    finally:
        if tmp and os.path.exists(tmp):
            try:
                os.remove(tmp)
            except Exception:
                pass


def remove_duplicate_downloads(root: str, extensions: set[str] | None = None) -> int:
    """
    Remove duplicate downloaded files by filename and exact byte size.

    This is intentionally lighter than dupeGuru/content hashing: it only treats files
    as duplicates when both their basename and capacity match.
    """
    if not os.path.isdir(root):
        logger.warning("Dedupe root does not exist: %s", root)
        return 0

    extensions = extensions if extensions is not None else DEDUPE_EXTENSIONS
    groups: dict[tuple[str, int], list[str]] = {}
    report_rows: list[dict] = []
    removed_count = 0
    reclaimed_bytes = 0

    with _locked_download_dir(root):
        for current_root, _, files in os.walk(root):
            for filename in files:
                if filename.startswith(".") or filename == DEDUPE_REPORT_NAME:
                    continue

                ext = os.path.splitext(filename)[1].lower()
                if extensions and ext not in extensions:
                    continue

                path = os.path.join(current_root, filename)
                if not os.path.isfile(path):
                    continue

                try:
                    size = os.path.getsize(path)
                except OSError as e:
                    logger.debug("Could not stat file during dedupe: %s -> %s", path, e)
                    continue

                if size <= 0:
                    continue

                key = (filename.casefold(), size)
                groups.setdefault(key, []).append(path)

        for (_, size), paths in sorted(groups.items(), key=lambda item: item[0]):
            if len(paths) < 2:
                continue

            ordered = sorted(paths, key=_dedupe_keep_sort_key)
            kept_path = ordered[0]
            filename = os.path.basename(kept_path)

            for duplicate_path in ordered[1:]:
                try:
                    os.remove(duplicate_path)
                    removed_count += 1
                    reclaimed_bytes += size
                    report_rows.append({
                        "filename": filename,
                        "capacity_bytes": size,
                        "capacity_human": _human_bytes(size),
                        "kept_path": kept_path,
                        "removed_path": duplicate_path,
                    })
                    logger.info(
                        "Removed duplicate by filename+size: %s (kept: %s, size: %s)",
                        duplicate_path,
                        kept_path,
                        _human_bytes(size),
                    )
                except Exception as e:
                    logger.warning("Failed removing duplicate %s: %s", duplicate_path, e)

        _write_dedupe_report(root, report_rows)

    logger.info(
        "Dedupe complete: removed %s duplicate file(s), reclaimed %s",
        removed_count,
        _human_bytes(reclaimed_bytes),
    )
    return removed_count


def is_valid_pdf(path: str) -> bool:
    try:
        with open(path, "rb") as f:
            return f.read(4) == b"%PDF"
    except Exception:
        return False


def is_valid_mhtml(path: str) -> bool:
    try:
        if not os.path.exists(path):
            return False
        size = os.path.getsize(path)
        if size <= 0:
            return False
        if size < MHTML_MIN_VALID_BYTES:
            return False
        with open(path, "rb") as f:
            sample = f.read(1024 * 1024).lower()
        header = sample[:4096]
        return b"mime-version:" in header and (
            b"multipart/related" in header or b"content-type:" in header
        ) and not _looks_like_block_page_bytes(sample)
    except Exception:
        return False


def _is_skippable_web_url(url: str) -> bool:
    from urllib.parse import urlparse, parse_qs

    try:
        parsed = urlparse(str(url))
        host = parsed.hostname or ""
        host = host.lower()
        path = parsed.path.lower()
        query = parsed.query.lower()

        if host in {"googleadservices.com", "googlesyndication.com", "doubleclick.net"}:
            return True
        if host.endswith(".googleadservices.com") or host.endswith(".googlesyndication.com") or host.endswith(".doubleclick.net"):
            return True
        if host == "google.com" or host.endswith(".google.com"):
            if (
                    path.startswith("/aclk")
                    or path.startswith("/pagead/")
                    or path.startswith("/sorry/")
                    or path.startswith("/url")
                    or path.startswith("/search")
            ):
                return True
            if "adurl=" in query:
                return True
        if "adurl=" in query or parse_qs(parsed.query).get("adurl"):
            return True
        return False
    except Exception:
        return False


def _ensure_target_extension(name: str, extension: str) -> str:
    return name if name.lower().endswith(extension.lower()) else name + extension


def _is_valid_download(path: str, mhtml_mode: bool = False) -> bool:
    return is_valid_mhtml(path) if mhtml_mode else is_valid_pdf(path)


def clean_urls(urls):
    clean = []
    for u in urls:
        if isinstance(u, str):
            u = u.strip()
            if u.startswith("http"):
                clean.append(u)
    return list(dict.fromkeys(clean))  # de-dup preserve order


def _target_name_from_url(url: str) -> str:
    """Derive a safe filename from a URL.

    - Removes query and fragment parts
    - Decodes percent-encoding
    - Replaces unsafe filesystem characters with '_'
    - Falls back to a short hash when no usable basename can be determined
    """
    from urllib.parse import urlparse, unquote
    import hashlib
    import re

    try:
        s = str(url)
        p = urlparse(s)
        # use the path component (no query or fragment)
        raw = os.path.basename(p.path)
        # if empty, try using last segment of the path-like string or use netloc
        if not raw:
            # create a candidate from netloc + path
            candidate = (p.netloc + p.path) or p.netloc
            raw = candidate.strip('/').split('/')[-1] if candidate else ''

        raw = unquote(raw or '')
        # sanitize: keep letters, numbers, dash, underscore, dot
        safe = re.sub(r'[^A-Za-z0-9._-]', '_', raw)
        # collapse repeated underscores
        safe = re.sub(r'_+', '_', safe).strip('_')
        # limit length
        if len(safe) > 180:
            safe = safe[:180]

        if not safe:
            # fallback to short hash
            h = hashlib.sha1(s.encode('utf-8')).hexdigest()[:10]
            safe = f'file_{h}'

        return safe
    except Exception:
        try:
            h = hashlib.sha1(str(url).encode('utf-8')).hexdigest()[:10]
            return f'file_{h}'
        except Exception:
            return 'file'


# ── CORE LOGIC ─────────────────────────────────────────────────────────────
def _canonical_url_for_dedupe(url: str) -> str:
    """Return the full URL identity used for duplicate checks."""
    from urllib.parse import urlsplit, urlunsplit

    s = str(url or "").strip()
    if not s:
        return ""
    try:
        parts = urlsplit(s)
        if not parts.scheme and not parts.netloc:
            return s
        return urlunsplit((
            parts.scheme.lower(),
            parts.netloc.lower(),
            parts.path or "",
            parts.query,
            "",
        ))
    except Exception:
        return s


def _url_hash_suffix(url_key: str) -> str:
    import hashlib

    return hashlib.sha1(str(url_key).encode("utf-8")).hexdigest()[:10]


def _append_name_suffix(filename: str, suffix: str) -> str:
    stem, ext = os.path.splitext(filename)
    if not stem:
        stem = "file"
    return f"{stem}__{suffix}{ext}"


def _casefold_names_in_dir(path: str) -> dict[str, str]:
    try:
        return {name.casefold(): name for name in os.listdir(path)}
    except Exception:
        return {}


def _unique_target_name(preferred: str, url_key: str, occupied_names: set[str], reserved_names: set[str]) -> str:
    blocked = occupied_names | reserved_names
    if preferred.casefold() not in blocked:
        return preferred

    suffix = _url_hash_suffix(url_key)
    candidate = _append_name_suffix(preferred, suffix)
    if candidate.casefold() not in blocked:
        return candidate

    counter = 2
    while True:
        candidate = _append_name_suffix(preferred, f"{suffix}_{counter}")
        if candidate.casefold() not in blocked:
            return candidate
        counter += 1


def _manifest_path(download_dir: str) -> str:
    return os.path.join(download_dir, DOWNLOAD_MANIFEST_NAME)


def _load_download_manifest(download_dir: str) -> dict:
    path = _manifest_path(download_dir)
    if not os.path.exists(path):
        return {"urls": {}}
    try:
        with _locked_state_file(path):
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        if not isinstance(data, dict):
            return {"urls": {}}
        urls = data.get("urls")
        if not isinstance(urls, dict):
            data["urls"] = {}
        return data
    except Exception as e:
        logger.debug("Could not load download manifest %s: %s", path, e)
        return {"urls": {}}


def _save_download_manifest(download_dir: str, manifest: dict):
    path = _manifest_path(download_dir)
    _atomic_write(path, manifest)


def _mark_download_manifest_success(download_dir: str, url: str, filename: str, manifest: dict | None = None) -> dict:
    manifest = manifest or _load_download_manifest(download_dir)
    urls = manifest.setdefault("urls", {})
    url_key = _canonical_url_for_dedupe(url)
    urls[url_key] = {
        "url": str(url),
        "filename": filename,
        "updated_at": datetime.utcnow().isoformat() + "Z",
    }
    _save_download_manifest(download_dir, manifest)
    return manifest


def _targets_for_urls(urls: list[str], target_by_url_key: dict[str, str]) -> dict[str, str]:
    url_keys = {_canonical_url_for_dedupe(u) for u in urls}
    return {k: v for k, v in target_by_url_key.items() if k in url_keys}


def _log_job_progress(
        file_index: int | None,
        total_files: int | None,
        item_index: int,
        total_items: int,
        label: str,
):
    if file_index is None or total_files is None or total_files <= 0:
        percent = item_index / total_items * 100 if total_items else 0
        logger.info("JOB_PROGRESS [%s/%s] %s (%.1f%%)", item_index, total_items, label, percent)
        return

    item_fraction = item_index / total_items if total_items else 1
    overall_value = (file_index - 1) + item_fraction
    overall_percent = overall_value / total_files * 100
    logger.info(
        "JOB_PROGRESS [%s/%s] %s (%.1f%%)",
        file_index,
        total_files,
        label,
        overall_percent,
    )


def download_from_csv(csv_path: str, base_dir: str, file_index: int | None = None, total_files: int | None = None):
    base_name = os.path.basename(csv_path)
    # Support two possible merged filename patterns:
    #  - <prefix>_merged.csv
    #  - <prefix>_merged.web.csv  (web pages captured as MHTML)
    if file_index is not None and total_files is not None and total_files > 0:
        percent = file_index / total_files * 100
        logger.info(
            f"\n📄 Downloading file {file_index}/{total_files} ({percent:.1f}%): {base_name}"
        )
    else:
        logger.info(f"\n📄 Downloading: {base_name}")
    use_mhtml_mode = False
    if base_name.endswith("_merged.web.csv"):
        prefix = base_name[: -len("_merged.web.csv")]
        use_mhtml_mode = True
    else:
        prefix = base_name.replace("_merged.csv", "")

    download_dir = os.path.join(base_dir, prefix)
    os.makedirs(download_dir, exist_ok=True)

    logger.info(f"➤ Download dir: {download_dir}")

    try:
        df = pd.read_csv(csv_path, usecols=["URL"])
    except Exception as e:
        logger.error(f"CSV read failed: {csv_path} → {e}")
        return

    initial_urls = clean_urls(df["URL"].dropna().tolist())
    if not initial_urls:
        logger.warning("No valid URLs found.")
        return

    state_path = os.path.join(download_dir, ".aria2_state.json")
    existing_state = load_state(state_path)

    # Determine which URL list to use: resume if same CSV recorded
    if existing_state and existing_state.get("csv") == base_name:
        urls = existing_state.get("remaining", initial_urls)
        resume_targets = existing_state.get("targets", {})
        if not isinstance(resume_targets, dict):
            resume_targets = {}
        logger.info(f"Resuming previous state with {len(urls)} remaining URL(s).")
    else:
        urls = initial_urls
        resume_targets = {}
        _atomic_write(state_path, {
            "csv": base_name,
            "remaining": urls,
            "started_at": datetime.utcnow().isoformat() + "Z"
        })
        logger.info(f"Created new state file with {len(urls)} URL(s).")

    # Filter by full URL identity and allocate non-conflicting target filenames.
    manifest = _load_download_manifest(download_dir)
    manifest_urls = manifest.setdefault("urls", {})
    occupied_names = _casefold_names_in_dir(download_dir)
    reserved_urls = set()
    reserved_names = set()
    target_by_url_key = {}
    filtered = []
    for u in urls:
        if use_mhtml_mode and _is_skippable_web_url(u):
            logger.info("Skipping ad/CAPTCHA web URL: %s", u)
            continue

        url_key = _canonical_url_for_dedupe(u)
        if not url_key:
            logger.debug("Skipping URL with no usable identity: %s", u)
            continue

        manifest_entry = manifest_urls.get(url_key)
        manifest_name = manifest_entry.get("filename") if isinstance(manifest_entry, dict) else None
        if manifest_name:
            manifest_path = os.path.join(download_dir, manifest_name)
            if os.path.exists(manifest_path) and _is_valid_download(manifest_path, mhtml_mode=use_mhtml_mode):
                logger.debug("Skipping existing valid file for URL: %s -> %s", manifest_name, u)
                continue

        if url_key in reserved_urls:
            logger.info("Skipping duplicate URL: %s", u)
            continue

        resume_name = resume_targets.get(url_key)
        tname = str(resume_name or manifest_name or "").strip() or _target_name_from_url(u)
        if not tname:
            logger.debug("Skipping URL with no usable basename: %s", u)
            continue

        if use_mhtml_mode:
            tname = _ensure_target_extension(tname, ".mhtml")

        preferred_key = tname.casefold()
        existing_name = occupied_names.get(preferred_key)
        if existing_name:
            existing_path = os.path.join(download_dir, existing_name)
            if os.path.isfile(existing_path) and not _is_valid_download(existing_path, mhtml_mode=use_mhtml_mode):
                logger.info("Removing invalid existing file before retry: %s", existing_name)
                try:
                    os.remove(existing_path)
                    occupied_names.pop(preferred_key, None)
                except Exception as e:
                    logger.warning("Could not remove invalid file %s: %s", existing_path, e)
            else:
                logger.info("Target filename already exists; using URL-specific name for: %s", tname)

        tname = _unique_target_name(tname, url_key, set(occupied_names.keys()), reserved_names)
        if tname.casefold() != preferred_key:
            logger.info("Resolved filename collision for URL: %s -> %s", u, tname)

        reserved_urls.add(url_key)
        reserved_key = tname.casefold()
        reserved_names.add(reserved_key)
        target_by_url_key[url_key] = tname
        filtered.append((u, tname))

        # Reserve the selected filename for later URLs in this same batch.
        occupied_names.setdefault(reserved_key, tname)

    if not filtered:
        logger.info("All files already present, cleaning up state.")
        remove_state(state_path)
        _log_job_progress(file_index, total_files, 1, 1, f"Completed {base_name}")
        return

    url_list = os.path.join(download_dir, f"{prefix}_urls.txt")
    with open(url_list, "w", encoding="utf-8") as f:
        for u, _ in filtered:
            f.write(u + "\n")

    # Save current remaining URL list before starting aria2
    _atomic_write(state_path, {
        "csv": base_name,
        "remaining": [u for u, _ in filtered],
        "targets": target_by_url_key,
        "started_at": datetime.utcnow().isoformat() + "Z"
    })

    # Run aria2 per-URL with a short timeout to avoid getting stuck on slow servers.
    ARIA2_PER_URL_TIMEOUT = 30  # seconds
    # If this is a web capture CSV, skip aria2 and use Playwright MHTML capture.
    if use_mhtml_mode:
        PLAYWRIGHT_TIMEOUT_MS = MHTML_CAPTURE_TIMEOUT_MS
        logger.info(
            "MHTML mode enabled: skipping aria2; sending %s URL(s) to Playwright (timeout=%sms, settle=%sms)",
            len(filtered),
            PLAYWRIGHT_TIMEOUT_MS,
            MHTML_CAPTURE_SETTLE_MS,
        )
        # Remaining URLs are the whole filtered list; Playwright will handle them.
        remaining = [u for u, _ in filtered]
    else:
        PLAYWRIGHT_TIMEOUT_MS = ARIA2_PER_URL_TIMEOUT * 1000  # milliseconds for Playwright API
        logger.info(f"▶ aria2c starting per-URL ({len(filtered)} URLs), timeout={ARIA2_PER_URL_TIMEOUT}s each")

        aria2_errors = []
        total_urls = len(filtered)
        for idx, (u, tname) in enumerate(filtered, start=1):
            percent = idx / total_urls * 100
            _log_job_progress(
                file_index,
                total_files,
                idx,
                total_urls,
                f"PDF file {base_name}: {tname}",
            )
            logger.info(
                "▶ [%s/%s] aria2c downloading: %s (%.1f%%)",
                idx,
                total_urls,
                tname,
                percent,
            )
            aria2_cmd = [
                "aria2c",
                f"--dir={download_dir}",
                f"--out={tname}",
                # pass the URL directly so each run handles one resource and can be timed out
                u,
                *ARIA2_COMMON_FLAGS,
            ]

            try:
                result = subprocess.run(
                    aria2_cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    timeout=ARIA2_PER_URL_TIMEOUT,
                )
                if result.returncode != 0:
                    logger.warning("aria2c failed for URL: %s (rc=%s)", u, result.returncode)
                    logger.debug(result.stderr)
                    aria2_errors.append(u)
            except subprocess.TimeoutExpired:
                logger.warning("aria2c timed out for URL: %s", u)
                aria2_errors.append(u)
            except Exception as e:
                logger.warning("aria2c raised exception for URL: %s -> %s", u, e)
                aria2_errors.append(u)

        # ── POST-VALIDATION ───────────────────────────────────────────────
        bad_files = []
        for fname in os.listdir(download_dir):
            if fname.lower().endswith(".pdf"):
                full = os.path.join(download_dir, fname)
                if not is_valid_pdf(full):
                    bad_files.append(fname)

        if bad_files:
            fail_log = os.path.join(download_dir, "invalid_pdfs.log")
            with open(fail_log, "w", encoding="utf-8") as f:
                for bf in bad_files:
                    f.write(bf + "\n")
            logger.warning(f"{len(bad_files)} invalid PDFs detected")

        # Recompute remaining URLs after aria2 run
        remaining = []
        for u, tname in filtered:
            target_path = os.path.join(download_dir, tname)
            # If aria2 produced a valid file, consider it done
            if os.path.exists(target_path) and is_valid_pdf(target_path):
                manifest = _mark_download_manifest_success(download_dir, u, tname, manifest)
                continue
            # otherwise mark for fallback (includes those errored/timed out)
            remaining.append(u)

    if remaining and not ENABLE_PLAYWRIGHT_FALLBACK and not use_mhtml_mode:
        logger.warning(
            "Playwright fallback is disabled in config; %s URL(s) remain unprocessed.",
            len(remaining),
        )
        _atomic_write(state_path, {
            "csv": base_name,
            "remaining": remaining,
            "targets": _targets_for_urls(remaining, target_by_url_key),
            "updated_at": datetime.utcnow().isoformat() + "Z",
        })
        return

    if remaining:
        # Try Playwright fallback for any remaining URLs (one-by-one)
        logger.info(f"Attempting Playwright fallback for {len(remaining)} remaining URL(s)...")
        if use_mhtml_mode and importlib.util.find_spec("playwright") is None:
            logger.error(
                "MHTML capture requires Playwright in this Python environment. "
                "Install it with: %s -m pip install playwright && %s -m playwright install chromium",
                sys.executable,
                sys.executable,
            )
            _atomic_write(state_path, {
                "csv": base_name,
                "remaining": remaining,
                "targets": _targets_for_urls(remaining, target_by_url_key),
                "updated_at": datetime.utcnow().isoformat() + "Z",
            })
            return

        try:
            from src.post_process.download_with_playwrite import download_with_playwright
        except Exception as e:
            logger.warning("Could not import Playwright downloader helper: %s", e)
            download_with_playwright = None

        still_remaining = []
        total_remaining = len(remaining)
        for idx, u in enumerate(remaining, start=1):
            percent = idx / total_remaining * 100
            _log_job_progress(
                file_index,
                total_files,
                idx,
                total_remaining,
                f"{'MHTML capture' if use_mhtml_mode else 'PDF fallback'} {base_name}",
            )
            logger.info(
                "▶ [%s/%s] Playwright fallback: %s (%.1f%%)",
                idx,
                total_remaining,
                u,
                percent,
            )
            url_key = _canonical_url_for_dedupe(u)
            tname = target_by_url_key.get(url_key) or _target_name_from_url(u)
            if use_mhtml_mode and tname:
                tname = _ensure_target_extension(tname, ".mhtml")
            if not tname:
                logger.debug("No target filename derived; skipping playwright fallback: %s", u)
                still_remaining.append(u)
                continue

            save_path = os.path.join(download_dir, tname)

            # If file already present (joined race), skip
            if os.path.exists(save_path) and _is_valid_download(save_path, mhtml_mode=use_mhtml_mode):
                logger.debug("File already present after aria2: %s", tname)
                manifest = _mark_download_manifest_success(download_dir, u, tname, manifest)
                continue

            ok = False
            if download_with_playwright:
                try:
                    # Run the playwright downloader as a separate process with an enforced timeout
                    script_path = os.path.join(os.path.dirname(__file__), "download_with_playwrite.py")
                    cmd = [sys.executable, script_path, u, save_path, "--timeout", str(PLAYWRIGHT_TIMEOUT_MS)]
                    logger.debug("Running Playwright subprocess: %s", cmd)
                    try:
                        if use_mhtml_mode:
                            cmd = cmd + ["--mhtml", "--settle", str(MHTML_CAPTURE_SETTLE_MS)]
                        proc = subprocess.run(
                            cmd,
                            stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE,
                            text=True,
                            timeout=(PLAYWRIGHT_TIMEOUT_MS // 1000) + 5,
                        )
                        logger.debug("Playwright subprocess stdout: %s", proc.stdout)
                        if proc.returncode == 0:
                            ok = True
                        else:
                            if proc.stderr.strip():
                                logger.warning("Playwright subprocess stderr (rc=%s): %s", proc.returncode, proc.stderr.strip())
                            if proc.stdout.strip():
                                logger.warning("Playwright subprocess stdout (rc=%s): %s", proc.returncode, proc.stdout.strip())
                            ok = False
                    except subprocess.TimeoutExpired:
                        logger.warning("Playwright subprocess timed out for URL: %s", u)
                        ok = False
                except Exception as e:
                    logger.warning("Playwright subprocess invocation error: %s", e)
                    ok = False
            else:
                logger.warning("Playwright downloader helper is unavailable; cannot process URL: %s", u)

            if ok and _is_valid_download(save_path, mhtml_mode=use_mhtml_mode):
                manifest = _mark_download_manifest_success(download_dir, u, tname, manifest)
                logger.info("Playwright downloaded: %s", tname)
            else:
                if use_mhtml_mode and os.path.exists(save_path):
                    try:
                        os.remove(save_path)
                        logger.warning("Removed invalid or blocked MHTML capture: %s", save_path)
                    except Exception as e:
                        logger.warning("Could not remove invalid MHTML capture %s: %s", save_path, e)
                logger.warning("Playwright fallback failed for URL: %s", u)
                still_remaining.append(u)

        if still_remaining:
            # Update state with remaining URLs so the next run can resume
            _atomic_write(state_path, {
                "csv": base_name,
                "remaining": still_remaining,
                "targets": _targets_for_urls(still_remaining, target_by_url_key),
                "updated_at": datetime.utcnow().isoformat() + "Z"
            })
            logger.warning(f"Some URLs remain ({len(still_remaining)}). State updated for resume.")
        else:
            # Completed successfully: remove state to avoid confusion next task
            remove_state(state_path)
            _log_job_progress(file_index, total_files, 1, 1, f"Completed {base_name}")
            logger.info("✓ All URLs completed, state removed.")
    else:
        # Completed successfully: remove state to avoid confusion next task
        remove_state(state_path)
        _log_job_progress(file_index, total_files, 1, 1, f"Completed {base_name}")
        logger.info("✓ All URLs completed, state removed.")

    logger.info("✓ Directory completed")


def process_directory(root: str):
    # Support normal merged CSVs and web-printed merged CSVs
    merged_csvs = glob.glob(os.path.join(root, "*_merged.csv"))
    merged_web = glob.glob(os.path.join(root, "*_merged.web.csv"))
    # combine and deduplicate
    merged_csvs = list(dict.fromkeys(merged_csvs + merged_web))
    if not merged_csvs:
        return

    logger.info(f"\n📁 Processing: {root}")
    for csv in merged_csvs:
        download_from_csv(csv, root)
    if DEDUPE_AFTER_DOWNLOAD:
        logger.info("Checking downloaded files for duplicates by filename+size under: %s", root)
        remove_duplicate_downloads(root)


def _find_all_merged_csvs(root: str) -> list[str]:
    merged_csvs = glob.glob(os.path.join(root, "**", "*_merged.csv"), recursive=True)
    merged_web = glob.glob(os.path.join(root, "**", "*_merged.web.csv"), recursive=True)
    return list(dict.fromkeys(sorted(merged_csvs + merged_web)))


def main():
    if not os.path.isdir(INPUT_DIR):
        logger.error(f"Input directory not found: {INPUT_DIR}")
        return

    merged_csvs = _find_all_merged_csvs(INPUT_DIR)
    if not merged_csvs:
        logger.info("No merged CSV files found to download.")
        return

    total_files = len(merged_csvs)
    for idx, csv in enumerate(merged_csvs, start=1):
        percent = idx / total_files * 100
        logger.info(f"\n📁 Processing file {idx}/{total_files} ({percent:.1f}%): {csv}")
        download_from_csv(csv, os.path.dirname(csv), idx, total_files)

    if DEDUPE_AFTER_DOWNLOAD:
        logger.info("\nChecking downloaded files for duplicates by filename+size under: %s", INPUT_DIR)
        remove_duplicate_downloads(INPUT_DIR)
    else:
        logger.info("\nDuplicate cleanup disabled by DEDUPE_AFTER_DOWNLOAD=false")

    logger.info(f"\n✅ Completed. Processed {total_files} merged CSV file(s).")


if __name__ == "__main__":
    logger.info("Starting production PDF crawler")
    main()
    logger.info("Crawler finished")
