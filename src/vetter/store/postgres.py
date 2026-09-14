"""Migration, load, and query functions for the Phase 0 Postgres store."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable
from datetime import date
from pathlib import Path

import psycopg
from psycopg import Connection
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict

from vetter.ingest.alerts import AlertRecord, DetectionRecord, NightlyCandidate
from vetter.ingest.archive import ArchiveManifest

MIGRATIONS_DIR = Path(__file__).with_name("migrations")
MIGRATION_NAME = re.compile(r"^(?P<version>[0-9]{3})_[a-z0-9_]+\.sql$")


class StoreConflictError(ValueError):
    """A stable database identity conflicts with different stored content."""


class StoredCandidate(BaseModel):
    """The persisted candidate representation returned by nightly queries."""

    model_config = ConfigDict(frozen=True)

    observing_date: date
    object_id: str
    candid: int
    observation_jd: float
    ra: float
    dec: float
    broker_probabilities: dict[str, float] | None
    eligible_history: tuple[DetectionRecord, ...]


def apply_migrations(
    connection: Connection,
    migrations_dir: Path = MIGRATIONS_DIR,
) -> tuple[int, ...]:
    """Apply pending numbered SQL files and record each version atomically."""

    migration_files: list[tuple[int, Path]] = []
    seen_versions: set[int] = set()
    for path in sorted(migrations_dir.glob("*.sql")):
        match = MIGRATION_NAME.fullmatch(path.name)
        if match is None:
            raise ValueError(f"invalid migration filename: {path.name}")
        version = int(match.group("version"))
        if version in seen_versions:
            raise ValueError(f"duplicate migration version: {version}")
        seen_versions.add(version)
        migration_files.append((version, path))

    applied_now: list[int] = []
    with connection.transaction():
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version INTEGER PRIMARY KEY,
                name TEXT NOT NULL UNIQUE,
                applied_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        applied = {
            row[0]
            for row in connection.execute(
                "SELECT version FROM schema_migrations"
            ).fetchall()
        }
        for version, path in migration_files:
            if version in applied:
                continue
            connection.execute(path.read_text(encoding="utf-8"), prepare=False)
            connection.execute(
                "INSERT INTO schema_migrations (version, name) VALUES (%s, %s)",
                (version, path.name),
            )
            applied_now.append(version)
    return tuple(applied_now)


def _manifest_values(manifest: ArchiveManifest) -> tuple[object, ...]:
    if manifest.parquet is None:
        raise ValueError("night manifest must contain a verified Parquet artifact")
    return (
        manifest.observing_date,
        str(manifest.source_url),
        manifest.archive.byte_size,
        manifest.archive.sha256,
        manifest.parquet.path,
        manifest.parquet.byte_size,
        manifest.parquet.sha256,
        manifest.minimum_night_alerts,
        Jsonb(manifest.model_dump(mode="json")),
    )


def _insert_or_verify_night(
    connection: Connection,
    manifest: ArchiveManifest,
    status: str,
) -> None:
    values = _manifest_values(manifest)
    connection.execute(
        """
        INSERT INTO nights (
            observing_date, source_url, archive_byte_size, archive_sha256,
            parquet_path, parquet_byte_size, parquet_sha256,
            minimum_night_alerts, manifest, status
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (observing_date) DO NOTHING
        """,
        (*values, status),
    )
    stored = connection.execute(
        "SELECT manifest FROM nights WHERE observing_date = %s",
        (manifest.observing_date,),
    ).fetchone()
    if stored is None or stored[0] != manifest.model_dump(mode="json"):
        raise StoreConflictError(
            f"night {manifest.observing_date} already has a different manifest"
        )


def _begin_run(
    connection: Connection,
    run_id: str,
    observing_date: date,
) -> None:
    connection.execute(
        """
        INSERT INTO processing_runs (run_id, observing_date, status)
        VALUES (%s, %s, 'running')
        ON CONFLICT (run_id) DO NOTHING
        """,
        (run_id, observing_date),
    )
    stored = connection.execute(
        "SELECT observing_date FROM processing_runs WHERE run_id = %s", (run_id,)
    ).fetchone()
    if stored is None or stored[0] != observing_date:
        raise StoreConflictError(f"run id {run_id!r} belongs to a different night")
    connection.execute(
        """
        UPDATE processing_runs
        SET status = 'running', error = NULL, finished_at = NULL
        WHERE run_id = %s
        """,
        (run_id,),
    )


def _insert_or_verify_alert(
    connection: Connection,
    observing_date: date,
    alert: AlertRecord,
) -> None:
    payload_sha256 = hashlib.sha256(alert.original_payload).hexdigest()
    probabilities = alert.broker_probabilities
    connection.execute(
        """
        INSERT INTO alerts (
            candid, observing_date, object_id, observation_jd, ra, dec,
            broker_probabilities, original_payload, payload_sha256
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (candid) DO NOTHING
        """,
        (
            alert.candid,
            observing_date,
            alert.object_id,
            alert.observation_jd,
            alert.ra,
            alert.dec,
            Jsonb(probabilities) if probabilities is not None else None,
            alert.original_payload,
            payload_sha256,
        ),
    )
    stored = connection.execute(
        """
        SELECT observing_date, object_id, observation_jd, ra, dec,
               broker_probabilities, original_payload, payload_sha256
        FROM alerts
        WHERE candid = %s
        """,
        (alert.candid,),
    ).fetchone()
    expected = (
        observing_date,
        alert.object_id,
        alert.observation_jd,
        alert.ra,
        alert.dec,
        probabilities,
        alert.original_payload,
        payload_sha256,
    )
    if stored != expected:
        raise StoreConflictError(
            f"alert candid {alert.candid} conflicts with stored content"
        )


def _history_json(candidate: NightlyCandidate) -> list[dict[str, object]]:
    return [item.model_dump(mode="json") for item in candidate.eligible_history]


def _insert_or_verify_candidate(
    connection: Connection,
    observing_date: date,
    candidate: NightlyCandidate,
) -> None:
    probabilities = candidate.broker_probabilities
    history = _history_json(candidate)
    connection.execute(
        """
        INSERT INTO candidates (
            observing_date, object_id, candid, observation_jd, ra, dec,
            broker_probabilities, eligible_history
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (observing_date, object_id) DO NOTHING
        """,
        (
            observing_date,
            candidate.object_id,
            candidate.candid,
            candidate.observation_jd,
            candidate.ra,
            candidate.dec,
            Jsonb(probabilities) if probabilities is not None else None,
            Jsonb(history),
        ),
    )
    stored = connection.execute(
        """
        SELECT candid, observation_jd, ra, dec,
               broker_probabilities, eligible_history
        FROM candidates
        WHERE observing_date = %s AND object_id = %s
        """,
        (observing_date, candidate.object_id),
    ).fetchone()
    expected = (
        candidate.candid,
        candidate.observation_jd,
        candidate.ra,
        candidate.dec,
        probabilities,
        history,
    )
    if stored != expected:
        raise StoreConflictError(
            f"candidate {(observing_date, candidate.object_id)!r} "
            "conflicts with stored content"
        )


def _record_failure(
    connection: Connection,
    manifest: ArchiveManifest,
    run_id: str,
    error: Exception,
) -> None:
    with connection.transaction():
        values = _manifest_values(manifest)
        connection.execute(
            """
            INSERT INTO nights (
                observing_date, source_url, archive_byte_size, archive_sha256,
                parquet_path, parquet_byte_size, parquet_sha256,
                minimum_night_alerts, manifest, status
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'failed')
            ON CONFLICT (observing_date) DO NOTHING
            """,
            values,
        )
        connection.execute(
            """
            INSERT INTO processing_runs (
                run_id, observing_date, status, error, finished_at
            )
            VALUES (%s, %s, 'failed', %s, CURRENT_TIMESTAMP)
            ON CONFLICT (run_id) DO UPDATE
            SET status = 'failed', error = EXCLUDED.error,
                finished_at = EXCLUDED.finished_at
            WHERE processing_runs.status != 'succeeded'
            """,
            (run_id, manifest.observing_date, str(error)[:2_000]),
        )


def load_night(
    connection: Connection,
    manifest: ArchiveManifest,
    alerts: Iterable[AlertRecord],
    candidates: Iterable[NightlyCandidate],
    run_id: str,
) -> None:
    """Atomically load one night, recording a failed run after DB rollbacks."""

    if not run_id:
        raise ValueError("run_id must not be empty")
    try:
        with connection.transaction():
            _insert_or_verify_night(connection, manifest, "loading")
            connection.execute(
                """
                UPDATE nights SET status = 'loading', updated_at = CURRENT_TIMESTAMP
                WHERE observing_date = %s
                """,
                (manifest.observing_date,),
            )
            _begin_run(connection, run_id, manifest.observing_date)
            for alert in alerts:
                _insert_or_verify_alert(connection, manifest.observing_date, alert)
            for candidate in candidates:
                _insert_or_verify_candidate(
                    connection, manifest.observing_date, candidate
                )
            connection.execute(
                """
                UPDATE nights SET status = 'complete', updated_at = CURRENT_TIMESTAMP
                WHERE observing_date = %s
                """,
                (manifest.observing_date,),
            )
            connection.execute(
                """
                UPDATE processing_runs
                SET status = 'succeeded', error = NULL,
                    finished_at = CURRENT_TIMESTAMP
                WHERE run_id = %s
                """,
                (run_id,),
            )
    except (psycopg.Error, StoreConflictError) as error:
        _record_failure(connection, manifest, run_id, error)
        raise


def get_candidates_for_night(
    connection: Connection,
    observing_date: date,
) -> tuple[StoredCandidate, ...]:
    """Return the exact nightly candidate universe ordered by object id."""

    with connection.cursor(row_factory=dict_row) as cursor:
        cursor.execute(
            """
            SELECT observing_date, object_id, candid, observation_jd, ra, dec,
                   broker_probabilities, eligible_history
            FROM candidates
            WHERE observing_date = %s
            ORDER BY object_id, candid
            """,
            (observing_date,),
        )
        return tuple(StoredCandidate.model_validate(row) for row in cursor.fetchall())
