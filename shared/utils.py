"""Small workflow helpers without Azure Functions triggers or bindings."""

import json
import os
import re

from shared.config import LOCAL_DIR
from shared.document_processing import identify_document_type
from shared.schemas import ProcessingMessage


def safe(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", str(value))


def durable_instance_id(employee_id: str, run_id: str) -> str:
    return f"{employee_id}-{run_id}"


def service_bus_body(message: ProcessingMessage) -> dict:
    """Keep required defaults while omitting empty optional skip metadata."""
    body = message.model_dump(exclude_none=True)
    if not body.get("skipped_files"):
        body.pop("skipped_files", None)
    return body


def save_local(rel_path: str, content) -> None:
    """Write text or JSON under LOCAL_DIR."""
    path = os.path.join(LOCAL_DIR, *rel_path.split("/"))
    os.makedirs(os.path.dirname(path), exist_ok=True)

    with open(path, "w", encoding="utf-8") as file:
        if isinstance(content, str):
            file.write(content)
        else:
            file.write(json.dumps(content, indent=2, ensure_ascii=False))


def error_code_summary(errors: list[dict]) -> dict:
    summary = {}
    for error in errors:
        code = error.get("error_code", "unknown")
        summary[code] = summary.get(code, 0) + 1
    return summary


def unprocessable_files(errors: list[dict]) -> list[dict]:
    return [
        {
            "file": _file_name(error.get("file")),
            "kind": error.get("kind"),
            "error_code": error.get("error_code"),
            "message": error.get("error") or error.get("message"),
        }
        for error in errors
    ]


def documents_for_report(documents: dict) -> dict:
    """Expose display names and fields to Foundry, never Blob paths."""
    result = {}
    for key, document in documents.items():
        base_type = re.sub(r"_\d+$", "", str(key))
        filename = document.get("file_name") or document.get("source_file") or key
        document_type = document.get("document_type") or identify_document_type(
            filename,
            "",
            base_type,
        )
        result[key] = {
            "document_type": document_type or "Document",
            "file_name": _file_name(filename),
            "fields": document.get("fields", {}),
        }
    return result


def _file_name(value) -> str | None:
    if value is None:
        return None
    return str(value).replace("\\", "/").rsplit("/", 1)[-1]


# Keep the previous private names available to existing callers.
_error_code_summary = error_code_summary
_unprocessable_files = unprocessable_files
