import os
import re
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictStr,
    ValidationError,
    field_validator,
    model_validator,
)


SUPPORTED_INPUT_EXTENSIONS = {
    ".pdf", ".jpg", ".jpeg", ".png", ".bmp",
    ".tif", ".tiff", ".webp", ".txt",
}
RUN_ID_PATTERN = re.compile(r"^(?:[0-9a-f]{8}|[0-9a-f]{32})$")


class UploadFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    file_name: str = Field(strict=True, min_length=1, max_length=255)
    # Empty Base64 is allowed so a zero-byte file can reach the worker and be
    # reported as empty_file instead of being rejected as a request-shape error.
    file_base64: str = Field(strict=True)
    doc_type: Literal["auto", "id", "invoice", "ssc_memo"] = "auto"

    @field_validator("file_name")
    @classmethod
    def validate_file_name(cls, value: str) -> str:
        if value in {".", ".."} or "/" in value or "\\" in value:
            raise ValueError("must be a file name, not a path")
        if os.path.splitext(value)[1].lower() not in SUPPORTED_INPUT_EXTENSIONS:
            raise ValueError("file extension is not supported")
        return value

    @field_validator("file_base64")
    @classmethod
    def validate_file_base64(cls, value: str) -> str:
        # Check transport Base64 syntax before the API decodes it into staging.
        pattern = r"(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?"
        if not re.fullmatch(pattern, value):
            raise ValueError("must contain valid Base64 file bytes")
        return value


class SkippedUploadFile(BaseModel):
    """Client-side validation result; the server supplies the display message."""
    model_config = ConfigDict(extra="forbid")

    file_name: str = Field(strict=True, min_length=1, max_length=255)
    error_code: Literal[
        "empty_file", "invalid_base64", "unsupported_file_type",
        "corrupt_image", "corrupt_pdf", "password_protected_pdf",
        "blank_document", "file_too_large",
    ]

    @field_validator("file_name")
    @classmethod
    def validate_skipped_file_name(cls, value: str) -> str:
        if value in {".", ".."} or "/" in value or "\\" in value:
            raise ValueError("must be a file name, not a path")
        return value


class UploadRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    employee_id: str = Field(
        strict=True,
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$",
    )
    kind: Literal["baseline", "verification"] = "verification"
    files: list[UploadFile] = Field(min_length=1)
    skipped_files: list[SkippedUploadFile] = Field(default_factory=list)


class SkippedFileMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    file: StrictStr
    kind: Literal["baseline", "verification"]
    error_code: Literal[
        "empty_file", "invalid_base64", "unsupported_file_type",
        "corrupt_image", "corrupt_pdf", "password_protected_pdf",
        "blank_document", "file_too_large",
    ]
    message: StrictStr


class ProcessingMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    employee_id: str = Field(
        strict=True,
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$",
    )
    run_id: str | None = Field(default=None, strict=True)
    kind: Literal["baseline", "verification"] = "verification"
    files: list[StrictStr] = Field(default_factory=list)
    skipped_files: list[SkippedFileMessage] = Field(default_factory=list)

    @field_validator("run_id")
    @classmethod
    def validate_run_id(cls, value: str | None) -> str | None:
        if value is not None and not RUN_ID_PATTERN.fullmatch(value.lower()):
            raise ValueError(
                "must be an 8- or 32-character hexadecimal run ID"
            )
        return value.lower() if value is not None else None

    @field_validator("files")
    @classmethod
    def validate_blob_paths(cls, values: list[str]) -> list[str]:
        for value in values:
            parts = value.split("/")
            if (
                len(parts) < 3
                or any(part in {"", ".", ".."} for part in parts)
                or "\\" in value
                or os.path.splitext(value)[1].lower()
                not in SUPPORTED_INPUT_EXTENSIONS
            ):
                raise ValueError("each file must be a supported Blob path")
        return values

    @model_validator(mode="after")
    def files_belong_to_employee(self) -> "ProcessingMessage":
        prefix = f"{self.employee_id}/"
        if any(not path.startswith(prefix) for path in self.files):
            raise ValueError("file paths must belong to employee_id")
        return self


class StatusPath(BaseModel):
    model_config = ConfigDict(extra="forbid")

    employee_id: str = Field(
        strict=True,
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$",
    )
    run_id: str = Field(strict=True)

    @field_validator("run_id")
    @classmethod
    def validate_run_id(cls, value: str) -> str:
        normalized = value.lower()
        if not RUN_ID_PATTERN.fullmatch(normalized):
            raise ValueError(
                "must be an 8- or 32-character hexadecimal run ID"
            )
        return normalized


def validation_details(error: ValidationError) -> list[dict]:
    return [
        {
            "field": ".".join(str(part) for part in issue["loc"]),
            "message": issue["msg"],
        }
        for issue in error.errors(include_input=False)
    ]
