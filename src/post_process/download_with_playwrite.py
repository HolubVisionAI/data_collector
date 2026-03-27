"""
Utility: download file using Playwright.

Usage (CLI):
    python download_with_playwrite.py <url> <save_path> [--timeout MS]

Examples:
    python download_with_playwrite.py "https://example.com/file.pdf" "/tmp/file.pdf"

Notes:
- Requires playwright: pip install playwright
  then run: playwright install
- If Playwright download fails (no download event), the script falls back to a direct HTTP download using requests.
"""

from __future__ import annotations
import argparse
import os
from pathlib import Path

import requests
import logging
import base64

# basic logger for this module
logger = logging.getLogger(__name__)
if not logger.handlers:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")


def _quick_http_probe_and_download(url: str, save_path: str, timeout_seconds: int = 10) -> bool:
    """Quickly probe the URL with requests; if it looks like a PDF serve, download via requests.
    This avoids launching Playwright for simple direct-file URLs and prevents Playwright hangs.
    """
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "application/pdf,*/*;q=0.9",
        "Accept-Language": "en-US,en;q=0.9",
    }
    try:
        with requests.get(url, stream=True, headers=headers, timeout=timeout_seconds, allow_redirects=True) as r:
            r.raise_for_status()
            ctype = r.headers.get("Content-Type", "").lower()
            disp = r.headers.get("Content-Disposition", "")
            # If content-type looks like a PDF, or content-disposition suggests a filename, download directly
            if "application/pdf" in ctype or "attachment" in disp.lower() or url.lower().endswith(".pdf"):
                logger.debug("Quick HTTP probe: direct PDF detected (ctype=%s, disp=%s). Streaming to %s", ctype, disp, save_path)
                tmp_path = str(Path(save_path).with_suffix(Path(save_path).suffix + ".partial"))
                with open(tmp_path, "wb") as f:
                    for chunk in r.iter_content(chunk_size=64 * 1024):
                        if chunk:
                            f.write(chunk)
                os.replace(tmp_path, save_path)
                return True
            else:
                logger.debug("Quick HTTP probe: not a direct PDF (ctype=%s). Let Playwright handle: %s", ctype, url)
                return False
    except Exception as e:
        logger.debug("Quick HTTP probe failed for %s: %s", url, e)
        return False


def download_with_playwright(url: str, save_path: str, timeout: int = 30000) -> bool:
    """Try to download a file using Playwright. Expects a direct download URL (no clicks required).
    Returns True on success."""
    # import Playwright lazily using dynamic import to avoid static import errors
    try:
        mod = __import__("playwright.sync_api", fromlist=["sync_playwright", "TimeoutError"])
        sync_playwright = getattr(mod, "sync_playwright")
        PlaywrightTimeoutError = getattr(mod, "TimeoutError")
    except Exception:
        return False

    save_path = str(Path(save_path))
    dest_dir = os.path.dirname(save_path)
    if dest_dir and not os.path.exists(dest_dir):
        os.makedirs(dest_dir, exist_ok=True)

    # Quick HTTP probe to avoid launching Playwright when a direct HTTP download will work.
    try:
        if _quick_http_probe_and_download(url, save_path, timeout_seconds=10):
            logger.info("Quick HTTP probe downloaded file: %s", save_path)
            return True
    except Exception:
        # non-fatal: continue to Playwright
        pass

    # helper to attempt a download with specific launch/context options
    def _attempt(headless: bool = True, extra_args: list | None = None, user_agent: str | None = None) -> bool:
        extra_args = extra_args or []
        # common browser-like headers
        headers = {
            "Accept": "application/pdf,*/*;q=0.9",
            "Accept-Language": "en-US,en;q=0.9",
        }

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=headless, args=extra_args)
            # create a more realistic context
            context = browser.new_context(accept_downloads=True, locale="en-US", user_agent=(user_agent or "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"), extra_http_headers=headers)
            page = context.new_page()

            # anti-detection script to mask navigator.webdriver and other properties
            try:
                page.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
                Object.defineProperty(navigator, 'languages', {get: () => ['en-US', 'en']});
                Object.defineProperty(navigator, 'plugins', {get: () => [1,2,3]});
                window.chrome = { runtime: {} };
                """)
            except Exception:
                pass

            try:
                # Set up a response handler to capture any response that is a PDF
                saved = {"done": False}

                def _on_response(resp):
                    try:
                        ct = (resp.headers.get('content-type') or '').lower()
                        # Only capture if response looks like a PDF
                        if 'application/pdf' in ct:
                            logger.debug('Playwright: response with PDF content-type detected: %s', resp.url)
                            body = resp.body()
                            tmp_path = str(Path(save_path).with_suffix(Path(save_path).suffix + '.partial'))
                            with open(tmp_path, 'wb') as f:
                                f.write(body)
                            os.replace(tmp_path, save_path)
                            saved['done'] = True
                    except Exception as e:
                        logger.debug('Playwright: error in response handler: %s', e)

                page.on('response', _on_response)

                # Try download event first (covers navigations that trigger browser download)
                wait_options = ['networkidle', 'load', 'domcontentloaded']
                download_saved = False
                for wait_mode in wait_options:
                    try:
                        logger.debug("Playwright: goto %s (wait_until=%s) headless=%s", url, wait_mode, headless)
                        with page.expect_download(timeout=timeout) as download_info:
                            page.goto(url, wait_until=wait_mode, timeout=timeout)
                        download = download_info.value
                        logger.debug("Playwright: download event received, saving to %s", save_path)
                        download.save_as(save_path)
                        if os.path.exists(save_path) and os.path.getsize(save_path) > 0:
                            download_saved = True
                            break
                    except PlaywrightTimeoutError:
                        logger.debug("Playwright: expect_download timed out for wait_until=%s", wait_mode)
                        # maybe response handler already saved it, check
                        if saved.get('done'):
                            download_saved = True
                            break
                        continue
                    except Exception as e:
                        logger.debug("Playwright: unexpected exception during expect_download (wait_until=%s): %s", wait_mode, e)
                        # stop trying expect_download variants
                        break

                if download_saved or saved.get('done'):
                    return True

                # If not saved, try to fetch via Playwright's request API (preserves cookies)
                try:
                    logger.debug("Playwright: trying page request GET for %s", url)
                    resp = None
                    if hasattr(page, 'request'):
                        resp = page.request.get(url, timeout=timeout)
                    else:
                        reqctx = getattr(context, 'request', None)
                        if reqctx:
                            resp = reqctx.get(url, timeout=timeout)

                    if resp and getattr(resp, 'status', None) == 200:
                        logger.debug('Playwright: page.request returned 200; saving body')
                        body = resp.body()
                        tmp_path = str(Path(save_path).with_suffix(Path(save_path).suffix + '.partial'))
                        with open(tmp_path, 'wb') as f:
                            f.write(body)
                        os.replace(tmp_path, save_path)
                        return True
                except Exception as e:
                    logger.debug('Playwright: exception during request GET fallback: %s', e)

                # Final attempt: handle blob: URLs or in-page generated PDFs by fetching within the page
                try:
                    logger.debug('Playwright: scanning for blob/data URLs in page')
                    blob_urls = page.evaluate("() => { const urls = []; const els = document.querySelectorAll('iframe,embed,object,a'); els.forEach(e=>{ const s = e.src || e.getAttribute('href'); if(s && (s.startsWith('blob:') || s.startsWith('data:'))) urls.push(s); }); return urls; }")
                    for burl in blob_urls:
                        try:
                            logger.debug('Playwright: attempting to fetch blob URL in page: %s', burl)
                            # fetch the blob and return base64 string
                            b64 = page.evaluate("(u) => fetch(u).then(r => r.arrayBuffer()).then(b => { let arr = new Uint8Array(b); let CHUNK = 0x8000; let s = ''; for (let i = 0; i < arr.length; i += CHUNK) { s += String.fromCharCode.apply(null, Array.from(arr.subarray(i, i + CHUNK))); } return btoa(s); })", burl)
                            if b64:
                                body = base64.b64decode(b64)
                                tmp_path = str(Path(save_path).with_suffix(Path(save_path).suffix + '.partial'))
                                with open(tmp_path, 'wb') as f:
                                    f.write(body)
                                os.replace(tmp_path, save_path)
                                return True
                        except Exception as e:
                            logger.debug('Playwright: blob fetch failed for %s: %s', burl, e)
                except Exception as e:
                    logger.debug('Playwright: error scanning/fetching blobs: %s', e)

                return False
            finally:
                try:
                    context.close()
                    browser.close()
                except Exception:
                    pass

    # First attempt: headless with standard args (fast)
    ok = _attempt(headless=True, extra_args=["--disable-dev-shm-usage"])
    if ok:
        return True

    # Second attempt (fallback): still run headless but add anti-detection args
    stealth_args = [
        "--disable-blink-features=AutomationControlled",
        "--no-sandbox",
        "--disable-dev-shm-usage",
    ]
    ok2 = _attempt(headless=True, extra_args=stealth_args)
    return ok2


def fallback_http_download(url: str, save_path: str, timeout: int = 60) -> bool:
    """Download file via HTTP as a fallback."""
    tmp_path = None
    try:
        with requests.get(url, stream=True, timeout=timeout) as r:
            r.raise_for_status()
            tmp_path = str(Path(save_path).with_suffix(Path(save_path).suffix + ".partial"))
            with open(tmp_path, "wb") as f:
                for chunk in r.iter_content(chunk_size=64 * 1024):
                    if chunk:
                        f.write(chunk)
            os.replace(tmp_path, save_path)
        return True
    except Exception:
        try:
            if tmp_path and os.path.exists(tmp_path):
                os.remove(tmp_path)
        except Exception:
            pass
        return False


def main() -> int:
    parser = argparse.ArgumentParser(description="Download a direct file URL using Playwright (with HTTP fallback).")
    parser.add_argument("url", help="URL to download")
    parser.add_argument("save_path", help="Local file path to save the download")
    parser.add_argument("--timeout", help="Timeout in milliseconds for Playwright download event", type=int, default=30000)

    args = parser.parse_args()

    print(f"Downloading: {args.url}\n  -> {args.save_path}")

    ok = download_with_playwright(args.url, args.save_path, timeout=args.timeout)
    if ok:
        print("Downloaded successfully with Playwright.")
        return 0

    print("Playwright download failed or timed out; trying HTTP fallback...")
    ok2 = fallback_http_download(args.url, args.save_path)
    if ok2:
        print("Downloaded successfully with HTTP fallback.")
        return 0

    print("Failed to download the file.")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
