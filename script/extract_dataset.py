import gc
import re
import json
import time
import zipfile
import shutil
import polars as pl
from pathlib import Path
from tempfile import mkdtemp
from datetime import datetime, timezone

from src.utils.logger import logger
from src.utils.config import (
    ROOT, RAW_DIR, TEMP_DIR, EXTRACT_DIR, LAYOUT_DIR, MANIFEST_PATH
)


# --- Manifest Utilities ---
def load_manifest(manifest_path: Path = MANIFEST_PATH) -> dict:
    """Loads manifest JSON if present, else returns empty dict."""
    if manifest_path.exists():
        try:
            with open(manifest_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.warning(f"Could not read manifest ({e}). Proceeding with empty manifest.")
    return {}


def save_manifest(manifest: dict, manifest_path: Path = MANIFEST_PATH) -> None:
    """Saves updated manifest dictionary to JSON."""
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)


def is_extract_needed(last_download_utc_str: str | None, extract_utc_str: str | None) -> bool:
    """Returns True if extraction is needed: extract_utc is missing, or download is newer.

    ISO-8601 UTC strings (e.g. '2026-08-10T17:22:55Z') sort lexicographically in
    chronological order, so direct string comparison is equivalent to datetime comparison.
    """
    if not extract_utc_str:
        return True
    if not last_download_utc_str:
        return False
    return last_download_utc_str > extract_utc_str


def clean_column_name(raw_name: str) -> str:
    """Cleans a raw layout attribute name into a standardized Python identifier."""
    col = re.sub(r"[^a-z0-9_]", "", str(raw_name).strip().lower().replace(" ", "_").replace("/", "_").replace("-", "_"))
    return re.sub(r"_+", "_", col).strip("_")


class LayoutManager:
    """Discovers and parses layout Excel files to map column lengths to attribute names."""

    def __init__(self, layout_dir: Path | None = None):
        self.layout_dir = layout_dir or LAYOUT_DIR
        self.layout_map: dict[tuple[str, int], list[str]] = {}
        self.attr_count_map: dict[int, list[str]] = {}
        self._load_all_layouts()

    def _load_all_layouts(self) -> None:
        """Finds all Excel layout files in layout directory or docs folder and builds column maps."""
        excel_files = []
        if self.layout_dir.exists():
            if self.layout_dir.is_dir():
                excel_files.extend(list(self.layout_dir.glob("*.xlsx")))
            elif self.layout_dir.is_file() and self.layout_dir.suffix == ".xlsx":
                excel_files.append(self.layout_dir)

        # Fallback to docs directory if none found
        if not excel_files and (ROOT / "docs").exists():
            excel_files.extend(list((ROOT / "docs").rglob("*.xlsx")))

        logger.info(f"LayoutManager: Found {len(excel_files)} Excel layout file(s).")

        for excel_path in excel_files:
            try:
                for sheet_name in pl.read_excel(excel_path, sheet_id=0):
                    df = pl.read_excel(excel_path, sheet_name=sheet_name, read_options={"header_row": 1})
                    col_names = [
                        cleaned for row in df.iter_rows(named=True)
                        if (attr := row.get("ATTRIBUTE NAME")) and (cleaned := clean_column_name(attr))
                    ]
                    if not col_names:
                        continue

                    s = sheet_name.lower()
                    dtype = "origination" if "orig" in s else ("performance" if any(k in s for k in ("perf", "svcg", "time", "monthly")) else "unknown")
                    self.layout_map[(dtype, len(col_names))] = col_names
                    self.attr_count_map[len(col_names)] = col_names
                    logger.info(f"Loaded layout: '{sheet_name}' ({excel_path.name}) type={dtype}, cols={len(col_names)}")
            except Exception as e:
                logger.warning(f"Could not load layout file {excel_path.name}: {e}")

    def get_column_names(self, dataset_type: str | None, num_cols: int) -> list[str]:
        """Returns column names matching dataset type and column count, or generates default names."""
        if dataset_type and (dataset_type, num_cols) in self.layout_map:
            return self.layout_map[(dataset_type, num_cols)]

        if num_cols in self.attr_count_map:
            return self.attr_count_map[num_cols]

        # Fallback column names
        return [f"column_{i+1}" for i in range(num_cols)]


class FreddieMacExtractor:
    """Traverses nested raw dataset archives, converts pipe-delimited text files into Parquet format."""

    def __init__(
        self,
        raw_dir: Path = RAW_DIR,
        temp_root: Path = TEMP_DIR,
        extract_dir: Path = EXTRACT_DIR,
        manifest_path: Path = MANIFEST_PATH,
        layout_manager: LayoutManager | None = None,
    ):
        self.raw_dir = raw_dir
        self.temp_root = temp_root
        self.extract_dir = extract_dir
        self.manifest_path = manifest_path
        self.layout_manager = layout_manager or LayoutManager()

        self.temp_root.mkdir(parents=True, exist_ok=True)
        self.extract_dir.mkdir(parents=True, exist_ok=True)

        self.manifest = load_manifest(self.manifest_path)
        self.failed_files: list[dict] = []
        self.processed_files: list[dict] = []

    @staticmethod
    def get_dataset_type(filename: str) -> str:
        """Determines if text file is origination or performance data."""
        fname = filename.lower()
        if "orig" in fname:
            return "origination"
        if any(k in fname for k in ("time", "svcg", "perf")):
            return "performance"
        return "unknown"

    @staticmethod
    def parse_year_quarter(filename: str) -> tuple[str, str]:
        """Extracts (year, quarter_number) e.g. ('2020', '1') from filename."""
        match = re.search(r"(\d{4})Q([1-4])", filename, re.IGNORECASE)
        if match:
            return match.group(1), match.group(2)
        
        match_year = re.search(r"(\d{4})", filename)
        year = match_year.group(1) if match_year else "unknown"
        return year, "1"

    @staticmethod
    def safe_rmtree(target_dir: Path, retries: int = 5, delay: float = 0.5) -> None:
        """Safely removes a directory tree, retrying if Windows file locks temporarily delay deletion."""
        if not target_dir.exists():
            return

        gc.collect()
        for attempt in range(retries):
            try:
                shutil.rmtree(target_dir)
                return
            except Exception:
                time.sleep(delay)
                gc.collect()

        shutil.rmtree(target_dir, ignore_errors=True)

    def purge_temp_dir(self) -> None:
        """Purges any remaining files or directories inside temp_root."""
        if not self.temp_root.exists():
            return

        gc.collect()
        for item in self.temp_root.glob("*"):
            if item.is_dir():
                self.safe_rmtree(item)
            elif item.is_file():
                try:
                    item.unlink()
                except Exception:
                    pass

    def is_yearly_archive_needed(self, zip_file: Path) -> bool:
        """Returns True if any inner file/quarter in the yearly zip needs extraction or is missing on disk."""
        zip_name = zip_file.name
        zip_info = self.manifest.get(zip_name, {})
        download_utc_str = zip_info.get("last_download_utc")

        try:
            with zipfile.ZipFile(zip_file) as z:
                inner_items = z.namelist()
        except Exception:
            return True

        for item in inner_items:
            if item.endswith(".zip") or item.endswith(".txt"):
                year, quarter = self.parse_year_quarter(item)
                q_key = f"{year}Q{quarter}_extract_utc"
                q_data = zip_info.get(q_key, {})

                if not isinstance(q_data, dict) or not q_data:
                    return True

                for dataset_type in ("origination", "performance"):
                    ext_ts = q_data.get(dataset_type)
                    parquet_file = (
                        self.extract_dir / dataset_type / f"year={year}" / f"quarter={quarter}" / "data.parquet"
                    )
                    if is_extract_needed(download_utc_str, ext_ts) or not parquet_file.exists():
                        return True

        return False

    def convert_txt_to_parquet(self, txt_path: Path, output_parquet_path: Path) -> tuple[int, int, float]:
        """Scans CSV lazily, enforces Utf8 (string) schema with layout headers, and sinks to Parquet."""
        start_time = time.process_time()

        # Step 1: Detect column count lazily
        lf_preview = pl.scan_csv(
            txt_path,
            separator="|",
            has_header=False,
            null_values=["."],
            ignore_errors=True,
            low_memory=True,
        )
        n_cols = len(lf_preview.collect_schema())
        del lf_preview

        # Step 2: Resolve column names and build string schema
        dataset_type = self.get_dataset_type(txt_path.name)
        col_names = self.layout_manager.get_column_names(dataset_type, n_cols)

        # Enforce exact column count length for schema
        if len(col_names) < n_cols:
            col_names.extend([f"column_{i+1}" for i in range(len(col_names), n_cols)])
        elif len(col_names) > n_cols:
            col_names = col_names[:n_cols]

        schema_map = {col_names[i]: pl.Utf8 for i in range(n_cols)}

        # Step 3: Lazy scan with schema and sink to Parquet
        lf = pl.scan_csv(
            txt_path,
            separator="|",
            has_header=False,
            schema=schema_map,
            null_values=["."],
            ignore_errors=True,
            low_memory=True,
        )

        n_rows = lf.select(pl.len()).collect().item()

        output_parquet_path.parent.mkdir(parents=True, exist_ok=True)
        lf.sink_parquet(output_parquet_path, compression="snappy")

        del lf
        gc.collect()

        time_taken = time.process_time() - start_time
        return n_rows, n_cols, time_taken

    def process_quarter_dir(self, quarter_dir: Path, inner_zip_name: str, outer_zip_name: str) -> None:
        """Processes all TXT files extracted inside a quarterly directory."""
        quarter_start = time.process_time()
        zip_year, zip_quarter = self.parse_year_quarter(inner_zip_name)
        zip_info = self.manifest.get(outer_zip_name, {})
        download_utc_str = zip_info.get("last_download_utc")

        txt_files = list(quarter_dir.glob("*.txt"))

        for txt_file in txt_files:
            dataset_type = self.get_dataset_type(txt_file.name)
            year, quarter = self.parse_year_quarter(txt_file.name)
            if year == "unknown":
                year, quarter = zip_year, zip_quarter

            output_parquet = (
                self.extract_dir / dataset_type / f"year={year}" / f"quarter={quarter}" / "data.parquet"
            )

            q_key = f"{year}Q{quarter}_extract_utc"
            existing_q_extracts = zip_info.get(q_key, {})
            existing_ext_ts = (
                existing_q_extracts.get(dataset_type)
                if isinstance(existing_q_extracts, dict)
                else None
            )

            if not is_extract_needed(download_utc_str, existing_ext_ts) and output_parquet.exists():
                logger.info(f"Skipping {year} Q{quarter} {dataset_type} - extract UTC is up to date.")
                continue

            try:
                try:
                    display_path = output_parquet.relative_to(ROOT)
                except ValueError:
                    display_path = output_parquet
                logger.info(f"Converting {txt_file.name} -> {display_path}")
                n_rows, n_cols, duration = self.convert_txt_to_parquet(txt_file, output_parquet)

                self.processed_files.append({
                    "file": txt_file.name,
                    "dataset_type": dataset_type,
                    "year": year,
                    "quarter": quarter,
                    "rows": n_rows,
                    "cols": n_cols,
                    "time_seconds": round(duration, 4),
                    "parquet_path": str(output_parquet),
                })
                logger.info(f"Converted {txt_file.name} ({n_rows:,} rows, {n_cols} cols) in {duration:.2f}s")

                # Update timestamp in manifest
                now_utc_str = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                self.manifest.setdefault(outer_zip_name, {}).setdefault(q_key, {})[dataset_type] = now_utc_str
                save_manifest(self.manifest, self.manifest_path)

            except Exception as e:
                logger.error(f"FAILED: {txt_file.name} -> {type(e).__name__}: {e}")
                self.failed_files.append({
                    "file": txt_file.name,
                    "inner_zip": inner_zip_name,
                    "error": f"{type(e).__name__}: {e}",
                })
            finally:
                gc.collect()

        elapsed = time.process_time() - quarter_start
        logger.info(f"Finished inner ZIP {inner_zip_name} in {elapsed:.2f}s")

    def run(self) -> None:
        """Traverses outer yearly ZIP files and converts inner quarterly ZIP datasets."""
        self.purge_temp_dir()

        outer_zips = sorted(list(self.raw_dir.glob("*.zip")))
        if not outer_zips:
            logger.warning(f"No ZIP files found in raw directory: {self.raw_dir}")
            return

        logger.info(f"Starting conversion for {len(outer_zips)} outer ZIP files.")

        try:
            for outer_zip in outer_zips:
                if not self.is_yearly_archive_needed(outer_zip):
                    logger.info(f"Skipping yearly archive {outer_zip.name} (all extract UTCs up to date).")
                    continue

                logger.info(f"Processing outer ZIP: {outer_zip.name}")

                try:
                    with zipfile.ZipFile(outer_zip) as year_zip:
                        for inner_name in year_zip.namelist():
                            if inner_name.endswith(".zip"):
                                quarter_dir = Path(mkdtemp(dir=self.temp_root))
                                try:
                                    year_zip.extract(inner_name, quarter_dir)
                                    inner_zip_path = quarter_dir / inner_name

                                    with zipfile.ZipFile(inner_zip_path) as quarter_zip:
                                        quarter_zip.extractall(quarter_dir)

                                    self.process_quarter_dir(quarter_dir, inner_name, outer_zip.name)
                                finally:
                                    self.safe_rmtree(quarter_dir)
                            elif inner_name.endswith(".txt"):
                                quarter_dir = Path(mkdtemp(dir=self.temp_root))
                                try:
                                    year_zip.extract(inner_name, quarter_dir)
                                    self.process_quarter_dir(quarter_dir, outer_zip.name, outer_zip.name)
                                finally:
                                    self.safe_rmtree(quarter_dir)
                except Exception as e:
                    logger.error(f"Error processing outer ZIP {outer_zip.name}: {e}")
        finally:
            self.purge_temp_dir()

        logger.info(f"Processed: {len(self.processed_files)} files. Failed: {len(self.failed_files)} files")
        if self.failed_files:
            for item in self.failed_files:
                logger.error(f"Failed item: {item['file']} -> {item['error']}")


if __name__ == "__main__":
    extractor = FreddieMacExtractor()
    extractor.run()