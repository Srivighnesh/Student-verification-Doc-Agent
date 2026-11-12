"""Upload a folder of documents to the document workflow.

Usage:
  python send_test.py emp123 baseline ./samples/previous
  python send_test.py emp123 verification ./samples/new_docs

An immediate subfolder named id, invoice, ssc_memo, or unknown sets that
document type. Otherwise, the type defaults to auto classification.

Optionally set UPLOAD_URL, for example:
  $env:UPLOAD_URL = "http://localhost:7071/api/upload"

For a deployed Function App, set FUNCTION_KEY to its function key.
"""

import base64
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

UPLOAD_URL = os.environ.get(
    "UPLOAD_URL",
    "http://localhost:7071/api/upload",
)

SUPPORTED_EXTENSIONS = {
    ".pdf",
    ".jpg",
    ".jpeg",
    ".png",
    ".bmp",
    ".tif",
    ".tiff",
    ".webp",
    ".txt",
}

DOCUMENT_TYPES = {
    "id",
    "invoice",
    "ssc_memo",
    "unknown",
}
FUNCTION_KEY = os.environ.get("FUNCTION_KEY")


def infer_doc_type(file_path: Path, root_folder: Path) -> str:
    """Use a recognized parent folder as the type; otherwise use auto."""
    relative_path = file_path.relative_to(root_folder)

    for folder_name in reversed(relative_path.parts[:-1]):
        if folder_name.casefold() in DOCUMENT_TYPES:
            value = folder_name.casefold()
            return "auto" if value == "unknown" else value

    return "auto"


def main() -> int:
    if len(sys.argv) != 4:
        print(__doc__)
        return 2

    employee_id, kind, folder_arg = sys.argv[1:]

    if kind not in {"baseline", "verification"}:
        print("kind must be 'baseline' or 'verification'")
        return 2

    root_folder = Path(folder_arg).expanduser().resolve()
    if not root_folder.is_dir():
        print(f"Folder not found: {root_folder}")
        return 2

    document_paths = sorted(
        path
        for path in root_folder.rglob("*")
        if path.is_file()
        and path.suffix.casefold() in SUPPORTED_EXTENSIONS
    )

    if not document_paths:
        print(f"No supported documents found in: {root_folder}")
        return 2

    files = []
    for path in document_paths:
        relative_name = path.relative_to(root_folder).as_posix()
        upload_name = relative_name.replace("/", "__")
        doc_type = infer_doc_type(path, root_folder)

        raw_data = path.read_bytes()
        encoded = base64.b64encode(raw_data).decode("ascii")

        files.append({
            # Keep nested folder paths out of file_name; preserve uniqueness
            # by flattening them into the filename.
            "file_name": upload_name,
            "doc_type": doc_type,
            "file_base64": encoded,
        })

    upload_body = {
        "employee_id": employee_id,
        "kind": kind,
        "files": files,
    }
    payload = json.dumps(upload_body).encode("utf-8")

    headers = {"Content-Type": "application/json"}
    if FUNCTION_KEY:
        headers["x-functions-key"] = FUNCTION_KEY

    request = urllib.request.Request(
        UPLOAD_URL,
        data=payload,
        headers=headers,
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=300) as response:
            print(response.read().decode("utf-8"))
        print(f"Summary: sent {len(files)} file(s)")
        return 0
    except urllib.error.HTTPError as error:
        body = error.read().decode("utf-8")
        try:
            result = json.loads(body)
            if isinstance(result.get("details"), list):
                for detail in result["details"]:
                    print(f"FAILED: {detail.get('file', 'unknown')} – {detail.get('code', 'unknown')}: {detail.get('message', 'Upload rejected')}")
            else:
                print(f"HTTP {error.code}: {body}")
        except (ValueError, AttributeError):
            print(f"HTTP {error.code}: {body}")
        print("Summary: upload rejected")
        return 1
    except urllib.error.URLError as error:
        print(f"Could not reach upload endpoint: {error}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
