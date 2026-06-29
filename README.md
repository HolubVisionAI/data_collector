# Data Collector

A modular, extensible Python framework for automating data-collection workflows: journal metadata crawling, PDF/video
downloading, data export and folder scanning.

---

## 🚀 Features

- **Web Crawling**  
  • Sage Publications  
  • J-Stage  
  • Wiley Online Library  
  • AAMI Array  
  • Springer Journals
- **Data Export**  
  • Parse metadata (title, authors, date, URL) into CSV  
  • Generate aggregate Excel reports
- **Media Download**  
  • Bulk PDF download via `aria2`  
  • Video download via `yt-dlp`
- **Utilities**  
  • Convert URL lists to CSV  
  • Scan folders for file counts & sizes
- **Scheduling**  
  • Integrate with Windows Task Scheduler or cron

---

## 📦 Installation

1. **Clone the repo**
   ```bash
   git clone https://github.com/HolubVisionAI/data_collector.git
   cd data_collector


2. **Create a virtual environment with uv (Python 3.10)**
   ```bash
   uv venv --python 3.10
   source .venv/bin/activate  # On Windows: .venv\Scripts\activate
   ```

3. **Install dependencies**
   ```bash
   uv pip install -r requirements.txt
   ```

4. **aria2 and yt-dlp are required for downloading PDFs and videos, respectively.  
   Make sure they are installed on your system:**
   ```bash
   sudo apt install aria2
   ```

5. **For video downloading, install yt-dlp:**

   this chrome extension is needed for yt-dlp:

   (https://chromewebstore.google.com/detail/get-cookiestxt-locally/cclelndahbckbenkjhflpdbgdldlbecc)

---

## ⚙️ Configuration

Copy the example and fill in your paths, keywords, and proxy list:

```bash
cp config.example.yaml config.yaml
```

### Key sections in `config.yaml`

```yaml
sagepub_crawling:
  START_URL:
    - "https://journals.sagepub.com/toc/roaa/47/5-6"
  STOP_YEAR:
    - 2022
  OUTPUT_CSV:
    - "./output/sagepub/meta.csv"
  PDF_DIR:
    - "./output/sagepub/pdfs"

jstage_crawling:
  ROOT_URLS:
    - "https://www.jstage.jst.go.jp/browse/example/1/_contents/-char/en"
  STOP_YEAR:
    - 2022
  OUTPUT_CSV:
    - "./output/jstage/meta.csv"
  PDF_DIR:
    - "./output/jstage/pdfs"

# …and similarly for wiley_crawling, aami_crawling, springer_crawling

excel_creator:
  CSV_PATH:
    - "./output/meta.csv"
  OUTPUT_XLSX:
    - "./output/meta.xlsx"
  MATCH_CUTOFF:
    - 0.7

yt_dlp_download:
  INPUT_DIR:
    - "./input/video_csvs"
  OUTPUT_DIR:
    - "./output/videos"
  YT_DLP_OPTS:
    - "--no-playlist"
    - "--retries=3"
```

* **Proxy rotation**, **search terms**, and other options are all configurable via the same file.
* See `config.example.yaml` for full defaults.

For PDF downloads, `aria2_download` also removes duplicate PDFs after a run when
`DEDUPE_AFTER_DOWNLOAD` is enabled. The duplicate check uses the filename plus
exact file size, then writes `duplicate_files_by_name_size.csv` in the download
root with the kept and removed paths.

---

## ▶️ Usage

### Web dashboard

Start the FastAPI app and open the dashboard:

```bash
python -m uvicorn src.download_server:app --host 0.0.0.0 --port 8000
```

Then open:

```text
http://localhost:8000/
```

The dashboard can edit `config.yaml`, start configured jobs, and show job logs. The active workflows also show progress
and estimated remaining time while they run. API docs are still available at `/docs`.

### Start dashboard with the OS

On Linux systems that use `systemd`, install the dashboard as a boot service:

```bash
chmod +x scripts/install_systemd_service.sh scripts/uninstall_systemd_service.sh
./scripts/install_systemd_service.sh
```

Useful service commands:

```bash
sudo systemctl status data-collector
sudo systemctl restart data-collector
sudo journalctl -u data-collector -f
```

To remove the service:

```bash
./scripts/uninstall_systemd_service.sh
```

Optional settings can be passed while installing:

```bash
PORT=8080 SERVICE_NAME=data-collector ./scripts/install_systemd_service.sh
```

### Command line

Replace `<module>` with one of:

```text
sagepub_crawling
jstage_crawling
wiley_crawling
aami_crawling
springer_crawling
excel_creator
urls2csv
aria2_download
yt_dlp_download
scan_folder
```

Run any task like so:

```bash
 python -m src.<module> --config config.yaml
```

> Example: run SagePub crawl
>
> ```bash
> python -m src.sagepub_crawling --config config.yaml
> ```

---

## 🛠️ Scheduling

* **Windows**: use Task Scheduler to invoke the above command hourly/daily.
* **Unix**: add a cron entry, e.g.

  ```cron
  0 * * * * cd /path/to/data_collector && python -m src.sagepub_crawling --config config.yaml
  ```

---

## 🤝 Contributing

1. Fork the repository
2. Create a branch (`git checkout -b feature/my-feature`)
3. Commit your changes (`git commit -m "Add new feature"`)
4. Push (`git push origin feature/my-feature`)
5. Open a Pull Request

---

## License

This project is licensed under the MIT License – see the [LICENSE](LICENSE) file for details.
