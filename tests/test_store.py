import hashlib
import shutil
import subprocess
import tempfile
from collections.abc import Iterator
from datetime import date
from pathlib import Path

import psycopg
import pytest
from psycopg import sql

from vetter.ingest.alerts import (
    RealBogusCut,
    parse_alert,
    select_nightly_candidates,
)
from vetter.ingest.archive import ArchiveManifest
from vetter.store.postgres import (
    apply_migrations,
    get_candidates_for_night,
    load_night,
)

NIGHT = date(2023, 2, 20)
NIGHT_JD = 2_459_995.5
CUT = RealBogusCut(drb_min=0.5, isdiffpos=True, nbad_max=0)


@pytest.fixture(scope="session")
def postgres_server() -> Iterator[dict[str, str]]:
    initdb = shutil.which("initdb")
    pg_ctl = shutil.which("pg_ctl")
    if initdb is None or pg_ctl is None:
        pytest.skip("Postgres server binaries are required for store tests")

    root = Path(tempfile.mkdtemp(prefix="vetter-pg-", dir="/tmp"))
    data_dir = root / "data"
    socket_dir = root / "socket"
    socket_dir.mkdir()
    started = False
    try:
        subprocess.run(
            [
                initdb,
                "-D",
                str(data_dir),
                "--username=postgres",
                "--auth=trust",
                "--no-locale",
                "--encoding=UTF8",
                "--no-sync",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        subprocess.run(
            [
                pg_ctl,
                "-D",
                str(data_dir),
                "-l",
                str(root / "postgres.log"),
                "-o",
                f"-F -k {socket_dir} -h ''",
                "-w",
                "start",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        started = True
        yield {"host": str(socket_dir), "user": "postgres"}
    finally:
        if started:
            subprocess.run(
                [pg_ctl, "-D", str(data_dir), "-m", "immediate", "-w", "stop"],
                check=True,
                capture_output=True,
                text=True,
            )
        shutil.rmtree(root)


@pytest.fixture
def postgres_connection(postgres_server, request):
    database = f"test_{request.node.name.replace('[', '_').replace(']', '_')}"
    with psycopg.connect(
        **postgres_server, dbname="postgres", autocommit=True
    ) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database)))
    connection = psycopg.connect(**postgres_server, dbname=database, autocommit=True)
    try:
        yield connection
    finally:
        connection.close()
        with psycopg.connect(
            **postgres_server, dbname="postgres", autocommit=True
        ) as admin:
            admin.execute(
                sql.SQL("DROP DATABASE {} WITH (FORCE)").format(
                    sql.Identifier(database)
                )
            )


def manifest() -> ArchiveManifest:
    return ArchiveManifest.model_validate(
        {
            "schema_version": 1,
            "source_url": "https://example.test/night.tar.gz",
            "observing_date": NIGHT.isoformat(),
            "minimum_night_alerts": 1,
            "threshold_rationale": "Test threshold.",
            "archive": {
                "path": "archives/night.tar.gz",
                "byte_size": 10,
                "sha256": "a" * 64,
            },
            "parquet": {
                "path": "parquet/night.parquet",
                "byte_size": 20,
                "sha256": "b" * 64,
            },
            "inspection": {
                "member_count": 2,
                "readable_avro_count": 2,
                "alert_count": 2,
                "unique_object_count": 2,
                "corrupt_member_count": 0,
                "eligibility": "eligible",
            },
        }
    )


def alert(object_id: str, candid: int, ra: float):
    candidate = {
        "candid": candid,
        "jd": NIGHT_JD,
        "ra": ra,
        "dec": -5.0,
        "drb": 0.5,
        "isdiffpos": "t",
        "nbad": 0,
        "broker_probabilities": {"stamp": 0.75},
    }
    return parse_alert(
        {
            "objectId": object_id,
            "candid": candid,
            "candidate": candidate,
            "prv_candidates": [],
        },
        f"payload-{candid}".encode(),
    )


def records():
    alerts = (alert("ZTFB", 2, 121.0), alert("ZTFA", 1, 120.0))
    candidates = select_nightly_candidates(alerts, NIGHT, CUT)
    return alerts, candidates


def table_counts(connection) -> tuple[int, int, int, int]:
    return tuple(
        connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
        for table in ("nights", "alerts", "candidates", "processing_runs")
    )


def test_migrations_apply_to_empty_postgres_and_record_versions(
    postgres_connection,
):
    assert apply_migrations(postgres_connection) == (1,)
    assert apply_migrations(postgres_connection) == ()
    assert postgres_connection.execute(
        "SELECT version, name FROM schema_migrations ORDER BY version"
    ).fetchall() == [(1, "001_phase0_store.sql")]


def test_loading_same_night_twice_is_idempotent(postgres_connection):
    apply_migrations(postgres_connection)
    alerts, candidates = records()

    load_night(postgres_connection, manifest(), alerts, candidates, "run-1")
    counts_after_first_load = table_counts(postgres_connection)
    payload_after_first_load = postgres_connection.execute(
        "SELECT original_payload, payload_sha256 FROM alerts WHERE candid = 1"
    ).fetchone()
    load_night(postgres_connection, manifest(), alerts, candidates, "run-1")

    assert table_counts(postgres_connection) == counts_after_first_load
    assert (
        postgres_connection.execute(
            "SELECT original_payload, payload_sha256 FROM alerts WHERE candid = 1"
        ).fetchone()
        == payload_after_first_load
    )
    assert payload_after_first_load == (
        b"payload-1",
        hashlib.sha256(b"payload-1").hexdigest(),
    )


def test_failed_load_rolls_back_and_records_failure(postgres_connection):
    apply_migrations(postgres_connection)
    alerts, candidates = records()
    invalid_candidate = candidates[0].model_copy(update={"candid": 999})

    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        load_night(
            postgres_connection,
            manifest(),
            alerts,
            (invalid_candidate,),
            "failed-run",
        )

    assert postgres_connection.execute("SELECT count(*) FROM alerts").fetchone()[0] == 0
    assert (
        postgres_connection.execute("SELECT count(*) FROM candidates").fetchone()[0]
        == 0
    )
    assert postgres_connection.execute(
        "SELECT status FROM nights WHERE observing_date = %s", (NIGHT,)
    ).fetchone() == ("failed",)
    assert postgres_connection.execute(
        "SELECT status FROM processing_runs WHERE run_id = 'failed-run'"
    ).fetchone() == ("failed",)


def test_required_payload_constraint_is_enforced(postgres_connection):
    apply_migrations(postgres_connection)
    load_night(postgres_connection, manifest(), (), (), "empty-run")

    with (
        pytest.raises(psycopg.errors.NotNullViolation),
        postgres_connection.transaction(),
    ):
        postgres_connection.execute(
            """
            INSERT INTO alerts (
                candid, observing_date, object_id, observation_jd, ra, dec,
                payload_sha256
            )
            VALUES (1, %s, 'ZTFTEST', %s, 120.0, -5.0, %s)
            """,
            (NIGHT, NIGHT_JD, "a" * 64),
        )


def test_candidate_query_returns_exact_deterministic_universe(postgres_connection):
    apply_migrations(postgres_connection)
    alerts, candidates = records()
    load_night(
        postgres_connection,
        manifest(),
        alerts,
        tuple(reversed(candidates)),
        "query-run",
    )

    stored = get_candidates_for_night(postgres_connection, NIGHT)

    assert [candidate.object_id for candidate in stored] == ["ZTFA", "ZTFB"]
    assert [candidate.candid for candidate in stored] == [1, 2]
    assert stored[0].broker_probabilities == {"stamp": 0.75}
    assert stored[0].eligible_history == candidates[0].eligible_history
