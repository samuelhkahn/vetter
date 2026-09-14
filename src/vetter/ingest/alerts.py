"""Pure parsing and nightly candidate selection for ZTF alerts."""

from __future__ import annotations

import io
import math
from collections.abc import Iterable, Mapping
from datetime import UTC, date, datetime
from pathlib import Path

import yaml
from fastavro import reader
from pydantic import BaseModel, ConfigDict, Field

UNIX_EPOCH_JD = 2_440_587.5


class AlertParseError(ValueError):
    """An alert cannot be converted into the internal record contract."""


class RealBogusCut(BaseModel):
    """The deterministic real/bogus thresholds from release.yaml."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    drb_min: float = Field(ge=0.0, le=1.0)
    isdiffpos: bool
    nbad_max: int = Field(ge=0)


class DetectionRecord(BaseModel):
    """Fields required to validate and filter one alert detection."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    candid: int = Field(gt=0)
    jd: float = Field(gt=0.0)
    observing_date: date
    ra: float = Field(ge=0.0, lt=360.0)
    dec: float = Field(ge=-90.0, le=90.0)
    drb: float | None = Field(default=None, ge=0.0, le=1.0)
    isdiffpos: bool | None = None
    nbad: int | None = Field(default=None, ge=0)
    broker_probabilities: dict[str, float] | None = None


class AlertRecord(BaseModel):
    """A validated current alert plus its prior detection history."""

    model_config = ConfigDict(frozen=True)

    object_id: str = Field(min_length=1)
    detection: DetectionRecord
    history: tuple[DetectionRecord, ...] = ()
    original_payload: bytes

    @property
    def candid(self) -> int:
        return self.detection.candid

    @property
    def observation_jd(self) -> float:
        return self.detection.jd

    @property
    def observing_date(self) -> date:
        return self.detection.observing_date

    @property
    def ra(self) -> float:
        return self.detection.ra

    @property
    def dec(self) -> float:
        return self.detection.dec

    @property
    def broker_probabilities(self) -> dict[str, float] | None:
        return self.detection.broker_probabilities


class NightlyCandidate(BaseModel):
    """One unique object eligible for consideration on a replayed night."""

    model_config = ConfigDict(frozen=True)

    object_id: str
    candid: int
    observation_jd: float
    observing_date: date
    ra: float
    dec: float
    broker_probabilities: dict[str, float] | None
    eligible_history: tuple[DetectionRecord, ...]
    original_payloads: tuple[bytes, ...]


def load_real_bogus_cut(path: Path = Path("release.yaml")) -> RealBogusCut:
    """Load only the real/bogus section of the owner-controlled release file."""

    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, Mapping) or not isinstance(
        data.get("real_bogus_cut"), Mapping
    ):
        raise AlertParseError(f"{path} has no real_bogus_cut mapping")
    return RealBogusCut.model_validate(data["real_bogus_cut"])


def julian_date_to_date(jd: float) -> date:
    """Convert a Julian date to its UTC calendar date."""

    if not math.isfinite(jd):
        raise AlertParseError("jd must be finite")
    try:
        return datetime.fromtimestamp((jd - UNIX_EPOCH_JD) * 86_400, UTC).date()
    except (OverflowError, OSError, ValueError) as error:
        raise AlertParseError(
            f"jd is outside the supported date range: {jd}"
        ) from error


def _required(mapping: Mapping[str, object], name: str, context: str) -> object:
    if name not in mapping or mapping[name] is None:
        raise AlertParseError(f"{context} is missing required field {name!r}")
    return mapping[name]


def _isdiffpos(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"t", "true", "1"}:
            return True
        if normalized in {"f", "false", "0"}:
            return False
    raise AlertParseError(f"invalid isdiffpos value: {value!r}")


def _broker_probabilities(
    alert: Mapping[str, object], candidate: Mapping[str, object]
) -> dict[str, float] | None:
    probabilities = alert.get("broker_probabilities")
    if probabilities is None:
        probabilities = candidate.get("broker_probabilities")
    if probabilities is None:
        return None
    if not isinstance(probabilities, Mapping):
        raise AlertParseError("broker_probabilities must be a mapping when present")

    parsed: dict[str, float] = {}
    for name, value in probabilities.items():
        try:
            probability = float(value)
        except (TypeError, ValueError) as error:
            raise AlertParseError(
                f"broker probability {name!r} must be numeric"
            ) from error
        if not math.isfinite(probability) or not 0.0 <= probability <= 1.0:
            raise AlertParseError(
                f"broker probability {name!r} must be between 0 and 1"
            )
        parsed[str(name)] = probability
    return parsed


def _parse_detection(
    candidate: Mapping[str, object],
    broker_probabilities: dict[str, float] | None = None,
    context: str = "candidate",
    require_cut_fields: bool = True,
) -> DetectionRecord:
    jd = float(_required(candidate, "jd", context))
    if require_cut_fields:
        drb = _required(candidate, "drb", context)
        isdiffpos = _isdiffpos(_required(candidate, "isdiffpos", context))
        nbad = _required(candidate, "nbad", context)
    else:
        drb = candidate.get("drb")
        isdiffpos_value = candidate.get("isdiffpos")
        isdiffpos = _isdiffpos(isdiffpos_value) if isdiffpos_value is not None else None
        nbad = candidate.get("nbad")
    return DetectionRecord(
        candid=_required(candidate, "candid", context),
        jd=jd,
        observing_date=julian_date_to_date(jd),
        ra=_required(candidate, "ra", context),
        dec=_required(candidate, "dec", context),
        drb=drb,
        isdiffpos=isdiffpos,
        nbad=nbad,
        broker_probabilities=broker_probabilities,
    )


def parse_alert(alert: Mapping[str, object], original_payload: bytes) -> AlertRecord:
    """Convert one decoded ZTF alert mapping into a validated typed record."""

    candidate_value = _required(alert, "candidate", "alert")
    if not isinstance(candidate_value, Mapping):
        raise AlertParseError("alert candidate must be a mapping")
    candidate = candidate_value

    current = dict(candidate)
    current.setdefault("candid", _required(alert, "candid", "alert"))
    probabilities = _broker_probabilities(alert, candidate)
    detection = _parse_detection(current, probabilities)

    history_value = alert.get("prv_candidates", [])
    if history_value is None:
        history_value = []
    if not isinstance(history_value, list):
        raise AlertParseError("prv_candidates must be a list when present")

    history: list[DetectionRecord] = []
    for index, previous in enumerate(history_value):
        if not isinstance(previous, Mapping):
            raise AlertParseError(f"prv_candidates[{index}] must be a mapping")
        # ZTF uses prior entries without a candid for non-detection upper limits.
        if previous.get("candid") is None:
            continue
        previous_probabilities = _broker_probabilities(previous, previous)
        history.append(
            _parse_detection(
                previous,
                previous_probabilities,
                context=f"prv_candidates[{index}]",
                require_cut_fields=False,
            )
        )

    return AlertRecord(
        object_id=_required(alert, "objectId", "alert"),
        detection=detection,
        history=tuple(history),
        original_payload=original_payload,
    )


def parse_avro_payload(payload: bytes) -> tuple[AlertRecord, ...]:
    """Decode every record in one Avro member without I/O or network access."""

    return tuple(parse_alert(alert, payload) for alert in reader(io.BytesIO(payload)))


def passes_real_bogus_cut(detection: DetectionRecord, cut: RealBogusCut) -> bool:
    """Return whether one detection passes the configured inclusive cut."""

    return (
        detection.drb is not None
        and detection.drb >= cut.drb_min
        and detection.isdiffpos is cut.isdiffpos
        and detection.nbad is not None
        and detection.nbad <= cut.nbad_max
    )


def select_nightly_candidates(
    alerts: Iterable[AlertRecord],
    replayed_night: date,
    cut: RealBogusCut,
) -> tuple[NightlyCandidate, ...]:
    """Filter detections before deterministically deduplicating nightly objects."""

    eligible_by_object: dict[str, list[AlertRecord]] = {}
    for alert in alerts:
        if alert.detection.observing_date == replayed_night and passes_real_bogus_cut(
            alert.detection, cut
        ):
            eligible_by_object.setdefault(alert.object_id, []).append(alert)

    candidates: list[NightlyCandidate] = []
    for object_id in sorted(eligible_by_object):
        object_alerts = sorted(
            eligible_by_object[object_id],
            key=lambda alert: (alert.detection.jd, alert.detection.candid),
        )
        detections_by_candid: dict[int, DetectionRecord] = {}
        for alert in object_alerts:
            for detection in (*alert.history, alert.detection):
                if detection.observing_date <= replayed_night and passes_real_bogus_cut(
                    detection, cut
                ):
                    detections_by_candid[detection.candid] = detection

        eligible_history = tuple(
            sorted(
                detections_by_candid.values(),
                key=lambda detection: (detection.jd, detection.candid),
            )
        )
        latest = eligible_history[-1]
        candidates.append(
            NightlyCandidate(
                object_id=object_id,
                candid=latest.candid,
                observation_jd=latest.jd,
                observing_date=latest.observing_date,
                ra=latest.ra,
                dec=latest.dec,
                broker_probabilities=latest.broker_probabilities,
                eligible_history=eligible_history,
                original_payloads=tuple(
                    alert.original_payload for alert in object_alerts
                ),
            )
        )
    return tuple(candidates)
