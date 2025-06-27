import os
import re
import pandas as pd
from difflib import get_close_matches
from src.utils.utils import load_config


def normalize(s: str) -> str:
    """Lowercase, strip punctuation/whitespace for rough matching."""
    return re.sub(r'[^a-z0-9]', '', s.lower())


def find_best_match(name: str, candidates: dict, cutoff=0.6):
    norm = normalize(name)
    matches = get_close_matches(norm, candidates.keys(), n=1, cutoff=cutoff)
    return candidates[matches[0]] if matches else None


def build_file_index(root_dir: str):
    idx = {}
    for dirpath, _, files in os.walk(root_dir):
        for fn in files:
            base, _ = os.path.splitext(fn)
            idx[normalize(base)] = os.path.join(dirpath, fn)
    return idx


def link_csv_to_files(
        csv_path: str,
        root_dir: str,
        output_xlsx: str,
        src_column: str,
        link_column: str = "File Link",
        match_cutoff: float = 0.6
):
    # 1) Load CSV
    df = pd.read_csv(csv_path, dtype=str).fillna("")

    # 2) Build file index
    file_index = build_file_index(root_dir)

    # 3) Determine where the Excel will live, for relative paths
    out_dir = os.path.dirname(os.path.abspath(output_xlsx))
    if not out_dir:
        out_dir = os.getcwd()

    # 4) For each row, find best file match and compute relative link
    links = []
    for val in df[src_column]:
        match = find_best_match(val, file_index, cutoff=match_cutoff)
        if match:
            # compute path relative to the Excel file
            rel = os.path.relpath(match, start=out_dir)
            # normalize to forward-slashes for Excel
            rel = rel.replace(os.sep, '/')
            display = os.path.basename(match)
            links.append(f'=HYPERLINK("{rel}", "{display}")')
        else:
            links.append("")

    df[link_column] = links

    # 5) Write to Excel (formulas preserved)
    with pd.ExcelWriter(output_xlsx, engine='xlsxwriter') as writer:
        df.to_excel(writer, index=False, sheet_name='Sheet1')


if __name__ == "__main__":
    config = load_config()

    ROOT_DIR = config["excel_creator"]["ROOT_DIR"][0]
    CSV_PATH = ROOT_DIR + config["excel_creator"]["CSV_PATH"][0]
    OUTPUT_XLSX = ROOT_DIR + config["excel_creator"]["OUTPUT_XLSX"][0]
    SRC_COLUMN = config["excel_creator"]["SRC_COLUMN"][0]
    MATCH_CUTOFF = config["excel_creator"]["MATCH_CUTOFF"][0]

    link_csv_to_files(
        CSV_PATH,
        ROOT_DIR,
        OUTPUT_XLSX,
        SRC_COLUMN,
        link_column="File Link",
        match_cutoff=MATCH_CUTOFF
    )
    print(f"Wrote {OUTPUT_XLSX} with relative‐path hyperlinks.")
