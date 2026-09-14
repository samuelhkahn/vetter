from datetime import date

import pytest
from pydantic import ValidationError

from vetter.ingest.alerts import (
    AlertParseError,
    RealBogusCut,
    load_real_bogus_cut,
    parse_alert,
    passes_real_bogus_cut,
    select_nightly_candidates,
)

NIGHT = date(2023, 2, 20)
NIGHT_JD = 2_459_995.5
CUT = RealBogusCut(drb_min=0.5, isdiffpos=True, nbad_max=0)


def candidate(
    candid=1,
    jd=NIGHT_JD,
    ra=120.0,
    dec=-5.0,
    drb=0.5,
    isdiffpos="t",
    nbad=0,
    **extra,
):
    return {
        "candid": candid,
        "jd": jd,
        "ra": ra,
        "dec": dec,
        "drb": drb,
        "isdiffpos": isdiffpos,
        "nbad": nbad,
        **extra,
    }


def alert(object_id="ZTFTEST", current=None, history=None, **extra):
    current = current or candidate()
    return {
        "objectId": object_id,
        "candid": current["candid"],
        "candidate": current,
        "prv_candidates": history or [],
        **extra,
    }


def test_valid_alert_parses_to_typed_record():
    payload = b"original avro bytes"
    record = parse_alert(
        alert(broker_probabilities={"alerce_stamp": 0.8, "alerce_lc": 0.6}),
        payload,
    )

    assert record.object_id == "ZTFTEST"
    assert record.candid == 1
    assert record.observing_date == NIGHT
    assert record.ra == 120.0
    assert record.dec == -5.0
    assert record.broker_probabilities == {
        "alerce_stamp": 0.8,
        "alerce_lc": 0.6,
    }
    assert record.original_payload == payload


def test_cut_is_loaded_from_release_and_boundary_is_inclusive():
    cut = load_real_bogus_cut()
    at_boundary = parse_alert(alert(), b"payload").detection

    assert cut == CUT
    assert passes_real_bogus_cut(at_boundary, cut) is True
    assert (
        passes_real_bogus_cut(at_boundary.model_copy(update={"drb": 0.499999}), cut)
        is False
    )
    assert (
        passes_real_bogus_cut(at_boundary.model_copy(update={"isdiffpos": False}), cut)
        is False
    )
    assert (
        passes_real_bogus_cut(at_boundary.model_copy(update={"nbad": 1}), cut) is False
    )


def test_duplicate_objects_yield_one_candidate_with_eligible_history():
    first = parse_alert(
        alert(
            current=candidate(candid=2, jd=NIGHT_JD + 0.2),
            history=[candidate(candid=1, jd=NIGHT_JD - 1)],
        ),
        b"payload-2",
    )
    second = parse_alert(
        alert(
            current=candidate(candid=3, jd=NIGHT_JD + 0.4),
            history=[
                candidate(candid=1, jd=NIGHT_JD - 1),
                candidate(candid=2, jd=NIGHT_JD + 0.2),
            ],
        ),
        b"payload-3",
    )

    candidates = select_nightly_candidates([second, first], NIGHT, CUT)

    assert len(candidates) == 1
    assert candidates[0].object_id == "ZTFTEST"
    assert candidates[0].candid == 3
    assert [item.candid for item in candidates[0].eligible_history] == [1, 2, 3]
    assert candidates[0].original_payloads == (b"payload-2", b"payload-3")


def test_cut_is_applied_before_object_deduplication():
    rejected = parse_alert(alert(current=candidate(candid=1, drb=0.49)), b"rejected")
    accepted = parse_alert(alert(current=candidate(candid=2, drb=0.5)), b"accepted")

    candidates = select_nightly_candidates([rejected, accepted], NIGHT, CUT)

    assert len(candidates) == 1
    assert candidates[0].candid == 2
    assert candidates[0].original_payloads == (b"accepted",)


def test_missing_optional_broker_probabilities_is_valid():
    record = parse_alert(alert(), b"payload")

    assert record.detection.broker_probabilities is None


def test_prior_detection_with_missing_drb_is_valid_but_ineligible():
    prior = candidate(candid=1, jd=NIGHT_JD - 1, drb=None)
    record = parse_alert(
        alert(current=candidate(candid=2), history=[prior]), b"payload"
    )

    assert record.history[0].drb is None
    assert passes_real_bogus_cut(record.history[0], CUT) is False


@pytest.mark.parametrize(
    ("ra", "dec"),
    [(-0.1, 0.0), (360.0, 0.0), (0.0, -90.1), (0.0, 90.1)],
)
def test_invalid_coordinates_are_explicit_validation_failures(ra, dec):
    with pytest.raises(ValidationError):
        parse_alert(alert(current=candidate(ra=ra, dec=dec)), b"payload")


def test_missing_required_field_is_an_explicit_failure():
    malformed = candidate()
    del malformed["jd"]

    with pytest.raises(AlertParseError, match="missing required field 'jd'"):
        parse_alert(alert(current=malformed), b"payload")


def test_future_history_is_excluded():
    record = parse_alert(
        alert(
            current=candidate(candid=2),
            history=[
                candidate(candid=1, jd=NIGHT_JD - 1),
                candidate(candid=3, jd=NIGHT_JD + 1),
            ],
        ),
        b"payload",
    )

    candidates = select_nightly_candidates([record], NIGHT, CUT)

    assert [item.candid for item in candidates[0].eligible_history] == [1, 2]


def test_candidate_order_is_deterministic():
    later_id = parse_alert(alert(object_id="ZTFB"), b"b")
    earlier_id = parse_alert(alert(object_id="ZTFA"), b"a")

    candidates = select_nightly_candidates([later_id, earlier_id], NIGHT, CUT)

    assert [item.object_id for item in candidates] == ["ZTFA", "ZTFB"]
