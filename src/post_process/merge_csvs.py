from __future__ import annotations
from pathlib import Path
import csv
import sys
import argparse
import unicodedata
import difflib
from typing import Dict, List, Tuple, Optional


# ---------- Text normalization helpers ----------

def normalize_for_match(s: str) -> str:
    """Normalize a title or filename for matching:
    - NFKC normalize (unify full-width/half-width)
    - lower-case
    - remove all whitespace
    - strip most punctuation/marks (Unicode categories P*, M*)
    """
    if s is None:
        return ""
    # Normalize width/compatibility
    s = unicodedata.normalize("NFKC", str(s))
    # Lowercase
    s = s.casefold()
    # Remove punctuation and marks
    out_chars = []
    for ch in s:
        cat = unicodedata.category(ch)
        if cat.startswith("P") or cat.startswith("M"):
            continue
        # Drop whitespace characters
        if ch.isspace():
            continue
        out_chars.append(ch)
    return "".join(out_chars)


# ---------- CSV reading with encoding fallbacks ----------

POSSIBLE_ENCODINGS = ["utf-8-sig", "utf-8", "gb18030", "cp1252"]


def read_csv_rows(path: Path) -> Tuple[List[str], List[dict]]:
    last_err = None
    for enc in POSSIBLE_ENCODINGS:
        try:
            with path.open("r", encoding=enc, newline="") as f:
                sniffer = csv.Sniffer()
                sample = f.read(4096)
                f.seek(0)
                has_header = False
                try:
                    has_header = sniffer.has_header(sample)
                except Exception:
                    pass
                dialect = None
                try:
                    dialect = sniffer.sniff(sample)
                except Exception:
                    dialect = csv.excel
                reader = csv.DictReader(f, dialect=dialect)
                headers = reader.fieldnames or []
                rows = [row for row in reader]
                return headers, rows
        except Exception as e:
            last_err = e
            continue
    raise RuntimeError(f"Failed to read CSV {path} with encodings {POSSIBLE_ENCODINGS}: {last_err}")


# ---------- Title extraction ----------

TITLE_CANDIDATES = ["title", "标题", "Title", "paper_title", "name"]


def pick_title(row: dict) -> Optional[str]:
    for key in row.keys():
        for cand in TITLE_CANDIDATES:
            if key.strip().lower() == cand.strip().lower():
                v = row.get(key, "")
                if v is not None and str(v).strip():
                    return str(v).strip()
    # fallback: try first non-empty field
    for k, v in row.items():
        if v is not None and str(v).strip():
            return str(v).strip()
    return None


# ---------- Build PDF index ----------

def build_pdf_index(pdf_paths: List[Path]) -> Dict[str, List[Path]]:
    index: Dict[str, List[Path]] = {}
    for p in pdf_paths:
        stem = p.stem
        key = normalize_for_match(stem)
        if not key:
            continue
        index.setdefault(key, []).append(p)
    return index


def find_pdf_for_title(title: str, idx: Dict[str, List[Path]], fuzzy: bool = True) -> Tuple[
    Optional[List[Path]], Optional[str]]:
    """Return (paths, matched_key). If none, (None, None)."""
    norm_title = normalize_for_match(title)
    if not norm_title:
        return None, None
    if norm_title in idx:
        return idx[norm_title], norm_title
    if fuzzy:
        # Use difflib over keys for approximate match
        keys = list(idx.keys())
        # Try high threshold first, then lower
        for cutoff in (0.92, 0.88, 0.85):
            matches = difflib.get_close_matches(norm_title, keys, n=1, cutoff=cutoff)
            if matches:
                k = matches[0]
                return idx[k], k
    return None, None


# ---------- Main ----------

def merge_and_link(base_dir: Path, recursive: bool) -> Path:
    pattern = "**/*.csv" if recursive else "*.csv"
    pdf_pat = "**/*.pdf" if recursive else "*.pdf"

    csv_files = sorted(p for p in base_dir.glob(pattern) if p.is_file())
    pdf_files = sorted(p for p in base_dir.glob(pdf_pat) if p.is_file())

    if not csv_files:
        print("No CSV files found.", file=sys.stderr)
        sys.exit(1)

    print(f"Found {len(csv_files)} CSV file(s) and {len(pdf_files)} PDF file(s).")

    # Read & merge CSVs (union of headers)
    all_rows: List[dict] = []
    all_headers_set = set()
    for csv_path in csv_files:
        try:
            headers, rows = read_csv_rows(csv_path)
            all_rows.extend(rows)
            for h in headers:
                if h is not None:
                    all_headers_set.add(h)
            print(f"  Loaded {csv_path.name}: {len(rows)} rows.")
        except Exception as e:
            print(f"  WARNING: Failed to read {csv_path}: {e}", file=sys.stderr)

    # Ensure we keep original columns order loosely, but it's okay to output union
    all_headers = list(all_headers_set)

    # Build PDF index
    pdf_index = build_pdf_index(pdf_files)
    print(f"Indexed {sum(len(v) for v in pdf_index.values())} PDFs by normalized name.")

    # Prepare output headers
    extra_cols = ["pdf_found", "pdf_count", "pdf_path", "pdf_file_url"]
    # Place 'title' early if present
    preferred_order = []
    # Try to put a title-like column first
    title_key = None
    for cand in TITLE_CANDIDATES:
        for h in all_headers:
            if h.lower() == cand.lower():
                title_key = h
                break
        if title_key:
            break
    if title_key:
        preferred_order.append(title_key)
    # Then the rest (excluding duplicates)
    for h in all_headers:
        if h not in preferred_order:
            preferred_order.append(h)
    out_headers = preferred_order + [c for c in extra_cols if c not in preferred_order]

    out_path = base_dir / "merged_with_pdfs.csv"
    with out_path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=out_headers, extrasaction="ignore")
        writer.writeheader()
        matched = 0
        for row in all_rows:
            title = pick_title(row) or ""
            paths, key = find_pdf_for_title(title, pdf_index, fuzzy=True)
            if paths:
                matched += 1
                # prefer the shortest path depth (closest) if multiple
                paths_sorted = sorted(paths, key=lambda p: len(p.parts))
                chosen = paths_sorted[0]
                try:
                    uri = chosen.resolve().as_uri()
                except Exception:
                    # Fallback for weird paths
                    uri = "file://" + str(chosen.resolve()).replace("\\", "/")
                row["pdf_found"] = True
                row["pdf_count"] = len(paths)
                row["pdf_path"] = str(chosen.resolve())
                row["pdf_file_url"] = uri
            else:
                row["pdf_found"] = False
                row["pdf_count"] = 0
                row["pdf_path"] = ""
                row["pdf_file_url"] = ""
            writer.writerow(row)

    print(f"Done. Wrote: {out_path}  (matched {matched}/{len(all_rows)} rows)")
    return out_path


def main():
    # ap = argparse.ArgumentParser(description="Merge CSVs and link matching PDFs by title.")
    # ap.add_argument("directory", type=str, help="Directory containing CSVs and PDFs")
    # ap.add_argument("--recursive", action="store_true", help="Search subfolders recursively")
    # args = ap.parse_args()
    directory = "/Work/tmp/journal/卫星应用/pdf"
    base_dir = Path(directory).expanduser().resolve()
    if not base_dir.exists() or not base_dir.is_dir():
        print(f"Not a directory: {base_dir}", file=sys.stderr)
        sys.exit(1)

    merge_and_link(base_dir, recursive=False)


if __name__ == "__main__":
    main()
# if __name__ == "__main__":
#     # Example usage:
#     INPUT_DIR = "/Work/tmp/journal/卫星应用"
#     OUTPUT_FILE = "/Work/tmp/journal/卫星应用/meta.csv"
#     # If you want to include subfolders, set recursive=True
#     merge_csv_files(INPUT_DIR, OUTPUT_FILE, pattern="*.csv", recursive=False)
