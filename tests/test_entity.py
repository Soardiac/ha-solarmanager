"""Tests für entity.py: Verknüpfung der Kind-Geräte mit dem Site-Gerät.

HA hat `via_device` (Identifier-Tupel) durch `via_device_id` (Registry-ID)
ersetzt. Ab 2026.9 eskaliert die Deprecation-Meldung zu einem RuntimeError,
sobald HA im Stack keinen Frame der Integration findet — im Sensor-Pfad
passierte genau das, wodurch sämtliche Kind-Geräte-Sensoren nicht mehr angelegt
wurden (Issue #30). Die Tests sichern beide Pfade ab.
"""
import pytest

from custom_components.solarmanager import entity as entity_mod
from custom_components.solarmanager.const import DOMAIN
from custom_components.solarmanager.entity import child_device_info, site_device_info

DEV_ID = "68679dccc0ef0eebee0a6f7a"


class _StubCoordinator:
    def __init__(self, site_device_id: str | None = "site-registry-id") -> None:
        self.site_id = "SM-0001"
        self.site_device_id = site_device_id
        self.is_local = False

    def get_device_name(self, dev_id: str) -> str | None:
        return "Wattpilot"


@pytest.fixture
def new_ha(monkeypatch):
    """HA 2026.8+ — DeviceInfo kennt via_device_id."""
    monkeypatch.setattr(entity_mod, "_SUPPORTS_VIA_DEVICE_ID", True)


@pytest.fixture
def old_ha(monkeypatch):
    """HA bis 2026.7 — DeviceInfo kennt nur via_device."""
    monkeypatch.setattr(entity_mod, "_SUPPORTS_VIA_DEVICE_ID", False)


def test_child_uses_via_device_id_on_new_ha(new_ha):
    """Neues HA: Verknüpfung über die Registry-ID des Site-Geräts."""
    info = child_device_info(_StubCoordinator(), DEV_ID)

    assert info["via_device_id"] == "site-registry-id"
    assert info["identifiers"] == {(DOMAIN, f"device_{DEV_ID}")}
    assert info["name"] == "Wattpilot"


def test_child_never_sets_via_device_on_new_ha(new_ha):
    """Der eigentliche Regressionsschutz: `via_device` ist auf neuem HA die
    Bugquelle und darf dort unter keinen Umständen mehr auftauchen."""
    info = child_device_info(_StubCoordinator(), DEV_ID)

    assert "via_device" not in info


def test_child_without_site_device_id_omits_link(new_ha):
    """Ohne Site-Device-ID lieber gar keine Verknüpfung als ein via_device-Fallback:
    letzterer löst auf 2026.9 den RuntimeError aus, der die Entität verhindert."""
    info = child_device_info(_StubCoordinator(site_device_id=None), DEV_ID)

    assert "via_device" not in info
    assert "via_device_id" not in info
    assert info["identifiers"] == {(DOMAIN, f"device_{DEV_ID}")}


def test_child_falls_back_to_via_device_on_old_ha(old_ha):
    """Altes HA (Mindestversion 2025.8) kennt via_device_id nicht -> altes Verhalten."""
    info = child_device_info(_StubCoordinator(), DEV_ID)

    assert info["via_device"] == (DOMAIN, "site_SM-0001")
    assert "via_device_id" not in info


def test_child_name_falls_back_to_short_id(new_ha):
    """Ohne Namen aus den Metadaten greift der ID-Kurzform-Fallback."""
    coord = _StubCoordinator()
    coord.get_device_name = lambda dev_id: None

    assert child_device_info(coord, DEV_ID)["name"] == "Solarmanager Gerät 0a6f7a"


@pytest.mark.parametrize("supported", [True, False])
def test_site_device_info_has_no_via_link(monkeypatch, supported):
    """Das Site-Gerät ist die Wurzel und verweist auf kein Elterngerät — deshalb
    blieben die Site-Sensoren vom Fehler unberührt."""
    monkeypatch.setattr(entity_mod, "_SUPPORTS_VIA_DEVICE_ID", supported)
    info = site_device_info(_StubCoordinator())

    assert "via_device" not in info
    assert "via_device_id" not in info
    assert info["identifiers"] == {(DOMAIN, "site_SM-0001")}
