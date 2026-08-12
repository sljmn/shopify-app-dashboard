"""Private content-addressed Backblaze storage for research attachments."""

from __future__ import annotations

import hashlib
import io
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

MAX_FILENAME = 180
MIME_BY_EXTENSION = {
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
    ".webp": "image/webp", ".gif": "image/gif", ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".csv": "text/csv", ".txt": "text/plain",
}


class StorageConfigurationError(RuntimeError):
    pass


class InvalidResearchFile(ValueError):
    pass


@dataclass(frozen=True)
class StoredObject:
    digest: str
    object_key: str
    mime_type: str
    byte_size: int
    filename: str
    created: bool


def sanitize_filename(value: str) -> str:
    name = Path(value or "attachment").name
    name = re.sub(r"[^A-Za-z0-9._ -]+", "-", name).strip(" .-")
    if not name:
        name = "attachment"
    stem, suffix = Path(name).stem, Path(name).suffix
    available = MAX_FILENAME - len(suffix)
    return f"{stem[:available]}{suffix}" if len(name) > MAX_FILENAME else name


def _zip_kind(data: bytes) -> str | None:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            names = set(archive.namelist())
    except (zipfile.BadZipFile, OSError):
        return None
    if "[Content_Types].xml" not in names:
        return None
    if any(name.startswith("word/") for name in names):
        return ".docx"
    if any(name.startswith("xl/") for name in names):
        return ".xlsx"
    if any(name.startswith("ppt/") for name in names):
        return ".pptx"
    return None


def inspect_file(data: bytes, filename: str, declared_type: str | None) -> tuple[str, str]:
    clean_name = sanitize_filename(filename)
    extension = Path(clean_name).suffix.casefold()
    expected = MIME_BY_EXTENSION.get(extension)
    if not expected:
        raise InvalidResearchFile("unsupported-file-type")
    if not data:
        raise InvalidResearchFile("empty-file")
    detected = None
    if data.startswith(b"\xff\xd8\xff"):
        detected = "image/jpeg"
    elif data.startswith(b"\x89PNG\r\n\x1a\n"):
        detected = "image/png"
    elif len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        detected = "image/webp"
    elif data.startswith((b"GIF87a", b"GIF89a")):
        detected = "image/gif"
    elif data.startswith(b"%PDF-"):
        detected = "application/pdf"
    elif data.startswith(b"PK"):
        kind = _zip_kind(data)
        detected = MIME_BY_EXTENSION.get(kind) if kind else None
    elif extension in {".csv", ".txt"}:
        try:
            data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise InvalidResearchFile("text-must-be-utf8") from exc
        if b"\x00" in data:
            raise InvalidResearchFile("invalid-text-file")
        detected = expected
    if detected != expected:
        raise InvalidResearchFile("file-content-does-not-match-extension")
    declared = (declared_type or "").split(";", 1)[0].strip().casefold()
    if declared and declared not in {expected.casefold(), "application/octet-stream"}:
        raise InvalidResearchFile("declared-content-type-does-not-match")
    return clean_name, expected


class ResearchObjectStore:
    def __init__(self, settings, *, client=None):
        self.bucket = settings.b2_bucket
        self.max_bytes = settings.research_upload_max_bytes
        if client is not None:
            self.client = client
            return
        if not settings.b2_configured:
            raise StorageConfigurationError("Backblaze B2 is not configured")
        self.client = boto3.client(
            "s3", endpoint_url=settings.b2_endpoint,
            aws_access_key_id=settings.b2_key_id,
            aws_secret_access_key=settings.b2_application_key,
            region_name=settings.b2_region,
            config=Config(signature_version="s3v4"),
        )

    def validate_and_upload(
        self, data: bytes, *, filename: str, content_type: str | None,
    ) -> StoredObject:
        if len(data) > self.max_bytes:
            raise InvalidResearchFile("file-too-large")
        clean_name, mime_type = inspect_file(data, filename, content_type)
        digest = hashlib.sha256(data).hexdigest()
        object_key = f"research/{digest[:2]}/{digest}"
        created = not self._exists(object_key)
        if created:
            self.client.put_object(
                Bucket=self.bucket, Key=object_key, Body=data,
                ContentType=mime_type, ContentDisposition="attachment",
                ServerSideEncryption="AES256", Metadata={"sha256": digest},
            )
        return StoredObject(
            digest, object_key, mime_type, len(data), clean_name, created,
        )

    def _exists(self, object_key: str) -> bool:
        try:
            self.client.head_object(Bucket=self.bucket, Key=object_key)
            return True
        except ClientError as exc:
            code = str(exc.response.get("Error", {}).get("Code", ""))
            if code in {"404", "NoSuchKey", "NotFound"}:
                return False
            raise
        except KeyError:
            return False

    def presigned_get(self, object_key: str, *, filename: str) -> str:
        return self.client.generate_presigned_url(
            "get_object",
            Params={
                "Bucket": self.bucket, "Key": object_key,
                "ResponseContentDisposition": f'attachment; filename="{sanitize_filename(filename)}"',
            },
            ExpiresIn=60,
        )

    def delete(self, object_key: str) -> None:
        self.client.delete_object(Bucket=self.bucket, Key=object_key)
