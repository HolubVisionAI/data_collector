import os
import time
import csv
import requests
from pathlib import Path
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager
from selenium_stealth import stealth
from src.utils.utils import load_config

# ── CONFIG ────────────────────────────────────────────────────────────────
cfg = load_config()["jstage_crawling"]
ROOT_URLS = cfg["ROOT_URLS"]  # List of listing page URLs
STOP_YEAR = int(cfg["STOP_YEAR"][0])
OUTPUT_CSV = cfg["OUTPUT_CSV"][0]
PDF_DIR = cfg["PDF_DIR"][0]
MAX_NAME_LEN = int(cfg.get("MAX_NAME_LEN", [30])[0])

os.makedirs(PDF_DIR, exist_ok=True)


def sanitize_filename(doi: str, max_len: int) -> str:
    """Clean and truncate DOI to ensure filename <= max_len (including .pdf)."""
    return f"{int(time.time())}.pdf"


def get_meta(name: str) -> str:
    """Return content of <meta name="..."> or empty if not found."""
    try:
        return driver.find_element(By.CSS_SELECTOR, f'meta[name="{name}"]').get_attribute("content") or ""
    except:
        return ""


# ── SET UP SELENIUM ────────────────────────────────────────────────────────
options = webdriver.ChromeOptions()
prefs = {
    "download.default_directory": str(Path(PDF_DIR).resolve()),
    "download.prompt_for_download": False,
    "download.directory_upgrade": True,
    "plugins.always_open_pdf_externally": True,
}
options.add_experimental_option("prefs", prefs)
options.add_argument("--no-sandbox")
options.add_argument("--disable-gpu")
options.add_argument("--disable-blink-features=AutomationControlled")

driver = webdriver.Chrome(
    service=Service(ChromeDriverManager().install()),
    options=options
)
stealth(driver,
        languages=["en-US", "en"],
        vendor="Google Inc.",
        platform="Win32",
        webgl_vendor="Intel Inc.",
        renderer="Intel Iris OpenGL Engine",
        fix_hairline=True)

# ── CSV SETUP ───────────────────────────────────────────────────────────────
with open(OUTPUT_CSV, "w", newline="", encoding="utf-8") as csvf:
    # Use semicolon as delimiter and quote all fields to handle any contained semicolons
    writer = csv.writer(
        csvf,
        delimiter=',',
        quotechar='"',
        quoting=csv.QUOTE_ALL
    )
    writer.writerow([
        "Volume", "Issue", "Title", "DOI", "Abstract",
        "Keywords", "Date", "Authors", "Article URL",
        "PDF URL", "FileName"
    ])

    # Iterate listing pages
    for listing_url in ROOT_URLS:
        driver.get(listing_url)
        WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "div.searchlist-title a"))
        )

        # Collect article links
        links = driver.find_elements(By.CSS_SELECTOR, "div.searchlist-title a")
        article_urls = [a.get_attribute("href") for a in links]

        for article_url in article_urls:
            driver.get(article_url)
            WebDriverWait(driver, 10).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, 'meta[name="citation_doi"]'))
            )

            # Extract metadata
            volume = get_meta("citation_volume") or get_meta("volume")
            issue = get_meta("citation_issue") or get_meta("issue")
            title = get_meta("title")
            doi = get_meta("citation_doi") or get_meta("doi")
            abstract = get_meta("abstract")
            date = get_meta("citation_publication_date") or get_meta("publication_date")
            pub_year = int(date.split("/")[0]) if date else 0

            if pub_year < STOP_YEAR:
                continue

            keywords = ", ".join(
                kw.get_attribute("content")
                for kw in driver.find_elements(By.CSS_SELECTOR, 'meta[name="citation_keywords"], meta[name="keywords"]')
            )
            authors = ", ".join(
                a.get_attribute("content")
                for a in driver.find_elements(By.CSS_SELECTOR, 'meta[name="authors"]')
            )

            # PDF link
            try:
                pdf_url = driver.find_element(By.CSS_SELECTOR, "a.thirdlevel-pdf-btn").get_attribute("href")
            except:
                pdf_url = ""

            # Download PDF
            filename = ""
            if pdf_url and doi:
                filename = sanitize_filename(doi, MAX_NAME_LEN)
                out_path = Path(PDF_DIR) / filename
                try:
                    resp = requests.get(pdf_url, stream=True, timeout=20)
                    resp.raise_for_status()
                    with open(out_path, "wb") as f:
                        for chunk in resp.iter_content(8192):
                            f.write(chunk)
                    time.sleep(1)
                    rel_path = os.path.relpath(out_path, Path(PDF_DIR).parent)
                    filename = rel_path
                except:
                    filename = ""

            # Write CSV row
            writer.writerow([
                volume, issue, title, doi, abstract,
                keywords, date, authors, article_url,
                pdf_url, filename
            ])

driver.quit()
print(f"Done! Output CSV: {OUTPUT_CSV}, PDFs in '{PDF_DIR}/'")
