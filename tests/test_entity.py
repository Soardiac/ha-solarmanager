"""Tests für entity.py: Verknüpfung der Kind-Geräte mit dem Site-Gerät.

HA hat `via_device` (Identifier-Tupel) durch `via_device_id` (Registry-ID)
ersetzt. Ab 2026.9 eskaliert die Deprecation-Meldung zu einem RuntimeError,
sobald HA im Stack keinen Frame der Integration findet — im Sensor-Pfad
passierte genau das, wodurch sämtliche Kind-Geräte-Sensoren nicht mehr angelegt
wurden (Issue #30). Mindestversion ist 2026.9, `via_device` darf nie auftauchen.
"""
from types import SimpleNamespace

from custom_components.solarmanager.const import DOMAIN
from custom_components.solarmanager.entity import (
    child_device_info,
    site_device_info,
    site_identifier,
)

DEV_ID = "68679dccc0ef0eebee0a6f7a"


class _StubCoordinator:
    def __init__(self, site_device_id: str | None = "site-registry-id") -> None:
        self.site_id = "SM-0001"
        self.entry = SimpleNamespace(entry_id="ENTRY01")
        self.site_device_id = site_device_id
        self.is_local = False
        self.is_cloud_extension = False

    def get_device_name(self, dev_id: str) -> str | None:
        return "Wattpilot"


def test_child_uses_via_device_id():
    """Verknüpfung über die Registry-ID des Site-Geräts."""
    info = child_device_info(_StubCoordinator(), DEV_ID)

    assert info["via_device_id"] == "site-registry-id"
    assert info["identifiers"] == {(DOMAIN, f"device_{DEV_ID}")}
    assert info["name"] == "Wattpilot"


def test_child_never_sets_via_device():
    """Der eigentliche Regressionsschutz: `via_device` ist die
    Bugquelle und darf unter keinen Umständen mehr auftauchen."""
    info = child_device_info(_StubCoordinator(), DEV_ID)

    assert "via_device" not in info


def test_child_without_site_device_id_omits_link():
    """Ohne Site-Device-ID lieber gar keine Verknüpfung als ein via_device-Fallback:
    letzterer löst auf 2026.9 den RuntimeError aus, der die Entität verhindert."""
    info = child_device_info(_StubCoordinator(site_device_id=None), DEV_ID)

    assert "via_device" not in info
    assert "via_device_id" not in info
    assert info["identifiers"] == {(DOMAIN, f"device_{DEV_ID}")}


def test_child_name_falls_back_to_short_id():
    """Ohne Namen aus den Metadaten greift der ID-Kurzform-Fallback."""
    coord = _StubCoordinator()
    coord.get_device_name = lambda dev_id: None

    assert child_device_info(coord, DEV_ID)["name"] == "Solarmanager Gerät 0a6f7a"


def test_site_device_info_has_no_via_link():
    """Das Site-Gerät ist die Wurzel und verweist auf kein Elterngerät — deshalb
    blieben die Site-Sensoren vom Fehler unberührt."""
    info = site_device_info(_StubCoordinator())

    assert "via_device" not in info
    assert "via_device_id" not in info


def test_site_identifier_bound_to_entry_not_site_id():
    """Identifier hängt an der entry_id, nicht an Host/smId: Moduswechsel und
    neue Gateway-IP erzeugen kein zweites Site-Gerät (Discussion #26)."""
    info = site_device_info(_StubCoordinator())

    assert info["identifiers"] == {(DOMAIN, site_identifier("ENTRY01"))}
    assert info["identifiers"] == {(DOMAIN, "site_ENTRY01")}


def test_site_device_named_solar_manager_with_id_as_serial():
    """Gerätename ohne smId/Host: er wird Präfix der Entity-IDs (sensor.solar_manager_…)."""
    info = site_device_info(_StubCoordinator())

    assert info["name"] == "Solar Manager"
    assert info["serial_number"] == "SM-0001"
