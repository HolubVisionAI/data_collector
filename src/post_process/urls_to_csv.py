import os
import glob
import logging
import argparse

import pandas as pd
from urllib.parse import unquote
from src.utils.utils import load_config

# ── CONFIG ────────────────────────────────────────────────────────────────
cfg = load_config()["urls2csv"]
INPUT_DIR = cfg["INPUT_DIR"][0]       # e.g. "/path/to/input"
OUTPUT_DIR = cfg["OUTPUT_DIR"][0]     # e.g. "/path/to/output"
URL_FILE_PATTERN = cfg["URL_FILE_PATTERN"][0]  # e.g. "_file_type_pdf_"

# ── SET UP LOGGING ─────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


def parse_raw_file(path: str) -> pd.DataFrame:
    """
    Read a “raw” file whose contents are percent-encoded URLs (with %22 quotes
    and %0D%0A newlines). Decode them and return a DataFrame with a single column 'URL'
    containing the decoded URLs (duplicates within this file are not yet dropped).
    """
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = f.read()
    except Exception as e:
        logger.warning(f"    ⚠️ Could not open '{os.path.basename(path)}': {e}")
        return pd.DataFrame(columns=["URL"])

    # Step 1: URL-decode the entire string (%22 → ", %0D%0A → newline)
    decoded = unquote(raw)

    # Step 2: Split on newlines to get each quoted URL line
    lines = decoded.splitlines()

    # Step 3: Strip surrounding quotes and ignore empty lines
    urls = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        # Remove surrounding double‐quotes if present
        if stripped.startswith('"') and stripped.endswith('"'):
            stripped = stripped[1:-1]
        urls.append(stripped)

    df = pd.DataFrame({"URL": urls})
    logger.info(f"    • Parsed {len(df)} URLs from '{os.path.basename(path)}'")
    return df


def process_group(prefix: str, file_list: list[str], output_dir: str):
    """
    For a given prefix (e.g. "cat"), read all matching raw files, parse their percent-encoded URLs,
    drop duplicates (across files in this group), extract file names, and write
    one merged CSV named "<prefix>_merged.csv" into output_dir.
    """
    logger.info(f"  → Processing group '{prefix}' with {len(file_list)} file(s):")
    for path in file_list:
        logger.info(f"      • {os.path.basename(path)}")

    dfs = []
    for path in file_list:
        df = parse_raw_file(path)
        if not df.empty:
            dfs.append(df)

    if not dfs:
        logger.warning(f"    ⚠️ No valid URLs found for prefix '{prefix}', skipping.")
        return

    # Concatenate all parsed URLs into one DataFrame
    combined = pd.concat(dfs, ignore_index=True)
    before_dedup = combined.shape[0]

    # Drop duplicates (after decoding)
    combined = combined.drop_duplicates(subset=["URL"])
    after_dedup = combined.shape[0]
    logger.info(f"    • Total URLs before dedup: {before_dedup}, after dedup: {after_dedup}")

    # Extract file name (text after the last '/')
    combined["File Name"] = combined["URL"].apply(lambda u: os.path.basename(u))

    # Ensure output directory exists
    os.makedirs(output_dir, exist_ok=True)
    out_path = os.path.join(output_dir, f"{prefix}_merged.csv")

    # Write out only the two columns: URL, File Name
    combined[["URL", "File Name"]].to_csv(out_path, index=False)
    logger.info(f"    ⇒ Wrote {after_dedup} unique URLs to '{os.path.basename(out_path)}'")


def main(input_dir: str, output_dir: str):
    """
    1) Find all files in input_dir whose names contain URL_FILE_PATTERN.
    2) Group by prefix (everything before URL_FILE_PATTERN).
    3) For each group, call process_group().
    """
    # Pattern: any filename containing URL_FILE_PATTERN (regardless of extension)
    pattern = os.path.join(input_dir, f"*{URL_FILE_PATTERN}*")
    all_files = glob.glob(pattern)

    if not all_files:
        logger.warning(f"No files matching '*{URL_FILE_PATTERN}*' found in '{input_dir}'.")
        return

    logger.info(f"Found {len(all_files)} file(s) in '{input_dir}'.")
    groups: dict[str, list[str]] = {}

    # Group by prefix (the part before URL_FILE_PATTERN)
    for full_path in all_files:
        base = os.path.basename(full_path)
        if URL_FILE_PATTERN not in base:
            logger.debug(f"Skipping '{base}' (does not match pattern).")
            continue
        prefix = base.split(URL_FILE_PATTERN, 1)[0]
        groups.setdefault(prefix, []).append(full_path)

    logger.info(f"Detected {len(groups)} group(s): {', '.join(groups.keys())}")

    # Process each group
    for prefix, files in groups.items():
        process_group(prefix, files, output_dir)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description=(
            "Scan a directory for files whose names contain URL_FILE_PATTERN, "
            "parse percent-encoded URLs in each file, drop duplicates, "
            "extract file names, and write one '<prefix>_merged.csv' per group."
        )
    )
    parser.add_argument(
        "--input_dir",
        default=INPUT_DIR,
        help="Directory containing raw files (e.g. cat_file_type_pdf_1.csv, ...)."
    )
    parser.add_argument(
        "--output_dir",
        default=OUTPUT_DIR,
        help="Directory where merged CSVs will be written."
    )
    args = parser.parse_args()

    logger.info("Starting merge process")
    logger.info(f"Input directory: '{args.input_dir}'")
    logger.info(f"Output directory: '{args.output_dir}'")
    main(args.input_dir, args.output_dir)
    logger.info("Merge process completed.")
