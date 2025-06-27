import os
import csv
import requests
import tempfile
import time
from urllib.parse import urlparse

# ── CONFIG ─────────────────────────────────────────────────────────────────────
INPUT_CSV = "output.csv"  # your CSV from the previous step
URL_COLUMN = "URL"  # column header containing the PDF URLs
DOI_COLUMN = "DOI"  # optional: used to name the file (else falls back to URL basename)
OUTPUT_DIR = "../../Cell and Tissue Research/pdfs"  # where to save downloaded PDFs
TIMEOUT = 10  # per-request timeout (seconds)
RETRIES = 3  # number of download attempts per file
BACKOFF = 5  # seconds to wait between retries
TITLE_COLUMN = "Title"


# ────────────────────────────────────────────────────────────────────────────────
def safe_filename(name: str) -> str:
    """
    Turn a title or identifier into a filesystem-safe filename.
    Keeps letters, digits, spaces, dots, underscores and hyphens.
    """
    keep = set("abcdefghijklmnopqrstuvwxyz"
               "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
               "0123456789"
               " ._-")
    # replace spaces with underscores, then strip disallowed chars
    name = name.replace(" ", "_")
    return "".join(c for c in name if c in keep).strip()


HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "application/pdf",
    "Referer": "https://journals.sagepub.com/",
}


def download_pdf(url: str, out_path: str,
                 timeout: int = TIMEOUT,
                 retries: int = RETRIES,
                 backoff: int = BACKOFF) -> None:
    """
    Download a single PDF from `url` into `out_path` safely:
    - Adds headers to avoid 403 errors
    - Streams PDF in chunks
    - Checks Content-Type
    - Writes to a temp file and renames on success
    - Retries on failure with backoff
    """
    for attempt in range(1, retries + 1):
        try:
            with requests.get(url, stream=True, timeout=timeout, headers=HEADERS) as resp:
                resp.raise_for_status()
                ctype = resp.headers.get("Content-Type", "")
                if "pdf" not in ctype.lower():
                    raise ValueError(f"unexpected content-type: {ctype}")

                os.makedirs(os.path.dirname(out_path), exist_ok=True)
                with tempfile.NamedTemporaryFile(dir=os.path.dirname(out_path), delete=False) as tmp:
                    for chunk in resp.iter_content(chunk_size=8192):
                        if chunk:
                            tmp.write(chunk)
                    tmp.flush()
                    tmp_path = tmp.name

                os.replace(tmp_path, out_path)
                print(f"✔ Downloaded: {out_path}")
                return

        except Exception as e:
            print(f"[{attempt}/{retries}] Error downloading {url!r}: {e}")
            if attempt < retries:
                time.sleep(backoff * attempt)  # exponential backoff
            else:
                print(f"✘ Failed after {retries} attempts: {url}")


def main():
    download_pdf("https://journals.sagepub.com/doi/pdf/10.1369/00221554251323657?download=true", "pdf")
# def main():
#     os.makedirs(OUTPUT_DIR, exist_ok=True)
#
#     with open(INPUT_CSV, newline="", encoding="utf-8") as csvfile:
#         reader = csv.DictReader(csvfile)
#         for row in reader:
#             url = row.get(URL_COLUMN, "").strip()
#             if not url:
#                 continue
#
#             # 1) Try Title
#             title = row.get(TITLE_COLUMN, "").strip()
#             if title:
#                 base = safe_filename(title)
#             else:
#                 # 2) Fallback to DOI
#                 doi = row.get(DOI_COLUMN, "").strip()
#                 base = safe_filename(doi) if doi else ""
#
#             # 3) Further fallback to URL basename
#             if not base:
#                 path = urlparse(url).path
#                 base = os.path.splitext(os.path.basename(path))[0] or "download"
#
#             fname = f"{base}.pdf"
#             out_path = os.path.join(OUTPUT_DIR, fname)
#
#             if os.path.exists(out_path):
#                 print(f"– Skipping, already exists: {fname}")
#                 continue
#
#             print(f"→ Downloading {fname}")
#             download_pdf(url, out_path)
#

# if __name__ == "__main__":
#     main()
