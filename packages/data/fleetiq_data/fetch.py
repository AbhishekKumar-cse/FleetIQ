"""Acquire immutable NASA-published archives with checked provenance and extraction."""

import argparse
import hashlib
import io
import json
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[3]
MAX_ARCHIVE = 64 * 1024 * 1024
MAX_EXPANDED = 256 * 1024 * 1024
ALLOWED = {
    "zenodo.org": {"/records/15346912/files/CMAPSSData.zip",
                   "/api/records/15346912/files/CMAPSSData.zip/content"},
    "data.nasa.gov": {"/docs/legacy/CMAPSSData.zip"},
    "data-nasa-bucket-production.s3.us-east-1.amazonaws.com": {"/legacy/CMAPSSData.zip"},
}


def validate_url(url: str) -> None:
    p = urllib.parse.urlsplit(url)
    if (p.scheme != "https" or p.username or p.password or p.port not in (None, 443)
            or p.path not in ALLOWED.get(p.hostname, set())):
        raise ValueError("unapproved dataset URL or redirect")


class ApprovedRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        validate_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def download(url: str) -> bytes:
    validate_url(url)
    opener = urllib.request.build_opener(ApprovedRedirects())
    for attempt in range(3):
        try:
            request = urllib.request.Request(url, headers={"User-Agent":"FleetIQ-dataset-bootstrap/1"})
            with opener.open(request, timeout=30) as response:
                payload = response.read(MAX_ARCHIVE + 1)
            if len(payload) > MAX_ARCHIVE:
                raise ValueError("archive exceeds size budget")
            return payload
        except (urllib.error.URLError, TimeoutError):
            if attempt == 2:
                raise ConnectionError("dataset download failed after three bounded attempts") from None
            time.sleep(attempt + 1)
    raise AssertionError("unreachable")


def validate_archive(payload: bytes, subset: str, expected_md5: str) -> dict[str, bytes]:
    if not payload.startswith(b"PK\x03\x04") or len(payload) > MAX_ARCHIVE:
        raise ValueError("response is not an allowed ZIP payload")
    if hashlib.md5(payload, usedforsecurity=False).hexdigest() != expected_md5:
        raise ValueError("archive differs from the publisher checksum")
    names = [f"{kind}_{subset}.txt" for kind in ("train", "test", "RUL")]
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        entries = archive.infolist()
        if sum(e.file_size for e in entries) > MAX_EXPANDED:
            raise ValueError("ZIP expanded size exceeds budget")
        if len({e.filename for e in entries}) != len(entries):
            raise ValueError("duplicate ZIP members")
        for e in entries:
            p = PurePosixPath(e.filename)
            if p.is_absolute() or ".." in p.parts or "\\" in e.filename or ":" in e.filename:
                raise ValueError("unsafe ZIP path")
            if (e.external_attr >> 16) & 0o170000 == 0o120000:
                raise ValueError("ZIP symlinks forbidden")
        if not set(names).issubset(archive.namelist()):
            raise ValueError("expected subset files missing")
        files = {name: archive.read(name) for name in names}
        for contents in files.values():
            if not contents.strip() or b"<html" in contents.lower():
                raise ValueError("invalid engine data content")
            contents.decode("ascii")
        return files


def fetch_dataset(output: Path, subset: str, source: dict, reader=download) -> dict:
    if subset not in source["subsets"]:
        raise ValueError("unknown subset")
    validate_url(source["archive_url"])
    archive_path = output / "CMAPSSData.zip"
    payload = archive_path.read_bytes() if archive_path.exists() else reader(source["archive_url"])
    files = validate_archive(payload, subset, source["published_md5"])
    output.mkdir(parents=True, exist_ok=True)
    all_files = {"CMAPSSData.zip": payload, **files}
    for name, contents in all_files.items():
        p = output / name
        if p.is_symlink():
            raise ValueError("refusing artifact symlink")
        if p.exists() and p.read_bytes() != contents:
            raise ValueError("existing raw artifact changed; refusing overwrite")
    manifest = {
        "source_kind":"nasa_cmapss", "subset":subset, "download_url":source["archive_url"],
        "catalog_url":source["catalog_url"], "endorsement_url":source["endorsement_url"],
        "license_review":source["license_review"], "publisher_md5":source["published_md5"],
        "archive_sha256":hashlib.sha256(payload).hexdigest(),
        "files":{name:hashlib.sha256(contents).hexdigest() for name,contents in files.items()},
    }
    manifest_path = output / "manifest.json"
    if manifest_path.exists():
        existing = json.loads(manifest_path.read_text())
        if any(existing.get(k) != v for k,v in manifest.items()):
            raise ValueError("raw manifest mismatch")
        manifest = existing
    else:
        manifest["acquired_at"] = datetime.now(UTC).isoformat()
    for name,contents in all_files.items():
        p = output / name
        if not p.exists():
            with p.open("xb") as f:f.write(contents)
    if not manifest_path.exists():
        manifest_path.write_text(json.dumps(manifest,indent=2,sort_keys=True)+"\n")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", choices=["cmapss"], required=True)
    parser.add_argument("--subset", choices=["FD001","FD002","FD003","FD004"], required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    if not output.is_relative_to((ROOT / "data/raw").resolve()):
        parser.error("output must stay within ignored data/raw")
    source = json.loads((ROOT / "config/data_sources.json").read_text())["cmapss"]
    try:
        manifest = fetch_dataset(output,args.subset,source)
    except (ValueError,ConnectionError,zipfile.BadZipFile,UnicodeError) as exc:
        parser.exit(1,f"Dataset acquisition rejected: {exc}\n")
    print(json.dumps({"subset":args.subset,"archive_sha256":manifest["archive_sha256"],
                      "extracted_files":list(manifest["files"])}))


if __name__ == "__main__":main()
