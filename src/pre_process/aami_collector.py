import os
import time
import csv
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
cfg = load_config()["aami_crawling"]
ROOT_URLS = cfg["ROOT_URLS"]  # List of listing page URLs
STOP_YEAR = int(cfg["STOP_YEAR"][0])  # e.g. 2022
OUTPUT_CSV = cfg["OUTPUT_CSV"][0]
PDF_DIR = cfg["PDF_DIR"][0]
MAX_NAME_LEN = int(cfg.get("MAX_NAME_LEN", [30])[0])

os.makedirs(PDF_DIR, exist_ok=True)


def sanitize_filename(doi: str, max_len: int) -> str:
    """
    Clean and truncate DOI to ensure filename <= max_len (including .pdf).
    Here we use a timestamp-based filename to guarantee uniqueness and length constraints.
    """
    timestamp = str(int(time.time()))
    filename = f"{timestamp}.pdf"
    if len(filename) > max_len:
        return filename[-max_len:]
    return filename


def get_meta(name: str) -> str:
    """
    Return content of <meta name="..."> or empty if not found.
    """
    try:
        return driver.find_element(By.CSS_SELECTOR, f'meta[name="{name}"]').get_attribute("content") or ""
    except:
        return ""


# ── SET UP SELENIUM ───────────────────────────────────────────────────────
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

# … (keep your imports, config load, driver setup, CSV header code exactly as before) …

# ── CRAWLING ────────────────────────────────────────────────────────────────
for root_url in ROOT_URLS:
    driver.get(root_url)
    wait = WebDriverWait(driver, 15)
    root_handle = driver.current_window_handle

    # 1) Wait for and collect all the <a class="issue-info"> links
    try:
        wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "a.issue-info")))
    except:
        print(f"Timeout loading root URL: {root_url}")
        continue

    issue_links = driver.find_elements(By.CSS_SELECTOR, "a.issue-info")
    for issue_el in issue_links:
        href = issue_el.get_attribute("href")
        if href.startswith("/"):
            issue_url = "https://array.aami.org/" + href
        else:
            issue_url = href

        # Volume|Issue text is in <span class="issue-info__vol-issue">
        vol_issue_text = issue_el.find_element(
            By.CSS_SELECTOR, "span.issue-info__vol-issue"
        ).text.strip()  # e.g. "Volume 58 | Issue 2"
        parts = [p for p in vol_issue_text.replace("|", "").split() if p]
        try:
            volume = parts[parts.index("Volume") + 1]
            issue_num = parts[parts.index("Issue") + 1]
        except ValueError:
            volume, issue_num = "", ""

        # Year is in <span class="issue-info__date cover-date">
        try:
            year_text = issue_el.find_element(
                By.CSS_SELECTOR, "span.issue-info__date.cover-date"
            ).text.strip()
            year = int(year_text)
        except:
            year = None

        if year is None or year < STOP_YEAR:
            continue

        # ── OPEN THIS ISSUE IN NEW TAB ─────────────────────────────────────
        driver.switch_to.window(root_handle)
        driver.execute_script("window.open('');")
        issue_handle = [h for h in driver.window_handles if h != root_handle][0]
        driver.switch_to.window(issue_handle)
        driver.get(issue_url)

        try:
            wait.until(EC.presence_of_element_located(
                (By.CSS_SELECTOR, "div.issue-item__body")
            ))
        except:
            print(f"Timeout loading issue URL: {issue_url}")
            driver.close()
            driver.switch_to.window(root_handle)
            continue

        # 2) For each article block in this issue…
        bodies = driver.find_elements(By.CSS_SELECTOR, "div.issue-item__body")
        for body in bodies:
            # title and article URL in <div.issue-item__title><a href=…><h5>
            try:
                link = body.find_element(By.CSS_SELECTOR, "div.issue-item__title > a")
                art_href = link.get_attribute("href")
                article_url = art_href if art_href.startswith("http") else "https://onlinelibrary.wiley.com" + art_href
                title = link.find_element(By.TAG_NAME, "h5").text.strip()
            except:
                continue

            # authors in <div.issue-item__authors>…<li>Author Name</li>
            try:
                author_els = body.find_elements(By.CSS_SELECTOR, "div.issue-item__authors li")
                authors = ", ".join(a.text.strip() for a in author_els if a.text.strip())
            except:
                authors = ""

            # ── OPEN ARTICLE IN NEW TAB ─────────────────────────────────
            current_issue_handle = driver.current_window_handle
            driver.execute_script("window.open('');")
            article_handle = [h for h in driver.window_handles
                              if h not in (root_handle, issue_handle)][0]
            driver.switch_to.window(article_handle)
            driver.get(article_url)

            try:
                wait.until(EC.presence_of_element_located((By.TAG_NAME, "header")))

                # Fetch DOI via meta tag (same as before)
                doi = get_meta("citation_doi")
                if doi.lower().startswith("https://doi.org/"):
                    doi = doi.split("https://doi.org/")[-1]

                # Abstract is under <section id="abstract">…<div role="paragraph">
                try:
                    paras = driver.find_elements(
                        By.CSS_SELECTOR, "section#abstract div[role='paragraph']"
                    )
                    abstract = " ".join(p.text.strip() for p in paras)
                except:
                    abstract = ""

                # … leave your click/download-PDF block here unchanged …
                pdf_url = ""
                filename = ""
                free_access = driver.find_elements(By.CSS_SELECTOR, "div.meta-panel__access--free")
                if free_access:
                    # find the “Open full-text in eReader (PDF)” button
                    pdf_btn = driver.find_element(
                        By.CSS_SELECTOR,
                        "a[aria-label='Open full-text in eReader']"
                    )
                    pdf_btn.click()

                    # wait for & click the download icon
                    wait.until(EC.element_to_be_clickable(
                        (By.CSS_SELECTOR, "a.navbar-download")
                    ))
                    dl_btn = driver.find_element(By.CSS_SELECTOR, "a.navbar-download")
                    pdf_url = dl_btn.get_attribute("href")
                    # … then your existing “wait for new file, rename” logic …
                    existing_files = set(f for f in os.listdir(PDF_DIR) if f.lower().endswith(".pdf"))
                    time.sleep(8)
                    dl_btn.click()

                    # Wait for new PDF to appear
                    new_file = None
                    for _ in range(60):
                        time.sleep(1)
                        current_files = set(f for f in os.listdir(PDF_DIR) if f.lower().endswith(".pdf"))
                        added = current_files - existing_files
                        if added:
                            candidate = added.pop()
                            if candidate.endswith(".crdownload"):
                                continue
                            new_file = candidate
                            break

                    if new_file:
                        filename = sanitize_filename(doi, MAX_NAME_LEN)
                        src_path = os.path.join(PDF_DIR, new_file)
                        dst_path = os.path.join(PDF_DIR, filename)
                        try:
                            os.rename(src_path, dst_path)
                        except:
                            filename = new_file
                    else:
                        filename = ""
                        pdf_url = ""
                # ── WRITE ROW ────────────────────────────────────────────
                with open(OUTPUT_CSV, "a", newline="", encoding="utf-8") as csvf:
                    writer = csv.writer(csvf, delimiter=',', quotechar='"', quoting=csv.QUOTE_ALL)
                    writer.writerow([
                        volume, issue_num, title, doi, abstract,
                        "",  # keywords (you can add if you like)
                        year, authors, article_url,
                        pdf_url, filename
                    ])

            except Exception as e:
                print(f"Error processing {article_url}: {e}")

            finally:
                driver.close()
                driver.switch_to.window(current_issue_handle)

        # ── CLOSE ISSUE TAB ───────────────────────────────────────────
        driver.close()
        driver.switch_to.window(root_handle)

driver.quit()
