import base64

import pytest
from pydantic import ValidationError
from PIL import Image
import io

import function_app
from shared.schemas import UploadRequest, validation_details


class Request:
    def __init__(self, body):
        self.body = body

    def get_json(self):
        return self.body


class Output:
    def __init__(self):
        self.value = None

    def set(self, value):
        self.value = value


def test_schema_keeps_invalid_base64_field_error():
    with pytest.raises(ValidationError) as raised:
        UploadRequest.model_validate({
            "employee_id": "emp1", "files": [{
                "file_name": "bad.pdf", "file_base64": "%%%",
            }],
        })
    assert "file_base64" in str(raised.value)
    assert "%%%" not in str(validation_details(raised.value))


def test_upload_validation_is_all_or_nothing(monkeypatch):
    storage_calls = []
    monkeypatch.setattr(function_app, "ensure_container", lambda name: storage_calls.append(name))
    monkeypatch.setattr(function_app, "blobs", lambda: storage_calls.append("blobs"))
    image_buffer = io.BytesIO()
    image = Image.new("RGB", (4, 4), "black")
    image.putpixel((0, 0), (255, 255, 255))
    image.save(image_buffer, "PNG")
    bad_request = {
        "employee_id": "emp1", "kind": "verification", "files": [
            {"file_name": "valid.png", "file_base64": base64.b64encode(
                image_buffer.getvalue()
            ).decode(), "doc_type": "auto"},
            {"file_name": "bad.jpg", "file_base64": base64.b64encode(b"garbage").decode(), "doc_type": "auto"},
        ],
    }
    out = Output()
    response = function_app.upload(Request(bad_request), out)
    assert response.status_code == 400
    assert storage_calls == []
    assert out.value is None
    assert b"unsupported_file_type" in response.get_body()
    assert b"garbage" not in response.get_body()
