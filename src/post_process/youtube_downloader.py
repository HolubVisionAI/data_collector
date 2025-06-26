import os
import glob
import subprocess
import logging

import pandas as pd
from src.utils.utils import load_config

# ── CONFIG ────────────────────────────────────────────────────────────────
cfg = load_config().get("yt_dlp_download", {})
INPUT_DIR = cfg.get("INPUT_DIR", [])[0]  # e.g. "/path/to/input"
OUTPUT_DIR = cfg.get("OUTPUT_DIR", [])[0]
YT_DLP_CMD = cfg.get("YT_DLP_CMD", ["yt-dlp"])[0]
YT_DLP_OPTS = cfg.get("YT_DLP_OPTS", [])  # list of additional yt-dlp flags

# ── SET UP LOGGING ─────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


def download_from_csv(csv_path: str, base_output_dir: str):
    """
    Given a merged CSV with a column 'URL', create a subfolder under base_output_dir named
    after the CSV prefix (before '_merged.csv'), then download all URLs into that folder
    using yt-dlp. Skip any videos whose expected output filename already exists.
    """
    base_name = os.path.basename(csv_path)
    if not base_name.endswith("_merged.csv"):
        logger.warning(f"Skipping '{base_name}': does not end with '_merged.csv'")
        return

    prefix = base_name[:-len("_merged.csv")]
    download_dir = os.path.join(base_output_dir, prefix)
    os.makedirs(download_dir, exist_ok=True)
    logger.info(f"➤ Ensured folder exists: '{download_dir}'")

    # Read URLs
    try:
        df = pd.read_csv(csv_path, usecols=["URL"]).dropna()
    except Exception as e:
        logger.error(f"Failed to read '{base_name}': {e}")
        return

    urls = df["URL"].astype(str).tolist()
    if not urls:
        logger.warning(f"No URLs found in '{base_name}'. Skipping.")
        return

    # Check existing files by ID (video ID in URL)
    to_download = []
    skipped = 0
    for url in urls:
        vid_id = url.split("v=")[-1]
        # we don't know ext/filename, so check any file starting with id in download_dir
        existing = any(fname.startswith(vid_id) for fname in os.listdir(download_dir))
        if existing:
            skipped += 1
        else:
            to_download.append(url)

    logger.info(f"   • {len(urls)} total URL(s) in '{base_name}'")
    if skipped:
        logger.info(f"   • {skipped} already downloaded, skipping them")
    if not to_download:
        logger.info("   • Nothing to download.")
        return

    logger.info(f"   • {len(to_download)} URL(s) to download")

    # Write to batch file
    batch_file = os.path.join(download_dir, f"{prefix}_urls.txt")
    try:
        with open(batch_file, 'w', encoding='utf-8') as f:
            for u in to_download:
                f.write(u + "\n")
    except Exception as e:
        logger.error(f"Could not write batch file '{batch_file}': {e}")
        return

    # Build yt-dlp command
    cmd = [YT_DLP_CMD, "-a", batch_file,
           "-o", os.path.join(download_dir, '%(title)s [%(id)s].%(ext)s'),
           "-f", "bestvideo+bestaudio/best"] + YT_DLP_OPTS

    logger.info(f"   • Running: {' '.join(cmd)}")
    try:
        subprocess.run(cmd, check=True)
        logger.info(f"   ✓ yt-dlp finished for '{prefix}'")
        os.remove(batch_file)
    except subprocess.CalledProcessError as e:
        logger.error(f"yt-dlp exited {e.returncode} for '{prefix}'")
    except FileNotFoundError:
        logger.error(f"{YT_DLP_CMD} not found; ensure yt-dlp is installed and in PATH.")


def main(input_dir: str, output_dir: str):
    pattern = os.path.join(input_dir, "*_merged.csv")
    merged = glob.glob(pattern)

    if not merged:
        logger.warning(f"No '*_merged.csv' in '{input_dir}'")
        return

    logger.info(f"Found {len(merged)} merged CSV(s) in '{input_dir}'")
    for csv_path in merged:
        logger.info(f"Processing '{os.path.basename(csv_path)}'")
        download_from_csv(csv_path, output_dir)


if __name__ == '__main__':
    logger.info("Starting bulk yt-dlp download")
    logger.info(f"INPUT: '{INPUT_DIR}', OUTPUT: '{OUTPUT_DIR}'")
    main(INPUT_DIR, OUTPUT_DIR)
    logger.info("Bulk download completed.")
