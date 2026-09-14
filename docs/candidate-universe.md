# Nightly candidate universe

Archived ZTF records are parsed into immutable typed records before selection.
Required candidate fields fail validation explicitly, including coordinates
outside `0 <= ra < 360` or `-90 <= dec <= 90`. Named broker probabilities are
kept in a separate optional mapping so downstream RL datasets can mask them
without removing other alert features.

The real/bogus cut is loaded from `release.yaml` and applied to detections
before objects are deduplicated. One candidate is returned per object that has
an eligible detection on the replayed UTC date. Its history is the union of
eligible detections from the source alerts, deduplicated by candid and sorted
by Julian date and candid. History later than the replayed date is excluded.
Candidates are sorted by object id, making selection independent of input
order.

Each candidate retains the original Avro payload for every eligible current
detection that contributed to it. Prior non-detection upper limits, identified
by a missing candid, are valid ZTF history entries but are not alert detections
and therefore do not enter the eligible history.
