"""Shared configuration constants for the document workflow."""

import os


RESULTS = "results"
STAGING_CONTAINER = "processing-staging"
QUEUE = "document-processing"

# Keys describe the logical document set; source uploads are only staged here
# while the corresponding run is being processed.
CONTAINERS = {
    "baseline": STAGING_CONTAINER,
    "verification": STAGING_CONTAINER,
}

MODELS = {
    "id": "prebuilt-idDocument",
    "invoice": "prebuilt-invoice",
    "ssc_memo": "prebuilt-layout",
}

KEYWORDS = {
    "invoice": [
        "invoice", "bill to", "amount due", "invoice no", "invoice number",
        "subtotal", "total due", "gst", "tax invoice", "due date", "qty",
        "unit price",
    ],
    "ssc_memo": [
        "memorandum of marks", "secondary school", "ssc",
        "board of secondary", "secondary education", "marks", "grade",
        "hall ticket", "roll no", "pass",
    ],
    "id": [
        "aadhaar", "unique identification", "government of india",
        "date of birth", "dob", "identity card", "id card",
        "driving licence", "driver license", "passport", "voter",
        "permanent account number", "income tax department",
        "employee id", "valid upto",
    ],
}

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOCAL_DIR = os.environ.get("LOCAL_OUTPUT_DIR") or os.path.join(
    PROJECT_ROOT,
    "local_results",
)
