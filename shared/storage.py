"""Blob Storage helpers used by HTTP and activity functions."""

import os

from azure.core.exceptions import ResourceExistsError
from azure.storage.blob import BlobServiceClient


def blobs() -> BlobServiceClient:
    return BlobServiceClient.from_connection_string(
        os.environ["StorageConnection"]
    )


def ensure_container(name: str) -> None:
    """Create a storage container if it does not exist."""
    try:
        blobs().create_container(name)
    except ResourceExistsError:
        pass
