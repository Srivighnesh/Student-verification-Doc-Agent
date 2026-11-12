import base64
import io
import json

from PIL import Image

import function_app
from shared.schemas import ProcessingMessage


class FakeRequest:
    def __init__(self, body=None, route_params=None, error=None):
        self.body = body
        self.route_params = route_params or {}
        self.error = error

    def get_json(self):
        if self.error:
            raise self.error
        return self.body


class FakeOutput:
    def __init__(self):
        self.value = None

    def set(self, value):
        self.value = value


class FakeBlobService:
    def __init__(self):
        self.uploads = []

    def get_blob_client(self, container, name):
        service = self

        class BlobClient:
            def upload_blob(self, data, overwrite=False):
                service.uploads.append((container, name, data, overwrite))

        return BlobClient()


def patterned_png():
    image = Image.new("RGB", (20, 20), "white")
    for x in range(5, 15):
        for y in range(5, 15):
            image.putpixel((x, y), (0, 0, 0))
    buffer = io.BytesIO()
    image.save(buffer, "PNG")
    return buffer.getvalue()


def make_upload_request(file_data, file_name="scan.png"):
    return {
        "employee_id": "emp123",
        "kind": "verification",
        "files": [{
            "file_name": file_name,
            "file_base64": base64.b64encode(file_data).decode("ascii"),
            "doc_type": "auto",
        }],
    }


def test_service_bus_body_keeps_default_verification_kind():
    message = ProcessingMessage.model_validate({
        "employee_id": "emp123",
        "run_id": "abcdef12",
    })

    body = function_app.service_bus_body(message)

    assert body["kind"] == "verification"
    assert "skipped_files" not in body


def test_client_skip_message_is_preserved_for_foundry():
    skips = [{
        "file": "blank.pdf",
        "kind": "verification",
        "error_code": "blank_document",
        "message": "Document contains no readable content.",
    }]

    assert function_app._unprocessable_files(skips) == skips


def test_upload_api_stores_file_and_queues_message(monkeypatch):
    storage = FakeBlobService()
    containers = []
    monkeypatch.setattr(function_app, "blobs", lambda: storage)
    monkeypatch.setattr(function_app, "ensure_container", containers.append)
    output = FakeOutput()

    response = function_app.upload(
        FakeRequest(make_upload_request(patterned_png())),
        output,
    )

    body = json.loads(response.get_body())
    queued = json.loads(output.value)
    assert response.status_code == 202
    assert body["status"] == "queued"
    assert body["run_id"] == queued["run_id"]
    assert queued["employee_id"] == "emp123"
    assert queued["kind"] == "verification"
    assert storage.uploads == [
        ("to-verify", queued["files"][0], patterned_png(), True)
    ]
    assert containers == ["to-verify"]


def test_upload_api_queues_local_skip_reason(monkeypatch):
    storage = FakeBlobService()
    monkeypatch.setattr(function_app, "blobs", lambda: storage)
    monkeypatch.setattr(function_app, "ensure_container", lambda name: None)
    request_body = make_upload_request(patterned_png())
    request_body["skipped_files"] = [{
        "file_name": "blank.pdf.base64.txt",
        "error_code": "blank_document",
    }]
    output = FakeOutput()

    response = function_app.upload(FakeRequest(request_body), output)

    body = json.loads(response.get_body())
    queued = json.loads(output.value)
    expected = {
        "file": "blank.pdf.base64.txt",
        "kind": "verification",
        "error_code": "blank_document",
        "message": "Document contains no readable content.",
    }
    assert response.status_code == 202
    assert queued["skipped_files"] == [expected]
    assert body["skipped_files"] == [expected]


def test_upload_api_returns_safe_validation_error_without_storage(monkeypatch):
    storage = FakeBlobService()
    monkeypatch.setattr(function_app, "blobs", lambda: storage)
    monkeypatch.setattr(function_app, "ensure_container", lambda name: None)
    output = FakeOutput()

    response = function_app.upload(
        FakeRequest(make_upload_request(b"private-file-content", "bad.jpg")),
        output,
    )

    body = json.loads(response.get_body())
    assert response.status_code == 400
    assert body["error"] == "Invalid upload request"
    assert body["details"][0]["file"] == "bad.jpg"
    assert body["details"][0]["code"] == "unsupported_file_type"
    assert storage.uploads == []
    assert output.value is None
    assert b"private-file-content" not in response.get_body()


def test_upload_api_keeps_pydantic_field_errors_safe():
    request_body = make_upload_request(b"x")
    request_body["files"][0]["file_base64"] = "%%%%"
    output = FakeOutput()

    response = function_app.upload(FakeRequest(request_body), output)

    body = json.loads(response.get_body())
    assert response.status_code == 400
    assert body["details"][0]["field"].endswith("file_base64")
    assert "%%%%" not in response.get_body().decode("utf-8")
    assert output.value is None


def test_upload_api_reports_invalid_json():
    response = function_app.upload(
        FakeRequest(error=ValueError("invalid JSON")),
        FakeOutput(),
    )

    assert response.status_code == 400
    assert json.loads(response.get_body()) == {
        "error": "Request body must be valid JSON."
    }


