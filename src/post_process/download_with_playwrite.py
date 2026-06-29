"""
Utility: download file using Playwright.

Usage (CLI):
    python download_with_playwrite.py <url> <save_path> [--timeout MS]

Examples:
    python download_with_playwrite.py "https://example.com/file.pdf" "/tmp/file.pdf"
    python download_with_playwrite.py "https://example.com" "/tmp/example.mhtml" --mhtml

Notes:
- Requires playwright: pip install playwright
  then run: playwright install
- If Playwright download fails (no download event), the script falls back to a direct HTTP download using requests.
"""

from __future__ import annotations
import argparse
import os
from pathlib import Path

import logging
import base64

# basic logger for this module
logger = logging.getLogger(__name__)
if not logger.handlers:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")


BLOCK_PAGE_STRONG_MARKERS = [
    "google.com/recaptcha",
    "g-recaptcha",
    "recaptcha/api",
    "/sorry/index",
    "unusual traffic",
    "our systems have detected unusual traffic",
    "verify you are human",
    "prove you are not a robot",
    "robot check",
    "cf-challenge",
    "checking if the site connection is secure",
    "access denied",
]

BLOCK_PAGE_WEAK_MARKERS = [
    "captcha",
    "blocked",
]


def _looks_like_block_page(text: str) -> bool:
    haystack = str(text or "").lower()
    if any(marker in haystack for marker in BLOCK_PAGE_STRONG_MARKERS):
        return True
    return (
        any(marker in haystack for marker in BLOCK_PAGE_WEAK_MARKERS)
        and any(
            phrase in haystack
            for phrase in (
                "verify you are human",
                "prove you are not a robot",
                "access denied",
                "unusual traffic",
                "security check",
            )
        )
    )


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
        import requests

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


def download_with_playwright(url: str, save_path: str, timeout: int = 30000, browser_name: str = "chromium", headless: bool = False, print_mode: bool = False, mhtml_mode: bool = False, mhtml_settle_ms: int = 300) -> bool:
    """Try to download a file using Playwright. Expects a direct download URL or an HTML page.
    If the resource is an HTML page, attempt to render/print it to PDF (page.pdf) when supported
    by the selected browser (Chromium), or capture it as MHTML when mhtml_mode=True.
    Returns True on success.
    browser_name: one of 'chromium', 'firefox', 'webkit'
    headless: run browser in headless mode
    """
    # import Playwright lazily using dynamic import to avoid static import errors
    try:
        mod = __import__("playwright.sync_api", fromlist=["sync_playwright", "TimeoutError"])
        sync_playwright = getattr(mod, "sync_playwright")
        PlaywrightTimeoutError = getattr(mod, "TimeoutError")
    except Exception as e:
        logger.warning("Playwright import failed: %s", e)
        return False

    save_path = str(Path(save_path))
    dest_dir = os.path.dirname(save_path)
    if dest_dir and not os.path.exists(dest_dir):
        os.makedirs(dest_dir, exist_ok=True)

    # Quick HTTP probe to avoid launching Playwright when a direct HTTP download will work.
    # MHTML mode must render the page, so do not short-circuit through requests.
    if not mhtml_mode:
        try:
            if _quick_http_probe_and_download(url, save_path, timeout_seconds=10):
                logger.info("Quick HTTP probe downloaded file: %s", save_path)
                return True
        except Exception:
            # non-fatal: continue to Playwright
            pass

    # helper to attempt a download with specific launch/context options
    def _attempt(headless_arg: bool = True, extra_args: list | None = None, user_agent: str | None = None) -> bool:
        extra_args = extra_args or []
        # common browser-like headers
        headers = {
            "Accept": "application/pdf,*/*;q=0.9",
            "Accept-Language": "en-US,en;q=0.9",
        }

        with sync_playwright() as p:
            # choose browser type dynamically
            bn = (browser_name or "chromium").lower()
            if not hasattr(p, bn):
                logger.warning("Playwright: unknown browser '%s', falling back to chromium", bn)
                bn = "chromium"
            browser_type = getattr(p, bn)
            browser = browser_type.launch(headless=headless_arg, args=extra_args)
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
                if mhtml_mode:
                    if bn != "chromium":
                        logger.warning("Playwright: MHTML capture requires Chromium, got %s", bn)
                        return False

                    wait_options = ['domcontentloaded', 'load']
                    for wait_mode in wait_options:
                        try:
                            logger.debug("Playwright: MHTML goto %s (wait_until=%s)", url, wait_mode)
                            page.goto(url, wait_until=wait_mode, timeout=timeout)
                            if mhtml_settle_ms > 0:
                                try:
                                    page.wait_for_timeout(mhtml_settle_ms)
                                except Exception:
                                    pass

                            try:
                                title = page.title()
                            except Exception:
                                title = ""
                            try:
                                body_text = page.locator("body").inner_text(timeout=2000)
                            except Exception:
                                body_text = ""
                            if _looks_like_block_page(f"{page.url}\n{title}\n{body_text}"):
                                logger.warning("Playwright: skipping blocked/CAPTCHA page: %s", page.url)
                                return False

                            client = context.new_cdp_session(page)
                            snapshot = client.send("Page.captureSnapshot", {"format": "mhtml"}).get("data", "")
                            if snapshot:
                                if _looks_like_block_page(snapshot[:1024 * 1024]):
                                    logger.warning("Playwright: captured MHTML looks like a blocked/CAPTCHA page: %s", page.url)
                                    return False
                                tmp_path = str(Path(save_path).with_suffix(Path(save_path).suffix + ".partial"))
                                with open(tmp_path, "w", encoding="utf-8", newline="") as f:
                                    f.write(snapshot)
                                os.replace(tmp_path, save_path)
                                return os.path.exists(save_path) and os.path.getsize(save_path) > 0
                        except PlaywrightTimeoutError:
                            logger.debug("Playwright: MHTML navigation timed out for wait_until=%s", wait_mode)
                            continue
                        except Exception as e:
                            logger.debug("Playwright: MHTML capture failed for wait_until=%s: %s", wait_mode, e)
                            continue

                    return False

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

                # If print_mode is requested, navigate and attempt to print to PDF immediately
                if print_mode:
                    try:
                        logger.debug('Playwright: print_mode enabled; navigating to %s', url)
                        page.goto(url, wait_until='networkidle', timeout=timeout)
                        if bn == 'chromium':
                            try:
                                page.emulate_media(media='print')
                            except Exception:
                                pass
                            try:
                                page.pdf(path=save_path, print_background=True)
                                if os.path.exists(save_path) and os.path.getsize(save_path) > 0:
                                    return True
                            except Exception as e:
                                logger.debug('Playwright: page.pdf in print_mode failed: %s', e)
                        else:
                            logger.debug('Playwright: print_mode requested but page.pdf not supported for %s', bn)
                            # As fallback, try request.get to fetch body and save
                            try:
                                resp = None
                                if hasattr(page, 'request'):
                                    resp = page.request.get(url, timeout=timeout)
                                else:
                                    reqctx = getattr(context, 'request', None)
                                    if reqctx:
                                        resp = reqctx.get(url, timeout=timeout)
                                if resp and getattr(resp, 'status', None) == 200:
                                    body = resp.body()
                                    tmp_path = str(Path(save_path).with_suffix(Path(save_path).suffix + '.partial'))
                                    with open(tmp_path, 'wb') as f:
                                        f.write(body)
                                    os.replace(tmp_path, save_path)
                                    return True
                            except Exception as e:
                                logger.debug('Playwright: print_mode fallback request failed: %s', e)
                    except Exception as e:
                        logger.debug('Playwright: navigation/print_mode error: %s', e)
                    # If print_mode did not succeed, continue to other strategies

                # Try download event first (covers navigations that trigger browser download)
                wait_options = ['networkidle', 'load', 'domcontentloaded']
                download_saved = False
                for wait_mode in wait_options:
                    try:
                        logger.debug("Playwright: goto %s (wait_until=%s) headless=%s", url, wait_mode, headless_arg)
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

                # If this looks like an HTML page, attempt to print it to PDF when supported
                try:
                    # Only Chromium reliably supports page.pdf in Playwright
                    if bn == 'chromium':
                        logger.debug('Playwright: attempting page.pdf for %s', url)
                        # ensure page is loaded
                        try:
                            # emulate print media to improve PDF layout
                            page.emulate_media(media='print')
                        except Exception:
                            pass
                        try:
                            # page.pdf writes file directly when path provided
                            page.pdf(path=save_path, print_background=True)
                            if os.path.exists(save_path) and os.path.getsize(save_path) > 0:
                                return True
                        except Exception as e:
                            logger.debug('Playwright: page.pdf failed: %s', e)
                    else:
                        # For non-chromium browsers, page.pdf may not be available. Log and continue to other fallbacks.
                        logger.debug('Playwright: page.pdf not attempted for browser %s', bn)
                except Exception as e:
                    logger.debug('Playwright: exception attempting page.pdf: %s', e)

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
    try:
        ok = _attempt(headless_arg=headless, extra_args=["--disable-dev-shm-usage"])
    except Exception as e:
        logger.warning("Playwright attempt failed: %s", e)
        ok = False
    if ok:
        return True

    # Second attempt (fallback): still run headless but add anti-detection args
    stealth_args = [
        "--disable-blink-features=AutomationControlled",
        "--no-sandbox",
        "--disable-dev-shm-usage",
    ]
    try:
        ok2 = _attempt(headless_arg=headless, extra_args=stealth_args)
    except Exception as e:
        logger.warning("Playwright stealth attempt failed: %s", e)
        ok2 = False
    return ok2


def fallback_http_download(url: str, save_path: str, timeout: int = 60) -> bool:
    """Download file via HTTP as a fallback."""
    tmp_path = None
    try:
        import requests

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
    parser.add_argument("--browser", help="Browser to use: chromium, firefox, webkit", default="chromium")
    parser.add_argument("--headless", help="Run browser headless (true/false)", default="true")
    parser.add_argument("--print", dest="print_mode", help="Treat URL as a web viewer page and render to PDF (Chromium)", action='store_true')
    parser.add_argument("--mhtml", dest="mhtml_mode", help="Capture URL as an MHTML archive (Chromium)", action='store_true')
    parser.add_argument("--settle", dest="mhtml_settle_ms", help="Extra wait before MHTML snapshot in milliseconds", type=int, default=300)

    args = parser.parse_args()

    print(f"Downloading: {args.url}\n  -> {args.save_path}")

    headless_flag = str(args.headless).lower() not in ("0", "false", "no")
    ok = download_with_playwright(
        args.url,
        args.save_path,
        timeout=args.timeout,
        browser_name=args.browser,
        headless=headless_flag,
        print_mode=bool(args.print_mode),
        mhtml_mode=bool(args.mhtml_mode),
        mhtml_settle_ms=max(0, int(args.mhtml_settle_ms)),
    )
    if ok:
        print("Downloaded successfully with Playwright.")
        return 0

    if args.mhtml_mode:
        print("Failed to capture the page as MHTML.")
        return 2

    print("Playwright download failed or timed out; trying HTTP fallback...")
    ok2 = fallback_http_download(args.url, args.save_path)
    if ok2:
        print("Downloaded successfully with HTTP fallback.")
        return 0

    print("Failed to download the file.")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
