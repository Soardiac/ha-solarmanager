"""Tests für die reine Prognose-Logik (forecast.py), ohne Home Assistant."""
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from custom_components.solarmanager.forecast import parse_forecast

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


def test_parse_reads_swagger_format_and_sorts():
    f = parse_forecast(
        _payload(
            ("2026-07-01T10:15:00.000Z", 2000, 1500, 2500),
            ("2026-07-01T10:00:00.000Z", 1000, 800, 1200),
        )
    )
    assert f is not None
    assert [p.start for p in f.points] == [_utc(10), _utc(10, 15)]
    assert f.points[0].end == _utc(10, 15)
    # Letztes Intervall bekommt die Standarddauer von 15 Minuten
    assert f.points[1].end == _utc(10, 30)
    assert f.points[0].watts_min == 800
    assert f.points[0].watts_max == 1200


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


@pytest.mark.parametrize("payload", [None, {}, {"data": None}, {"data": []}, {"data": [{"x": 1}]}, "text"])
def test_parse_returns_none_without_usable_data(payload):
    assert parse_forecast(payload) is None


def test_power_at_uses_interval_containing_time_and_none_outside():
    f = parse_forecast(_payload(("2026-07-01T10:00:00Z", 1000, 800, 1200), ("2026-07-01T10:15:00Z", 2000, None, None)))
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
    f = parse_forecast(_payload(("2026-07-01T10:00:00Z", 1000, 400, None), ("2026-07-01T10:15:00Z", 2000, 1000, None)))
    # Volle Intervalle: 1000 W * 0.25 h + 2000 W * 0.25 h
    assert f.energy_between(_utc(0), _utc(23)) == pytest.approx(750)
    # Ab 10:10 → 5 min von 1000 W + 15 min von 2000 W
    assert f.energy_between(_utc(10, 10), _utc(11)) == pytest.approx(1000 * 5 / 60 + 500)
    assert f.energy_between(_utc(0), _utc(23), "min") == pytest.approx(100 + 250)
    assert f.energy_between(_utc(11), _utc(12)) == 0


def test_covers_and_points_between():
    f = parse_forecast(_payload(("2026-07-01T10:00:00Z", 1, None, None), ("2026-07-01T10:15:00Z", 2, None, None)))
    assert f.covers(_utc(10), _utc(10, 30))
    assert not f.covers(_utc(9, 45), _utc(10, 30))
    assert not f.covers(_utc(10), _utc(10, 45))
    assert [p.watts for p in f.points_between(_utc(10, 20), _utc(11))] == [2]


def test_peak_between_prefers_earlier_on_tie_and_respects_window():
    f = parse_forecast(
        _payload(
            ("2026-07-01T10:00:00Z", 3000, None, None),
            ("2026-07-01T10:15:00Z", 3000, None, None),
            ("2026-07-02T10:00:00Z", 9000, None, None),
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
            ("2026-06-30T23:45:00Z", 5, None, None),  # gestern → verworfen
            ("2026-07-01T10:00:00Z", 1000, None, None),
            ("2026-07-01T10:15:00Z", 1100, None, None),  # von neuer Reihe überdeckt
        )
    )
    newer = parse_forecast(_payload(("2026-07-01T10:15:00Z", 1500, None, None)))
    merged = newer.merged_with(older, day_start)
    assert [(p.start, p.watts) for p in merged.points] == [(_utc(10), 1000), (_utc(10, 15), 1500)]
    assert merged.points[0].end == _utc(10, 15)
    assert newer.merged_with(None, day_start) is newer


def test_wh_hours_splits_at_hour_boundaries_in_local_time():
    # Unregelmäßiges Raster: 10:30–11:30 UTC, eine Stunde mit 1200 W
    f = parse_forecast(_payload(("2026-07-01T10:30:00Z", 1200, None, None), ("2026-07-01T11:30:00Z", 0, None, None)))
    hours = f.wh_hours(ZURICH)
    # Je 600 Wh auf die Stunden 10:00 und 11:00 UTC (= 12:00/13:00 lokal);
    # das 0-W-Intervall (Rasterweite 1 h) reicht bis in die Stunde 12:00
    assert hours == {_utc(10): pytest.approx(600), _utc(11): pytest.approx(600), _utc(12): 0}


def test_wh_hours_follows_local_half_hour_offsets():
    # Indien (UTC+5:30): lokale Stunden beginnen um :30 UTC
    f = parse_forecast(_payload(("2026-07-01T10:00:00Z", 1000, None, None), ("2026-07-01T11:00:00Z", 0, None, None)))
    hours = f.wh_hours(ZoneInfo("Asia/Kolkata"))
    assert hours[_utc(9, 30)] == pytest.approx(500)
    assert hours[_utc(10, 30)] == pytest.approx(500)


def test_wh_hours_keeps_both_hours_at_dst_fall_back():
    # 25.10.2026: 03:00 CEST → 02:00 CET. Die lokale Stunde 02:00 kommt zweimal vor.
    start = datetime(2026, 10, 25, 0, 0, tzinfo=UTC)  # = 02:00 CEST
    entries = [((start + timedelta(minutes=15 * i)).strftime("%Y-%m-%dT%H:%M:%SZ"), 400, None, None) for i in range(8)]
    hours = parse_forecast(_payload(*entries)).wh_hours(ZURICH)
    # Zwei getrennte Stunden (02:00 CEST und 02:00 CET) mit je 4 × 400 W × 0.25 h
    assert hours == {
        datetime(2026, 10, 25, 0, tzinfo=UTC): pytest.approx(400),
        datetime(2026, 10, 25, 1, tzinfo=UTC): pytest.approx(400),
    }


def test_gap_in_series_is_not_filled_with_last_value():
    f = parse_forecast(
        _payload(
            ("2026-07-01T10:00:00Z", 1000, None, None),
            ("2026-07-01T10:15:00Z", 2000, None, None),
            ("2026-07-01T10:30:00Z", 2000, None, None),
            ("2026-07-01T12:00:00Z", 500, None, None),
        )
    )
    assert f.points[2].end == _utc(10, 45)
    assert f.power_at(_utc(11)) is None
    assert f.energy_between(_utc(10), _utc(13)) == pytest.approx(250 + 500 + 500 + 125)
    assert f.covers(_utc(10), _utc(10, 45))
    assert not f.covers(_utc(10), _utc(12, 15))
