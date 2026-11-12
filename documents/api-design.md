# API Design and Message Contracts

This guide describes the HTTP API, Blob Storage layout, Service Bus message, Durable Functions run, and Foundry request used by the document verification pipeline.

Related guides: [end-to-end flow](flow-explanation.md) · [roles, security, and errors](roles-security-error-handling.md)

## 1. Service overview

The system has three kinds of calls:

1. A client sends Base64-encoded document bytes to `POST /api/upload`.
2. The API decodes the HTTP Base64 once, stores original binary bytes temporarily in Blob Storage, and queues blob paths with run metadata.
3. A Durable Functions orchestration reads and extracts the documents, saves combined results, and for verification runs asks the Foundry agent to create a report.

The HTTP request does **not** wait for OCR or the Foundry report. A `202 Accepted` response means that request validation passed, the temporary source blobs were written, and the work message was queued. Document-content errors are reported by the background run. Check it later with `GET /api/status/{employee_id}/{run_id}`.

## 2. Upload API

### `POST /api/upload`

The function authorization level is `FUNCTION`. A deployed request normally needs a Function key, for example in the `x-functions-key` header. Local Functions Core Tools may permit local calls without a key.

### Request body

```json
{
  "employee_id": "emp123",
  "kind": "verification",
  "files": [
    {
      "file_name": "identity-card.jpg",
      "file_base64": "<Base64 of the original binary file>",
      "doc_type": "id"
    }
  ],
  "skipped_files": [
    {
      "file_name": "blank-scan.pdf.base64.txt",
      "error_code": "blank_document"
    }
  ]
}
```

`skipped_files` is optional for compatibility with clients that report local skips. The current `send_test.py` sends supported files without local content validation so the worker can record per-file results.

| Field | Required | Meaning and validation |
| --- | --- | --- |
| `employee_id` | Yes | 1–64 letters, digits, `.`, `_`, or `-`; begins with a letter or digit. |
| `kind` | No | `baseline` or `verification`; defaults to `verification`. |
| `files` | Yes | At least one file. Each file has a supported extension, a single filename (not a path), Base64 content, and an optional classification hint. |
| `file_name` | Yes per file | Original filename; max 255 characters. Supported extensions: PDF, JPG/JPEG, PNG, BMP, TIFF, WEBP, and legacy Base64 `.txt`. |
| `file_base64` | Yes per file | Base64 text for original file bytes. The API checks transport Base64 syntax and decodes it once. An empty string is allowed so zero-byte files can be reported as `empty_file` by the worker. |
| `doc_type` | No | `auto`, `id`, `invoice`, or `ssc_memo`; defaults to `auto`. |
| `skipped_files` | No | Client-side validation results. Each item supplies a filename and one allowed `error_code`; the server adds `kind` and a safe message. |

For a new file named `scan.pdf`, Base64-encode the PDF bytes. Do not Base64-encode the textual characters of a Base64 string a second time unless the file itself is a legacy `.txt` source.

### Upload and staging

Before any blob write, the endpoint:

1. Parses JSON and validates request fields and transport Base64 syntax with Pydantic.
2. Decodes each file's Base64 content into the original binary bytes.
3. Writes the bytes to `processing-staging` under a unique employee/run path.
4. Emits a Service Bus message containing the file paths and run metadata.

Content validation happens in the worker after queueing. A malformed request Base64 string is rejected with HTTP 400 because it cannot be decoded into the binary staging file. A Blob or queue failure can leave staged blobs; the lifecycle policy is the cleanup fallback.

The endpoint uses one temporary source container:

| Container | Blob path | Purpose |
| --- | --- | --- |
| `processing-staging` | `{employee_id}/{doc_type}/{run_id}/{safe_file_name}` | Temporary source bytes for this queued run. |

`safe_file_name` replaces characters outside letters, digits, `.`, `_`, and `-` with `_`. The service stores original binary bytes temporarily. It does not store normalized OCR copies.

### Responses

Successful queueing returns HTTP `202`:

```json
{
  "status": "queued",
  "employee_id": "emp123",
  "run_id": "127bd46aef8b43389fd29847c941eca1",
  "kind": "verification",
  "files": ["emp123/id/127bd46aef8b43389fd29847c941eca1/identity-card.jpg"],
  "skipped_files": [
    {
      "file": "blank-scan.pdf.base64.txt",
      "kind": "verification",
      "error_code": "blank_document",
      "message": "Document contains no readable content."
    }
  ]
}
```

The `skipped_files` property is omitted when there are no client-side skips.

Malformed JSON, invalid Pydantic fields, or malformed request Base64 return HTTP `400`. Content validation failures are recorded asynchronously as per-file run errors instead of rejecting the upload request.

```json
{"state":"Completed","output":{"status":"all_files_failed","errors":[{"file":"emp123/auto/<run-id>/bad-image.jpg","error_code":"corrupt_image","error":"Image could not be read."}]}}
```

Storage or queue-output failures return HTTP `500` with a generic message. The response does not contain Base64 or document bytes.

## 3. What Service Bus carries

The queue is named `document-processing`. The queue message contains run metadata and temporary Blob paths; it never contains file bytes or Base64.

```json
{
  "employee_id": "emp123",
  "run_id": "127bd46aef8b43389fd29847c941eca1",
  "kind": "verification",
  "files": ["emp123/id/127bd46aef8b43389fd29847c941eca1/identity-card.jpg"],
  "skipped_files": [
    {
      "file": "blank-scan.pdf.base64.txt",
      "kind": "verification",
      "error_code": "blank_document",
      "message": "Document contains no readable content."
    }
  ]
}
```

The message schema is revalidated by `ProcessingMessage` in the queue trigger. Old messages without `skipped_files` remain valid. If `run_id` is missing, the trigger creates one. The Durable instance ID is `{employee_id}-{run_id}`. Repeated delivery for an existing instance is ignored to avoid duplicate work.

### Queue field definitions

| Field | Meaning |
| --- | --- |
| `employee_id` | Employee folder namespace. |
| `run_id` | 32-character UUID hex value assigned by the upload API. |
| `kind` | `baseline` or `verification`. |
| `files` | Temporary staging Blob paths written by upload. |
| `skipped_files` | Optional local validation details, each with `file`, `kind`, `error_code`, and server-generated `message`. |

## 4. Baseline and verification behavior

### Baseline upload

A baseline upload stages files for that run. Its Durable run processes those paths, updates the baseline section in the combined result, and deletes the staged files. It does not call Foundry.

### Verification upload

A verification upload stages only the new files for that run. Its Durable run processes those paths, updates the verification section, and compares the new fields with the baseline fields already saved in the combined result. It then deletes the staged files.

The orchestrator also carries `skipped_files` into the verification input. Those files were rejected locally and have no blob path to process; they are still included in the Foundry comparison as unprocessable files.

## 5. What the Foundry agent receives

The app first combines extracted fields. It then sends a text prompt and JSON comparison data to the configured agent using an `agent_reference`:

```json
{
  "employee_id": "emp123",
  "previous_documents": {
    "id": {
      "document_type": "Aadhaar Card",
      "file_name": "employee-aadhaar.jpg",
      "fields": "..."
    }
  },
  "new_documents": {
    "id": {
      "document_type": "Aadhaar Card",
      "file_name": "new-aadhaar.jpg",
      "fields": "..."
    }
  },
  "unprocessable_files": [
    {
      "file": "blank-scan.pdf.base64.txt",
      "kind": "verification",
      "error_code": "blank_document",
      "message": "Document contains no readable content."
    }
  ]
}
```

The real `fields` values are extracted document data; the placeholder above is illustrative. The original PDF/image, Base64 payload, and full Blob paths are not sent to the agent. `document_type` is inferred from OCR text when possible and uses the filename as a fallback; otherwise it remains a broad category such as `Identity Document`. The prompt asks for `DOCUMENTS REVIEWED` to show the document type and basename only. It also instructs the agent to treat document content as data, report unprocessable files under `UNPROCESSABLE FILES`, and not call them missing or mismatched.

Each verification creates a separate Foundry conversation, submits one response, and then attempts to delete that conversation. This prevents concurrent verification runs from using the same active conversation. The optional `FOUNDRY_AGENT_VERSION` is included if configured.

The formatted report is saved to `{LOCAL_OUTPUT_DIR}/{employee_id}/verification_report.txt`. It is local process storage, not a Blob. Combined JSON is saved to `results/{employee_id}/combined.json` in Blob Storage and to the local output directory.

## 6. Check run status

### `GET /api/status/{employee_id}/{run_id}`

The route looks up Durable instance `{employee_id}-{run_id}` and returns its runtime state and output:

```json
{
  "state": "Completed",
  "output": {
    "status": "verification_report_saved_with_errors",
    "error_count": 1,
    "error_codes": {"blank_document": 1},
    "report_path": "..."
  }
}
```

The Durable runtime state and the application result are separate. For example, the orchestrator can finish normally (`state: Completed`) with an application output of `all_files_failed`. If a run is not found or route parameters are invalid, the endpoint returns HTTP `404`.

See [the flow guide](flow-explanation.md) for the full sequence and [security and error handling](roles-security-error-handling.md) for the trust boundaries and error behavior.
