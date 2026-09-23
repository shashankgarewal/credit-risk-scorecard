import sys
import json
import tempfile
import zipfile
from pathlib import Path
import polars as pl
import pytest

# Ensure project root is in sys.path
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from script.extract_dataset import clean_column_name, is_extract_needed, LayoutManager, FreddieMacExtractor


def test_clean_column_name():
    assert clean_column_name("Credit Score") == "credit_score"
    assert clean_column_name("First Payment Date") == "first_payment_date"
    assert clean_column_name("DATA TYPE & FORMAT") == "data_type_format"
    assert clean_column_name("MSA / Division") == "msa_division"
    assert clean_column_name("") == ""
    assert clean_column_name("  __Hello--World__  ") == "hello_world"


class TestIsExtractNeeded:
    def test_no_extract_utc_always_needed(self):
        assert is_extract_needed("2026-08-10T10:00:00Z", None) is True
        assert is_extract_needed(None, None) is True

    def test_no_download_utc_never_needed(self):
        # Already extracted, no download recorded — nothing to update
        assert is_extract_needed(None, "2026-08-10T10:00:00Z") is False

    def test_download_newer_than_extract(self):
        assert is_extract_needed("2026-08-15T10:00:00Z", "2026-08-10T10:00:00Z") is True

    def test_extract_newer_than_download(self):
        assert is_extract_needed("2026-08-10T10:00:00Z", "2026-08-15T10:00:00Z") is False

    def test_same_timestamp_not_needed(self):
        ts = "2026-08-10T10:00:00Z"
        assert is_extract_needed(ts, ts) is False


class TestStaticHelpers:
    def test_get_dataset_type_origination(self):
        assert FreddieMacExtractor.get_dataset_type("historical_data_orig_2020Q1.txt") == "origination"

    def test_get_dataset_type_performance(self):
        assert FreddieMacExtractor.get_dataset_type("historical_data_time_2020Q1.txt") == "performance"
        assert FreddieMacExtractor.get_dataset_type("historical_data_svcg_2020Q1.txt") == "performance"

    def test_get_dataset_type_unknown(self):
        assert FreddieMacExtractor.get_dataset_type("readme.txt") == "unknown"

    def test_extract_vintage_quarter(self):
        assert FreddieMacExtractor.extract_vintage("historical_data_2020Q1.zip") == "2020Q1"
        assert FreddieMacExtractor.extract_vintage("historical_data_1999Q4.zip") == "1999Q4"

    def test_extract_vintage_year_fallback(self):
        assert FreddieMacExtractor.extract_vintage("historical_data_2020.zip") == "2020"

    def test_extract_vintage_unknown(self):
        assert FreddieMacExtractor.extract_vintage("readme.txt") == "unknown_vintage"


class TestLayoutManager:
    def test_fallback_column_names(self):
        lm = LayoutManager()
        cols = lm.get_column_names("unknown", 5)
        assert cols == ["column_1", "column_2", "column_3", "column_4", "column_5"]

    def test_real_layout_if_available(self):
        lm = LayoutManager()
        if lm.layout_map:
            # At least one layout loaded — column names should be clean identifiers
            for (dtype, n), cols in lm.layout_map.items():
                assert len(cols) == n
                for col in cols:
                    assert col == col.lower()
                    assert " " not in col


def _make_converter(tmp_path: Path, manifest: dict | None = None) -> FreddieMacExtractor:
    """Creates a FreddieMacExtractor with a small nested zip structure."""
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir(exist_ok=True)
    temp_dir = tmp_path / "temp"
    extract_dir = tmp_path / "extract"
    manifest_path = tmp_path / "manifest.json"

    if manifest is not None:
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    # inner zip: historical_data_2020Q1.zip -> historical_data_2020Q1.txt
    inner_zip_path = tmp_path / "historical_data_2020Q1.zip"
    with zipfile.ZipFile(inner_zip_path, "w") as z:
        z.writestr("historical_data_2020Q1.txt", "10001|202001|A\n10002|202002|B\n")

    outer_zip_path = raw_dir / "historical_data_2020.zip"
    with zipfile.ZipFile(outer_zip_path, "w") as z:
        z.write(inner_zip_path, arcname="historical_data_2020Q1.zip")

    return FreddieMacExtractor(
        raw_dir=raw_dir,
        temp_root=temp_dir,
        extract_dir=extract_dir,
        manifest_path=manifest_path,
    )


class TestFreddieMacExtractor:
    def test_first_run_converts_file(self, tmp_path):
        conv = _make_converter(tmp_path)
        conv.run()

        assert len(conv.processed_files) == 1
        assert len(conv.failed_files) == 0

        parquet_path = Path(conv.processed_files[0]["parquet_path"])
        assert parquet_path.exists()

        df = pl.read_parquet(parquet_path)
        assert len(df) == 2
        assert len(df.columns) == 3
        assert all(dt == pl.Utf8 for dt in df.dtypes)

    def test_manifest_written_after_conversion(self, tmp_path):
        conv = _make_converter(tmp_path)
        conv.run()

        manifest = json.loads(conv.manifest_path.read_text(encoding="utf-8"))
        zip_entry = manifest.get("historical_data_2020.zip", {})
        assert "2020Q1_extract_utc" in zip_entry
        assert "unknown" in zip_entry["2020Q1_extract_utc"]

    def test_second_run_skips_uptodate(self, tmp_path):
        conv = _make_converter(tmp_path)
        conv.run()

        conv2 = _make_converter(tmp_path, manifest=json.loads(conv.manifest_path.read_text()))
        conv2.run()
        assert len(conv2.processed_files) == 0

    def test_rerun_after_newer_download_reconverts(self, tmp_path):
        # Set extract_utc in past, download_utc in future -> should re-extract
        manifest = {
            "historical_data_2020.zip": {
                "last_download_utc": "2026-09-01T00:00:00Z",  # newer than extract
                "2020Q1_extract_utc": {"unknown": "2026-08-01T00:00:00Z"},  # older
            }
        }
        conv = _make_converter(tmp_path, manifest=manifest)
        conv.run()
        assert len(conv.processed_files) == 1

    def test_is_yearly_archive_needed_no_manifest_entry(self, tmp_path):
        conv = _make_converter(tmp_path)
        outer_zip = conv.raw_dir / "historical_data_2020.zip"
        assert conv.is_yearly_archive_needed(outer_zip) is True

    def test_is_yearly_archive_needed_with_fresh_extract(self, tmp_path):
        manifest = {
            "historical_data_2020.zip": {
                "last_download_utc": "2026-08-01T00:00:00Z",
                "2020Q1_extract_utc": {"unknown": "2026-08-15T00:00:00Z"},  # extract newer
            }
        }
        conv = _make_converter(tmp_path, manifest=manifest)
        outer_zip = conv.raw_dir / "historical_data_2020.zip"
        assert conv.is_yearly_archive_needed(outer_zip) is False
