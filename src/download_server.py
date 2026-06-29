"""
PDF Download Server

Endpoints:
- POST /download
  Payload: {"url": "https://example.com/file.pdf", "filename": "optional_name.pdf", "timeout": 30}
  Returns: application/pdf file streamed as attachment.

Behavior:
1. Try to download with aria2c (if available) with the given timeout.
2. If aria2c not available or fails, try a direct requests HTTP stream download.
3. If direct request fails, run the project's Playwright downloader script (src/post_process/download_with_playwrite.py).
4. All attempts enforce the provided timeout (seconds). If no response within timeout, move to next method.

Auto API docs: /docs (Swagger UI) and /redoc

Example:
  curl -X POST "http://localhost:8000/download" -H "Content-Type: application/json" \
    -d '{"url":"http://example.com/a.pdf"}' --output a.pdf

Requirements:
  pip install fastapi uvicorn requests

"""
from fastapi import FastAPI, HTTPException, BackgroundTasks, Request
from pydantic import BaseModel, HttpUrl
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from typing import Optional
import subprocess
import shutil
import tempfile
import os
import sys
import requests
import logging
from pathlib import Path
import concurrent.futures
import threading
import time
import uuid
import yaml
import re

logger = logging.getLogger("download_server")
logging.basicConfig(level=logging.INFO)

ROOT_DIR = Path(__file__).resolve().parents[1]
LOG_DIR = ROOT_DIR / "logs" / "ui_jobs"
CONFIG_PATH = ROOT_DIR / "config.yaml"

app = FastAPI(
    title="Data Collector",
    description="Dashboard and PDF download service for data collection tasks",
    version="1.1",
)

templates = Jinja2Templates(directory=str(ROOT_DIR / "src" / "web" / "templates"))
app.mount("/static", StaticFiles(directory=str(ROOT_DIR / "src" / "web" / "static")), name="static")


TASKS = {
    "urls2csv": {
        "name": "URL to CSV",
        "module": "src.post_process.urls_to_csv",
        "description": "Convert exported URL files into merged CSV files.",
        "config_section": "urls2csv",
        "primary": True,
    },
    "aria2_download": {
        "name": "PDF Download",
        "module": "src.post_process.download_with_aria2",
        "description": "Download PDFs from *_merged.csv files with aria2 or IDM, plus optional Playwright fallback.",
        "config_section": "aria2_download",
        "primary": True,
    },
    "yt_dlp_download": {
        "name": "Video Download",
        "module": "src.post_process.youtube_downloader",
        "description": "Download videos from *_merged.csv files with yt-dlp.",
        "config_section": "yt_dlp_download",
        "primary": True,
    },
    "excel_creator": {
        "name": "Excel Export",
        "module": "src.post_process.filter_pdfs_create_excel",
        "description": "Create an Excel file with links from metadata rows to local files.",
        "config_section": "excel_creator",
    },
    "scan_folder": {
        "name": "Folder Scan",
        "module": "src.post_process.scan_folder_summary",
        "description": "Count files, sizes, duplicate removals, and non-target removals.",
        "config_section": "scan_folder",
        "primary": True,
    },
    "sagepub_crawling": {
        "name": "SagePub Crawl",
        "module": "src.pre_process.sagepub_collector",
        "description": "Collect article metadata from Sage Publications.",
        "config_section": "sagepub_crawling",
    },
    "jstage_crawling": {
        "name": "J-STAGE Crawl",
        "module": "src.pre_process.jstage_collector",
        "description": "Collect article metadata from J-STAGE.",
        "config_section": "jstage_crawling",
    },
    "wiley_crawling": {
        "name": "Wiley Crawl",
        "module": "src.pre_process.wiley_collector",
        "description": "Collect article metadata from Wiley Online Library.",
        "config_section": "wiley_crawling",
    },
    "aami_crawling": {
        "name": "AAMI Crawl",
        "module": "src.pre_process.aami_collector",
        "description": "Collect article metadata from AAMI Array.",
        "config_section": "aami_crawling",
    },
    "springer_crawling": {
        "name": "Springer Crawl",
        "module": "src.pre_process.springer_nature_collector",
        "description": "Collect article metadata from Springer.",
        "config_section": "springer_crawling",
    },
    "tandf_crawling": {
        "name": "Taylor & Francis Crawl",
        "module": "src.pre_process.tandf_playwright_collector",
        "description": "Collect Taylor & Francis article metadata, or download issue ZIPs, with Playwright.",
        "config_section": "tandf_crawling",
    },
}

jobs_lock = threading.Lock()
jobs: dict[str, dict] = {}
job_processes: dict[str, subprocess.Popen] = {}
status_lock = threading.Lock()
google_refreshing = False
system_status_cache = {
    "google": {
        "ok": None,
        "status_code": None,
        "latency_ms": None,
        "message": "Not checked yet",
        "checked_at": None,
    },
    "disk": None,
    "checked_at": None,
}

PROGRESS_LOG_TAIL_BYTES = 64 * 1024
DISPLAY_LOG_TAIL_BYTES = 80 * 1024
GOOGLE_STATUS_TTL_SECONDS = 300
GOOGLE_STATUS_FAILURE_TTL_SECONDS = 20
DISK_STATUS_TTL_SECONDS = 60
GOOGLE_CONNECT_TIMEOUT_SECONDS = 4.0

class DownloadRequest(BaseModel):
    url: HttpUrl
    filename: Optional[str] = None
    timeout: Optional[int] = 30  # seconds


class StartJobRequest(BaseModel):
    task_id: str


class ConfigUpdateRequest(BaseModel):
    content: str


def _job_snapshot(job: dict) -> dict:
    data = dict(job)
    data["log_url"] = f"/jobs/{job['id']}/log"
    data["progress"] = _job_progress(job)
    return data


def _read_text_tail(path: Path, max_bytes: int) -> str:
    if not path.exists() or max_bytes <= 0:
        return ""
    size = path.stat().st_size
    with open(path, "rb") as f:
        if size > max_bytes:
            f.seek(-max_bytes, os.SEEK_END)
        data = f.read()
    text = data.decode("utf-8", errors="replace")
    if size > max_bytes:
        return "[Showing latest log output only]\n" + text
    return text


def _format_eta(seconds: float | None) -> str:
    if seconds is None or seconds < 0:
        return ""
    seconds = int(seconds)
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}h {minutes}m"
    if minutes:
        return f"{minutes}m {secs}s"
    return f"{secs}s"


def _parse_progress_from_log(log_text: str) -> dict:
    progress = {
        "percent": 0.0,
        "current": None,
        "total": None,
        "label": "Waiting for progress",
    }
    if not log_text:
        return progress

    job_progress_pattern = re.compile(r"JOB_PROGRESS\s+\[(\d+)\s*/\s*(\d+)\].*?\((\d+(?:\.\d+)?)%\)")
    for line in reversed(log_text.splitlines()):
        match = job_progress_pattern.search(line)
        if match:
            current = int(match.group(1))
            total = int(match.group(2))
            percent = float(match.group(3))
            progress.update(
                {
                    "percent": max(0.0, min(100.0, percent)),
                    "current": current,
                    "total": total,
                    "label": line[-180:].replace("JOB_PROGRESS", "").strip(),
                }
            )
            return progress

    patterns = [
        re.compile(r"\[(\d+)\s*/\s*(\d+)\].*?\((\d+(?:\.\d+)?)%\)"),
        re.compile(r"(\d+)\s*/\s*(\d+)\s*\((\d+(?:\.\d+)?)%\)"),
        re.compile(r"Processing file\s+(\d+)\s*/\s*(\d+)\s*\((\d+(?:\.\d+)?)%\)"),
        re.compile(r"Processing directory\s+(\d+)\s*/\s*(\d+)\s*\((\d+(?:\.\d+)?)%\)"),
        re.compile(r"Scanning root\s+(\d+)\s*/\s*(\d+)\s*\((\d+(?:\.\d+)?)%\)"),
    ]

    for line in reversed(log_text.splitlines()):
        for pattern in patterns:
            match = pattern.search(line)
            if match:
                current = int(match.group(1))
                total = int(match.group(2))
                percent = float(match.group(3))
                progress.update(
                    {
                        "percent": max(0.0, min(100.0, percent)),
                        "current": current,
                        "total": total,
                        "label": line[-180:],
                    }
                )
                return progress

    percent_matches = re.findall(r"(\d+(?:\.\d+)?)%", log_text)
    if percent_matches:
        progress["percent"] = max(0.0, min(100.0, float(percent_matches[-1])))
        progress["label"] = "Progress detected from log"
    return progress


def _job_progress(job: dict) -> dict:
    if job["status"] == "completed":
        return {
            "percent": 100.0,
            "current": None,
            "total": None,
            "label": "Completed",
            "eta": "0s",
        }
    if job["status"] == "failed":
        return {
            "percent": 0.0,
            "current": None,
            "total": None,
            "label": "Failed",
            "eta": "",
        }

    log_path = Path(job["log_path"])
    log_text = _read_text_tail(log_path, PROGRESS_LOG_TAIL_BYTES)

    progress = _parse_progress_from_log(log_text)
    elapsed = max(0.0, time.time() - job["started_at"])
    percent = progress["percent"]
    if percent > 0 and job["status"] in {"running", "stopping"}:
        remaining = elapsed * ((100.0 - percent) / percent)
        progress["eta"] = _format_eta(remaining)
    else:
        progress["eta"] = ""
    return progress


def _wait_for_job(job_id: str, proc: subprocess.Popen):
    return_code = proc.wait()
    with jobs_lock:
        job_processes.pop(job_id, None)
        job = jobs.get(job_id)
        if not job:
            return
        job["return_code"] = return_code
        job["finished_at"] = time.time()
        job["status"] = "completed" if return_code == 0 else "failed"


def _start_task(task_id: str) -> dict:
    if task_id not in TASKS:
        raise HTTPException(status_code=404, detail="Unknown task")

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    job_id = uuid.uuid4().hex[:12]
    task = TASKS[task_id]
    log_path = LOG_DIR / f"{job_id}_{task_id}.log"
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT_DIR)
    cmd = [sys.executable, "-m", task["module"]]

    log_file = open(log_path, "w", encoding="utf-8")
    try:
        proc = subprocess.Popen(
            cmd,
            cwd=str(ROOT_DIR),
            stdout=log_file,
            stderr=subprocess.STDOUT,
            text=True,
            env=env,
        )
    except Exception:
        log_file.close()
        raise
    finally:
        log_file.close()

    job = {
        "id": job_id,
        "task_id": task_id,
        "task_name": task["name"],
        "command": " ".join(cmd),
        "status": "running",
        "pid": proc.pid,
        "return_code": None,
        "started_at": time.time(),
        "finished_at": None,
        "log_path": str(log_path),
    }

    with jobs_lock:
        jobs[job_id] = job
        job_processes[job_id] = proc

    watcher = threading.Thread(target=_wait_for_job, args=(job_id, proc), daemon=True)
    watcher.start()
    return job


def _read_config_text() -> str:
    if not CONFIG_PATH.exists():
        return ""
    return CONFIG_PATH.read_text(encoding="utf-8")


def _format_bytes(value: int) -> str:
    units = ["B", "KB", "MB", "GB", "TB", "PB"]
    amount = float(value)
    for unit in units:
        if amount < 1024 or unit == units[-1]:
            return f"{amount:.1f} {unit}" if unit != "B" else f"{int(amount)} {unit}"
        amount /= 1024
    return f"{value} B"


def _check_google_connection() -> dict:
    started = time.time()
    probes = [
        ("https://www.google.com/generate_204", {204, 200}),
        ("https://www.google.com/favicon.ico", set(range(200, 500))),
        ("https://www.google.com/", set(range(200, 500))),
    ]
    errors = []
    last_status_code = None

    for url, ok_codes in probes:
        try:
            response = requests.get(
                url,
                timeout=(2, GOOGLE_CONNECT_TIMEOUT_SECONDS),
                allow_redirects=False,
                headers={"User-Agent": "DataCollectorStatus/1.0"},
            )
            last_status_code = response.status_code
            latency_ms = int((time.time() - started) * 1000)
            if response.status_code in ok_codes:
                return {
                    "ok": True,
                    "status_code": response.status_code,
                    "latency_ms": latency_ms,
                    "message": "Connected",
                }
            errors.append(f"{url} HTTP {response.status_code}")
        except Exception as exc:
            errors.append(f"{url}: {exc}")

    latency_ms = int((time.time() - started) * 1000)
    return {
        "ok": False,
        "status_code": last_status_code,
        "latency_ms": min(latency_ms, int(GOOGLE_CONNECT_TIMEOUT_SECONDS * 1000)),
        "message": errors[-1] if errors else "Google check failed",
    }


def _refresh_google_status_worker():
    global google_refreshing
    result = _check_google_connection()
    result["checked_at"] = time.time()
    with status_lock:
        system_status_cache["google"] = result
        google_refreshing = False


def _maybe_refresh_google_status(force: bool = False):
    global google_refreshing
    now = time.time()
    with status_lock:
        checked_at = system_status_cache["google"].get("checked_at")
        last_ok = system_status_cache["google"].get("ok")
        ttl = GOOGLE_STATUS_TTL_SECONDS if last_ok else GOOGLE_STATUS_FAILURE_TTL_SECONDS
        stale = checked_at is None or now - checked_at > ttl
        if force:
            stale = True
        if not stale or google_refreshing:
            return
        google_refreshing = True

    thread = threading.Thread(target=_refresh_google_status_worker, daemon=True)
    thread.start()


def _disk_status(path: Path) -> dict:
    usage = shutil.disk_usage(path)
    used = usage.total - usage.free
    percent_used = used / usage.total * 100 if usage.total else 0
    percent_free = usage.free / usage.total * 100 if usage.total else 0
    return {
        "path": str(path),
        "total": usage.total,
        "used": used,
        "free": usage.free,
        "total_human": _format_bytes(usage.total),
        "used_human": _format_bytes(used),
        "free_human": _format_bytes(usage.free),
        "percent_used": round(percent_used, 1),
        "percent_free": round(percent_free, 1),
        "checked_at": time.time(),
    }


def _refresh_disk_status_if_needed():
    now = time.time()
    with status_lock:
        disk = system_status_cache.get("disk")
        checked_at = disk.get("checked_at") if disk else None
        if disk and checked_at and now - checked_at <= DISK_STATUS_TTL_SECONDS:
            return

    disk_status = _disk_status(ROOT_DIR)
    with status_lock:
        system_status_cache["disk"] = disk_status
        system_status_cache["checked_at"] = now


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request):
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "tasks": TASKS,
            "config_path": str(CONFIG_PATH),
        },
    )


@app.get("/api/tasks")
def list_tasks():
    return {"tasks": [{"id": task_id, **task} for task_id, task in TASKS.items()]}


@app.get("/api/system-status")
def system_status(refresh: bool = False):
    _maybe_refresh_google_status(force=refresh)
    _refresh_disk_status_if_needed()
    with status_lock:
        return {
            "google": dict(system_status_cache["google"]),
            "disk": dict(system_status_cache["disk"]),
            "checked_at": system_status_cache["checked_at"],
        }


@app.get("/api/jobs")
def list_jobs():
    with jobs_lock:
        ordered = sorted(jobs.values(), key=lambda item: item["started_at"], reverse=True)
        return {"jobs": [_job_snapshot(job) for job in ordered]}


@app.post("/api/jobs")
def start_job(req: StartJobRequest):
    job = _start_task(req.task_id)
    return {"job": _job_snapshot(job)}


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str):
    with jobs_lock:
        job = jobs.get(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Unknown job")
        return {"job": _job_snapshot(job)}


@app.delete("/api/jobs/{job_id}")
def stop_job(job_id: str):
    with jobs_lock:
        job = jobs.get(job_id)
        proc = job_processes.get(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Unknown job")
        if job["status"] != "running" or not proc:
            return {"job": _job_snapshot(job)}
        job["status"] = "stopping"

    try:
        proc.terminate()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Could not stop job: {exc}") from exc

    return {"job": _job_snapshot(job)}


@app.get("/jobs/{job_id}/log", response_class=PlainTextResponse)
def get_job_log(job_id: str, tail_bytes: int = DISPLAY_LOG_TAIL_BYTES, full: bool = False):
    with jobs_lock:
        job = jobs.get(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Unknown job")
        log_path = Path(job["log_path"])

    if not log_path.exists():
        return ""
    if full:
        return log_path.read_text(encoding="utf-8", errors="replace")
    tail_bytes = max(4096, min(tail_bytes, 512 * 1024))
    return _read_text_tail(log_path, tail_bytes)


@app.get("/api/config")
def get_config():
    return {"path": str(CONFIG_PATH), "content": _read_config_text()}


@app.put("/api/config")
def update_config(req: ConfigUpdateRequest):
    try:
        parsed = yaml.safe_load(req.content) or {}
    except yaml.YAMLError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid YAML: {exc}") from exc

    if not isinstance(parsed, dict):
        raise HTTPException(status_code=400, detail="config.yaml must contain a YAML mapping at the top level")

    if CONFIG_PATH.exists():
        backup_path = CONFIG_PATH.with_suffix(".yaml.bak")
        shutil.copy2(CONFIG_PATH, backup_path)

    CONFIG_PATH.write_text(req.content, encoding="utf-8")
    return {"ok": True, "path": str(CONFIG_PATH)}


def is_valid_pdf(path: str) -> bool:
    try:
        with open(path, "rb") as f:
            head = f.read(5)
        return head.startswith(b"%PDF-")
    except Exception:
        return False


def validate_downloaded_pdf(path: str) -> bool:
    """Return True if file at path appears to be a valid PDF. Reject HTML/error bodies saved as .pdf."""
    try:
        if not os.path.exists(path):
            return False
        size = os.path.getsize(path)
        if size == 0:
            return False
        with open(path, "rb") as f:
            head = f.read(8)
        # valid PDF starts with %PDF
        if head.startswith(b"%PDF"):
            return True
        # not a PDF: inspect as text for common server error markers
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                txt = f.read(1024).lower()
            for marker in ("internal server error", "<html", "404 not found", "403 forbidden", "error"):
                if marker in txt:
                    return False
        except Exception:
            pass
        return False
    except Exception:
        return False


def _cleanup_dir(path: str):
    try:
        shutil.rmtree(path)
        logger.debug("Cleaned temp dir: %s", path)
    except Exception as e:
        logger.warning("Failed to cleanup temp dir %s: %s", path, e)


@app.post("/download", response_class=FileResponse, responses={502: {"description": "Download failed"}})
def download_file(req: DownloadRequest, background_tasks: BackgroundTasks):
    tmpdir = tempfile.mkdtemp(prefix="pdfdl_")
    out_name = req.filename or Path(req.url.path).name or "download.pdf"
    save_path = os.path.join(tmpdir, out_name)
    ok = False

    # 1) Try aria2c if installed
    aria2_bin = shutil.which("aria2c")
    if aria2_bin:
        cmd = [aria2_bin, "--dir", tmpdir, "--out", out_name, "--max-tries=1", "--check-certificate=true", str(req.url)]
        logger.info("Attempting aria2c: %s", " ".join(map(str, cmd)))
        try:
            subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=req.timeout)
            if os.path.exists(save_path) and validate_downloaded_pdf(save_path):
                ok = True
                logger.info("aria2c succeeded: %s", save_path)
            else:
                # remove invalid file to avoid returning HTML/error blobs
                if os.path.exists(save_path):
                    try:
                        os.remove(save_path)
                    except Exception:
                        pass
        except subprocess.TimeoutExpired:
            logger.warning("aria2c timed out for URL: %s", str(req.url))
        except Exception as e:
            logger.warning("aria2c error: %s", e)

    # 2) Try direct HTTP streaming using requests
    if not ok:
        logger.info("Attempting direct HTTP download for %s", req.url)
        try:
            with requests.get(str(req.url), stream=True, timeout=req.timeout, allow_redirects=True) as r:
                r.raise_for_status()
                # quick header check: prefer content-type application/pdf
                ctype = (r.headers.get("content-type") or "").lower()
                disp = r.headers.get("content-disposition") or ""
                tmp_path = str(Path(save_path).with_suffix(Path(save_path).suffix + ".partial"))
                with open(tmp_path, "wb") as f:
                    for chunk in r.iter_content(chunk_size=8192):
                        if chunk:
                            f.write(chunk)
                os.replace(tmp_path, save_path)

            if validate_downloaded_pdf(save_path):
                ok = True
                logger.info("Direct HTTP download succeeded: %s (content-type=%s, content-disposition=%s)", save_path, ctype, disp)
            else:
                logger.warning("Downloaded file is not a valid PDF (direct HTTP): %s", save_path)
                try:
                    os.remove(save_path)
                except Exception:
                    pass
        except requests.exceptions.Timeout:
            logger.warning("Direct HTTP download timed out for URL: %s", req.url)
        except Exception as e:
            logger.warning("Direct HTTP download failed: %s", e)

    # 3) Fallback: use project's Playwright downloader script
    if not ok:
        logger.info("Attempting Playwright fallback for %s", req.url)
        # Prefer importing the project's function to avoid extra process overhead.
        try:
            from src.post_process.download_with_playwrite import download_with_playwright
        except Exception:
            download_with_playwright = None

        timeout_ms = int(req.timeout * 1000)
        if download_with_playwright:
            try:
                # Run the blocking function in a thread and enforce timeout
                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
                    fut = ex.submit(download_with_playwright, str(req.url), save_path, timeout_ms)
                    try:
                        res = fut.result(timeout=(req.timeout + 5))
                        if res and validate_downloaded_pdf(save_path):
                            ok = True
                            logger.info("Playwright function succeeded: %s", save_path)
                        else:
                            logger.warning("Playwright function returned False or produced invalid file for %s", req.url)
                            try:
                                if os.path.exists(save_path):
                                    os.remove(save_path)
                            except Exception:
                                pass
                    except concurrent.futures.TimeoutError:
                        logger.warning("Playwright function timed out for URL: %s", req.url)
                        try:
                            fut.cancel()
                        except Exception:
                            pass
            except Exception as e:
                logger.warning("Playwright function invocation error: %s", e)
        else:
            # Last resort: try to spawn the script if present (keeps prior behavior)
            script_path = os.path.join(os.path.dirname(__file__), "post_process", "download_with_playwrite.py")
            if os.path.exists(script_path):
                cmd = [sys.executable, script_path, str(req.url), save_path, "--timeout", str(timeout_ms)]
                logger.info("Running Playwright script subprocess: %s", " ".join(map(str, cmd)))
                try:
                    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=req.timeout + 5)
                    logger.debug("Playwright subprocess stdout: %s", proc.stdout)
                    if proc.returncode == 0 and os.path.exists(save_path) and validate_downloaded_pdf(save_path):
                        ok = True
                        logger.info("Playwright subprocess succeeded: %s", save_path)
                    else:
                        logger.warning("Playwright subprocess failed (rc=%s): %s", proc.returncode, proc.stderr)
                        try:
                            if os.path.exists(save_path):
                                os.remove(save_path)
                        except Exception:
                            pass
                except subprocess.TimeoutExpired:
                    logger.warning("Playwright subprocess timed out for URL: %s", req.url)
                except Exception as e:
                    logger.warning("Playwright subprocess invocation error: %s", e)
            else:
                logger.warning("Playwright downloader not available; skipped")

    if not ok:
        # cleanup immediately
        _cleanup_dir(tmpdir)
        raise HTTPException(status_code=502, detail="All download methods failed or file is not a valid PDF")

    # Schedule cleanup after response
    background_tasks.add_task(_cleanup_dir, tmpdir)

    # Return as FileResponse (FastAPI will stream the file)
    return FileResponse(save_path, media_type="application/pdf", filename=out_name)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("src.download_server:app", host="0.0.0.0", port=8000, reload=False)
