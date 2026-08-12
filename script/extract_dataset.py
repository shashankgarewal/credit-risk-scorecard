import json
import time
import logging
import warnings
import tempfile
import zipfile
from datetime import datetime, timezone
import polars as pl
from pathlib import Path

from src.utils.common import get_project_root, parse_layout_schema
import src.utils.logger  # Ensures logging configuration is loaded

warnings.filterwarnings("ignore", category=FutureWarning)

logger = logging.getLogger(__name__)

# --- Configurations & Paths ---
ROOT = get_project_root()
RAW_DIR = ROOT / "dataset" / "raw"
EXTRACT_DIR = ROOT / "dataset" / "extract"
LAYOUT_OLD = ROOT / "dataset" / "file_layout.xlsx"
LAYOUT_NEW = ROOT / "dataset" / "file_layout_july_2026.xlsx"
MANIFEST_PATH = ROOT / "dataset" / "manifest.json"


# --- Manifest Utilities ---
def load_manifest() -> dict:
    """Loads manifest JSON if present, else returns empty dict."""
    if MANIFEST_PATH.exists():
        try:
            with open(MANIFEST_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.warning(f"Could not read manifest ({e}). Proceeding with empty manifest.")
            print(f"Warning: Could not read manifest ({e}). Proceeding with empty manifest.")
    return {}


def save_manifest(manifest: dict) -> None:
    """Saves updated manifest dictionary to JSON."""
    MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(MANIFEST_PATH, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)


def parse_utc_timestamp(ts_str: str | None) -> datetime | None:
    """Parses ISO 8601 UTC timestamp string to datetime object."""
    if not ts_str:
        return None
    try:
        if ts_str.endswith("Z"):
            ts_str = ts_str[:-1] + "+00:00"
        return datetime.fromisoformat(ts_str)
    except Exception:
        return None


def is_extract_needed(last_download_utc_str: str | None, extract_utc_str: str | None) -> bool:
    """Returns True if extraction is needed (extract_utc is missing or strictly before download_utc)."""
    download_dt = parse_utc_timestamp(last_download_utc_str)
    extract_dt = parse_utc_timestamp(extract_utc_str)

    if not download_dt:
        return True
    if not extract_dt:
        return True

    return extract_dt < download_dt


# --- Layout & Schema Parsers ---
def load_layouts(layout_excel: Path) -> tuple[dict, dict]:
    """Reads layout sheet, returning schema maps for origination and performance data."""
    orig = pl.read_excel(
        layout_excel,
        sheet_name="Origination Data File",
        read_options={"header_row": 1},
        has_header=True,
    )
    perf = pl.read_excel(
        layout_excel,
        sheet_name="Monthly Performance Data File",
        read_options={"header_row": 1},
        has_header=True,
    )
    return parse_layout_schema(orig), parse_layout_schema(perf)


def resolve_schema(vintage: str, file_type: str, schemas: dict) -> dict:
    """Dynamically selects pre vs. post-July 2026 layout schemas based on vintage quarter.
    
    Freddie Mac July 2026 disclosure changes take effect starting with 2026 Q3 data.
    """
    year = int(vintage[:4])
    quarter = int(vintage[-1]) if "Q" in vintage else 1

    is_post_july_2026 = (year > 2026) or (year == 2026 and quarter >= 3)
    era_key = "post_jul_2026" if is_post_july_2026 else "pre_jul_2026"

    return schemas[era_key][file_type]


# --- TXT to PARQUET ---
def process_txt_with_polars(txt_path: Path, output_parquet_path: Path, schema: dict) -> bool:
    """Streams pipe-delimited text to Parquet via Polars LazyFrames. Returns True if converted."""
    output_parquet_path.parent.mkdir(parents=True, exist_ok=True)

    df_lazy = pl.scan_csv(
        txt_path,
        separator="|",
        has_header=False,
        schema=schema,
        truncate_ragged_lines=True,
        ignore_errors=True,
    )

    df_lazy.sink_parquet(output_parquet_path, compression="snappy")
    return True


def get_dataset_type(file_name: str) -> str | None:
    """Determines if the text file is origination or performance data."""
    fname = file_name.lower()
    if "orig" in fname:
        return "origination"
    if any(k in fname for k in ("time", "svcg", "perf")):
        return "performance"
    return None


# --- ZIP Extraction ---
def extract_zip(archive_path: Path, destination: Path) -> Path:
    """Unzips archive into specified target directory."""
    with zipfile.ZipFile(archive_path, "r") as zip_ref:
        zip_ref.extractall(destination)
    return destination


def process_quarterly_archive(
    q_zip_file: Path, work_dir: Path, schemas: dict, zip_name: str, manifest: dict
) -> float:
    """Extracts quarterly zip if needed and updates manifest extraction timestamps upon Parquet conversion.
    
    Returns total processing elapsed time in seconds.
    """
    start_time = time.perf_counter()
    vintage = q_zip_file.stem.split("_")[-1].upper()  # e.g., '2026Q3'
    quarter_key = f"{vintage}_extract_utc"

    zip_info = manifest.get(zip_name, {})
    download_utc_str = zip_info.get("last_download_utc")
    existing_q_extracts = zip_info.get(quarter_key, {})

    orig_extract_utc = existing_q_extracts.get("origination") if isinstance(existing_q_extracts, dict) else None
    perf_extract_utc = existing_q_extracts.get("performance") if isinstance(existing_q_extracts, dict) else None

    need_orig = is_extract_needed(download_utc_str, orig_extract_utc)
    need_perf = is_extract_needed(download_utc_str, perf_extract_utc)

    if not (need_orig or need_perf):
        logger.info(f"Skipping quarter {vintage} - extract UTCs are up to date.")
        print(f"  [SKIP QUARTER] {vintage} (All extract UTCs are after download UTC)")
        return 0.0

    extracted_path = extract_zip(q_zip_file, work_dir / vintage)

    for txt_file in extracted_path.glob("*.txt"):
        data_type = get_dataset_type(txt_file.name)
        if not data_type:
            continue

        if data_type == "origination" and not need_orig:
            logger.info(f"Skipping {vintage} origination - extract UTC up to date.")
            print(f"  [SKIP ORIG] {vintage} origination extract UTC is up to date.")
            continue
        if data_type == "performance" and not need_perf:
            logger.info(f"Skipping {vintage} performance - extract UTC up to date.")
            print(f"  [SKIP PERF] {vintage} performance extract UTC is up to date.")
            continue

        schema = resolve_schema(vintage, data_type, schemas)
        output_parquet = EXTRACT_DIR / data_type / f"vintage={vintage}" / "data.parquet"
        
        ds_start = time.perf_counter()
        logger.info(f"Starting conversion: {vintage} {data_type}")
        print(f"  [PROCESSING] {vintage} {data_type}...", end="", flush=True)

        process_txt_with_polars(txt_file, output_parquet, schema)

        ds_elapsed = time.perf_counter() - ds_start
        logger.info(f"Completed conversion: {vintage} {data_type} in {ds_elapsed:.2f}s")
        print(f"\r  [COMPLETE] {vintage} {data_type} ({ds_elapsed:.2f}s)")

        # Update timestamp in manifest using Z format without microseconds (e.g. 2026-08-10T17:22:55Z)
        now_utc_str = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        if zip_name not in manifest:
            manifest[zip_name] = {}
        if quarter_key not in manifest[zip_name] or not isinstance(manifest[zip_name][quarter_key], dict):
            manifest[zip_name][quarter_key] = {}

        manifest[zip_name][quarter_key][data_type] = now_utc_str
        save_manifest(manifest)

    q_elapsed = time.perf_counter() - start_time
    return q_elapsed


def process_yearly_archive(zip_file: Path, temp_dir: Path, schemas: dict, manifest: dict) -> None:
    """Unpacks archive if any quarterly extract is missing/needed, and processes inner quarterly zips."""
    zip_name = zip_file.name
    zip_info = manifest.get(zip_name, {})
    download_utc_str = zip_info.get("last_download_utc")

    year_str = "".join(filter(str.isdigit, zip_name))
    any_extract_needed = False

    if year_str and len(year_str) == 4:
        expected_quarters = [f"{year_str}Q{q}_extract_utc" for q in range(1, 5)]
        for q_key in expected_quarters:
            q_data = zip_info.get(q_key)
            if not isinstance(q_data, dict):
                any_extract_needed = True
                break
            for dataset_type in ("origination", "performance"):
                ext_ts = q_data.get(dataset_type)
                if is_extract_needed(download_utc_str, ext_ts):
                    any_extract_needed = True
                    break
            if any_extract_needed:
                break
    else:
        quarterly_keys = [k for k in zip_info.keys() if k.endswith("_extract_utc")]
        if not quarterly_keys:
            any_extract_needed = True
        else:
            for q_key in quarterly_keys:
                q_data = zip_info.get(q_key, {})
                if isinstance(q_data, dict):
                    for dataset_type in ("origination", "performance"):
                        ext_ts = q_data.get(dataset_type)
                        if is_extract_needed(download_utc_str, ext_ts):
                            any_extract_needed = True
                            break
                if any_extract_needed:
                    break

    if not any_extract_needed:
        logger.info(f"Skipping yearly archive {zip_name} - all extract UTCs up to date.")
        print(f"[SKIP YEARLY] {zip_name} (All extract UTCs are after download UTC)")
        return

    year_start = time.perf_counter()
    logger.info(f"Starting yearly extraction: {zip_name}")
    print(f"Extracting yearly archive: {zip_name}")

    archive_dir = extract_zip(zip_file, temp_dir / zip_file.stem)
    nested_zips = list(archive_dir.rglob("*.zip"))
    targets = nested_zips if nested_zips else [zip_file]

    yearly_compute_time = 0.0
    for q_zip_file in targets:
        q_time = process_quarterly_archive(q_zip_file, archive_dir, schemas, zip_name, manifest)
        yearly_compute_time += q_time

    year_total_elapsed = time.perf_counter() - year_start
    logger.info(
        f"Completed yearly archive {zip_name}: total elapsed {year_total_elapsed:.2f}s "
        f"(compute time: {yearly_compute_time:.2f}s)"
    )
    print(f"[YEAR COMPLETE] {zip_name} - Total time: {year_total_elapsed:.2f}s (Compute saved when skipped in future: {yearly_compute_time:.2f}s)")


# --- Execution Entry Point ---
def main() -> None:
    logger.info("Starting dataset extraction pipeline.")

    orig_pre, perf_pre = load_layouts(LAYOUT_OLD)
    orig_post, perf_post = load_layouts(LAYOUT_NEW)

    schemas = {
        "pre_jul_2026": {"origination": orig_pre, "performance": perf_pre},
        "post_jul_2026": {"origination": orig_post, "performance": perf_post},
    }

    manifest = load_manifest()
    zip_files = sorted(list(RAW_DIR.glob("*.zip")))
    if not zip_files:
        logger.warning(f"No ZIP files found in {RAW_DIR}")
        print(f"No ZIP files found in {RAW_DIR}")
        return

    total_compute_time = 0.0

    for zip_file in zip_files:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_unzip_path = Path(temp_dir)
            # Accumulate compute time returned from yearly processing
            year_start = time.perf_counter()
            process_yearly_archive(zip_file, temp_unzip_path, schemas, manifest)


if __name__ == "__main__":
    main()