import os
import re
import time
import csv
import json
import requests
from pathlib import Path
from urllib.parse import urljoin
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager
from selenium_stealth import stealth
from src.utils.utils import load_config

# ── CONFIG ────────────────────────────────────────────────────────────────
cfg = load_config()["degruyter_crawling"]  # << use a separate section for clarity
ROOT_URLS = cfg["ROOT_URLS"]               # List of DeGruyterBrill issue pages
STOP_YEAR = int(cfg["STOP_YEAR"][0])       # e.g. 2022
OUTPUT_CSV = cfg["OUTPUT_CSV"][0]
PDF_DIR = cfg["PDF_DIR"][0]
MAX_NAME_LEN = int(cfg.get("MAX_NAME_LEN", [30])[0])

BASE = "https://www.degruyterbrill.com"
os.makedirs(PDF_DIR, exist_ok=True)

# ── HELPERS ───────────────────────────────────────────────────────────────
def sanitize_filename(seed: str, max_len: int) -> str:
    """
    Create a filesystem-safe PDF filename based on DOI or timestamp.
    """
    seed = (seed or "").strip().lower().replace("https://doi.org/", "")
    seed = re.sub(r"[^a-z0-9._-]+", "_", seed)
    if not seed:
        seed = str(int(time.time()))
    if not seed.endswith(".pdf"):
        seed += ".pdf"
    return seed[:max_len] if len(seed) > max_len else seed

def get_meta(driver, name: str) -> str:
    try:
        el = driver.find_element(By.CSS_SELECTOR, f'meta[name="{name}"]')
        return el.get_attribute("content") or ""
    except:
        return ""

def get_og(driver, prop: str) -> str:
    try:
        el = driver.find_element(By.CSS_SELECTOR, f'meta[property="{prop}"]')
        return el.get_attribute("content") or ""
    except:
        return ""

def abs_url(href: str) -> str:
    if not href:
        return ""
    return href if href.startswith("http") else urljoin(BASE, href)

def year_from_date(datestr: str) -> int:
    """
    Accepts formats like 2025/06/01, 2025-06-01, or 2025; returns int year or 0.
    """
    if not datestr:
        return 0
    m = re.search(r"(\d{4})", datestr)
    return int(m.group(1)) if m else 0

def transfer_cookies_to_session(driver) -> requests.Session:
    """
    Create a requests session using Selenium's cookies for the current domain.
    """
    s = requests.Session()
    for c in driver.get_cookies():
        # requests expects 'name'/'value', others are optional
        cookie_dict = {k: c.get(k) for k in ["name", "value", "domain", "path", "secure", "expires"]}
        # Some cookies require domain without leading dot
        if cookie_dict.get("domain", "").startswith("."):
            cookie_dict["domain"] = cookie_dict["domain"][1:]
        try:
            s.cookies.set(cookie_dict["name"], cookie_dict["value"], domain=cookie_dict.get("domain"), path=cookie_dict.get("path"))
        except Exception:
            pass
    # reasonable headers
    s.headers.update({
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36",
        "Accept": "*/*",
        "Referer": driver.current_url
    })
    return s

def find_pdf_url(driver) -> str:
    """
    Priority:
    1) Explicit Download button (licenseType=free, etc.)
    2) Any anchor ending with /pdf or containing '/pdf?'
    3) citation_pdf_url meta
    """
    # 1) Button as provided
    try:
        btn = driver.find_element(By.CSS_SELECTOR, 'a.ga_download_button_pdf_article.downloadPdf, a.btn-download-dgb.downloadPdf')
        href = btn.get_attribute("href")
        if href:
            return abs_url(href)
    except:
        pass

    # 2) Any /pdf link on the page
    try:
        pdf_links = driver.find_elements(By.CSS_SELECTOR, 'a[href*="/pdf"]')
        for a in pdf_links:
            href = a.get_attribute("href")
            if href and "/pdf" in href:
                return abs_url(href)
    except:
        pass

    # 3) citation_pdf_url meta (not always present on De Gruyter Brill)
    meta_pdf = get_meta(driver, "citation_pdf_url")
    if meta_pdf:
        return abs_url(meta_pdf)

    return ""

# ── SET UP SELENIUM ───────────────────────────────────────────────────────
options = webdriver.ChromeOptions()
prefs = {
    "download.default_directory": str(Path(PDF_DIR).resolve()),
    "download.prompt_for_download": False,
    "plugins.always_open_pdf_externally": True,
}
options.add_experimental_option("prefs", prefs)
# options.add_argument("--headless=new")
options.add_argument("--no-sandbox")
options.add_argument("--disable-gpu")
options.add_argument("--disable-blink-features=AutomationControlled")
options.add_argument("--window-size=1200,2000")

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

wait = WebDriverWait(driver, 20)

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
    issue_handle = driver.current_window_handle

    # The issue page lists articles as anchors with class="text-dark" and data-doi...
    try:
        wait.until(EC.presence_of_all_elements_located((By.CSS_SELECTOR, 'a.text-dark[data-doi][href*="/document/doi/"]')))
    except:
        print(f"No articles found or timeout on {root_url}")
        continue

    # Try to parse volume/issue from the issue page URL (optional fallback)
    vol_guess, issue_guess = "", ""
    tail = root_url.rstrip("/").split("/")[-1]
    # Some issue URLs may look like ".../volume-40/issue-2" or ".../40/2"
    m = re.findall(r"(\d+)", tail)
    if len(m) >= 2:
        vol_guess, issue_guess = m[0], m[1]

    article_links = driver.find_elements(By.CSS_SELECTOR, 'a.text-dark[data-doi][href*="/document/doi/"]')

    for a in article_links:
        raw_href = a.get_attribute("href")
        article_url = abs_url(raw_href)

        # Open article in a new tab
        before_handles = set(driver.window_handles)
        driver.execute_script("window.open('');")
        new_handle = (set(driver.window_handles) - before_handles).pop()
        driver.switch_to.window(new_handle)

        driver.get(article_url)
        try:
            wait.until(EC.presence_of_element_located((By.TAG_NAME, "head")))
        except:
            print(f"Failed loading article {article_url}")
            driver.close()
            driver.switch_to.window(issue_handle)
            continue

        # ── METADATA ─────────────────────────────────────────────
        doi = get_meta(driver, "citation_doi") or a.get_attribute("data-doi") or ""
        title = (get_meta(driver, "citation_title")
                 or get_og(driver, "og:title")
                 or driver.title
                 or a.text.strip())
        abstract = (get_meta(driver, "description") or get_og(driver, "og:description") or "")
        date = (get_meta(driver, "citation_publication_date") or "")
        year = year_from_date(date)

        # STOP_YEAR filter
        if year and year < STOP_YEAR:
            driver.close()
            driver.switch_to.window(issue_handle)
            continue

        volume = get_meta(driver, "citation_volume") or vol_guess
        issue = get_meta(driver, "citation_issue") or issue_guess

        # authors
        authors = []
        # DeGruyter uses citation_author; dc.creator less common here
        for el in driver.find_elements(By.CSS_SELECTOR, 'meta[name="citation_author"]'):
            content = (el.get_attribute("content") or "").strip()
            if content:
                authors.append(content)
        if not authors:
            for el in driver.find_elements(By.CSS_SELECTOR, 'meta[name="dc.creator"]'):
                content = (el.get_attribute("content") or "").strip()
                if content:
                    authors.append(content)
        authors_str = ", ".join(authors)

        # keywords (citation_keywords -> semicolon/multiple; also OG tags)
        keywords_str = get_meta(driver, "citation_keywords")
        if not keywords_str:
            # Try article:tag metas as fallback
            tags = [el.get_attribute("content") for el in driver.find_elements(By.CSS_SELECTOR, 'meta[property="article:tag"]')]
            tags = [t for t in tags if t]
            if tags:
                keywords_str = "; ".join(tags)

        # Canonical as article URL (normalized)
        try:
            canonical = driver.find_element(By.CSS_SELECTOR, 'link[rel="canonical"]').get_attribute("href") or article_url
        except:
            canonical = article_url

        # PDF URL
        pdf_url = find_pdf_url(driver)

        # ── DOWNLOAD PDF with session cookies (if possible) ─────
        filename = ""
        if pdf_url:
            try:
                sess = transfer_cookies_to_session(driver)
                resp = sess.get(pdf_url, timeout=45, allow_redirects=True)
                if resp.status_code == 200 and "application/pdf" in resp.headers.get("content-type", "").lower():
                    filename = sanitize_filename(doi or title, MAX_NAME_LEN)
                    with open(os.path.join(PDF_DIR, filename), "wb") as of:
                        of.write(resp.content)
                else:
                    # Some PDFs redirect behind a short HTML page; last resort: navigate in driver to trigger Chrome download
                    driver.get(pdf_url)
                    time.sleep(4)  # allow Chrome to download if it streams PDF
                    # We can't reliably know the final filename here; we keep filename empty in CSV in this branch
                    if not (resp.status_code == 200 and "pdf" in resp.headers.get("content-type", "").lower()):
                        print(f"PDF fetch not confirmed for {doi or title} ({resp.status_code})")
            except Exception as e:
                print(f"Error downloading PDF for {doi or title}: {e}")

        # ── WRITE TO CSV ────────────────────────────────────────
        with open(OUTPUT_CSV, "a", newline="", encoding="utf-8") as f:
            w = csv.writer(f, delimiter=",", quotechar='"', quoting=csv.QUOTE_ALL)
            w.writerow([
                volume, issue, title, doi, abstract,
                keywords_str, date, authors_str, canonical,
                pdf_url, filename
            ])

        # Close tab and return
        driver.close()
        driver.switch_to.window(issue_handle)
        # Polite delay
        time.sleep(0.7)

driver.quit()
