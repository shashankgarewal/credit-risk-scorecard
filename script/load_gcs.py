from pathlib import Path
from google.cloud import storage

def upload_to_gcs(file_path: Path, bucket_name: str, blob_name: str):
    if storage is None:
        print("Warning: google-cloud-storage is not installed. Skipping GCS upload.")
        return False
    print(f"Uploading {file_path.name} to GCS bucket '{bucket_name}' as '{blob_name}'...")
    try:
        storage_client = storage.Client()
        bucket = storage_client.bucket(bucket_name)
        blob = bucket.blob(blob_name)
        blob.upload_from_filename(str(file_path))
        print("GCS upload successful.")
        return True
    except Exception as e:
        print(f"Error uploading to GCS: {e}")
        return False