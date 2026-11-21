import os
import logging
import asyncio
from urllib.parse import urlparse
from botocore.exceptions import ClientError
import boto3

logger = logging.getLogger("context_manager")

BASE_URL = os.getenv("CONTEXT_S3_BASE_URL", "https://vriddhi-context.s3.ap-south-1.amazonaws.com")

def _parse_s3_details(base_url: str):
    parsed = urlparse(base_url)
    bucket = parsed.netloc.split(".")[0]
    region = parsed.netloc.split(".")[2]
    return bucket, region

async def download_context(context_id: str, session_id: str) -> str:
    if not context_id:
        logger.warning("No context_id provided. Using default kb.json.")
        return "src/kb.json"

    bucket, region = _parse_s3_details(BASE_URL)

    cache_dir = os.path.join(os.getcwd(), "cache")
    os.makedirs(cache_dir, exist_ok=True)

    file_path = os.path.join(cache_dir, f"{context_id}_{session_id}.json")
    s3_key = f"{context_id}.json"

    logger.info(f"Downloading context from S3 | bucket={bucket} key={s3_key}")

    s3_client = boto3.client("s3", region_name=region)
    loop = asyncio.get_event_loop()

    try:
        await loop.run_in_executor(
            None,
            s3_client.download_file,
            bucket,
            s3_key,
            file_path
        )
        logger.info(f"Context downloaded to {file_path}")
        return file_path

    except ClientError as e:
        logger.error(f"S3 download failed | error={e}")
        return "src/kb.json"

    except Exception:
        logger.exception("Error downloading context")
        return "src/kb.json"


def cleanup_context(file_path: str):
    """
    Deletes the cached context file.
    """
    # Safety check: don't delete source code or default kb
    if "src/kb.json" in file_path:
        return

    if os.path.exists(file_path):
        try:
            os.remove(file_path)
            logger.info(f"Deleted cached context: {file_path}")
        except Exception as e:
            logger.error(f"Failed to delete {file_path}: {e}")
