# forecast.py — PV-Prognose aus GET /v3/users/{smId}/data/forecast
"""Zeitreihe der Solar-Manager-PV-Prognose und Abfragen darauf.

Bewusst ohne Home-Assistant-Abhängigkeit: Sensoren, Energie-Dashboard und ein
späteres Abregelungsmodell (#23) arbeiten alle auf derselben Zeitreihe.

Annahme zur Semantik: ``t`` ist der Beginn eines Intervalls, ``pW`` die mittlere
Leistung darin. Ein Intervall dauert eine Rasterweite (häufigster Abstand der
Zeitstempel, sonst DEFAULT_INTERVAL), höchstens aber bis zum nächsten Zeitstempel.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone, tzinfo
from typing import Any, Literal

DEFAULT_INTERVAL = timedelta(minutes=15)

Band = Literal["mid", "min", "max"]


@dataclass(frozen=True, slots=True)
class ForecastPoint:
    """Ein Prognoseintervall [start, end)."""

    start: datetime
    end: datetime
    watts: float
    watts_min: float | None = None
    watts_max: float | None = None

    def value(self, which: Band = "mid") -> float:
        """Leistung im gewählten Band; fehlt min/max, gilt der Mittelwert."""
        if which == "min" and self.watts_min is not None:
            return self.watts_min
        if which == "max" and self.watts_max is not None:
            return self.watts_max
        return self.watts


def _parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _parse_watts(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


class PvForecast:
    """Sortierte, lückenlos interpretierte Prognose-Zeitreihe."""

    def __init__(self, points: list[ForecastPoint]) -> None:
        self.points = points

    @property
    def first_start(self) -> datetime:
        return self.points[0].start

    @property
    def last_end(self) -> datetime:
        return self.points[-1].end

    def power_at(self, when: datetime, which: Band = "mid") -> float | None:
        """Leistung (W) des Intervalls, das ``when`` enthält.

        Außerhalb der Zeitreihe None — dort wird nichts geraten.
        """
        for point in self.points:
            if point.start <= when < point.end:
                return point.value(which)
        return None

    def energy_between(
        self, start: datetime, end: datetime, which: Band = "mid"
    ) -> float:
        """Energie (Wh) zwischen start und end; angeschnittene Intervalle anteilig."""
        total = 0.0
        for point in self.points:
            lo = max(start, point.start)
            hi = min(end, point.end)
            if hi > lo:
                total += point.value(which) * (hi - lo).total_seconds() / 3600
        return total

    def covers(self, start: datetime, end: datetime) -> bool:
        """True, wenn die Zeitreihe [start, end) lückenlos abdeckt."""
        cursor = start
        for point in self.points:
            if point.end <= cursor:
                continue
            if point.start > cursor:
                return False
            cursor = point.end
            if cursor >= end:
                return True
        return cursor >= end

    def peak_between(
        self, start: datetime, end: datetime, which: Band = "mid"
    ) -> ForecastPoint | None:
        """Intervall mit der höchsten Leistung, das [start, end) überlappt.

        Bei Gleichstand gewinnt das frühere Intervall.
        """
        best: ForecastPoint | None = None
        for point in self.points:
            if point.end <= start or point.start >= end:
                continue
            if best is None or point.value(which) > best.value(which):
                best = point
        return best

    def points_between(self, start: datetime, end: datetime) -> list[ForecastPoint]:
        """Alle Intervalle, die [start, end) überlappen."""
        return [p for p in self.points if p.end > start and p.start < end]

    def merged_with(self, older: PvForecast | None, keep_from: datetime) -> PvForecast:
        """Ältere Intervalle ab ``keep_from`` vor den Beginn dieser Zeitreihe setzen.

        Liefert die API nur ab „jetzt“, gingen sonst bei jedem Abruf die bereits
        vergangenen Intervalle des Tages verloren — „Prognose heute“ würde dann
        im Tagesverlauf schrumpfen. Überlappende ältere Intervalle werden am
        Beginn der neuen Zeitreihe abgeschnitten, die neuen Werte haben Vorrang.
        """
        if older is None:
            return self
        head: list[ForecastPoint] = []
        for point in older.points:
            if point.start < keep_from or point.start >= self.first_start:
                continue
            end = min(point.end, self.first_start)
            head.append(
                ForecastPoint(point.start, end, point.watts, point.watts_min, point.watts_max)
            )
        return PvForecast(head + self.points) if head else self

    def wh_hours(self, tz: tzinfo) -> dict[datetime, float]:
        """Energie (Wh) je lokaler Stunde, Schlüssel = Stundenbeginn in UTC.

        Format für das HA-Energie-Dashboard (energy.py). Intervalle, die eine
        Stundengrenze schneiden, werden anteilig aufgeteilt. Die Stundengrenzen
        richten sich nach ``tz`` (relevant für Zonen mit halbstündigem Versatz),
        die Schlüssel sind aber UTC: Lokale Datetimes derselben Zone vergleichen
        ohne ``fold`` — die doppelte Stunde bei der Zeitumstellung fiele sonst
        in einen einzigen Eintrag zusammen.
        """
        hours: dict[datetime, float] = {}
        for point in self.points:
            cursor = point.start
            while cursor < point.end:
                local = cursor.astimezone(tz)
                hour_start = local.replace(minute=0, second=0, microsecond=0).astimezone(timezone.utc)
                seg_end = min(point.end, hour_start + timedelta(hours=1))
                wh = point.watts * (seg_end - cursor).total_seconds() / 3600
                hours[hour_start] = hours.get(hour_start, 0.0) + wh
                cursor = seg_end
        return hours


def _typical_step(starts: list[datetime]) -> timedelta:
    """Häufigster Abstand zweier Zeitstempel (Rasterweite), sonst DEFAULT_INTERVAL."""
    diffs = Counter(b - a for a, b in zip(starts, starts[1:]))
    if not diffs:
        return DEFAULT_INTERVAL
    # Bei Gleichstand der kleinere Abstand — lieber eine Lücke als ein gestreckter Wert
    return min(diffs, key=lambda d: (-diffs[d], d))


def parse_forecast(payload: Any) -> PvForecast | None:
    """Antwort von /v3/users/{smId}/data/forecast in eine Zeitreihe übersetzen.

    Erwartet ``{"data": [{"t", "pW", "pWmin", "pWmax"}, ...]}``; eine nackte
    Liste wird ebenfalls akzeptiert. Einträge ohne gültiges ``t``/``pW`` werden
    übersprungen. None, wenn kein einziger Eintrag brauchbar ist.
    """
    entries = payload.get("data") if isinstance(payload, dict) else payload
    if not isinstance(entries, list):
        return None

    raw: dict[datetime, tuple[float, float | None, float | None]] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        start = _parse_time(entry.get("t"))
        watts = _parse_watts(entry.get("pW"))
        if start is None or watts is None:
            continue
        raw[start] = (
            watts,
            _parse_watts(entry.get("pWmin")),
            _parse_watts(entry.get("pWmax")),
        )
    if not raw:
        return None

    starts = sorted(raw)
    step = _typical_step(starts)
    points: list[ForecastPoint] = []
    for idx, start in enumerate(starts):
        # Höchstens eine Rasterweite: Fehlt ein Stück der Reihe, bleibt die
        # Lücke leer, statt den letzten Wert über Stunden zu strecken
        end = start + step
        if idx + 1 < len(starts):
            end = min(end, starts[idx + 1])
        watts, watts_min, watts_max = raw[start]
        points.append(ForecastPoint(start, end, watts, watts_min, watts_max))
    return PvForecast(points)
