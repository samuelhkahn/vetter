# ZTF nightly archive contract

Task 01 uses the public 2023-02-20 UTC archive because it is the smallest
non-empty 2023 archive listed by the University of Washington service. The
committed manifest records its source, byte size, checksums, inspection, and
eligibility decision.

Run the repeatable download and extraction with:

```bash
uv run python -m vetter.ingest.archive
```

The command downloads only when the local Parquet output or tarball cannot be
verified. It streams each Avro member into one Parquet file, records the
Parquet checksum and inspection in the manifest, and only then deletes the
tarball. A verified Parquet file makes subsequent runs network-free.

## Minimum-night threshold

`N = 213` is the alert count observed in the smallest non-empty 2023 archive.
It is an empirical lower bound rather than an estimate of a typical night:
empty archive placeholders and smaller partial nights are skipped, while the
inspected night is eligible at the inclusive boundary. Task 02 can apply this
same recorded threshold before selecting the remaining nights.

## Parquet fields

Each alert row contains `object_id`, `candid`, `jd`, `ra`, `dec`, `drb`,
`isdiffpos`, `nbad`, optional broker probabilities, previous candidates, the
science/template/difference cutout bytes, and the exact original Avro member
bytes in `avro_payload`.
