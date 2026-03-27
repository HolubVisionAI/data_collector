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
from fastapi import FastAPI, HTTPException, BackgroundTasks
from pydantic import BaseModel, HttpUrl
from fastapi.responses import FileResponse
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
import io

logger = logging.getLogger("download_server")
logging.basicConfig(level=logging.INFO)

app = FastAPI(title="PDF Download Service", description="Download PDF via aria2 or Playwright fallback", version="1.0")

class DownloadRequest(BaseModel):
    url: HttpUrl
    filename: Optional[str] = None
    timeout: Optional[int] = 30  # seconds


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
