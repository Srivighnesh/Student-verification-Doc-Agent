import base64
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
    file_base64: str = Field(strict=True, min_length=4)
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
        try:
            decoded = base64.b64decode(value, validate=True)
        except (ValueError, base64.binascii.Error) as error:
            raise ValueError("must contain valid Base64 file bytes") from error
        if not decoded:
            raise ValueError("must not decode to an empty file")
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
