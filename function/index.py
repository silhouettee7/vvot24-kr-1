import ipaddress
import json
import os
import re
import secrets
import socket
import tempfile
import time
import urllib.parse
import urllib.request
import uuid
import zipfile

MAX_FILE_BYTES = 2 * 1024 * 1024
DOWNLOAD_SECONDS = 120
CHUNK_SIZE = 64 * 1024


def uuid7():
    """RFC 9562: 48-bit Unix milliseconds, version 7, variant 10, 74 random bits."""
    value = (time.time_ns() // 1_000_000) << 80
    value |= 7 << 76
    value |= secrets.randbits(12) << 64
    value |= 2 << 62
    value |= secrets.randbits(62)
    return uuid.UUID(int=value)


def validate_task(task):
    if not isinstance(task, dict) or set(task) != {"name", "url"}:
        raise ValueError("Task must contain exactly name and url")
    name, url = task["name"], task["url"]
    if (not isinstance(name, str) or not 1 <= len(name) <= 200
            or name in {".", ".."} or re.search(r'[/\\\x00-\x1f\x7f]', name)):
        raise ValueError("Invalid file name")
    if not isinstance(url, str) or len(url) > 4096:
        raise ValueError("Invalid URL")
    parts = urllib.parse.urlsplit(url)
    if (parts.scheme not in {"http", "https"} or not parts.hostname
            or parts.username is not None or parts.password is not None):
        raise ValueError("Only HTTP(S) URLs without credentials are supported")
    return name, url


def validate_destination(url):
    """Reject local/metadata destinations, including on HTTP redirects."""
    validate_task({"name": "download", "url": url})
    parts = urllib.parse.urlsplit(url)
    port = parts.port or (443 if parts.scheme == "https" else 80)
    addresses = socket.getaddrinfo(parts.hostname, port, type=socket.SOCK_STREAM)
    if not addresses or any(not ipaddress.ip_address(a[4][0]).is_global for a in addresses):
        raise ValueError("Download destination must have public IP addresses")


class PublicRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        validate_destination(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def create_zip(name, url, target):
    validate_destination(url)
    opener = urllib.request.build_opener(PublicRedirectHandler())
    request = urllib.request.Request(url, headers={"User-Agent": "vvot-archiver/1.0"})
    deadline = time.monotonic() + DOWNLOAD_SECONDS
    with opener.open(request, timeout=15) as source:
        length = source.headers.get("Content-Length")
        if length and int(length) > MAX_FILE_BYTES:
            raise ValueError("Source file exceeds 2 MiB")
        with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            with archive.open(name, "w") as entry:
                total = 0
                while True:
                    if time.monotonic() > deadline:
                        raise TimeoutError("Download exceeded time limit")
                    chunk = source.read(CHUNK_SIZE)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > MAX_FILE_BYTES:
                        raise ValueError("Source file exceeds 2 MiB")
                    entry.write(chunk)


def clients():
    import boto3
    from botocore.config import Config

    session = boto3.session.Session(
        aws_access_key_id=os.environ["ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["SECRET_ACCESS_KEY"],
        region_name="ru-central1",
    )
    config = Config(connect_timeout=5, read_timeout=30,
                    retries={"max_attempts": 3, "mode": "standard"})
    storage = session.client("s3", endpoint_url="https://storage.yandexcloud.net", config=config)
    database = session.client("dynamodb", endpoint_url=os.environ["YDB_DOCUMENT_ENDPOINT"], config=config)
    return storage, database


def handler(event, context):
    messages = event.get("messages", [])
    if len(messages) != 1:
        raise ValueError("Expected exactly one message from the queue trigger")
    task = json.loads(messages[0]["details"]["message"]["body"])
    name, url = validate_task(task)
    key = f"{uuid7()}.zip"
    storage, database = clients()
    with tempfile.TemporaryDirectory() as directory:
        path = os.path.join(directory, "archive.zip")
        create_zip(name, url, path)
        with open(path, "rb") as archive:
            storage.put_object(Bucket=os.environ["BUCKET_NAME"], Key=key,
                               Body=archive, ContentType="application/zip")
        database.put_item(
            TableName=os.environ.get("TABLE_NAME", "archives"),
            Item={"key": {"S": key}, "name": {"S": name}, "url": {"S": url}},
        )
    return {"statusCode": 200}
