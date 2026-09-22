## load extract data to cloud storage bucket 
import os
import re
import sys
import json
import time
from pathlib import Path
from dotenv import load_dotenv
from typing import Dict, List
from datetime import datetime, timezone

from src.utils.config import EXTRACT_DIR, ROOT
from src.utils.logger import logger

from script.extract_dataset import load_manifest, save_manifest

import boto3
import botocore
from botocore.exceptions import ClientError

load_dotenv(ROOT / ".env")

AWS_BUCKET_NAME = os.getenv("AWS_BUCKET_NAME")

def s3_object_exists(s3_client: boto3.client, s3_key: str) -> bool:
    try:
        s3_client.head_object(Bucket=AWS_BUCKET_NAME, Key=s3_key)
        return True
    except ClientError as e:
        error_code = e.response.get("Error", {}).get("Code", "")
        http_status = e.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
        if error_code in ['NoSuchObject', 'NoSuchKey', '404'] or http_status == 404:
            return False
        logger.error(f"Object check error: [{e}]")
        raise e

def need_push(
    s3_client: boto3.client,
    parquet_path: Path,
    last_extract_utc_str: str | None,
    push_utc_str: str | None
    ) -> bool:
    """
    Returns True when file needs to be pushed to cloud storage bucket.
    
    """
    s3_key = str(parquet_path.relative_to(EXTRACT_DIR.parent).as_posix())
    if not s3_object_exists(s3_client, s3_key):
        return True
    if not push_utc_str:
        return True
    if not last_extract_utc_str:
        return True
    return last_extract_utc_str > push_utc_str

def get_utc(
    manifest: dict, 
    year: str, 
    quarter: str, 
    dataset_type: str
    ) -> tuple[str | None, str | None]:
    """
    Returns last_extract_utc and push_utc from manifest.
    
    """
    target_suffix = f"_{year}.zip"
    yearzip_entry = next((data for key, data in manifest.items() if key.endswith(target_suffix)), {})
    quarter_extract_key = f"{year}Q{quarter}_extract_utc"
    quarter_push_key = f"{year}Q{quarter}_push_utc"
    
    return (
        yearzip_entry.get(quarter_extract_key, {}).get(dataset_type),
        yearzip_entry.get(quarter_push_key, {}).get(dataset_type)
    )

def update_manifest(
    manifest: dict, 
    dataset_type: str,
    year: str, 
    quarter: str,
    ):
    """Update the manifest with the given UTC.
    """
    quarter_push_key = f"{year}Q{quarter}_push_utc"
    sample_zip = next(iter(manifest)) 
    prefix = sample_zip.rsplit("_", 1)[0] 

    zip_file_key = f"{prefix}_{year}.zip"
    manifest[zip_file_key].setdefault(quarter_push_key, {})[dataset_type] = (
        datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    )
    return manifest

def get_aws_session():
    """Returns an AWS session using environment variables.
    """
    return boto3.Session(
        aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
        aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY"),
        region_name=os.getenv("AWS_REGION"),
    )


def parquet_metadata(parquet_path: Path):
    """returns dataset_type, year, quarter of parquet file"""
    parts = parquet_path.relative_to(EXTRACT_DIR).parts
    dataset_type = parts[0]
    year = parts[1].replace("year=", "")[-4:]
    quarter = parts[2].replace("quarter=", "")[-1]
    return dataset_type, year, quarter


def load_bucket():
    """load data from extract dir to cloud storage bucket"""
    push_parquets = []

    s3_client = get_aws_session().client("s3")
    manifest = load_manifest()
    for parquet_path in EXTRACT_DIR.glob("*/*/*/data.parquet"):
        dataset_type, year, quarter = parquet_metadata(parquet_path)
        last_extract_utc, push_utc = get_utc(manifest, year, quarter, dataset_type)
        push_status = need_push(s3_client, parquet_path, last_extract_utc, push_utc)
        if push_status:
            push_parquets.append((parquet_path, dataset_type, year, quarter))

    logger.info(f"Found {len(push_parquets)} files to push to cloud storage bucket!")
    file_push_counter = 0

    for parquet_path, dataset_type, year, quarter in push_parquets:

        s3_key = str(parquet_path.relative_to(EXTRACT_DIR.parent).as_posix())
        logger.info(f"({file_push_counter + 1}/{len(push_parquets)}) Start push [{s3_key}] to cloud storage bucket!")
        s3_client.upload_file(str(parquet_path), AWS_BUCKET_NAME, s3_key)
        logger.info(f"({file_push_counter + 1}/{len(push_parquets)}) Successfully pushed [{s3_key}] to cloud storage bucket!")

        #update manifest
        manifest = update_manifest(manifest, dataset_type, year, quarter)
        save_manifest(manifest)
        file_push_counter += 1
    
    return

if __name__ == "__main__":
    load_bucket()
