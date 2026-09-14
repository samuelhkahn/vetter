# Phase 0 Postgres store

Numbered SQL migrations live under `src/vetter/store/migrations/`. The
`apply_migrations` function applies each pending file in order and records its
version in `schema_migrations`. Reapplying migrations is a no-op.

`load_night` takes an autocommit psycopg connection, the committed night
manifest, parsed alerts, selected candidates, and a stable run id. The night,
alerts, candidates, and successful run transition commit together. A database
failure rolls that transaction back, then records the failed run and a
non-complete night. Repeating the same load and run id verifies existing
content without changing row counts or payload bytes.

Alerts are unique by candid. Candidates are unique by observing date and
object id. A conflicting reuse of either identity raises an error instead of
silently replacing durable data. `get_candidates_for_night` returns candidates
in object-id order.

The tests create an ephemeral Postgres cluster using local `initdb` and
`pg_ctl`, listen only on a temporary Unix socket, and remove it after the test
session. If Postgres server binaries are unavailable, the store integration
tests are skipped; no test makes an external network request.
