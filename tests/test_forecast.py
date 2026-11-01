"""Tests für die reine Prognose-Logik (forecast.py), ohne Home Assistant.

``t`` in den API-Daten ist das Intervallende: ``t = 10:15`` beschreibt 10:00–10:15.
"""
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from custom_components.solarmanager.forecast import (
    SlotMeasurement,
    correction_factor,
    parse_forecast,
    slot_start,
)

UTC = timezone.utc
ZURICH = ZoneInfo("Europe/Zurich")


def _utc(hour: int, minute: int = 0, day: int = 1, month: int = 7) -> datetime:
    return datetime(2026, month, day, hour, minute, tzinfo=UTC)


def _payload(*entries: tuple[str, float, float | None, float | None]) -> dict:
    return {
        "data": [
            {"t": t, "pW": w, **({"pWmin": lo} if lo is not None else {}), **({"pWmax": hi} if hi is not None else {})}
            for t, w, lo, hi in entries
        ]
    }


def test_parse_reads_swagger_format_sorts_and_treats_t_as_interval_end():
    f = parse_forecast(
        _payload(
            ("2026-07-01T10:30:00.000Z", 2000, 1500, 2500),
            ("2026-07-01T10:15:00.000Z", 1000, 800, 1200),
        )
    )
    assert f is not None
    assert [(p.start, p.end) for p in f.points] == [
        (_utc(10), _utc(10, 15)),
        (_utc(10, 15), _utc(10, 30)),
    ]
    assert f.points[0].watts_min == 800
    assert f.points[0].watts_max == 1200


def test_running_quarter_hour_is_covered_right_after_fetch():
    """Echtbetrieb: Abruf um 12:08:56 liefert als ersten Punkt t=12:15 — das ist jetzt."""
    f = parse_forecast(
        _payload(
            ("2026-10-24T12:15:00Z", 658, 226, 1090),
            ("2026-10-24T12:30:00Z", 647, 219, 1076),
        )
    )
    now = datetime(2026, 10, 24, 12, 8, 56, tzinfo=UTC)
    assert f.power_at(now) == 658
    assert f.power_at(now, "max") == 1090


def test_parse_accepts_bare_list_and_skips_invalid_entries():
    f = parse_forecast(
        [
            {"t": "2026-07-01T10:00:00Z", "pW": 1000},
            {"t": "kaputt", "pW": 1},
            {"pW": 5},
            {"t": "2026-07-01T10:15:00Z", "pW": None},
            {"t": "2026-07-01T10:15:00Z", "pW": True},
            "kein dict",
        ]
    )
    assert f is not None
    assert len(f.points) == 1
    assert f.points[0].watts_min is None
    # Einzelner Punkt: Standarddauer von 15 Minuten vor t
    assert f.points[0].start == _utc(9, 45)


@pytest.mark.parametrize("payload", [None, {}, {"data": None}, {"data": []}, {"data": [{"x": 1}]}, "text"])
def test_parse_returns_none_without_usable_data(payload):
    assert parse_forecast(payload) is None


def test_power_at_uses_interval_containing_time_and_none_outside():
    f = parse_forecast(_payload(("2026-07-01T10:15:00Z", 1000, 800, 1200), ("2026-07-01T10:30:00Z", 2000, None, None)))
    assert f.power_at(_utc(10)) == 1000
    assert f.power_at(_utc(10, 14)) == 1000
    assert f.power_at(_utc(10, 15)) == 2000
    assert f.power_at(_utc(10, 5), "min") == 800
    assert f.power_at(_utc(10, 5), "max") == 1200
    # Fehlendes Band fällt auf den Mittelwert zurück
    assert f.power_at(_utc(10, 20), "max") == 2000
    assert f.power_at(_utc(9, 59)) is None
    assert f.power_at(_utc(10, 30)) is None


def test_energy_between_prorates_partial_intervals():
    f = parse_forecast(_payload(("2026-07-01T10:15:00Z", 1000, 400, None), ("2026-07-01T10:30:00Z", 2000, 1000, None)))
    # Volle Intervalle: 1000 W * 0.25 h + 2000 W * 0.25 h
    assert f.energy_between(_utc(0), _utc(23)) == pytest.approx(750)
    # Ab 10:10 → 5 min von 1000 W + 15 min von 2000 W
    assert f.energy_between(_utc(10, 10), _utc(11)) == pytest.approx(1000 * 5 / 60 + 500)
    assert f.energy_between(_utc(0), _utc(23), "min") == pytest.approx(100 + 250)
    assert f.energy_between(_utc(11), _utc(12)) == 0


def test_covers_and_points_between():
    f = parse_forecast(_payload(("2026-07-01T10:15:00Z", 1, None, None), ("2026-07-01T10:30:00Z", 2, None, None)))
    assert f.covers(_utc(10), _utc(10, 30))
    assert not f.covers(_utc(9, 45), _utc(10, 30))
    assert not f.covers(_utc(10), _utc(10, 45))
    assert [p.watts for p in f.points_between(_utc(10, 20), _utc(11))] == [2]


def test_peak_between_prefers_earlier_on_tie_and_respects_window():
    f = parse_forecast(
        _payload(
            ("2026-07-01T10:15:00Z", 3000, None, None),
            ("2026-07-01T10:30:00Z", 3000, None, None),
            ("2026-07-02T10:15:00Z", 9000, None, None),
        )
    )
    peak = f.peak_between(_utc(0), _utc(0, day=2))
    assert peak.start == _utc(10)
    assert peak.watts == 3000
    assert f.peak_between(_utc(0, day=3), _utc(0, day=4)) is None


def test_merged_with_keeps_todays_past_intervals_and_prefers_new_values():
    day_start = _utc(0)
    older = parse_forecast(
        _payload(
            ("2026-07-01T00:00:00Z", 5, None, None),  # 23:45–00:00 gestern → verworfen
            ("2026-07-01T10:15:00Z", 1000, None, None),
            ("2026-07-01T10:30:00Z", 1100, None, None),  # von neuer Reihe überdeckt
        )
    )
    newer = parse_forecast(_payload(("2026-07-01T10:30:00Z", 1500, None, None)))
    merged = newer.merged_with(older, day_start)
    assert [(p.start, p.watts) for p in merged.points] == [(_utc(10), 1000), (_utc(10, 15), 1500)]
    assert merged.points[0].end == _utc(10, 15)
    assert newer.merged_with(None, day_start) is newer


def test_wh_hours_splits_at_hour_boundaries_in_local_time():
    # Stundenraster: Punkt t=11:30 beschreibt 10:30–11:30 UTC mit 1200 W
    f = parse_forecast(_payload(("2026-07-01T11:30:00Z", 1200, None, None), ("2026-07-01T12:30:00Z", 0, None, None)))
    hours = f.wh_hours(ZURICH)
    # Je 600 Wh auf die Stunden 10:00 und 11:00 UTC (= 12:00/13:00 lokal);
    # das 0-W-Intervall 11:30–12:30 trägt 0 zu 11:00 und 12:00 bei
    assert hours == {_utc(10): pytest.approx(600), _utc(11): pytest.approx(600), _utc(12): 0}


def test_wh_hours_follows_local_half_hour_offsets():
    # Indien (UTC+5:30): lokale Stunden beginnen um :30 UTC
    f = parse_forecast(_payload(("2026-07-01T11:00:00Z", 1000, None, None), ("2026-07-01T12:00:00Z", 0, None, None)))
    hours = f.wh_hours(ZoneInfo("Asia/Kolkata"))
    assert hours[_utc(9, 30)] == pytest.approx(500)
    assert hours[_utc(10, 30)] == pytest.approx(500)


def test_wh_hours_keeps_both_hours_at_dst_fall_back():
    # 25.10.2026: 03:00 CEST → 02:00 CET. Die lokale Stunde 02:00 kommt zweimal vor.
    start = datetime(2026, 10, 25, 0, 0, tzinfo=UTC)  # = 02:00 CEST
    entries = [((start + timedelta(minutes=15 * (i + 1))).strftime("%Y-%m-%dT%H:%M:%SZ"), 400, None, None) for i in range(8)]
    hours = parse_forecast(_payload(*entries)).wh_hours(ZURICH)
    # Zwei getrennte Stunden (02:00 CEST und 02:00 CET) mit je 4 × 400 W × 0.25 h
    assert hours == {
        datetime(2026, 10, 25, 0, tzinfo=UTC): pytest.approx(400),
        datetime(2026, 10, 25, 1, tzinfo=UTC): pytest.approx(400),
    }


def test_gap_in_series_is_not_filled_with_last_value():
    f = parse_forecast(
        _payload(
            ("2026-07-01T10:15:00Z", 1000, None, None),
            ("2026-07-01T10:30:00Z", 2000, None, None),
            ("2026-07-01T10:45:00Z", 2000, None, None),
            ("2026-07-01T12:15:00Z", 500, None, None),
        )
    )
    # Der Punkt nach der Lücke reicht nur eine Rasterweite zurück
    assert (f.points[3].start, f.points[3].end) == (_utc(12), _utc(12, 15))
    assert f.power_at(_utc(11)) is None
    assert f.energy_between(_utc(10), _utc(13)) == pytest.approx(250 + 500 + 500 + 125)
    assert f.covers(_utc(10), _utc(10, 45))
    assert not f.covers(_utc(10), _utc(12, 15))


# -------------------- Korrekturfaktor --------------------

_FACTOR_ARGS = dict(min_forecast_wh=100, min_seconds=720, min_slots=3, lo=0.1, hi=3.0)


def _slots(*items: tuple[int, int, float, float, float]) -> dict:
    """(Stunde, Minute, gemessen Wh, Prognose Wh, Messzeit s) → Slots am 01.07. UTC."""
    return {_utc(h, m): SlotMeasurement(a, f, s) for h, m, a, f, s in items}


def test_slot_start_rounds_down_to_quarter_hour_in_utc():
    assert slot_start(datetime(2026, 7, 1, 12, 29, 59, tzinfo=ZURICH)) == _utc(10, 15)
    assert slot_start(_utc(10, 30)) == _utc(10, 30)


def test_correction_factor_is_ratio_over_valid_closed_slots():
    slots = _slots(
        (10, 0, 300, 500, 900),
        (10, 15, 300, 500, 900),
        (10, 30, 600, 1000, 900),
        (10, 45, 9999, 500, 300),  # laufender Slot (endet nach `until`) → ignoriert
    )
    factor, count = correction_factor(slots, since=_utc(9), until=_utc(10, 50), **_FACTOR_ARGS)
    assert count == 3
    assert factor == pytest.approx(1200 / 2000)


def test_correction_factor_skips_small_forecast_and_incomplete_slots():
    slots = _slots(
        (10, 0, 300, 500, 900),
        (10, 15, 300, 500, 900),
        (10, 30, 300, 500, 900),
        (10, 45, 5, 50, 900),  # Prognose zu klein
        (11, 0, 10, 500, 300),  # zu wenig Messzeit (Neustart)
        (8, 0, 1000, 500, 900),  # vor `since`
    )
    factor, count = correction_factor(slots, since=_utc(9), until=_utc(12), **_FACTOR_ARGS)
    assert count == 3
    assert factor == pytest.approx(0.6)


def test_correction_factor_none_with_too_few_slots():
    slots = _slots((10, 0, 300, 500, 900), (10, 15, 300, 500, 900))
    assert correction_factor(slots, since=_utc(9), until=_utc(12), **_FACTOR_ARGS) == (None, 2)


@pytest.mark.parametrize(("actual", "expected"), [(1, 0.1), (100000, 3.0)])
def test_correction_factor_is_clamped(actual, expected):
    slots = _slots(*[(10, m, actual, 500, 900) for m in (0, 15, 30)])
    factor, _ = correction_factor(slots, since=_utc(9), until=_utc(12), **_FACTOR_ARGS)
    assert factor == expected
