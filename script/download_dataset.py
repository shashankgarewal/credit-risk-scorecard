import os
import json
import requests
import threading

from pathlib import Path
from functools import wraps
from dotenv import load_dotenv
from datetime import datetime, timezone
from playwright.sync_api import sync_playwright
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Callable, Dict, List, Optional, Tuple

from src.utils.config import MANIFEST_PATH, RAW_DIR
from src.utils.logger import logger

DOWNLOAD_BASE = "https://claritydownload.fmapps.freddiemac.com"
REFERER = "https://claritydownload.fmapps.freddiemac.com/CRT/"

_manifest_lock = threading.Lock()


def get_current_utc_iso() -> str:
    """Return formatted UTC timestamp (e.g., '2026-08-20T00:15:30Z')."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _update_manifest_entry(file_name: str, updates: Dict[str, Any]) -> None:
    """Thread-safe update to a single entry in manifest.json."""
    with _manifest_lock:
        manifest: Dict[str, Any] = {}
        manifest_path = Path(MANIFEST_PATH)
        if manifest_path.exists():
            try:
                with open(manifest_path, "r", encoding="utf-8") as f:
                    manifest = json.load(f)
            except Exception as e:
                logger.warning(f"Error reading manifest: {e}")

        entry = manifest.setdefault(file_name, {})
        entry.update(updates)

        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        with open(manifest_path, "w", encoding="utf-8") as f:
            json.dump(manifest, f, indent=2)


def track_manifest_download(func: Callable) -> Callable:
    """Decorator that updates manifest with local_content_length and last_download_utc upon successful download."""
    @wraps(func)
    def wrapper(self, url: str, save_path: Path, expected_size: Optional[int] = None, *args, **kwargs) -> bool:
        success = func(self, url, save_path, expected_size, *args, **kwargs)
        if success:
            file_name = save_path.name
            actual_size = save_path.stat().st_size if save_path.exists() else (expected_size or 0)
            now_iso = get_current_utc_iso()
            _update_manifest_entry(
                file_name,
                {
                    "local_content_length": actual_size,
                    "last_download_utc": now_iso,
                    "last_checked_utc": now_iso,
                },
            )
            logger.debug(f"Manifest updated after download: {file_name} ({actual_size} bytes)")
        return success
    return wrapper


class FreddieMacDownloader:
    """Automates Freddie Mac dataset authentication, remote change detection, and parallel file downloading."""

    def __init__(
        self,
        email: Optional[str] = None,
        password: Optional[str] = None,
        raw_dir: Path = RAW_DIR,
        manifest_path: Path = MANIFEST_PATH,
        check_workers: int = 8,
        download_workers: int = 4,
    ):
        self.email = email or os.getenv("FM_EMAIL")
        self.password = password or os.getenv("FM_PASSWORD")
        self.raw_dir = Path(raw_dir)
        self.manifest_path = Path(manifest_path)
        self.check_workers = check_workers
        self.download_workers = download_workers

        self.session: Optional[requests.Session] = None
        self.user_id: Optional[str] = None

    def _store_session(self, playwright_context) -> requests.Session:
        """Build a requests.Session pre-loaded with cookies from the Playwright context."""
        session = requests.Session()
        for c in playwright_context.cookies():
            session.cookies.set(c["name"], c["value"], domain=c["domain"].lstrip("."))
        session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/149.0.0.0 Safari/537.36 Edg/149.0.0.0"
            ),
            "Referer": REFERER,
            "Accept": "application/octet-stream, */*",
        })
        return session

    def get_session(self) -> Tuple[requests.Session, str]:
        """Log in via Playwright, extract session cookies and userId."""
        if not self.email or not self.password:
            raise ValueError("Credentials missing: FM_EMAIL and FM_PASSWORD must be provided.")

        logger.info("Initiating browser login...")
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, channel="msedge")
            context = browser.new_context(viewport={"width": 1280, "height": 800})
            page = context.new_page()

            page.goto(REFERER)
            page.wait_for_selector("input[name='pf.username']", timeout=15000)
            page.type("input[name='pf.username']", self.email, delay=80)
            page.type("input[name='pf.pass']", self.password, delay=80)

            logger.info("Submitting credentials and waiting for token response...")
            with page.expect_response("**/token**") as response_info:
                page.click("#signOnButton")
                page.wait_for_load_state("networkidle")

            token_response = response_info.value.json()
            self.user_id = token_response.get("userName", "")
            self.session = self._store_session(context)
            browser.close()

        logger.info(f"Authenticated session acquired for user: {self.user_id}")
        return self.session, self.user_id

    def fetch_sflld_metadata(self) -> List[Dict[str, Any]]:
        """Fetch SFLLD file metadata list directly via authenticated API request."""
        if not self.session:
            self.get_session()

        list_url = f"{DOWNLOAD_BASE}/api/retrieval/v1/drs/sflld/listSflldFiles"
        resp = self.session.get(
            list_url,
            headers={"Accept": "application/json, text/plain, */*"},
            timeout=(10, 60),
        )
        if resp.status_code != 200:
            logger.error(f"Failed to fetch SFLLD file list: HTTP {resp.status_code}")
            return []

        data = resp.json()
        metadata = data.get("sflldHistoricalFilesList", [])
        logger.info(f"Retrieved metadata for {len(metadata)} files.")
        return metadata

    def check_zip_update(self, sffld_file_json: Dict[str, Any]) -> Dict[str, Any]:
        """Perform a HEAD request to check remote file size and status."""
        file_path = sffld_file_json.get("filePath", "")
        file_name = sffld_file_json.get("fileName", file_path)

        download_url = (
            f"{DOWNLOAD_BASE}/api/retrieval/v1/drs/sflld/downloadSflldZippedFile"
            f"?userId={self.user_id}&filePath={file_path}"
        )

        try:
            resp = self.session.head(
                download_url,
                allow_redirects=True,
                timeout=(10, 120),
            )
            content_length = resp.headers.get("content-length")
            content_length_int = int(content_length) if content_length and content_length.isdigit() else None

            logger.debug(
                f"HEAD [{resp.status_code}] {file_name} -> Content-Length: {content_length_int}"
            )
            return {
                "file_name": file_name,
                "download_url": download_url,
                "status": resp.status_code,
                "content_length": content_length_int,
            }
        except Exception as e:
            logger.error(f"Error checking HEAD for {file_name}: {e}")
            return {
                "file_name": file_name,
                "download_url": download_url,
                "status": None,
                "content_length": None,
            }

    def _evaluate_file_status(
        self, zip_status: Dict[str, Any], manifest: Dict[str, Any]
    ) -> Optional[Tuple[str, Dict[str, Any]]]:
        """Compare a file's remote status with manifest and update check timestamp if matching."""
        file_name = zip_status["file_name"]
        remote_length = zip_status["content_length"]

        local_zip_info = manifest.get(file_name, {})
        local_content_length = (
            local_zip_info.get("local_content_length")
            if isinstance(local_zip_info, dict)
            else local_zip_info
        )

        if remote_length is not None and remote_length != local_content_length:
            logger.info(
                f"Update detected for '{file_name}': remote={remote_length} vs manifest={local_content_length}"
            )
            return file_name, zip_status
        elif remote_length is not None and remote_length == local_content_length:
            now_iso = get_current_utc_iso()
            _update_manifest_entry(file_name, {"last_checked_utc": now_iso})
            logger.debug(f"'{file_name}' is up-to-date. Updated last_checked_utc={now_iso}")

        return None

    def get_updated_file_info(self) -> Dict[str, Dict[str, Any]]:
        """Fetch metadata, compare remote file lengths against local manifest, and return updated/missing files."""
        if not self.session or not self.user_id:
            self.get_session()

        sflld_metadata = self.fetch_sflld_metadata()
        if not sflld_metadata:
            logger.warning("No SFLLD metadata returned. Skipping update check.")
            return {}

        manifest: Dict[str, Any] = {}
        if self.manifest_path.exists():
            with open(self.manifest_path, "r", encoding="utf-8") as f:
                try:
                    manifest = json.load(f)
                except Exception as e:
                    logger.warning(f"Failed to read manifest from {self.manifest_path}: {e}")
        else:
            logger.info(f"Manifest not found at {self.manifest_path}. All files will be considered for update.")

        logger.info(f"Checking remote file lengths concurrently (workers={self.check_workers})...")
        updated_files_metadata = {}

        with ThreadPoolExecutor(max_workers=self.check_workers) as executor:
            future_to_item = {
                executor.submit(self.check_zip_update, item): item
                for item in sflld_metadata
            }
            for future in as_completed(future_to_item):
                evaluated = self._evaluate_file_status(future.result(), manifest)
                if evaluated:
                    file_name, zip_status = evaluated
                    updated_files_metadata[file_name] = zip_status

        logger.info(f"Total files requiring download/update: {len(updated_files_metadata)}")
        return updated_files_metadata

    @track_manifest_download
    def download_file(
        self,
        url: str,
        save_path: Path,
        expected_size: Optional[int] = None,
    ) -> bool:
        """Stream download a single file to destination path."""
        logger.info(f"Starting download: {save_path.name}")
        try:
            resp = self.session.get(url, stream=True, allow_redirects=True, timeout=(10, 300))
            if resp.status_code != 200:
                logger.error(f"Failed to download {save_path.name} (HTTP {resp.status_code})")
                return False

            save_path.parent.mkdir(parents=True, exist_ok=True)
            with open(save_path, "wb") as f:
                for chunk in resp.iter_content(chunk_size=1024 * 1024):
                    if chunk:
                        f.write(chunk)

            logger.info(f"Successfully downloaded: {save_path.name}")
            return True
        except Exception as e:
            logger.error(f"Exception during download of {save_path.name}: {e}")
            return False

    def download_files(
        self,
        updated_files_metadata: Dict[str, Dict[str, Any]],
    ) -> None:
        """Download updated files concurrently and update manifest records."""
        if not updated_files_metadata:
            logger.info("No updated files to download.")
            return

        logger.info(f"Starting download for {len(updated_files_metadata)} files (workers={self.download_workers})...")
        with ThreadPoolExecutor(max_workers=self.download_workers) as executor:
            futures = {
                executor.submit(
                    self.download_file,
                    file_info["download_url"],
                    self.raw_dir / file_name,
                    file_info.get("content_length"),
                ): file_name
                for file_name, file_info in updated_files_metadata.items()
            }
            for future in as_completed(futures):
                file_name = futures[future]
                try:
                    future.result()
                except Exception as e:
                    logger.error(f"Download failed for {file_name}: {e}")

    def run(self) -> None:
        """Full pipeline: Login -> Check updates -> Download updated files."""
        self.get_session()
        updated_files_metadata = self.get_updated_file_info()
        self.download_files(updated_files_metadata)


if __name__ == "__main__":
    load_dotenv()
    downloader = FreddieMacDownloader()
    downloader.run()
