"""Download, inspect, and convert a ZTF nightly alert archive."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import tarfile
from collections.abc import Mapping
from datetime import date
from pathlib import Path
from typing import Literal

import httpx
import pyarrow as pa
import pyarrow.parquet as pq
from fastavro import reader
from pydantic import AnyHttpUrl, BaseModel, Field, field_validator

PARQUET_SCHEMA = pa.schema(
    [
        pa.field("object_id", pa.string()),
        pa.field("candid", pa.int64()),
        pa.field("jd", pa.float64()),
        pa.field("ra", pa.float64()),
        pa.field("dec", pa.float64()),
        pa.field("drb", pa.float64()),
        pa.field("isdiffpos", pa.bool_()),
        pa.field("nbad", pa.int64()),
        pa.field("broker_probabilities", pa.string()),
        pa.field("previous_candidates", pa.string()),
        pa.field("cutout_science", pa.binary()),
        pa.field("cutout_template", pa.binary()),
        pa.field("cutout_difference", pa.binary()),
        pa.field("avro_payload", pa.binary(), nullable=False),
    ]
)


class ArchiveIntegrityError(ValueError):
    """A local or downloaded artifact does not match its manifest."""


class Artifact(BaseModel):
    """A checksummed file stored outside Git."""

    path: str
    byte_size: int = Field(ge=0)
    sha256: str

    @field_validator("sha256")
    @classmethod
    def validate_sha256(cls, value: str) -> str:
        normalized = value.lower()
        if len(normalized) != 64 or any(
            c not in "0123456789abcdef" for c in normalized
        ):
            raise ValueError("sha256 must contain exactly 64 hexadecimal characters")
        return normalized


class Inspection(BaseModel):
    """Observed contents and eligibility of one nightly archive."""

    member_count: int = Field(ge=0)
    readable_avro_count: int = Field(ge=0)
    alert_count: int = Field(ge=0)
    unique_object_count: int = Field(ge=0)
    corrupt_member_count: int = Field(ge=0)
    eligibility: Literal["eligible", "skipped"]


class ArchiveManifest(BaseModel):
    """Reproducible inputs and outputs for one observing night."""

    schema_version: Literal[1]
    source_url: AnyHttpUrl
    observing_date: date
    minimum_night_alerts: int = Field(gt=0)
    threshold_rationale: str = Field(min_length=1)
    archive: Artifact
    parquet: Artifact | None = None
    inspection: Inspection | None = None


class ProcessResult(BaseModel):
    """Machine-readable result returned by the archive command."""

    downloaded: bool
    reused_parquet: bool
    parquet: Artifact
    inspection: Inspection


def load_manifest(path: Path) -> ArchiveManifest:
    """Parse and validate a committed archive manifest."""

    return ArchiveManifest.model_validate_json(path.read_text(encoding="utf-8"))


def save_manifest(path: Path, manifest: ArchiveManifest) -> None:
    """Atomically persist a manifest after its Parquet checksum is known."""

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    temporary.write_text(
        json.dumps(manifest.model_dump(mode="json"), indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def sha256_file(path: Path) -> str:
    """Return the SHA-256 digest of a file without loading it into memory."""

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def classify_night(
    alert_count: int, minimum_night_alerts: int
) -> Literal["eligible", "skipped"]:
    """Apply the inclusive minimum-night boundary."""

    return "eligible" if alert_count >= minimum_night_alerts else "skipped"


def _artifact_path(data_root: Path, relative_path: str) -> Path:
    relative = Path(relative_path)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError(f"artifact path must stay below data root: {relative_path}")
    return data_root / relative


def _verify_artifact(path: Path, artifact: Artifact) -> None:
    actual_size = path.stat().st_size
    if actual_size != artifact.byte_size:
        raise ArchiveIntegrityError(
            f"byte-size mismatch for {path}: expected {artifact.byte_size}, "
            f"found {actual_size}"
        )
    actual_sha256 = sha256_file(path)
    if actual_sha256 != artifact.sha256:
        raise ArchiveIntegrityError(
            f"checksum mismatch for {path}: expected {artifact.sha256}, "
            f"found {actual_sha256}"
        )


def ensure_archive(
    manifest: ArchiveManifest,
    data_root: Path,
    client: httpx.Client | None = None,
) -> tuple[Path, bool]:
    """Return a verified archive, downloading only when it is absent."""

    archive_path = _artifact_path(data_root, manifest.archive.path)
    if archive_path.exists():
        _verify_artifact(archive_path, manifest.archive)
        return archive_path, False

    archive_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = archive_path.with_suffix(f"{archive_path.suffix}.part")
    owns_client = client is None
    download_client = client or httpx.Client(follow_redirects=True, timeout=None)
    try:
        digest = hashlib.sha256()
        byte_size = 0
        with download_client.stream("GET", str(manifest.source_url)) as response:
            response.raise_for_status()
            with temporary.open("wb") as handle:
                for chunk in response.iter_bytes():
                    handle.write(chunk)
                    digest.update(chunk)
                    byte_size += len(chunk)
        if byte_size != manifest.archive.byte_size:
            raise ArchiveIntegrityError(
                f"downloaded byte-size mismatch: expected {manifest.archive.byte_size}, "
                f"found {byte_size}"
            )
        if digest.hexdigest() != manifest.archive.sha256:
            raise ArchiveIntegrityError(
                "downloaded checksum mismatch: "
                f"expected {manifest.archive.sha256}, found {digest.hexdigest()}"
            )
        os.replace(temporary, archive_path)
        return archive_path, True
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    finally:
        if owns_client:
            download_client.close()


def _int_or_none(value: object) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _float_or_none(value: object) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _difference_is_positive(value: object) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return bool(value) if value in (0, 1) else None
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"t", "true", "1"}:
            return True
        if normalized in {"f", "false", "0"}:
            return False
    return None


def _json_default(value: object) -> object:
    if isinstance(value, bytes):
        return {"__bytes_hex__": value.hex()}
    raise TypeError(f"cannot encode {type(value).__name__} as JSON")


def _json_value(value: object) -> str | None:
    if value is None:
        return None
    return json.dumps(
        value,
        default=_json_default,
        separators=(",", ":"),
        sort_keys=True,
    )


def _cutout_bytes(record: Mapping[str, object], name: str) -> bytes | None:
    cutout = record.get(name)
    if not isinstance(cutout, Mapping):
        return None
    stamp_data = cutout.get("stampData")
    return stamp_data if isinstance(stamp_data, bytes) else None


def _parquet_row(record: Mapping[str, object], payload: bytes) -> dict[str, object]:
    candidate_value = record.get("candidate")
    candidate = candidate_value if isinstance(candidate_value, Mapping) else {}
    broker_probabilities = record.get("broker_probabilities")
    if broker_probabilities is None:
        broker_probabilities = candidate.get("broker_probabilities")
    candid = record.get("candid", candidate.get("candid"))
    object_id = record.get("objectId")
    return {
        "object_id": str(object_id) if object_id is not None else None,
        "candid": _int_or_none(candid),
        "jd": _float_or_none(candidate.get("jd")),
        "ra": _float_or_none(candidate.get("ra")),
        "dec": _float_or_none(candidate.get("dec")),
        "drb": _float_or_none(candidate.get("drb")),
        "isdiffpos": _difference_is_positive(candidate.get("isdiffpos")),
        "nbad": _int_or_none(candidate.get("nbad")),
        "broker_probabilities": _json_value(broker_probabilities),
        "previous_candidates": _json_value(record.get("prv_candidates", [])),
        "cutout_science": _cutout_bytes(record, "cutoutScience"),
        "cutout_template": _cutout_bytes(record, "cutoutTemplate"),
        "cutout_difference": _cutout_bytes(record, "cutoutDifference"),
        "avro_payload": payload,
    }


def inspect_and_extract(
    archive_path: Path,
    parquet_path: Path,
    minimum_night_alerts: int,
    batch_size: int = 1_000,
) -> Inspection:
    """Stream an archive into Parquet and report readable and corrupt members."""

    parquet_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = parquet_path.with_suffix(f"{parquet_path.suffix}.part")
    member_count = 0
    readable_avro_count = 0
    alert_count = 0
    corrupt_member_count = 0
    object_ids: set[str] = set()
    rows: list[dict[str, object]] = []

    writer = pq.ParquetWriter(temporary, PARQUET_SCHEMA, compression="zstd")
    try:
        with tarfile.open(archive_path, "r|gz") as archive:
            for member in archive:
                member_count += 1
                if not member.isfile() or not member.name.lower().endswith(".avro"):
                    continue
                try:
                    member_file = archive.extractfile(member)
                    if member_file is None:
                        raise ValueError("tar member has no readable content")
                    payload = member_file.read()
                    records = list(reader(io.BytesIO(payload)))
                    member_rows = [_parquet_row(record, payload) for record in records]
                # Each archive member is untrusted input. Any decode or projection
                # failure marks only that member corrupt so the inspection can finish.
                except Exception:  # noqa: BLE001
                    corrupt_member_count += 1
                    continue

                readable_avro_count += 1
                alert_count += len(member_rows)
                for row in member_rows:
                    object_id = row["object_id"]
                    if isinstance(object_id, str):
                        object_ids.add(object_id)
                rows.extend(member_rows)
                if len(rows) >= batch_size:
                    writer.write_table(
                        pa.Table.from_pylist(rows, schema=PARQUET_SCHEMA)
                    )
                    rows.clear()

        if rows:
            writer.write_table(pa.Table.from_pylist(rows, schema=PARQUET_SCHEMA))
    except Exception:
        writer.close()
        temporary.unlink(missing_ok=True)
        raise
    else:
        writer.close()
        os.replace(temporary, parquet_path)

    return Inspection(
        member_count=member_count,
        readable_avro_count=readable_avro_count,
        alert_count=alert_count,
        unique_object_count=len(object_ids),
        corrupt_member_count=corrupt_member_count,
        eligibility=classify_night(alert_count, minimum_night_alerts),
    )


def process_manifest(
    manifest_path: Path,
    data_root: Path,
    client: httpx.Client | None = None,
) -> ProcessResult:
    """Materialize and verify the Parquet artifact described by a manifest."""

    manifest = load_manifest(manifest_path)
    if manifest.parquet is not None:
        parquet_path = _artifact_path(data_root, manifest.parquet.path)
        if parquet_path.exists():
            _verify_artifact(parquet_path, manifest.parquet)
            if manifest.inspection is None:
                raise ValueError("manifest has a Parquet artifact but no inspection")
            return ProcessResult(
                downloaded=False,
                reused_parquet=True,
                parquet=manifest.parquet,
                inspection=manifest.inspection,
            )
    else:
        parquet_path = _artifact_path(
            data_root, f"parquet/ztf_public_{manifest.observing_date:%Y%m%d}.parquet"
        )

    archive_path, downloaded = ensure_archive(manifest, data_root, client)
    inspection = inspect_and_extract(
        archive_path, parquet_path, manifest.minimum_night_alerts
    )
    parquet = Artifact(
        path=str(parquet_path.relative_to(data_root)),
        byte_size=parquet_path.stat().st_size,
        sha256=sha256_file(parquet_path),
    )

    if manifest.parquet is not None and parquet != manifest.parquet:
        raise ArchiveIntegrityError(
            f"generated Parquet does not match manifest: expected {manifest.parquet}, "
            f"found {parquet}"
        )
    if manifest.inspection is not None and inspection != manifest.inspection:
        raise ArchiveIntegrityError(
            f"inspection does not match manifest: expected {manifest.inspection}, "
            f"found {inspection}"
        )

    if manifest.parquet is None or manifest.inspection is None:
        manifest = manifest.model_copy(
            update={"parquet": parquet, "inspection": inspection}
        )
        save_manifest(manifest_path, manifest)

    # Archive removal happens only after the output checksum is in the manifest.
    archive_path.unlink()
    return ProcessResult(
        downloaded=downloaded,
        reused_parquet=False,
        parquet=parquet,
        inspection=inspection,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path("data/ztf_archive_manifest.json"),
    )
    parser.add_argument("--data-root", type=Path, default=Path("data/local"))
    args = parser.parse_args(argv)
    result = process_manifest(args.manifest, args.data_root)
    print(result.model_dump_json(indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
