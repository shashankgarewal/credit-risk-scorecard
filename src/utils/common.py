import re
import polars as pl
from pathlib import Path
from src.utils import logger

def _parse_layout_dtype(raw_type_str: str) -> pl.DataType:
    """Parses Freddie Mac layout data type string into a Polars dtype."""
    raw = str(raw_type_str).strip().lower()

    # Text / Date / Categorical
    if "alpha" in raw or "date" in raw:
        return pl.Utf8

    # Numeric (Decimals vs Integers)
    if "numeric" in raw:
        # Check for decimal specifications like "Numeric - 12,2", "Numeric - 6,3", or "Numeric (12,2)"
        if "," in raw or "." in raw:
            return pl.Float64
        return pl.Int64

    return pl.Utf8

def parse_layout_schema(layout_df: pl.DataFrame) -> dict:
    """Parses Freddie Mac layout Excel file into a Polars schema dictionary,

    handling both pre- and post-July 2026 column header variations.
    """
    schema = {}

    for row in layout_df.iter_rows(named=True):
        # Case-insensitive column retrieval
        row_normalized = {
            str(k).strip().upper(): v for k, v in row.items() if k is not None
        }

        col_raw_name = row_normalized.get("ATTRIBUTE NAME", "")
        raw_type = row_normalized.get("DATA TYPE & FORMAT", "")

        if not col_raw_name:
            continue

        # Clean column name: strip, lowercase, replace spaces/slashes/special characters with underscores
        col_name = re.sub(
            r"[^a-z0-9_]", "", col_raw_name.strip().lower().replace(" ", "_")
        )
        col_name = re.sub(r"_+", "_", col_name)  # Clean up duplicate underscores

        schema[col_name] = _parse_layout_dtype(raw_type)

    return schema