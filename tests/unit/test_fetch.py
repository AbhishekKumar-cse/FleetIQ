import hashlib
import io
import zipfile

import pytest
from fleetiq_data.fetch import fetch_dataset, validate_archive, validate_url


def archive(extra=None):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as z:
        for kind in ("train", "test", "RUL"):
            z.writestr(f"{kind}_FD001.txt", "1 2 3\n")
        if extra:
            z.writestr(extra, "fixture")
    return stream.getvalue()


def source(payload):
    return {
        "subsets": ["FD001"],
        "archive_url": "https://zenodo.org/records/15346912/files/CMAPSSData.zip?download=1",
        "published_md5": hashlib.md5(payload, usedforsecurity=False).hexdigest(),
        "catalog_url": "https://data.nasa.gov/dataset/cmapss-jet-engine-simulated-data",
        "endorsement_url": "https://github.com/nasa/progpy",
        "license_review": "unspecified",
    }


def test_acquisition_is_immutable_and_retry_is_local(tmp_path):
    payload = archive()
    first = fetch_dataset(tmp_path, "FD001", source(payload), reader=lambda _: payload)
    second = fetch_dataset(
        tmp_path, "FD001", source(payload), reader=lambda _: pytest.fail("no network on retry")
    )
    assert first == second
    assert (tmp_path / "train_FD001.txt").read_bytes() == b"1 2 3\n"
    (tmp_path / "train_FD001.txt").write_text("corrupted")
    with pytest.raises(ValueError, match="overwrite"):
        fetch_dataset(tmp_path, "FD001", source(payload))


@pytest.mark.parametrize(
    "name", ["../escape.txt", "/absolute.txt", "C:/escape.txt", "sub\\escape.txt"]
)
def test_unsafe_zip_paths_are_rejected(name):
    payload = archive(name)
    with pytest.raises(ValueError, match="unsafe"):
        validate_archive(payload, "FD001", source(payload)["published_md5"])


def test_html_and_wrong_checksum_are_rejected():
    with pytest.raises(ValueError):
        validate_archive(b"<html>error</html>", "FD001", "bad")
    with pytest.raises(ValueError, match="checksum"):
        validate_archive(archive(), "FD001", "0" * 32)


@pytest.mark.parametrize(
    "url",
    [
        "http://zenodo.org/records/15346912/files/CMAPSSData.zip",
        "https://example.com/archive.zip",
        "https://zenodo.org/records/1/files/x.zip",
    ],
)
def test_only_reviewed_https_resources_are_allowed(url):
    with pytest.raises(ValueError):
        validate_url(url)
