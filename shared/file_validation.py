"""Content checks shared by uploads and the processing activity."""

import base64
import binascii
import io
import logging
import os
import re
from dataclasses import dataclass


class DocumentError(Exception):
    def __init__(self, code: str, message: str, permanent: bool = True):
        super().__init__(message)
        self.code = code
        self.message = message
        self.permanent = permanent


@dataclass(frozen=True)
class ValidatedDocument:
    data: bytes
    file_type: str


SIGNATURES = (
    (b"%PDF-", ".pdf"),
    (b"\xff\xd8\xff", ".jpg"),
    (b"\x89PNG\r\n\x1a\n", ".png"),
    (b"BM", ".bmp"),
    (b"II*\x00", ".tif"),
    (b"MM\x00*", ".tif"),
)
EXTENSIONS = {
    ".pdf": ".pdf", ".jpg": ".jpg", ".jpeg": ".jpg",
    ".png": ".png", ".bmp": ".bmp", ".tif": ".tif",
    ".tiff": ".tif", ".webp": ".webp", ".txt": ".txt",
}
DOCUMENT_ERROR_MESSAGES = {
    "empty_file": "File is empty.",
    "invalid_base64": "Text file is not valid Base64.",
    "unsupported_file_type": "File type is not supported or its signature is invalid.",
    "corrupt_image": "Image could not be read.",
    "corrupt_pdf": "PDF could not be read.",
    "password_protected_pdf": "PDF is password protected.",
    "blank_document": "Document contains no readable content.",
    "file_too_large": "File exceeds the allowed size.",
}


def _detect(data: bytes) -> str | None:
    for signature, file_type in SIGNATURES:
        if data.startswith(signature):
            return file_type
    if len(data) >= 12 and data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return ".webp"
    return None


def validate_document_bytes(data: bytes, file_name: str) -> ValidatedDocument:
    """Validate content without exposing it in errors or logs."""
    if not isinstance(data, bytes):
        raise TypeError("data must be bytes")
    max_bytes = int(os.getenv("MAX_FILE_BYTES", str(20 * 1024 * 1024)))
    extension = os.path.splitext(file_name)[1].lower()
    if extension != ".txt" and len(data) > max_bytes:
        raise DocumentError("file_too_large", "File exceeds the allowed size.")

    payload = data
    if extension == ".txt":
        if not data.strip():
            raise DocumentError("empty_file", "File is empty.")
        try:
            encoded = re.sub(rb"\s+", b"", data)
            if encoded.lower().startswith(b"data:") and b"," in encoded:
                encoded = encoded.split(b",", 1)[1]
            payload = base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error):
            raise DocumentError("invalid_base64", "Text file is not valid Base64.") from None

    if len(payload) > max_bytes:
        raise DocumentError("file_too_large", "File exceeds the allowed size.")
    if not payload:
        raise DocumentError("empty_file", "File is empty.")
    detected = _detect(payload)
    if detected is None:
        if extension == ".pdf":
            raise DocumentError("corrupt_pdf", "PDF could not be read.")
        raise DocumentError("unsupported_file_type", "File type is not supported or its signature is invalid.")

    expected = EXTENSIONS.get(extension)
    if expected not in (detected, ".txt"):
        logging.warning(
            "File extension does not match its content (code=extension_mismatch, file=%s)",
            file_name,
        )

    if detected == ".pdf":
        try:
            from pypdf import PdfReader

            reader = PdfReader(io.BytesIO(payload), strict=False)
            if reader.is_encrypted:
                try:
                    if reader.decrypt("") == 0:
                        raise DocumentError("password_protected_pdf", "PDF is password protected.")
                except DocumentError:
                    raise
                except Exception:
                    raise DocumentError("password_protected_pdf", "PDF is password protected.") from None
            if len(reader.pages) == 0:
                raise DocumentError("corrupt_pdf", "PDF contains no readable pages.")
            if all(not (page.extract_text() or "").strip() and not page.images for page in reader.pages):
                raise DocumentError("blank_document", "Document contains no readable content.")
        except DocumentError:
            raise
        except Exception:
            raise DocumentError("corrupt_pdf", "PDF could not be read.") from None
    else:
        try:
            from PIL import Image, ImageStat

            with Image.open(io.BytesIO(payload)) as image:
                image.verify()
            with Image.open(io.BytesIO(payload)) as image:
                image.load()
                if image.width <= 0 or image.height <= 0:
                    raise DocumentError("corrupt_image", "Image has invalid dimensions.")
                grayscale = image.convert("L")
                threshold = float(os.getenv("BLANK_IMAGE_STDDEV_THRESHOLD", "2.0"))
                if ImageStat.Stat(grayscale).stddev[0] < threshold:
                    raise DocumentError("blank_document", "Document appears to be blank.")
        except DocumentError:
            raise
        except Exception:
            raise DocumentError("corrupt_image", "Image could not be read.") from None

    return ValidatedDocument(payload, detected)
