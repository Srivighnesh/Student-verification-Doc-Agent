"""Pure document normalization, classification, and result-shaping helpers."""

import io
import logging
import os
import re

from shared.config import KEYWORDS, MODELS
from shared.file_validation import validate_document_bytes


def decode_base64_document(data: bytes, source_name: str) -> tuple[bytes, str]:
    """Decode a Base64 .txt blob and infer the document type from its bytes."""
    validated = validate_document_bytes(data, source_name)
    source_stem = os.path.splitext(os.path.basename(source_name))[0]
    source_stem = re.sub(r"_b64$", "", source_stem, flags=re.IGNORECASE)
    return validated.data, f"{source_stem or 'document'}{validated.file_type}"


def normalize(data: bytes, name: str) -> bytes:
    """Normalize images in memory; PDFs pass through unchanged."""
    extension = os.path.splitext(name)[1].lower()

    if extension in (
        ".png", ".jpg", ".jpeg", ".bmp",
        ".tif", ".tiff", ".webp",
    ):
        from PIL import Image, ImageOps

        image_file = Image.open(io.BytesIO(data))
        image_file = ImageOps.exif_transpose(image_file).convert("RGB")
        image_file.thumbnail((4000, 4000))

        buffer = io.BytesIO()
        image_file.save(buffer, "JPEG", quality=90)
        return buffer.getvalue()

    return data


def analyze(client, model: str, data: bytes):
    return client.begin_analyze_document(
        model,
        body=io.BytesIO(data),
    ).result()


def classify(client, data: bytes):
    """Classify with a configured DI classifier or OCR keyword scoring."""
    classifier_id = os.environ.get("DI_CLASSIFIER_ID")

    if classifier_id:
        result = client.begin_classify_document(
            classifier_id,
            body=io.BytesIO(data),
        ).result()

        label = (
            result.documents[0].doc_type
            if result.documents
            else None
        )
        if label in MODELS:
            return label, None

    layout = analyze(client, "prebuilt-layout", data)
    text = (layout.content or "").lower()

    scores = {
        doc_type: sum(1 for keyword in keywords if keyword in text)
        for doc_type, keywords in KEYWORDS.items()
    }

    best = max(scores, key=scores.get)
    logging.info("Classifier scores: %s", scores)

    return (
        best if scores[best] > 0 else "unknown",
        layout,
    )


def identify_document_type(name: str, text: str, category: str) -> str:
    """Return a readable document label, using OCR evidence before the name."""
    ocr_text = (text or "").casefold()
    file_name = os.path.basename(name).casefold()

    if any(marker in ocr_text for marker in (
        "aadhaar", "aadhar", "adhar", "uidai", "unique identification authority",
    )):
        return "Aadhaar Card"
    if (
        "pan card" in ocr_text
        or "permanent account number" in ocr_text
        or re.search(r"\b[A-Z]{5}\d{4}[A-Z]\b", (text or "").upper())
    ):
        return "PAN Card"
    if any(marker in ocr_text for marker in (
        "driving licence", "driving license", "motor driving licence",
        "motor driving license", "learner's licence", "learner's license",
    )):
        return "Driving Licence"

    if any(marker in file_name for marker in ("aadhaar", "aadhar", "adhar")):
        return "Aadhaar Card"
    if re.search(r"(^|[^a-z])pan([^a-z]|$)", file_name) or "pancard" in file_name:
        return "PAN Card"
    if "driving" in file_name and ("licen" in file_name or "dl" in file_name):
        return "Driving Licence"

    return {
        "id": "Identity Document",
        "invoice": "Invoice",
        "ssc_memo": "SSC Marks Memo",
    }.get(category, "Document")


def field_value(field):
    """Convert a Document Intelligence field while retaining confidence."""
    field_type = getattr(field, "type", None)

    if field_type == "array":
        value = [field_value(item) for item in (field.value_array or [])]
    elif field_type == "object":
        value = {
            key: field_value(item)
            for key, item in (field.value_object or {}).items()
        }
    else:
        value = getattr(field, "content", None)

    return {
        "value": value,
        "confidence": getattr(field, "confidence", None),
    }


def layout_fields(layout) -> dict:
    tables = []

    for table in layout.tables or []:
        rows = {}
        for cell in table.cells:
            rows.setdefault(cell.row_index, {})[
                cell.column_index
            ] = cell.content

        tables.append([
            [row[index] for index in sorted(row)]
            for _, row in sorted(rows.items())
        ])

    return {
        "text": (layout.content or "")[:6000],
        "tables": tables,
    }
