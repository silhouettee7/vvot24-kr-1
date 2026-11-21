import argparse
import io
import json
import time
import urllib.error
import urllib.request
import uuid
import zipfile


def request(base, path, body=None):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(base.rstrip("/") + path, data=data,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.read()


def expect(base, path, status, body=None):
    actual, payload = request(base, path, body)
    if actual != status:
        raise RuntimeError(f"{path}: expected {status}, got {actual}: {payload[:500]!r}")
    print(f"OK {status} {path}")
    return payload


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("api_url")
    parser.add_argument("--source-url", default="https://www.example.com/")
    parser.add_argument("--wait", type=int, default=180)
    args = parser.parse_args()
    name = f"smoke-{uuid.uuid4().hex}.html"
    expect(args.api_url, "/tasks", 400, {"name": "missing-url"})
    expect(args.api_url, "/tasks", 400, {"name": "x", "url": "not-a-url"})
    expect(args.api_url, "/tasks", 202, {"name": name, "url": args.source_url})
    deadline = time.monotonic() + args.wait
    while time.monotonic() < deadline:
        items = json.loads(expect(args.api_url, "/archives", 200))
        if not isinstance(items, list):
            raise RuntimeError("GET /archives must return a JSON array")
        found = next((item for item in items if item["name"] == name), None)
        if found:
            break
        time.sleep(3)
    else:
        raise RuntimeError("Archive did not appear; inspect the DLQ, function error metrics and source URL")
    key = found["key"]
    assert key.endswith(".zip") and uuid.UUID(key[:-4]).version == 7
    item = json.loads(expect(args.api_url, f"/archives/{key}", 200))
    assert item == found
    assert item["url"] == args.source_url
    payload = expect(args.api_url, f"/download/{key}", 200)
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        assert archive.namelist() == [name]
        assert archive.testzip() is None
        print(f"ZIP valid, file {name}, {len(archive.read(name))} bytes")
    missing = "00000000-0000-7000-8000-000000000000.zip"
    expect(args.api_url, f"/archives/{missing}", 404)
    expect(args.api_url, f"/download/{missing}", 404)
    print("All API checks passed")


if __name__ == "__main__":
    main()
