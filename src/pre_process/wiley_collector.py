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
cfg = load_config()["wiley_crawling"]
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

# ── CRAWLING ────────────────────────────────────────────────────────────────
for root_url in ROOT_URLS:
    # 1) Open the root listing page (e.g., year listing)
    driver.get(root_url)
    wait = WebDriverWait(driver, 15)

    # Save the root tab handle to switch back later
    root_handle = driver.current_window_handle

    # Wait until at least one issue element appears
    try:
        wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "div.loi__issue")))
    except:
        print(f"Timeout loading root URL: {root_url}")
        continue

    issue_elements = driver.find_elements(By.CSS_SELECTOR, "div.loi__issue")
    for issue_el in issue_elements:
        # Extract the issue link
        try:
            link_el = issue_el.find_element(By.CSS_SELECTOR, "h4.parent-item > a.visitable")
        except:
            continue

        issue_href = link_el.get_attribute("href")
        if issue_href.startswith("/"):
            issue_url = "https://onlinelibrary.wiley.com" + issue_href
        else:
            issue_url = issue_href

        # Parse volume/issue text
        vol_issue_text = link_el.text.strip()
        parts = vol_issue_text.replace(",", "").split()
        try:
            vol_index = parts.index("Volume")
            volume = parts[vol_index + 1]
        except:
            volume = ""
        try:
            issue_index = parts.index("Issue")
            issue_num = parts[issue_index + 1]
        except:
            issue_num = ""

        # Determine year from URL or cover-date
        year = None
        try:
            href_parts = issue_href.split("/")
            year_candidate = int(href_parts[3])
            year = year_candidate
        except:
            try:
                year_text = issue_el.find_element(By.CSS_SELECTOR, "div.coverDate .cover-date-value").text
                year = int(year_text.strip().split()[-1])
            except:
                year = None

        if year is None or year < STOP_YEAR:
            continue

        # ── OPEN ISSUE IN NEW TAB ────────────────────────────────────────
        # Save current handle (root listing) in case it's changed
        # Then open a new tab and navigate to the issue
        driver.switch_to.window(root_handle)
        driver.execute_script("window.open('');")
        all_handles = driver.window_handles
        # Identify the newly opened handle as issue_handle
        issue_handle = [h for h in all_handles if h != root_handle][0]
        driver.switch_to.window(issue_handle)
        driver.get(issue_url)

        try:
            wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "a.issue-item__title.visitable")))
        except:
            print(f"Timeout loading issue URL: {issue_url}")
            # If issue page fails to load, close its tab and go back
            driver.close()
            driver.switch_to.window(root_handle)
            continue

        # 2) Once issue page is open in its own tab, gather article links
        article_link_elements = driver.find_elements(By.CSS_SELECTOR, "a.issue-item__title.visitable")
        for art_link_el in article_link_elements:
            art_href = art_link_el.get_attribute("href")
            if art_href.startswith("/"):
                article_url = "https://onlinelibrary.wiley.com" + art_href
            else:
                article_url = art_href

            try:
                title = art_link_el.find_element(By.TAG_NAME, "h2").text.strip()
                if 'Issue Information' in title:
                    continue
            except:
                title = ""

            # ── OPEN ARTICLE IN NEW TAB ─────────────────────────────────
            # Save the issue tab handle before opening article
            current_issue_handle = driver.current_window_handle
            driver.execute_script("window.open('');")
            next_handles = driver.window_handles
            article_handle = [h for h in next_handles if h != root_handle and h != issue_handle][0]
            driver.switch_to.window(article_handle)
            driver.get(article_url)

            try:
                wait.until(EC.presence_of_element_located((By.TAG_NAME, "meta")))

                # Fetch metadata
                doi = get_meta("citation_doi")
                if doi and doi.lower().startswith("https://doi.org/"):
                    doi = doi.replace("https://doi.org/", "").strip()
                date = get_meta("citation_date") or get_meta("citation_publication_date")

                author_meta_els = driver.find_elements(By.CSS_SELECTOR, 'meta[name="citation_author"]')
                authors = ", ".join([el.get_attribute("content").strip()
                                     for el in author_meta_els if
                                     el.get_attribute("content")]) if author_meta_els else ""
                keywords = get_meta("citation_keywords")

                try:
                    abs_container = driver.find_element(By.CSS_SELECTOR, "div.article-section__content.en.main")
                    abstract_p = abs_container.find_element(By.TAG_NAME, "p")
                    abstract = abstract_p.text.strip()
                except:
                    abstract = ""

                # ── PDF DOWNLOAD VIA CLICK ────────────────────────────────
                pdf_url = ""
                filename = ""
                free_access = driver.find_elements(By.CSS_SELECTOR, "div.free-access.access-type")
                open_access = driver.find_elements(By.CSS_SELECTOR, "div.open-access.access-type")
                if free_access or open_access:
                    try:
                        # Precise selector for the ePDF button
                        pdf_button_selector = (
                            "div.coolBar__section.PdfLink.cloned "
                            "a.coolBar__ctrl.pdf-download"
                        )

                        # Wait for presence & clickable, then click
                        wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, pdf_button_selector)))
                        epdf_btn = driver.find_element(By.CSS_SELECTOR, pdf_button_selector)
                        driver.execute_script("arguments[0].scrollIntoView(true);", epdf_btn)
                        wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, pdf_button_selector)))
                        epdf_btn.click()

                        # Wait for embedded PDF viewer
                        wait.until(
                            EC.presence_of_element_located((By.CSS_SELECTOR, "div.dropdown-widget.paging.grouped")))
                        wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "a.navbar-download")))
                        dl_btn = driver.find_element(By.CSS_SELECTOR, "a.navbar-download")

                        existing_files = set(f for f in os.listdir(PDF_DIR) if f.lower().endswith(".pdf"))
                        pdf_url = dl_btn.get_attribute("href")
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
                    except Exception as e:
                        print(f"Error during PDF click/download for DOI {doi}: {e}")
                        filename = ""
                        pdf_url = ""
                else:
                    pdf_url = ""
                    filename = ""

                # ── WRITE TO CSV ───────────────────────────────────────
                with open(OUTPUT_CSV, "a", newline="", encoding="utf-8") as csvf:
                    writer = csv.writer(
                        csvf,
                        delimiter=',',
                        quotechar='"',
                        quoting=csv.QUOTE_ALL
                    )
                    writer.writerow([
                        volume,
                        issue_num,
                        title,
                        doi,
                        abstract,
                        keywords,
                        date,
                        authors,
                        article_url,
                        pdf_url,
                        filename
                    ])

            except Exception as e:
                print(f"Exception processing article {article_url}: {e}")
            finally:
                # Close the article tab and return focus to the issue tab
                try:
                    driver.close()
                except:
                    pass
                driver.switch_to.window(current_issue_handle)

        # ── AFTER ALL ARTICLES FOR THIS ISSUE ─────────────────────────────
        # Close the issue tab and switch back to the root listing tab
        try:
            driver.close()
        except:
            pass
        driver.switch_to.window(root_handle)

driver.quit()
