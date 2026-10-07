"""Google Flow coloring-image automation for the local KDP dashboard.

This module uses Playwright with a persistent local browser profile. The user must
log in to Google manually when first prompted. It does not bypass CAPTCHA,
security challenges, or Google account protections.

The automation reads MASTER_PROMPTS.json and feeds the original coloring prompt
for each page into Google Flow. Completed downloads are normalized to PNG and
saved exactly as:
    Book_1_Page1_Image.png
    Book_1_Page2_Image.png
    ...

Because Google Flow's UI can change, selectors are intentionally heuristic and
fail safely with a screenshot + progress error instead of claiming success.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import subprocess
import urllib.request
from pathlib import Path
from typing import Any, Dict, Optional

FLOW_URL = "https://labs.google/fx/tools/flow"
DEFAULT_CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"

def browser_executable() -> Optional[str]:
    """Use installed Chrome instead of downloading Playwright Chromium.
    Override with GOOGLE_FLOW_BROWSER if needed.
    """
    candidates = [os.environ.get("GOOGLE_FLOW_BROWSER"), DEFAULT_CHROME,
                  r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
                  r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
                  r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"]
    for path in candidates:
        if path and Path(path).exists():
            return path
    return None





def start_chrome_cdp(profile: Path, flow_url: str, port: int = 9222):
    """Start real Chrome (not Playwright Chromium) and connect over CDP.

    This avoids Playwright's --no-sandbox/automation launch flags, which can
    cause Google sign-in to show "This browser or app may not be secure".
    The profile is dedicated to this automation, so the user can log in once.
    """
    exe = browser_executable()
    if not exe:
        raise RuntimeError("Google Chrome was not found. Set GOOGLE_FLOW_BROWSER to chrome.exe.")
    profile.mkdir(parents=True, exist_ok=True)
    args = [
        exe,
        f"--user-data-dir={profile}",
        f"--remote-debugging-port={port}",
        "--no-first-run",
        "--no-default-browser-check",
        flow_url,
    ]
    proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    endpoint = f"http://127.0.0.1:{port}/json/version"
    deadline = time.time() + 30
    last = None
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(endpoint, timeout=2) as r:
                data = json.loads(r.read().decode("utf-8"))
                ws = data.get("webSocketDebuggerUrl")
                if ws:
                    return proc, f"http://127.0.0.1:{port}"
        except Exception as e:
            last = e
        time.sleep(0.5)
    try:
        proc.terminate()
    except Exception:
        pass
    raise RuntimeError(f"Chrome remote debugging did not start on port {port}: {last}")


def write_json(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def update_progress(root: Path, **updates: Any) -> Dict[str, Any]:
    path = root / "GOOGLE_FLOW_PROGRESS.json"
    data: Dict[str, Any] = {}
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            data = {}
    data.update(updates)
    data["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    write_json(path, data)
    return data


def load_payload(root: Path) -> Dict[str, Any]:
    path = root / "MASTER_PROMPTS.json"
    if not path.exists():
        raise FileNotFoundError(f"Missing {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def payload_from_page_dir(page_dir: Path) -> tuple[Path, Dict[str, Any]]:
    """Build a one-page payload directly from BOOK_XX/PAGE_XX output.

    This supports the user's actual Step 0 folder layout without requiring
    MASTER_PROMPTS.json to exist.
    """
    page_dir = page_dir.resolve()
    if not page_dir.is_dir():
        raise FileNotFoundError(f"Page directory not found: {page_dir}")
    m_page = re.search(r"page\s*_?0*(\d+)", page_dir.name, re.I)
    if not m_page:
        raise ValueError(f"Could not determine page number from folder: {page_dir.name}")
    page_num = int(m_page.group(1))
    book_dir = page_dir.parent
    m_book = re.search(r"book\s*_?0*(\d+)", book_dir.name, re.I)
    if not m_book:
        # Walk upward for BOOK_XX, allowing an outer folder such as "BOOK 1".
        for ancestor in page_dir.parents:
            m_book = re.search(r"book\s*_?0*(\d+)", ancestor.name, re.I)
            if m_book:
                book_dir = ancestor
                break
    if not m_book:
        raise ValueError(f"Could not determine book number from path: {page_dir}")
    book_num = int(m_book.group(1))

    prompt_candidates = [
        page_dir / "coloring_image_prompt.txt",
        page_dir / "coloring prompt.txt",
        page_dir / "coloring_prompt.txt",
    ]
    prompt_file = next((x for x in prompt_candidates if x.exists()), None)
    if not prompt_file:
        matches = list(page_dir.glob("*color*prompt*.txt"))
        prompt_file = matches[0] if matches else None
    if not prompt_file:
        raise FileNotFoundError(f"No coloring prompt TXT found in {page_dir}")
    prompt = prompt_file.read_text(encoding="utf-8").strip()
    if not prompt:
        raise ValueError(f"Coloring prompt is empty: {prompt_file}")

    # STEP_0_OUTPUT is the automation root so output stays beside BOOK_01.
    root = None
    for ancestor in page_dir.parents:
        if ancestor.name.upper() == "STEP_0_OUTPUT":
            root = ancestor
            break
    if root is None:
        # Fallback: use the nearest parent that contains BOOK_XX.
        root = book_dir.parent
    return root, {"records": [{"book": book_num, "page": page_num,
                                "coloring_image_prompt": prompt}]}


def safe_name(book: int, page: int) -> str:
    return f"Book_{int(book)}_Page{int(page)}_Image.png"


def already_done(root: Path, record: Dict[str, Any]) -> Optional[Path]:
    out = root / f"BOOK_{int(record['book']):02d}" / "Coloring Images" / safe_name(record['book'], record['page'])
    return out if out.exists() and out.stat().st_size > 1000 else None


def save_download_as_png(download, out: Path) -> None:
    tmp = out.with_suffix(".download")
    download.save_as(str(tmp))
    try:
        from PIL import Image
        with Image.open(tmp) as im:
            if im.mode not in ("RGB", "RGBA"):
                im = im.convert("RGBA")
            im.save(out, format="PNG")
    finally:
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass


def first_visible(page, locators):
    for loc in locators:
        try:
            if loc.count() and loc.first.is_visible():
                return loc.first
        except Exception:
            continue
    return None


def click_first(page, locators, timeout=5000):
    for loc in locators:
        try:
            target = loc.first
            if target.is_visible(timeout=timeout):
                target.click()
                return True
        except Exception:
            continue
    return False


def prompt_editor(page):
    # Google Flow has used both textarea-like and contenteditable prompt controls.
    candidates = [
        page.locator("textarea"),
        page.locator("[contenteditable='true']"),
        page.locator("textarea[placeholder*='prompt' i]"),
        page.locator("input[placeholder*='prompt' i]"),
    ]
    return first_visible(page, candidates)


def click_new_project(page):
    import re as _re
    return click_first(page, [
        page.get_by_role("button", name=_re.compile(r"new project|create project", _re.I)),
        page.get_by_text(_re.compile(r"new project|create project", _re.I)),
    ])


def click_generate(page):
    import re as _re
    return click_first(page, [
        page.get_by_role("button", name=_re.compile(r"generate|create|run", _re.I)),
        page.get_by_text(_re.compile(r"generate", _re.I)),
    ])


def download_generated_image(page, out: Path, timeout_ms: int = 180000) -> bool:
    import re as _re
    # Prefer a real browser download event.
    buttons = [
        page.get_by_role("button", name=_re.compile(r"download|save|export", _re.I)),
        page.locator("a[download]"),
    ]
    for button in buttons:
        try:
            if button.count() and button.last.is_visible():
                with page.expect_download(timeout=timeout_ms) as info:
                    button.last.click()
                save_download_as_png(info.value, out)
                return True
        except Exception:
            continue
    return False


def run_automation(root: Path, headless: bool = False, start_book: int = 1, start_page: int = 1,
                   end_book: Optional[int] = None, end_page: Optional[int] = None,
                   flow_url: str = FLOW_URL, login_wait: int = 180,
                   page_payload: Optional[Dict[str, Any]] = None) -> None:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as e:
        raise RuntimeError("Playwright is not installed. Run: pip install playwright") from e

    payload = page_payload if page_payload is not None else load_payload(root)
    records = payload.get("records", [])
    selected = []
    for r in records:
        b, p = int(r["book"]), int(r["page"])
        if b < start_book:
            continue
        if b == start_book and p < start_page:
            continue
        if end_book is not None and b > end_book:
            continue
        if end_book is not None and b == end_book and end_page is not None and p > end_page:
            continue
        selected.append(r)

    update_progress(root, status="starting", total=len(selected), completed=0, current=None, error=None)

    profile = root / "google_flow_browser_profile"
    downloads = root / "google_flow_downloads"
    downloads.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as pw:
        if headless:
            raise RuntimeError("Google Flow automation requires a visible Chrome window; use headless=False.")
        chrome_proc, cdp_url = start_chrome_cdp(profile, flow_url)
        browser = pw.chromium.connect_over_cdp(cdp_url)
        context = browser.contexts[0] if browser.contexts else browser.new_context(accept_downloads=True)
        page = context.pages[0] if context.pages else context.new_page()
        try:
            page.goto(flow_url, wait_until="domcontentloaded", timeout=120000)
        except Exception:
            pass

        # First run: give the user time to complete Google login/security checks.
        update_progress(root, status="waiting_for_login", message="Complete Google login manually if required.")
        try:
            page.wait_for_timeout(login_wait * 1000)
        except Exception:
            pass
        update_progress(root, status="running", message="Google Flow browser is ready.")

        completed = 0
        active_book = None
        for record in selected:
            b, p = int(record["book"]), int(record["page"])
            out_dir = root / f"BOOK_{b:02d}" / "Coloring Images"
            out_dir.mkdir(parents=True, exist_ok=True)
            out = out_dir / safe_name(b, p)
            if already_done(root, record):
                completed += 1
                update_progress(root, completed=completed, current={"book": b, "page": p}, skipped=True)
                continue

            try:
                update_progress(root, status="running", completed=completed, current={"book": b, "page": p}, error=None)

                # Create one Flow project per book, then reuse that project for all
                # pages in the book. This avoids creating hundreds of projects.
                if active_book != b:
                    if not click_new_project(page):
                        raise RuntimeError("Could not find Google Flow New Project control. UI may have changed.")
                    page.wait_for_timeout(1500)
                    active_book = b

                # Prefer image mode when Flow exposes an Image tab/button.
                try:
                    import re as _re
                    click_first(page, [
                        page.get_by_role("button", name=_re.compile(r"^image$", _re.I)),
                        page.get_by_text(_re.compile(r"^image$", _re.I)),
                    ])
                except Exception:
                    pass

                editor = prompt_editor(page)
                if not editor:
                    raise RuntimeError("Could not find Google Flow prompt editor. UI may have changed.")

                prompt_text = record.get("coloring_image_prompt", "")
                editor.fill(prompt_text)
                (out_dir / f"Book_{b}_Page{p}_Image.prompt.txt").write_text(prompt_text, encoding="utf-8")
                page.wait_for_timeout(500)

                if not click_generate(page):
                    raise RuntimeError("Could not find Google Flow Generate button. UI may have changed.")

                # Give generation time. Avoid a blind fixed 10-second success claim.
                page.wait_for_timeout(5000)
                if not download_generated_image(page, out, timeout_ms=180000):
                    screenshot = root / f"GOOGLE_FLOW_ERROR_Book_{b}_Page{p}.png"
                    page.screenshot(path=str(screenshot), full_page=True)
                    raise RuntimeError(f"Generation finished or changed state, but no Download/Save control was detected. Screenshot: {screenshot.name}")

                completed += 1
                update_progress(root, status="running", completed=completed, total=len(selected),
                                current={"book": b, "page": p}, saved=str(out))
            except Exception as exc:
                update_progress(root, status="error", completed=completed, total=len(selected),
                                current={"book": b, "page": p}, error=str(exc))
                context.close()
                return

        update_progress(root, status="completed", completed=completed, total=len(selected), current=None, error=None)
        try:
            browser.close()
        except Exception:
            pass
        try:
            chrome_proc.terminate()
        except Exception:
            pass


def login_only(root: Path, flow_url: str = FLOW_URL) -> None:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as e:
        raise RuntimeError("Playwright is not installed. Run: pip install playwright") from e
    profile = root / "google_flow_browser_profile"
    with sync_playwright() as pw:
        chrome_proc, cdp_url = start_chrome_cdp(profile, flow_url)
        browser = pw.chromium.connect_over_cdp(cdp_url)
        context = browser.contexts[0] if browser.contexts else browser.new_context(accept_downloads=True)
        page = context.pages[0] if context.pages else context.new_page()
        try:
            page.goto(flow_url, wait_until="domcontentloaded", timeout=120000)
        except Exception:
            pass
        update_progress(root, status="browser_open", message="Log in manually. Close the browser when finished.")
        try:
            page.wait_for_timeout(24 * 60 * 60 * 1000)
        except Exception:
            pass
        try:
            browser.close()
        except Exception:
            pass
        try:
            chrome_proc.terminate()
        except Exception:
            pass


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root")
    ap.add_argument("--page-dir", help="Run exactly one page directly from a Step 0 BOOK_XX/PAGE_XX folder.")
    ap.add_argument("--login", action="store_true")
    ap.add_argument("--headless", action="store_true")
    ap.add_argument("--start-book", type=int, default=1)
    ap.add_argument("--start-page", type=int, default=1)
    ap.add_argument("--end-book", type=int)
    ap.add_argument("--end-page", type=int)
    ap.add_argument("--url", default=FLOW_URL)
    ap.add_argument("--login-wait", type=int, default=180)
    args = ap.parse_args()
    try:
        if args.page_dir:
            root, page_payload = payload_from_page_dir(Path(args.page_dir))
        else:
            if not args.root:
                ap.error("one of --root or --page-dir is required")
            root = Path(args.root).resolve()
            page_payload = None
        if args.login:
            login_only(root, args.url)
        else:
            if args.page_dir:
                run_automation(root, args.headless, 1, 1, 1, 1, args.url, args.login_wait, page_payload)
            else:
                run_automation(root, args.headless, args.start_book, args.start_page,
                               args.end_book, args.end_page, args.url, args.login_wait)
        return 0
    except Exception as e:
        update_progress(root, status="error", error=str(e))
        print(f"ERROR: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
