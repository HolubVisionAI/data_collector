# python
import os
import glob
import subprocess
import logging
import pandas as pd
import json
import sys
import tempfile
from datetime import datetime
from src.utils.utils import load_config

# ── CONFIG ────────────────────────────────────────────────────────────────
cfg = load_config()["aria2_download"]
INPUT_DIR = cfg["INPUT_DIR"][0]
OUTPUT_DIR = cfg["OUTPUT_DIR"][0]

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
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def remove_state(path: str):
    try:
        if os.path.exists(path):
            os.remove(path)
    except Exception:
        logger.debug("Could not remove state file: %s", path)


# ── UTILS ──────────────────────────────────────────────────────────────────
def is_valid_pdf(path: str) -> bool:
    try:
        with open(path, "rb") as f:
            return f.read(4) == b"%PDF"
    except Exception:
        return False


def clean_urls(urls):
    clean = []
    for u in urls:
        if isinstance(u, str):
            u = u.strip()
            if u.startswith("http"):
                clean.append(u)
    return list(dict.fromkeys(clean))  # de-dup preserve order


def _target_name_from_url(url: str) -> str:
    """Derive a best-effort filename from URL (strip query)."""
    base = os.path.basename(url.split("?", 1)[0])
    return base


# ── CORE LOGIC ─────────────────────────────────────────────────────────────
def download_from_csv(csv_path: str, base_dir: str):
    base_name = os.path.basename(csv_path)
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
        logger.info(f"Resuming previous state with {len(urls)} remaining URL(s).")
    else:
        urls = initial_urls
        _atomic_write(state_path, {
            "csv": base_name,
            "remaining": urls,
            "started_at": datetime.utcnow().isoformat() + "Z"
        })
        logger.info(f"Created new state file with {len(urls)} URL(s).")

    # Filter out already existing target files to avoid re-downloads
    filtered = []
    for u in urls:
        tname = _target_name_from_url(u)
        if tname and os.path.exists(os.path.join(download_dir, tname)):
            logger.debug("Skipping existing file: %s", tname)
            continue
        filtered.append(u)

    if not filtered:
        logger.info("All files already present, cleaning up state.")
        remove_state(state_path)
        return

    url_list = os.path.join(download_dir, f"{prefix}_urls.txt")
    with open(url_list, "w", encoding="utf-8") as f:
        for u in filtered:
            f.write(u + "\n")

    # Save current remaining list before starting aria2
    _atomic_write(state_path, {
        "csv": base_name,
        "remaining": filtered,
        "started_at": datetime.utcnow().isoformat() + "Z"
    })

    # Run aria2 per-URL with a short timeout to avoid getting stuck on slow servers.
    ARIA2_PER_URL_TIMEOUT = 30  # seconds
    PLAYWRIGHT_TIMEOUT_MS = ARIA2_PER_URL_TIMEOUT * 1000  # milliseconds for Playwright API
    logger.info(f"▶ aria2c starting per-URL ({len(filtered)} URLs), timeout={ARIA2_PER_URL_TIMEOUT}s each")

    aria2_errors = []
    for u in filtered:
        tname = _target_name_from_url(u) or u
        aria2_cmd = [
            "aria2c",
            f"--dir={download_dir}",
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
    for u in filtered:
        tname = _target_name_from_url(u)
        target_path = os.path.join(download_dir, tname) if tname else None
        # If aria2 produced a valid file, consider it done
        if tname and target_path and os.path.exists(target_path) and is_valid_pdf(target_path):
            continue
        # otherwise mark for fallback (includes those errored/timed out)
        remaining.append(u)

    if remaining:
        # Try Playwright fallback for any remaining URLs (one-by-one)
        logger.info(f"Attempting Playwright fallback for {len(remaining)} remaining URL(s)...")
        try:
            from src.post_process.download_with_playwrite import download_with_playwright
        except Exception:
            download_with_playwright = None

        still_remaining = []
        for u in remaining:
            tname = _target_name_from_url(u)
            if not tname:
                logger.debug("No target filename derived; skipping playwright fallback: %s", u)
                still_remaining.append(u)
                continue

            save_path = os.path.join(download_dir, tname)

            # If file already present (joined race), skip
            if os.path.exists(save_path) and is_valid_pdf(save_path):
                logger.debug("File already present after aria2: %s", tname)
                continue

            ok = False
            if download_with_playwright:
                try:
                    # Run the playwright downloader as a separate process with an enforced timeout
                    script_path = os.path.join(os.path.dirname(__file__), "download_with_playwrite.py")
                    cmd = [sys.executable, script_path, u, save_path, "--timeout", str(PLAYWRIGHT_TIMEOUT_MS)]
                    logger.debug("Running Playwright subprocess: %s", cmd)
                    try:
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
                            logger.debug("Playwright subprocess failed (rc=%s): %s", proc.returncode, proc.stderr)
                            ok = False
                    except subprocess.TimeoutExpired:
                        logger.warning("Playwright subprocess timed out for URL: %s", u)
                        ok = False
                except Exception as e:
                    logger.debug("Playwright subprocess invocation error: %s", e)
                    ok = False

            if ok and is_valid_pdf(save_path):
                logger.info("Playwright downloaded: %s", tname)
            else:
                logger.warning("Playwright fallback failed for URL: %s", u)
                still_remaining.append(u)

        if still_remaining:
            # Update state with remaining URLs so the next run can resume
            _atomic_write(state_path, {
                "csv": base_name,
                "remaining": still_remaining,
                "updated_at": datetime.utcnow().isoformat() + "Z"
            })
            logger.warning(f"Some URLs remain ({len(still_remaining)}). State updated for resume.")
        else:
            # Completed successfully: remove state to avoid confusion next task
            remove_state(state_path)
            logger.info("✓ All URLs completed, state removed.")
    else:
        # Completed successfully: remove state to avoid confusion next task
        remove_state(state_path)
        logger.info("✓ All URLs completed, state removed.")

    logger.info("✓ Directory completed")


def process_directory(root: str):
    merged_csvs = glob.glob(os.path.join(root, "*_merged.csv"))
    if not merged_csvs:
        return

    logger.info(f"\n📁 Processing: {root}")
    for csv in merged_csvs:
        download_from_csv(csv, root)


def main():
    if not os.path.isdir(INPUT_DIR):
        logger.error(f"Input directory not found: {INPUT_DIR}")
        return

    processed = 0
    for root, _, files in os.walk(INPUT_DIR):
        if any(f.endswith("_merged.csv") for f in files):
            process_directory(root)
            processed += 1

    logger.info(f"\n✅ Completed. Processed {processed} directories.")


if __name__ == "__main__":
    logger.info("Starting production PDF crawler")
    main()
    logger.info("Crawler finished")
