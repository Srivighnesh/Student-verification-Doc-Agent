import base64
import io

import pytest
from PIL import Image
from pypdf import PdfWriter
from pypdf.generic import (
    DecodedStreamObject, DictionaryObject, NameObject,
)

from shared.file_validation import DocumentError, validate_document_bytes


def image_bytes(color="white", size=(20, 20), format="PNG"):
    buffer = io.BytesIO()
    image = Image.new("RGB", size, color)
    if color == "black":
        for x in range(max(1, size[0] // 4), max(2, size[0] // 2)):
            for y in range(max(1, size[1] // 4), max(2, size[1] // 2)):
                image.putpixel((x, y), (255, 255, 255))
    image.save(buffer, format=format)
    return buffer.getvalue()


def pdf_bytes(encrypted=False, with_text=True):
    writer = PdfWriter()
    page = writer.add_blank_page(width=200, height=200)
    if with_text:
        font = DictionaryObject({
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        })
        font_ref = writer._add_object(font)
        page[NameObject("/Resources")] = DictionaryObject({
            NameObject("/Font"): DictionaryObject({NameObject("/F1"): font_ref})
        })
        stream = DecodedStreamObject()
        stream.set_data(b"BT /F1 12 Tf 10 100 Td (Hello) Tj ET")
        page[NameObject("/Contents")] = writer._add_object(stream)
    if encrypted:
        writer.encrypt("secret")
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


def assert_code(code, data, name):
    with pytest.raises(DocumentError) as raised:
        validate_document_bytes(data, name)
    assert raised.value.code == code


def test_empty_and_whitespace_text():
    assert_code("empty_file", b"", "a.pdf")
    assert_code("empty_file", b" \n\t", "a.txt")


def test_invalid_base64_and_unsupported_signature():
    assert_code("invalid_base64", b"not base64!", "legacy.txt")
    assert_code("unsupported_file_type", b"random bytes", "photo.jpg")


@pytest.mark.parametrize("data,name", [
    (b"\xff\xd8\xffbroken", "bad.jpg"),
    (b"\x89PNG\r\n\x1a\ntruncated", "bad.png"),
])
def test_truncated_images(data, name):
    assert_code("corrupt_image", data, name)


def test_corrupt_and_zero_page_pdf():
    assert_code("corrupt_pdf", b"%PDF-garbage", "bad.pdf")
    assert_code("corrupt_pdf", b"not a pdf", "missing-header.pdf")
    writer = PdfWriter()
    output = io.BytesIO()
    writer.write(output)
    assert_code("corrupt_pdf", output.getvalue(), "empty.pdf")


def test_blank_pdf():
    assert_code("blank_document", pdf_bytes(with_text=False), "blank.pdf")


def test_encrypted_and_valid_pdf():
    assert_code("password_protected_pdf", pdf_bytes(encrypted=True), "locked.pdf")
    result = validate_document_bytes(pdf_bytes(), "valid.pdf")
    assert result.file_type == ".pdf"


def test_valid_image_and_blank_image():
    assert validate_document_bytes(image_bytes("black"), "photo.png").file_type == ".png"
    assert_code("blank_document", image_bytes("white"), "blank.png")


def test_faint_image_is_not_blank():
    image = Image.new("L", (100, 100), 252)
    for x in range(20, 80):
        for y in range(45, 55):
            image.putpixel((x, y), 240)
    buffer = io.BytesIO()
    image.save(buffer, "PNG")
    assert validate_document_bytes(buffer.getvalue(), "scan.png").file_type == ".png"


def test_oversize_and_extension_mismatch(monkeypatch, caplog):
    monkeypatch.setenv("MAX_FILE_BYTES", "10")
    assert_code("file_too_large", b"x" * 11, "large.jpg")
    monkeypatch.setenv("MAX_FILE_BYTES", "100000")
    assert validate_document_bytes(image_bytes("black"), "photo.jpg").file_type == ".png"
    assert "does not match" in caplog.text


def test_legacy_txt_decodes_and_detects_type():
    encoded = base64.b64encode(image_bytes("black"))
    result = validate_document_bytes(encoded, "legacy.txt")
    assert result.file_type == ".png"
    assert result.data.startswith(b"\x89PNG")
