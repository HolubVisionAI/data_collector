import os
import springernature_api_client.openaccess as openaccess
import csv
from src.utils.utils import load_config

config = load_config()
API_KEY = config['springer_openaccess']['API_KEY'][0]
JOURNAL_NAME = config['springer_openaccess']['JOURNAL_NAME'][0]
JOURNAL_ISSN = config['springer_openaccess']['JOURNAL_ISSN'][0]
PAGE_SIZE = config['springer_openaccess']['PAGE_SIZE'][0]
CUT_OFF_YEAR = config['springer_openaccess']['CUT_OFF_YEAR'][0]
OUTPUT_CSV = config['springer_openaccess']['OUTPUT_CSV'][0]


def fetch_until_cutoff():
    client = openaccess.OpenAccessAPI(api_key=API_KEY)
    rows = []
    start = 1

    while True:
        resp = client.search(
            q=f'issn:"{JOURNAL_ISSN}"',
            p=PAGE_SIZE,
            s=start,
            fetch_all=False,
            is_premium=False
        )
        # if resp.status_code != 200:
        #     raise RuntimeError(f"API error: {resp.status_code}")

        # data = resp.json()
        recs = resp.get("records", [])
        if not recs:
            break

        for r in recs:
            # pick the date field
            pub_date = r.get("publicationDate") or r.get("onlineDate")
            if not pub_date:
                continue

            # parse year and enforce cutoff
            year = int(pub_date.split("-")[0])
            if year < CUT_OFF_YEAR:
                print("date limit:", CUT_OFF_YEAR)
                return rows

            volume = r.get("volume", "")
            issue = r.get("number", "")
            title = r.get("title", "")
            doi = r.get("doi", "").strip()
            abstract = ""
            # some records embed abstract under r['abstract']['p']
            if isinstance(r.get("abstract"), dict):
                abstract = r["abstract"].get("p", "")
            # join all creators
            authors = "; ".join([c.get("creator", "") for c in r.get("creators", [])])
            # build URL from DOI
            url = f"https://link.springer.com/content/pdf/{doi}.pdf" if doi else ""

            rows.append([
                volume, issue, title, doi,
                abstract, pub_date, authors, url
            ])

        start += PAGE_SIZE

    return rows


def write_csv(rows, path):
    header = ["Volume", "Issue", "Title", "DOI", "Abstract", "Date", "Authors", "URL"]
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(header)
        writer.writerows(rows)


if __name__ == "__main__":
    all_rows = fetch_until_cutoff()
    write_csv(all_rows, OUTPUT_CSV)
    print(f"Wrote {len(all_rows)} records to {OUTPUT_CSV!r}")
