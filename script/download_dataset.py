import argparse
import sys
import time
import os
import subprocess
from pathlib import Path
from dotenv import load_dotenv
from src.utils.common import get_project_root

def silent_run(cmd):
    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    # Install playwright silently
    silent_run([sys.executable, "-m", "pip", "install", "playwright"])

    # Install chromium silently (no progress bars)
    silent_run(["playwright", "install", "chromium", "-p"])

    # Retry import
    from playwright.sync_api import sync_playwright

def download_file(email: str, password: str, filename: str, out_dir: Path, headless: bool = True):
    """Download the specified SFLLD zip and report download speed.
    The file is saved to ``out_dir`` (created if missing).
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless, channel="msedge")
        context = browser.new_context(viewport={"width": 1280, "height": 800})
        page = context.new_page()

        # --- Login ---------------------------------------------------
        page.goto("https://claritydownload.fmapps.freddiemac.com/CRT/")
        page.wait_for_selector("input[name='pf.username']", timeout=15000)
        page.type("input[name='pf.username']", email, delay=120)
        page.wait_for_timeout(800)
        page.type("input[name='pf.pass']", password, delay=150)
        page.wait_for_timeout(1200)
        page.click("#signOnButton")

        # --- Navigate to SFLLD --------------------------------------
        page.wait_for_selector("text='SFLLD Data'", timeout=60000)
        page.click("text='SFLLD Data'")
        print('reached SFLLD data tab')
        # Locate the explicit viewport that scrolls the file list
        viewport = page.wait_for_selector("div.react-grid-Canvas", timeout=15000)
        # Ensure we start at the top of the list
        page.evaluate("el => el.scrollTo(0, 0)", viewport)
        max_scrolls = 200  # enough to traverse entire list
        found = False
        for i in range(max_scrolls):
            # Try to locate the div containing the filename
            link = page.query_selector(f"div.non-full-set-download-link:has-text('{filename}')")
            if link:
                found = True
                break
            # Scroll the viewport down by its own height
            page.evaluate("el => el.scrollBy(0, el.clientHeight)", viewport)
            page.wait_for_timeout(300)
        if not found:
            raise Exception(f"Could not find zip file '{filename}' after scrolling.")

        # --- Download & timing --------------------------------------
        start = time.time()
        with page.expect_download(timeout=300000) as dl_info:
            link.click()
        download = dl_info.value
        # Save to the desired location
        target_path = out_dir / filename
        download.save_as(target_path)
        elapsed = time.time() - start
        size_bytes = target_path.stat().st_size
        speed_mbps = (size_bytes * 8) / (elapsed * 1_000_000)  # megabits per second
        print(f"Downloaded {filename} to {target_path}")
        print(f"Size: {size_bytes / (1024*1024):.2f} MiB")
        print(f"Time: {elapsed:.2f} s")
        print(f"Average speed: {speed_mbps:.2f} Mbit/s ({speed_mbps/8:.2f} MiB/s)")
        browser.close()
        return target_path, elapsed, speed_mbps


def main():
    load_dotenv()
    ROOT = get_project_root()
    
    parser = argparse.ArgumentParser(description="Download a Freddie Mac SFLLD zip locally and report download speed.")
    parser.add_argument("--email", default=os.getenv('FM_EMAIL'), help="Login email")
    parser.add_argument("--password", default=os.getenv('FM_PASSWORD'), help="Login password")
    parser.add_argument("--filename", default="historical_data_2004.zip", help="Zip file name to download")
    parser.add_argument("--outdir", default="./downloads", help="Directory to save the file")
    parser.add_argument("--headful", action="store_true", help="Show the browser window for debugging")
    args = parser.parse_args()

    out_path = Path(args.outdir)
    download_file(args.email, args.password, args.filename, out_path, headless=not args.headful)

if __name__ == "__main__":
    main()
