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
cfg = load_config()["springer_crawling"]
ROOT_URLS = cfg["ROOT_URLS"]  # List of Springer issue pages
STOP_YEAR = int(cfg["STOP_YEAR"][0])  # e.g. 2022
OUTPUT_CSV = cfg["OUTPUT_CSV"][0]
PDF_DIR = cfg["PDF_DIR"][0]
MAX_NAME_LEN = int(cfg.get("MAX_NAME_LEN", [30])[0])

os.makedirs(PDF_DIR, exist_ok=True)


def sanitize_filename(doi: str, max_len: int) -> str:
    ts = str(int(time.time()))
    fn = f"{ts}.pdf"
    return fn if len(fn) <= max_len else fn[-max_len:]


def get_meta(driver, name: str) -> str:
    try:
        return driver.find_element(By.CSS_SELECTOR, f'meta[name="{name}"]').get_attribute("content") or ""
    except:
        return ""


# ── SET UP SELENIUM ───────────────────────────────────────────────────────
options = webdriver.ChromeOptions()
prefs = {
    "download.default_directory": str(Path(PDF_DIR).resolve()),
    "download.prompt_for_download": False,
    "plugins.always_open_pdf_externally": True,
}
options.add_experimental_option("prefs", prefs)
options.add_argument("--headless")
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

wait = WebDriverWait(driver, 15)

# ── CSV SETUP ───────────────────────────────────────────────────────────────
with open(OUTPUT_CSV, "w", newline="", encoding="utf-8") as f:
    w = csv.writer(f, delimiter=",", quotechar='"', quoting=csv.QUOTE_ALL)
    w.writerow([
        "Volume", "Issue", "Title", "DOI", "Abstract",
        "Keywords", "Date", "Authors", "Article URL",
        "PDF URL", "FileName"
    ])

# ── CRAWLING ────────────────────────────────────────────────────────────────
for root_url in ROOT_URLS:
    driver.get(root_url)
    # remember this tab as the “issue” tab
    issue_handle = driver.current_window_handle

    # find all article links on this issue page
    try:
        wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "h3.app-card-open__heading a")))
    except:
        print(f"No articles found or timeout on {root_url}")
        continue

    # parse volume & issue from the URL path (e.g. "12-1")
    vol_str, issue_str = root_url.rstrip("/").split("/")[-1].split("-")

    article_links = driver.find_elements(By.CSS_SELECTOR, "h3.app-card-open__heading a")
    for a in article_links:
        raw_href = a.get_attribute("href")
        article_url = raw_href if raw_href.startswith("http") else "https://link.springer.com" + raw_href

        # ── OPEN ARTICLE IN NEW TAB ──────────────────────────────
        before_handles = set(driver.window_handles)
        driver.execute_script("window.open('');")
        # identify the new tab
        new_handle = (set(driver.window_handles) - before_handles).pop()
        driver.switch_to.window(new_handle)

        driver.get(article_url)
        try:
            wait.until(EC.presence_of_element_located((By.TAG_NAME, "meta")))
        except:
            print(f"Failed loading article {article_url}")
            driver.close()
            driver.switch_to.window(issue_handle)
            continue

        # ── SCRAPE METADATA ────────────────────────────────────
        doi = get_meta(driver, "citation_doi").replace("https://doi.org/", "")
        title = get_meta(driver, "citation_title") or a.text.strip()
        abstract = get_meta(driver, "description")
        date = get_meta(driver, "citation_online_date") or get_meta(driver, "citation_publication_date")
        volume = get_meta(driver, "citation_volume") or vol_str
        issue = get_meta(driver, "citation_issue") or issue_str

        # authors
        authors = [el.get_attribute("content").strip()
                   for el in driver.find_elements(By.CSS_SELECTOR, 'meta[name="dc.creator"]')
                   if el.get_attribute("content").strip()]
        authors_str = ", ".join(authors)

        # keywords
        kws = []
        try:
            ul = driver.find_element(By.CSS_SELECTOR, "ul.c-article-subject-list")
            kws = [li.text.strip() for li in ul.find_elements(By.TAG_NAME, "li") if li.text.strip()]
        except:
            pass
        keywords_str = "; ".join(kws)

        # PDF URL
        pdf_url = get_meta(driver, "citation_pdf_url")
        if not pdf_url:
            try:
                link = driver.find_element(By.CSS_SELECTOR, "a.c-pdf-download__link")
                href = link.get_attribute("href")
                pdf_url = href if href.startswith("http") else "https://link.springer.com" + href
            except:
                pdf_url = ""

        # ── OPTIONAL: DOWNLOAD PDF VIA REQUESTS ────────────────
        filename = ""
        if pdf_url:
            try:
                resp = requests.get(pdf_url, timeout=30)
                if resp.status_code == 200 and resp.headers.get("content-type", "").startswith("application/pdf"):
                    filename = sanitize_filename(doi, MAX_NAME_LEN)
                    with open(os.path.join(PDF_DIR, filename), "wb") as of:
                        of.write(resp.content)
                else:
                    print(f"PDF fetch failed ({resp.status_code}) for {doi}")
            except Exception as e:
                print(f"Error downloading PDF for {doi}: {e}")

        # ── WRITE TO CSV ────────────────────────────────────────
        with open(OUTPUT_CSV, "a", newline="", encoding="utf-8") as f:
            w = csv.writer(f, delimiter=",", quotechar='"', quoting=csv.QUOTE_ALL)
            w.writerow([
                volume, issue, title, doi, abstract,
                keywords_str, date, authors_str, article_url,
                pdf_url, filename
            ])

        # ── CLOSE ARTICLE TAB AND RETURN ───────────────────────
        driver.close()
        driver.switch_to.window(issue_handle)

driver.quit()
