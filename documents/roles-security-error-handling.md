# Roles, Security, Guardrails, and Errors

This guide explains who or what may call each part of the system, where identity-document data moves, what the code validates, and how failures are handled.

Related guides: [API design](api-design.md) · [end-to-end flow](flow-explanation.md)

## 1. Main trust boundaries

| Component | Input it receives | Identity or access used |
| --- | --- | --- |
| Upload/status HTTP routes | Client JSON or route parameters | Azure Functions authorization level is `FUNCTION`; deployed clients use a Function key. |
| Blob Storage staging/results containers | Temporary original files and combined extracted JSON | The app creates the Blob client from `StorageConnection`. Treat this setting as a secret connection string and scope its storage access tightly. |
| Service Bus queue | Metadata and Blob paths only | The function binding uses `ServiceBusConnection`. The documented configuration is identity-based and needs queue sender/receiver roles. |
| Durable Functions runtime | Orchestration state and activity messages | Uses `AzureWebJobsStorage`, which must be separate/configured for the Functions host and Durable state. |
| Document Intelligence | Normalized file bytes for analysis | The client uses `DI_ENDPOINT` and `DI_KEY`. Keep the key secret and give the resource only required access. |
| Microsoft Foundry | Extracted fields, filenames, skip/error details, and prompt | `DefaultAzureCredential` is used locally through Azure CLI login and in Azure through the Function App managed identity. The identity needs Foundry agent invocation access. |
| `send_test.py` | Local files and upload URL | Uses `UPLOAD_URL` and optional `FUNCTION_KEY`; it does not use Azure credentials to upload directly. |

`local.settings.json` contains secrets and should not be committed. In Azure, configure the equivalent application settings and use managed identity where the client supports it. The Blob client in this code currently uses `StorageConnection` as a connection string; switching it to managed identity would require an implementation change.

## 2. Data classification and where data goes

Identity cards, invoices, and school records can contain personal information. The pipeline handles them as sensitive data.

| Data | Stored or transmitted to |
| --- | --- |
| Original document bytes | Temporarily stored in `processing-staging`; deleted after extraction results are saved. |
| Base64 upload representation | HTTP request only; decoded once before the Blob write. It is not put in Blob Storage or Service Bus. |
| Normalized image bytes | In memory for the activity; not uploaded as a second source file. |
| Extracted fields | Combined JSON in Blob Storage and local output; also passed to Foundry for verification. |
| Local skip metadata | Service Bus and Foundry comparison data: filename, error code, safe explanation. No bytes are present. |
| Verification report | Local `LOCAL_OUTPUT_DIR` only. The report can contain document details and should be protected accordingly. |
| Operational log fields | Employee ID, run ID, filename, error code, and error type/context. File naming itself can reveal information, so avoid personal details in filenames. |

The Foundry prompt tells the agent to treat document text as data, not instructions, and to avoid inventing facts. This reduces prompt-injection risk, but it does not replace access controls or review of the generated report.

## 3. Input guardrails

### Request-shape checks

Pydantic models in `shared/schemas.py` reject unknown properties and enforce:

- Employee IDs with a restricted character set and length.
- Supported filename extensions and filenames without path separators.
- Base64 syntax on uploaded file strings.
- Supported `kind`, `doc_type`, and stable skip error codes.
- Service Bus blob paths that belong to the specified employee prefix.
- Run IDs in the allowed hexadecimal forms.

### Document-content checks

The shared `validate_document_bytes` function runs in the processing activity after the staged blob is downloaded. It checks:

- Maximum content size from `MAX_FILE_BYTES` (default 20 MiB).
- Actual file signatures rather than trusting only the extension.
- Image decoding with Pillow `verify()`, a full reopen, and `load()`.
- PDF structure, page count, encryption, and whether pages have text or images, using `pypdf`.
- Near-uniform image content using grayscale standard deviation and `BLANK_IMAGE_STDDEV_THRESHOLD` (default `2.0`).
- Legacy `.txt` Base64 decoding and the type of the decoded bytes.

An extension mismatch is logged as a warning and the detected content type is used. It is accepted rather than rejected.

Transport Base64 syntax is checked before staging. Content validation is asynchronous and per file: a corrupt file is recorded as a failed result while other files in the same run continue. A Storage or queue failure can leave temporary staged blobs; an explicit cleanup activity removes them after processing, with a Blob lifecycle policy as a fallback.

## 4. Authorization and least privilege

1. Require a Function key for deployed upload/status calls; do not publish a key in source control or examples.
2. Keep Service Bus permissions limited to the `document-processing` queue. The app needs sender access for upload output and receiver access for the trigger.
3. Keep Storage access scoped to the containers and Durable host resources the app needs. Protect `StorageConnection` and `AzureWebJobsStorage` independently.
4. Grant the runtime identity the Foundry agent invocation role required by the project. Local development uses the signed-in Azure CLI identity; deployment should use the Function App managed identity.
5. Protect `DI_KEY`, Foundry endpoints, local output, combined JSON, and reports. Avoid copying real identity documents into logs, issue trackers, or examples.
6. Use a private or controlled local output directory. Reports are not automatically uploaded, backed up, or shared by this application.

The app validates that file paths are under an employee prefix, but it does not implement a separate per-caller authorization check that proves the HTTP caller owns that employee ID. Function keys authorize the function, not an individual employee. If multiple independent customers use this API, add caller identity and tenant/employee authorization before deployment.

## 5. Error handling map

### Upload/API errors

| Condition | HTTP/result behavior | Stored or queued? |
| --- | --- | --- |
| Invalid JSON | HTTP 400 with a generic JSON-body message. | No. |
| Malformed transport Base64 or Pydantic field error | HTTP 400 with field names and safe messages. | No file is queued. |
| Per-file content error | Recorded in the run output and `combined.json` with a stable code and safe message. | Other files in the same run continue processing. |
| Blob write or queue output exception | HTTP 500 with a generic message; server logs the exception. | Some staged blobs may remain; lifecycle cleanup is the fallback. |
| Duplicate Service Bus delivery | Existing Durable instance is found by employee/run ID and the duplicate is ignored. | No duplicate orchestration. |

### Stable document error codes

| Code | Meaning | Retry behavior |
| --- | --- | --- |
| `empty_file` | Zero-byte input or whitespace-only `.txt`. | Permanent; no retry. |
| `invalid_base64` | Legacy `.txt` content is not valid Base64. | Permanent; no retry. |
| `unsupported_file_type` | Bytes do not match a supported signature. | Permanent; no retry. |
| `extension_mismatch` | Extension differs from detected type. Warning only; accepted. | No retry. |
| `corrupt_image` | Image cannot be verified or loaded. | Permanent; no retry. |
| `corrupt_pdf` | PDF header/structure is invalid or there are no pages. | Permanent; no retry. |
| `password_protected_pdf` | PDF cannot be opened without a password. | Permanent; no retry. |
| `blank_document` | Image appears nearly uniform or PDF has no text/images; layout fallback can also detect empty extraction for `id`/`invoice`. | Permanent; no retry. |
| `file_too_large` | File exceeds `MAX_FILE_BYTES`. | Permanent; no retry. |
| `transient_service_error` | Processing still failed after the activity retry policy. | The activity is retried up to three attempts first. |

### Processing and orchestration errors

- `DocumentError` is caught by `process_file` and returned as a per-file failed result with `error_code`, safe message, filename, kind, and run ID.
- Document Intelligence HTTP 400/415 errors are converted to permanent corrupt-content results. Other `HttpResponseError`s and unexpected download/network errors are re-raised so Durable retry can run.
- Each file has an independent retry child orchestration. When its retries are exhausted, it returns `transient_service_error` instead of allowing one file to abort `task_all` for the employee.
- If all processed files fail, the orchestrator returns `all_files_failed`, skips Foundry, records errors, and preserves the stored document sections.
- When some files fail but at least one processed file succeeds, verification continues. Foundry receives those failures plus any optional client-side skips under `unprocessable_files` and is instructed not to mislabel them as missing or mismatched.
- `combine_and_save` raises for invalid internal kinds or a failed result save. These are run-level errors rather than file-format errors.
- A Foundry request/report failure is not converted into a document error. It propagates to the orchestration, which is marked failed and logged with employee/run context.
- The Foundry activity creates a unique conversation per run and attempts cleanup in `finally`; cleanup failure is logged by error type and does not replace the response result or original Foundry exception.

### Useful application statuses

| Status | Meaning |
| --- | --- |
| `queued` | Upload passed validation, blobs were written, and Service Bus accepted the message. |
| `baseline_processed` | Baseline run finished with no per-file errors. |
| `baseline_processed_with_errors` | Baseline work finished with one or more file/client-skip errors. |
| `verification_report_saved` | Verification report was generated and saved locally without reported file errors. |
| `verification_report_saved_with_errors` | Report was generated while some files were unprocessable. |
| `all_files_failed` | No processed file succeeded; Foundry was skipped. |
| `no_files_found` | No supported source blobs were found to process. |
| `no_processing_results` | No processing results came back from the activities. |

`GET /api/status/...` returns both Durable runtime `state` and application `output`. A normally completed orchestration can return an application status such as `all_files_failed`; a Foundry exception can instead make the Durable state `Failed`.

## 6. Assertions, checks, and exceptions

There are no Python `assert` statements in the runtime guardrails. Production input checks use Pydantic validators, explicit `if` conditions, and typed exceptions instead. This is intentional: Python can remove `assert` statements when run with optimization, so assertions should not enforce security or request validity.

`DocumentError` represents expected permanent document problems. It carries a stable code and user-readable message and does not include document bytes, Base64, or extracted values. Unexpected exceptions are not turned into success: transient-looking processing exceptions are re-raised for retry; run-level Storage, orchestration, or Foundry exceptions fail the run and are logged.

Resource creation is idempotent: `ResourceExistsError` from creating a container is caught because the container already exists. A missing prior combined-result blob is handled as an empty initial result. These are expected states, not processing failures.

## 7. Limits and operational notes

- The local report directory is not durable shared storage. A deployed worker restart can make locally written reports unavailable.
- The combined JSON contains extracted PII. Restrict Blob access and retention.
- Blob files are organized under `{employee_id}/...`; do not use real personal data as filenames or employee IDs if the system can use opaque internal identifiers instead.
- A malformed Service Bus message is logged and rejected by raising a validation error. Configure queue delivery/dead-letter policies to handle repeated invalid messages.
- The Foundry agent sees extracted fields and the unprocessable-file list, not raw documents. Treat those extracted fields and generated reports as sensitive.

For exact request/queue/agent JSON shapes, see [API design](api-design.md). For the sequence from upload to status, see [the flow guide](flow-explanation.md).
