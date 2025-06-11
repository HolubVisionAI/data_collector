#!/usr/bin/env python3
"""
SAGE Journals Scraper with selenium-stealth + webdriver-manager.

Requirements:
    pip install requests selenium webdriver-manager selenium-stealth

Usage:
    python sagepub_collector.py
"""

import os
import re
import time
import csv
from urllib.parse import urljoin
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager
from selenium_stealth import stealth

from src.pre_process.utils import sanitize_filename
from src.utils.utils import load_config

# ── CONFIG ────────────────────────────────────────────────────────────────
config = load_config()["sagepub_crawling"]
START_URL = config["START_URL"][0]
# START_URL = "https://journals.sagepub.com/toc/jhca/72/5"
STOP_YEAR = config["STOP_YEAR"][0]

OUTPUT_CSV = config["OUTPUT_CSV"][0]
PDF_DIR = config["PDF_DIR"][0]

os.makedirs(PDF_DIR, exist_ok=True)

# ── LAUNCH CHROME with selenium-stealth ────────────────────────────────────────
options = webdriver.ChromeOptions()
prefs = {
    "download.default_directory": PDF_DIR,  # Set default download folder
    "download.prompt_for_download": False,  # Disable prompt
    "download.directory_upgrade": True,  # Allow directory upgrade
    "plugins.always_open_pdf_externally": True  # Bypass PDF viewer
}
options.add_experimental_option("prefs", prefs)
# options.add_argument("--headless")            # comment out to watch
options.add_argument("--no-sandbox")
options.add_argument("--disable-gpu")
options.add_argument("--disable-blink-features=AutomationControlled")

driver = webdriver.Chrome(
    service=Service(ChromeDriverManager().install()),
    options=options
)


def get_meta_content(name):
    try:
        tag = driver.find_element(By.CSS_SELECTOR, f'meta[name="{name}"]')
        return tag.get_attribute("content")
    except:
        return None


# stealth settings
stealth(driver,
        languages=["en-US", "en"],
        vendor="Google Inc.",
        platform="Win32",
        webgl_vendor="Intel Inc.",
        renderer="Intel Iris OpenGL Engine",
        fix_hairline=True)

# ── CSV SETUP ─────────────────────────────────────────────────────────────────
csvf = open(OUTPUT_CSV, "w", newline="", encoding="utf-8")
writer = csv.writer(csvf)
writer.writerow(
    ["Volume", "Issue", "Title", "DOI", "Abstract", "Keywords", "Date", "Authors", "URL", "PDF URL", "FileName"])

# ── MAIN LOOP ─────────────────────────────────────────────────────────────────
current_url = START_URL
while True:
    driver.get(current_url)
    time.sleep(2)  # let JS load

    # parse volume/issue/year
    header = driver.find_element(By.CSS_SELECTOR, "div.spd__title h2").text
    m = re.search(r"Volume\s+(\d+)\s+Issue\s+([\d-]+).*,\s.*\s(\d{4})", header)
    if not m:
        break
    volume, issue, year = m.group(1), m.group(2), int(m.group(3))
    if year <= STOP_YEAR:
        break

    # scrape each article on the page
    articles = driver.find_elements(By.CSS_SELECTOR, "div.issue-item__body")
    for item in articles:
        a = item.find_element(By.CSS_SELECTOR, "div.issue-item__title a")
        title = a.text
        article_url = a.get_attribute("href")

        # preview abstract
        preview = ""
        try:
            preview = item.find_element(By.CSS_SELECTOR, "div.issue-item__abstract__content").text
        except:
            pass

        # open detail tab
        driver.execute_script("window.open(arguments[0]);", article_url)
        driver.switch_to.window(driver.window_handles[-1])
        time.sleep(2)

        # restricted?
        restricted = bool(driver.find_elements(By.CSS_SELECTOR, "div.meta-panel__access--other"))

        # abstract
        if restricted:
            abstract_section = driver.find_element(By.ID, "abstract")
            # Extract text content
            abstract = abstract_section.text
        else:
            try:
                abstract = driver.find_element(By.CSS_SELECTOR, "section#abstract").text
            except:
                abstract = preview

        # DOI
        doi = ""
        try:
            doi = driver.find_element(By.CSS_SELECTOR, ".core-self-citation .doi a") \
                .get_attribute("href")
        except:
            pass

        # Date
        date = ""
        try:
            date = driver.find_element(By.CSS_SELECTOR, "div.meta-panel__onlineDate") \
                .text.replace("First published online ", "")
        except:
            pass

        keywords = get_meta_content("keywords")
        # keywords = [kw.strip() for kw in keywords_raw.split(",")] if keywords_raw else []
        # keywords = keywords.
        # Authors
        authors = []
        for auth in driver.find_elements(By.CSS_SELECTOR, "div.contributors [property='author']"):
            try:
                gn = auth.find_element(By.CSS_SELECTOR, "[property='givenName']").text
                fn = auth.find_element(By.CSS_SELECTOR, "[property='familyName']").text
                if not gn or not fn:
                    continue
                authors.append(f"{gn} {fn}")
            except:
                continue
        authors_str = ", ".join(authors)

        pdf_url = ""
        filename = ""
        # download PDF if available
        try:
            if not restricted:
                pdf_btn = driver.find_element(By.CSS_SELECTOR, "a[data-id='article-toolbar-pdf-epub']")
                pdf_url = pdf_btn.get_attribute("href")
                driver.get(pdf_url)
                wait = WebDriverWait(driver, 10)
                EC.element_to_be_clickable((By.ID, "favourite-download"))
                pdf_button = wait.until(
                    EC.element_to_be_clickable((By.CSS_SELECTOR, "a#favourite-download.download")))

                time.sleep(5)
                existing_files = set(f for f in os.listdir(PDF_DIR) if f.lower().endswith(".pdf"))
                pdf_button.click()

                print("✔ Clicked the PDF download button")
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
                    filename = sanitize_filename()
                    src_path = os.path.join(PDF_DIR, new_file)
                    dst_path = os.path.join(PDF_DIR, filename)
                    try:
                        os.rename(src_path, dst_path)
                    except:
                        filename = new_file
                else:
                    filename = ""
                    pdf_url = ""

        except:
            pass
        # write CSV row
        writer.writerow(
            [volume, issue, title, doi, abstract, keywords, date, authors_str, article_url, pdf_url, filename])
        csvf.flush()
        # close detail tab
        driver.close()
        driver.switch_to.window(driver.window_handles[0])
        time.sleep(1)

    # go to previous issue
    prev = driver.find_elements(By.CSS_SELECTOR, "a[data-id='toc-previous-issue']")
    if not prev:
        break
    current_url = prev[0].get_attribute("href")
    time.sleep(1)

# ── CLEANUP ────────────────────────────────────────────────────────────────────
csvf.close()
driver.quit()
print(f"Done! CSV → {OUTPUT_CSV}, PDFs → {PDF_DIR}/")
