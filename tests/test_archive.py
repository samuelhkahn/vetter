import hashlib
import io
import json
import tarfile
from pathlib import Path

import httpx
import pyarrow.parquet as pq
import pytest
from fastavro import writer
from pydantic import ValidationError

from vetter.ingest.archive import (
    ArchiveIntegrityError,
    ArchiveManifest,
    Artifact,
    Inspection,
    classify_night,
    ensure_archive,
    inspect_and_extract,
    load_manifest,
    process_manifest,
)

SHA256_EMPTY = hashlib.sha256(b"").hexdigest()


def manifest_data(**overrides):
    data = {
        "schema_version": 1,
        "source_url": "https://example.test/ztf_public_20230220.tar.gz",
        "observing_date": "2023-02-20",
        "minimum_night_alerts": 1,
        "threshold_rationale": "One alert is enough for this test.",
        "archive": {
            "path": "archives/ztf_public_20230220.tar.gz",
            "byte_size": 0,
            "sha256": SHA256_EMPTY,
        },
        "parquet": None,
        "inspection": None,
    }
    data.update(overrides)
    return data


def write_manifest(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data), encoding="utf-8")


def avro_payload(object_id="ZTFTEST", candid=123) -> bytes:
    candidate_schema = {
        "name": "candidate",
        "type": "record",
        "fields": [
            {"name": "candid", "type": "long"},
            {"name": "jd", "type": "double"},
            {"name": "ra", "type": "double"},
            {"name": "dec", "type": "double"},
            {"name": "drb", "type": ["null", "double"], "default": None},
            {"name": "isdiffpos", "type": "string"},
            {"name": "nbad", "type": "int"},
        ],
    }
    cutout_schema = {
        "name": "cutout",
        "type": "record",
        "fields": [{"name": "stampData", "type": "bytes"}],
    }
    schema = {
        "name": "alert",
        "type": "record",
        "fields": [
            {"name": "objectId", "type": "string"},
            {"name": "candid", "type": "long"},
            {"name": "candidate", "type": candidate_schema},
            {
                "name": "prv_candidates",
                "type": {"type": "array", "items": "candidate"},
            },
            {"name": "cutoutScience", "type": cutout_schema},
            {"name": "cutoutTemplate", "type": "cutout"},
            {"name": "cutoutDifference", "type": "cutout"},
        ],
    }
    candidate = {
        "candid": candid,
        "jd": 2459995.5,
        "ra": 120.0,
        "dec": -5.0,
        "drb": 0.5,
        "isdiffpos": "t",
        "nbad": 0,
    }
    record = {
        "objectId": object_id,
        "candid": candid,
        "candidate": candidate,
        "prv_candidates": [candidate],
        "cutoutScience": {"stampData": b"science"},
        "cutoutTemplate": {"stampData": b"template"},
        "cutoutDifference": {"stampData": b"difference"},
    }
    buffer = io.BytesIO()
    writer(buffer, schema, [record])
    return buffer.getvalue()


def tar_payload(members: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for name, payload in members.items():
            member = tarfile.TarInfo(name)
            member.size = len(payload)
            archive.addfile(member, io.BytesIO(payload))
    return buffer.getvalue()


def test_manifest_parsing(tmp_path):
    path = tmp_path / "manifest.json"
    write_manifest(path, manifest_data())

    manifest = load_manifest(path)

    assert str(manifest.source_url) == (
        "https://example.test/ztf_public_20230220.tar.gz"
    )
    assert manifest.observing_date.isoformat() == "2023-02-20"
    assert manifest.archive.sha256 == SHA256_EMPTY


def test_manifest_rejects_invalid_checksum(tmp_path):
    path = tmp_path / "manifest.json"
    archive = manifest_data()["archive"] | {"sha256": "not-a-checksum"}
    write_manifest(path, manifest_data(archive=archive))

    with pytest.raises(ValidationError, match="sha256"):
        load_manifest(path)


def test_checksum_mismatch_does_not_download(tmp_path):
    archive_path = tmp_path / "archives" / "night.tar.gz"
    archive_path.parent.mkdir()
    archive_path.write_bytes(b"wrong")
    manifest = ArchiveManifest.model_validate(
        manifest_data(
            archive={
                "path": "archives/night.tar.gz",
                "byte_size": 5,
                "sha256": SHA256_EMPTY,
            }
        )
    )
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(500)

    with (
        httpx.Client(transport=httpx.MockTransport(handler)) as client,
        pytest.raises(ArchiveIntegrityError, match="checksum mismatch"),
    ):
        ensure_archive(manifest, tmp_path, client)

    assert requests == []


def test_verified_local_archive_is_not_downloaded(tmp_path):
    archive_payload = b"verified"
    archive_path = tmp_path / "archives" / "night.tar.gz"
    archive_path.parent.mkdir()
    archive_path.write_bytes(archive_payload)
    manifest = ArchiveManifest.model_validate(
        manifest_data(
            archive={
                "path": "archives/night.tar.gz",
                "byte_size": len(archive_payload),
                "sha256": hashlib.sha256(archive_payload).hexdigest(),
            }
        )
    )
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(500)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        path, downloaded = ensure_archive(manifest, tmp_path, client)

    assert path == archive_path
    assert downloaded is False
    assert requests == []


def test_unreadable_member_is_reported_and_payload_is_preserved(tmp_path):
    valid_payload = avro_payload()
    archive_path = tmp_path / "night.tar.gz"
    archive_path.write_bytes(
        tar_payload({"valid.avro": valid_payload, "broken.avro": b"not avro"})
    )
    parquet_path = tmp_path / "night.parquet"

    inspection = inspect_and_extract(archive_path, parquet_path, 1)

    assert inspection == Inspection(
        member_count=2,
        readable_avro_count=1,
        alert_count=1,
        unique_object_count=1,
        corrupt_member_count=1,
        eligibility="eligible",
    )
    row = pq.read_table(parquet_path).to_pylist()[0]
    assert row["object_id"] == "ZTFTEST"
    assert row["isdiffpos"] is True
    assert row["cutout_science"] == b"science"
    assert row["cutout_template"] == b"template"
    assert row["cutout_difference"] == b"difference"
    assert row["avro_payload"] == valid_payload


def test_eligibility_boundary_is_inclusive():
    assert classify_night(212, 213) == "skipped"
    assert classify_night(213, 213) == "eligible"


def test_verified_parquet_skips_download(tmp_path):
    manifest_path = tmp_path / "manifest.json"
    parquet_path = tmp_path / "parquet" / "night.parquet"
    parquet_path.parent.mkdir()
    parquet_path.write_bytes(b"verified parquet")
    parquet = Artifact(
        path="parquet/night.parquet",
        byte_size=parquet_path.stat().st_size,
        sha256=hashlib.sha256(parquet_path.read_bytes()).hexdigest(),
    )
    inspection = Inspection(
        member_count=1,
        readable_avro_count=1,
        alert_count=1,
        unique_object_count=1,
        corrupt_member_count=0,
        eligibility="eligible",
    )
    write_manifest(
        manifest_path,
        manifest_data(
            parquet=parquet.model_dump(mode="json"),
            inspection=inspection.model_dump(mode="json"),
        ),
    )
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(500)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = process_manifest(manifest_path, tmp_path, client)

    assert result.reused_parquet is True
    assert result.downloaded is False
    assert requests == []
