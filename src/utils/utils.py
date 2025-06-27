import os
import yaml
from pathlib import Path

# Get the directory of this script: src/utils
current_file = Path(__file__).resolve()

# Go up two levels to reach project root
project_root = current_file.parents[2]

# Create a new file in the root
output_file = project_root / "config.yaml"


def load_config(config_path=output_file):
    if not os.path.exists(config_path):
        print(f"⚠️ Config file not found: {config_path}")
        print("🔧 Creating a blank config.yaml file...")

        default_yaml = """# Example config.yaml
# Example config.yaml

####################################### crawling #######################################

sagepub_crawling:
  START_URL:
    - "https://journals.sagepub.com/toc/examplejournal/vol/issue"
  STOP_YEAR:
    - 2023
  OUTPUT_CSV:
    - "./output/sagepub/meta.csv"
  PDF_DIR:
    - "./output/sagepub/pdfs"

jstage_crawling:
  ROOT_URLS:
    - "https://www.jstage.jst.go.jp/browse/example/1/_contents/-char/en"
    - "https://www.jstage.jst.go.jp/browse/example/2/_contents/-char/en"
  STOP_YEAR:
    - 2023
  OUTPUT_CSV:
    - "./output/jstage/meta.csv"
  PDF_DIR:
    - "./output/jstage/pdfs"
  MAX_NAME_LEN:
    - 50

wiley_crawling:
  ROOT_URLS:
    - "https://onlinelibrary.wiley.com/loi/examplejournal/year/2023"
    - "https://onlinelibrary.wiley.com/loi/examplejournal/year/2024"
  STOP_YEAR:
    - 2023
  OUTPUT_CSV:
    - "./output/wiley/meta.csv"
  PDF_DIR:
    - "./output/wiley/pdfs"
  MAX_NAME_LEN:
    - 50

aami_crawling:
  ROOT_URLS:
    - "https://array.aami.org/loi/example/group/d2020.y2023"
    - "https://array.aami.org/loi/example/group/d2020.y2024"
  STOP_YEAR:
    - 2023
  OUTPUT_CSV:
    - "./output/aami/meta.csv"
  PDF_DIR:
    - "./output/aami/pdfs"
  MAX_NAME_LEN:
    - 50

springer_crawling:
  ROOT_URLS:
    - "https://link.springer.com/journal/example/volumes-and-issues/12-1"
    - "https://link.springer.com/journal/example/volumes-and-issues/11-1"
  STOP_YEAR:
    - 2023
  OUTPUT_CSV:
    - "./output/springer/meta.csv"
  PDF_DIR:
    - "./output/springer/pdfs"
  MAX_NAME_LEN:
    - 50

####################################### export excel #######################################

excel_creator:
  CSV_PATH:
    - "./output/meta.csv"
  ROOT_DIR:
    - "./output"
  OUTPUT_XLSX:
    - "./output/meta.xlsx"
  SRC_COLUMN:
    - "FileName"
  MATCH_CUTOFF:
    - 0.7

####################################### url to csv #######################################

urls2csv:
  INPUT_DIR:
    - "./input/urls"
  OUTPUT_DIR:
    - "./output/csvs"
  URL_FILE_PATTERN:
    - "_filetype_pdf_"

####################################### pdf downloader #######################################

aria2_download:
  INPUT_DIR:
    - "./output/csvs"
  OUTPUT_DIR:
    - "./output/pdfs"

####################################### video downloader #######################################

yt_dlp_download:
  INPUT_DIR:
    - "./input/video_csvs"
  OUTPUT_DIR:
    - "./output/videos"
  YT_DLP_CMD:
    - "yt-dlp"
  YT_DLP_OPTS:
    - "--no-playlist"
    - "--retries=3"
    - "--rate-limit=500K"
    - "--user-agent=Mozilla/5.0"

####################################### final stage #######################################

scan_folder:
  INPUT_DIR:
    - "./output"
  LANG:
    - EN
    - CN
    - RU
  FILE_TYPE:
    - pdf
    - video

"""
        with open(config_path, "w", encoding="utf-8") as f:
            f.write(default_yaml)

        print("✅ Created config.yaml. Please edit it and re-run the script.")
        exit(1)

    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)
