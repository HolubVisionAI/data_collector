import os
import glob
import subprocess
import logging

import pandas as pd
from src.utils.utils import load_config

# ── CONFIG ────────────────────────────────────────────────────────────────
cfg = load_config()["aria2_download"]
INPUT_DIR = cfg["INPUT_DIR"][0]  # e.g. "/path/to/input"
OUTPUT_DIR = cfg["OUTPUT_DIR"][0]
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 " \
             "(KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36"
# ── SET UP LOGGING ─────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


def download_from_csv(csv_path: str, base_output_dir: str):
    """
    Given a merged CSV (ending with '_merged.csv') containing a column 'URL',
    create a subfolder under base_output_dir named after the CSV prefix
    (everything before '_merged.csv'), then download all URLs into that folder
    using aria2c. Skip any files whose basename already exists in that folder.
    """
    base_name = os.path.basename(csv_path)
    if not base_name.endswith("_merged.csv"):
        logger.warning(f"Skipping '{base_name}': does not end with '_merged.csv'")
        return

    prefix = base_name[: -len("_merged.csv")]
    download_dir = os.path.join(base_output_dir, prefix)
    os.makedirs(download_dir, exist_ok=True)
    logger.info(f"➤ Ensured folder exists: '{download_dir}'")

    # Read the CSV to get URLs
    try:
        df = pd.read_csv(csv_path, usecols=["URL"])
    except Exception as e:
        logger.error(f"Failed to read '{base_name}': {e}")
        return

    urls = df["URL"].dropna().astype(str).tolist()
    if not urls:
        logger.warning(f"No URLs found in '{base_name}'. Skipping download.")
        return

    # Determine which URLs point to already existing files
    to_download = []
    skipped_count = 0
    for url in urls:
        fname = os.path.basename(url)
        target_path = os.path.join(download_dir, fname)
        if os.path.exists(target_path):
            skipped_count += 1
        else:
            to_download.append(url)

    logger.info(f"   • {len(urls)} total URL(s) in '{base_name}'")
    if skipped_count:
        logger.info(f"   • {skipped_count} file(s) already exist in '{download_dir}', skipping them")

    if not to_download:
        logger.info(f"   • All files already downloaded in '{download_dir}'. Nothing to do.")
        return

    logger.info(f"   • {len(to_download)} URL(s) remain to download")

    # Write remaining URLs to a temporary text file inside download_dir
    url_list_path = os.path.join(download_dir, f"{prefix}_urls.txt")
    try:
        with open(url_list_path, "w", encoding="utf-8") as f:
            for url in to_download:
                f.write(url.strip() + "\n")
    except Exception as e:
        logger.error(f"Could not write URL list to '{url_list_path}': {e}")
        return

    logger.info(f"   • URL list written to '{url_list_path}'")

    # Build aria2c command
    aria2_cmd = [
        "aria2c",
        f"--dir={download_dir}",
        "--continue=true",
        "--split=4",
        "--max-connection-per-server=4",
        f"--user-agent={USER_AGENT}",
        f"--input-file={url_list_path}",
    ]

    logger.info(f"   • Starting aria2c for '{prefix}'…")
    try:
        subprocess.run(aria2_cmd, check=True)
        logger.info(f"   ✓ aria2c finished downloading for '{prefix}'.")
    except subprocess.CalledProcessError as e:
        logger.error(f"aria2c exited with status {e.returncode} for '{prefix}'.")
    except FileNotFoundError:
        logger.error("aria2c not found. Make sure aria2 is installed and in PATH.")


def main(input_dir: str, output_dir: str):
    """
    1) Find all files in input_dir matching '*_merged.csv'.
    2) For each CSV, call download_from_csv() to create a folder (named after prefix)
       under output_dir and download all URLs into it—skipping already existing files.
    """
    pattern = os.path.join(input_dir, "*_merged.csv")
    merged_csvs = glob.glob(pattern)

    if not merged_csvs:
        logger.warning(f"No files ending with '_merged.csv' found in '{input_dir}'.")
        return

    logger.info(f"Found {len(merged_csvs)} merged CSV(s) in '{input_dir}'.")
    for csv_path in merged_csvs:
        logger.info(f"Processing '{os.path.basename(csv_path)}'")
        download_from_csv(csv_path, output_dir)


if __name__ == "__main__":
    logger.info("Starting bulk download process")
    logger.info(f"Input directory: '{INPUT_DIR}'")
    logger.info(f"Output directory: '{OUTPUT_DIR}'")

    main(INPUT_DIR, OUTPUT_DIR)

    logger.info("Bulk download process completed.")
