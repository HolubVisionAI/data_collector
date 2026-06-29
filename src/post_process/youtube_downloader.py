import os
import re
import glob
import subprocess
import logging
import json
import sys
import shlex
import shutil
from datetime import datetime, timezone
from urllib.parse import urlparse, parse_qs

import pandas as pd
from src.utils.utils import load_config

# ── CONFIG ────────────────────────────────────────────────────────────────
cfg = load_config().get("yt_dlp_download", {})
INPUT_DIR = cfg.get("INPUT_DIR", [])[0]
OUTPUT_DIR = cfg.get("OUTPUT_DIR", [])[0]
YT_DLP_CMD = cfg.get("YT_DLP_CMD", ["yt-dlp"])[0]
YT_DLP_OPTS = cfg.get("YT_DLP_OPTS", [])

# Optional config
COOKIES_FILE = os.path.abspath("./yt_cookies.txt")
COOKIES_FROM_BROWSER = cfg.get("COOKIES_FROM_BROWSER", [None])[0]  # e.g. "chrome"
USE_ANDROID_CLIENT = False
DOWNLOAD_ARCHIVE = ".yt-dlp-archive.txt"
DOWNLOAD_STATE = ".download_state.json"
TEMP_EXTENSIONS = (".part", ".ytdl", ".tmp", ".temp")
SIDE_CAR_EXTENSIONS = (".json", ".description", ".info.json", ".vtt", ".srt", ".webp", ".jpg", ".png")
INPUT_PATTERNS = ("*_merged.csv", "*.txt", "*.list")

# ── LOGGING ───────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


def extract_video_id(url: str) -> str | None:
    """
    Extract YouTube video ID from common URL formats:
    - https://www.youtube.com/watch?v=VIDEO_ID
    - https://youtu.be/VIDEO_ID
    - https://www.youtube.com/shorts/VIDEO_ID
    - https://www.youtube.com/embed/VIDEO_ID
    """
    try:
        parsed = urlparse(url)
        host = parsed.netloc.lower()
        path = parsed.path.strip("/")

        if "youtu.be" in host:
            return path.split("/")[0] if path else None

        if "youtube.com" in host:
            qs = parse_qs(parsed.query)

            if path == "watch":
                return qs.get("v", [None])[0]

            if path.startswith("shorts/"):
                return path.split("/")[1] if len(path.split("/")) > 1 else None

            if path.startswith("embed/"):
                return path.split("/")[1] if len(path.split("/")) > 1 else None

        if "vimeo.com" in host:
            match = re.search(r"(?:video/)?(\d+)", path)
            return match.group(1) if match else None

        return None
    except Exception:
        return None


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _state_file(download_dir: str) -> str:
    return os.path.join(download_dir, DOWNLOAD_STATE)


def _archive_file(download_dir: str) -> str:
    return os.path.join(download_dir, DOWNLOAD_ARCHIVE)


def load_download_state(download_dir: str) -> dict:
    path = _state_file(download_dir)
    if not os.path.exists(path):
        return {"version": 1, "completed_urls": {}, "failed_urls": {}}

    try:
        with open(path, "r", encoding="utf-8") as f:
            state = json.load(f)
    except Exception as e:
        logger.warning("Could not read download state '%s': %s", path, e)
        return {"version": 1, "completed_urls": {}, "failed_urls": {}}

    state.setdefault("version", 1)
    state.setdefault("completed_urls", {})
    state.setdefault("failed_urls", {})
    return state


def save_download_state(download_dir: str, state: dict) -> None:
    path = _state_file(download_dir)
    tmp_path = path + ".tmp"
    state["updated_at"] = _utc_now()
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2, sort_keys=True)
        os.replace(tmp_path, path)
    except Exception as e:
        logger.warning("Could not write download state '%s': %s", path, e)
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except OSError:
            pass


def mark_download_success(download_dir: str, state: dict, url: str, files: list[str]) -> None:
    state["completed_urls"][url] = {
        "completed_at": _utc_now(),
        "files": sorted(files),
    }
    state["failed_urls"].pop(url, None)
    save_download_state(download_dir, state)


def mark_download_failure(download_dir: str, state: dict, url: str, error: str) -> None:
    state["failed_urls"][url] = {
        "failed_at": _utc_now(),
        "error": error,
    }
    save_download_state(download_dir, state)


def list_downloaded_files(download_dir: str) -> set[str]:
    files = set()
    for fname in os.listdir(download_dir):
        path = os.path.join(download_dir, fname)
        lower = fname.lower()
        if not os.path.isfile(path):
            continue
        if fname in {DOWNLOAD_ARCHIVE, DOWNLOAD_STATE}:
            continue
        if lower.endswith(TEMP_EXTENSIONS):
            continue
        if lower.endswith(SIDE_CAR_EXTENSIONS):
            continue
        files.add(fname)
    return files


def completed_by_state(download_dir: str, state: dict, url: str) -> bool:
    entry = state.get("completed_urls", {}).get(url)
    if not entry:
        return False

    files = entry.get("files") or []
    if not files:
        return True

    return any(os.path.exists(os.path.join(download_dir, fname)) for fname in files)


def already_downloaded(download_dir: str, video_id: str) -> bool:
    """
    Check whether a file containing [video_id] already exists.
    Matches your output template: %(title)s [%(id)s].%(ext)s
    """
    needle = f"[{video_id}]"
    for fname in os.listdir(download_dir):
        if needle in fname:
            return True
    return False


def run_command(cmd: list[str]) -> int:
    result = subprocess.run(
        cmd,
        check=False,
    )
    return result.returncode


def yt_dlp_command() -> list[str]:
    configured = shlex.split(YT_DLP_CMD) if isinstance(YT_DLP_CMD, str) else list(YT_DLP_CMD)
    if not configured:
        configured = ["yt-dlp"]

    executable = configured[0]
    if os.path.isabs(executable) or shutil.which(executable):
        return configured

    logger.warning(
        "Could not find '%s' on PATH; falling back to '%s -m yt_dlp'",
        executable,
        sys.executable,
    )
    return [sys.executable, "-m", "yt_dlp"] + configured[1:]


def cookie_options() -> list[str]:
    if COOKIES_FROM_BROWSER:
        return ["--cookies-from-browser", COOKIES_FROM_BROWSER]

    if os.path.exists(COOKIES_FILE):
        return ["--cookies", COOKIES_FILE]

    return []


def output_prefix(input_path: str) -> str:
    base_name = os.path.basename(input_path)
    if base_name.endswith("_merged.csv"):
        return base_name[:-len("_merged.csv")]
    return os.path.splitext(base_name)[0]


def read_urls(input_path: str) -> list[str]:
    try:
        df = pd.read_csv(input_path, usecols=["URL"]).dropna()
        urls = df["URL"].astype(str).str.strip()
        return urls.loc[urls != ""].tolist()
    except Exception:
        pass

    urls = []
    try:
        with open(input_path, "r", encoding="utf-8-sig") as f:
            for line in f:
                value = line.strip()
                if not value or value.lower() == "url":
                    continue
                if value.startswith(("http://", "https://")):
                    urls.append(value)
    except Exception as e:
        logger.error("Failed to read URL list '%s': %s", os.path.basename(input_path), e)
    return urls


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


def download_from_csv(
        csv_path: str,
        base_output_dir: str,
        file_index: int | None = None,
        total_files: int | None = None,
) -> int:
    base_name = os.path.basename(csv_path)
    if not base_name.endswith(("_merged.csv", ".txt", ".list")):
        logger.warning(f"Skipping '{base_name}': not a supported video URL list")
        return 0

    prefix = output_prefix(csv_path)
    download_dir = os.path.join(base_output_dir, prefix)
    os.makedirs(download_dir, exist_ok=True)
    logger.info(f"Ensured folder exists: '{download_dir}'")
    logger.info("Using yt-dlp archive: '%s'", _archive_file(download_dir))
    logger.info("Using download state: '%s'", _state_file(download_dir))
    state = load_download_state(download_dir)

    urls = read_urls(csv_path)
    if not urls:
        logger.warning(f"No URLs found in '{base_name}'. Skipping.")
        return 0

    to_download = []
    skipped = 0
    unresolved = 0

    for url in urls:
        vid_id = extract_video_id(url)

        if completed_by_state(download_dir, state, url):
            skipped += 1
            continue

        if vid_id and already_downloaded(download_dir, vid_id):
            skipped += 1
            mark_download_success(download_dir, state, url, sorted(list_downloaded_files(download_dir)))
            continue

        if vid_id is None:
            unresolved += 1

        to_download.append(url)

    logger.info(f"{len(urls)} total URL(s) in '{base_name}'")
    if skipped:
        logger.info(f"{skipped} already downloaded, skipping them")
    if unresolved:
        logger.warning(f"{unresolved} URL(s) had no detectable video ID; skip-check was bypassed")

    if not to_download:
        logger.info("Nothing to download.")
        _log_job_progress(file_index, total_files, 1, 1, f"Completed {base_name}")
        return 0

    logger.info(f"{len(to_download)} URL(s) to download")

    total_urls = len(to_download)
    failures = 0
    for idx, url in enumerate(to_download, start=1):
        percent = idx / total_urls * 100
        _log_job_progress(file_index, total_files, idx, total_urls, f"Video file {base_name}")
        logger.info("[%s/%s] yt-dlp downloading (%.1f%%): %s", idx, total_urls, percent, url)
        before_files = list_downloaded_files(download_dir)
        cmd = yt_dlp_command() + [
            "--continue",
            "--no-overwrites",
            "--download-archive",
            _archive_file(download_dir),
            "-o",
            os.path.join(download_dir, "%(title)s [%(id)s].%(ext)s"),
            "-f",
            "bv*+ba/b",  # more tolerant than bestvideo+bestaudio/best
            url,
        ]
        cmd += cookie_options()
        cmd += YT_DLP_OPTS

        logger.info(f"Running: {' '.join(cmd)}")
        try:
            rc = run_command(cmd)
            if rc == 0:
                after_files = list_downloaded_files(download_dir)
                mark_download_success(download_dir, state, url, sorted(after_files - before_files))
                logger.info("[%s/%s] yt-dlp completed (%.1f%%)", idx, total_urls, percent)
            else:
                failures += 1
                mark_download_failure(download_dir, state, url, f"yt-dlp exited {rc}")
                logger.error("[%s/%s] yt-dlp exited %s (%.1f%%)", idx, total_urls, rc, percent)
        except Exception as e:
            failures += 1
            mark_download_failure(download_dir, state, url, str(e))
            logger.error("[%s/%s] yt-dlp failed (%.1f%%): %s", idx, total_urls, percent, e)

    if failures:
        logger.warning("yt-dlp finished for '%s' with %s failure(s)", prefix, failures)
    else:
        logger.info("yt-dlp finished for '%s'", prefix)
    _log_job_progress(file_index, total_files, 1, 1, f"Completed {base_name}")
    return failures


def main(input_dir: str, output_dir: str) -> int:
    merged = []
    for pattern in INPUT_PATTERNS:
        merged.extend(glob.glob(os.path.join(input_dir, pattern)))
    merged = sorted(set(merged))

    if not merged:
        logger.warning(f"No supported URL list files in '{input_dir}'")
        return 0

    logger.info(f"Found {len(merged)} URL list file(s) in '{input_dir}'")
    total_files = len(merged)
    failures = 0
    for idx, csv_path in enumerate(merged, start=1):
        percent = idx / total_files * 100
        logger.info("Processing file %s/%s (%.1f%%): %s", idx, total_files, percent, csv_path)
        failures += download_from_csv(csv_path, output_dir, idx, total_files)
    return failures


if __name__ == "__main__":
    logger.info("Starting bulk yt-dlp download")
    logger.info(f"INPUT: '{INPUT_DIR}', OUTPUT: '{OUTPUT_DIR}'")
    failure_count = main(INPUT_DIR, OUTPUT_DIR)
    if failure_count:
        logger.warning("Bulk download completed with %s failure(s).", failure_count)
        sys.exit(1)
    logger.info("Bulk download completed.")
