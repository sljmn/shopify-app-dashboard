import io
import zipfile

import pytest
from botocore.exceptions import ClientError

from app_dashboard.object_storage import ContentObjectStore, InvalidResearchFile, ResearchObjectStore, inspect_file


class Settings:
    b2_bucket = "research-test"
    research_upload_max_bytes = 100
    b2_configured = True
    content_image_max_bytes = 100


class FakeS3:
    def __init__(self):
        self.objects = {}
        self.puts = 0

    def head_object(self, *, Bucket, Key):
        if (Bucket, Key) not in self.objects:
            raise ClientError({"Error": {"Code": "404"}}, "HeadObject")

    def put_object(self, *, Bucket, Key, Body, **kwargs):
        self.objects[(Bucket, Key)] = (Body, kwargs)
        self.puts += 1

    def generate_presigned_url(self, operation, Params, ExpiresIn):
        assert operation == "get_object" and ExpiresIn == 60
        return f"https://signed.test/{Params['Key']}"

    def delete_object(self, *, Bucket, Key):
        self.objects.pop((Bucket, Key), None)


def test_upload_is_content_addressed_private_and_deduplicated():
    client = FakeS3()
    store = ResearchObjectStore(Settings(), client=client)
    data = b"%PDF-1.7\nhello"
    first = store.validate_and_upload(data, filename="notes.pdf", content_type="application/pdf")
    second = store.validate_and_upload(data, filename="copy.pdf", content_type="application/pdf")
    assert first.object_key == f"research/{first.digest[:2]}/{first.digest}"
    assert first.created is True and second.created is False and client.puts == 1
    _, kwargs = client.objects[("research-test", first.object_key)]
    assert kwargs["ServerSideEncryption"] == "AES256"
    assert store.presigned_get(first.object_key, filename="notes.pdf").startswith(
        "https://signed.test/research/"
    )


def test_file_validation_rejects_size_extension_and_mime_mismatches():
    store = ResearchObjectStore(Settings(), client=FakeS3())
    with pytest.raises(InvalidResearchFile, match="file-too-large"):
        store.validate_and_upload(b"x" * 101, filename="note.txt", content_type="text/plain")
    with pytest.raises(InvalidResearchFile, match="unsupported"):
        inspect_file(b"hello", "page.html", "text/html")
    with pytest.raises(InvalidResearchFile, match="content-does-not-match"):
        inspect_file(b"hello", "fake.pdf", "application/pdf")


def test_office_open_xml_is_inspected_not_only_trusted_by_extension():
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w") as archive:
        archive.writestr("[Content_Types].xml", "types")
        archive.writestr("word/document.xml", "doc")
    name, mime = inspect_file(
        data.getvalue(), "research.docx",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )
    assert name == "research.docx"
    assert mime.endswith("wordprocessingml.document")


def test_content_images_use_their_own_private_namespace():
    client=FakeS3()
    stored=ContentObjectStore(Settings(),client=client).upload_image(
        b"\x89PNG\r\n\x1a\nimage",mime_type="image/png",
    )
    assert stored.object_key.startswith("content/")
    assert client.objects[("research-test",stored.object_key)][1]["ContentDisposition"] == "inline"
