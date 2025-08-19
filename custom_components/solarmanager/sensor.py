# sensor.py
from __future__ import annotations
from typing import Any, Optional

from homeassistant.components.sensor import (
    SensorEntity,
    SensorDeviceClass,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, CONF_SM_ID, MANUFACTURER, MODEL
from .coordinator import SolarmanagerCoordinator

# --- Site-weite Sensoren (aus dem Stream, Einheiten direkt übernehmen) ---

POWER_SENSORS = [
    ("pW", "PV-Leistung", "W", SensorDeviceClass.POWER),
    ("cW", "Hausverbrauch", "W", SensorDeviceClass.POWER),
    ("batW", "Batterie-Leistung (+Laden/-Entladen)", "W", SensorDeviceClass.POWER),
    ("iW", "Netz Import", "W", SensorDeviceClass.POWER),
    ("eW", "Netz Export", "W", SensorDeviceClass.POWER),
    ("gridW", "Netzleistung (+Bezug/-Einspeisung)", "W", SensorDeviceClass.POWER),
]

# Achtung: …Wh-Felder kommen bereits als kWh (z. B. 23.48) – KEINE /1000-Umrechnung!
ENERGY_SENSORS = [
    ("pWh", "PV-Energie heute", "kWh"),
    ("cWh", "Verbrauch heute", "kWh"),
    ("iWh", "Netzbezug heute", "kWh"),
    ("eWh", "Netzeinspeisung heute", "kWh"),
    ("bcWh", "Batterie geladen heute", "kWh"),
    ("bdWh", "Batterie entladen heute", "kWh"),
]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback):
    coord: SolarmanagerCoordinator = hass.data[DOMAIN][entry.entry_id]

    # Site-Sensoren
    site_entities: list[SensorEntity] = [
        *(SolarmanagerPowerSensor(coord, *spec) for spec in POWER_SENSORS),
        *(SolarmanagerEnergySensor(coord, key, name, unit) for key, name, unit in ENERGY_SENSORS),
        SocSensor(coord),
        DevicesOverviewSensor(coord),
    ]

    # Geräte-Sensoren dynamisch aus der aktuellen devices[]-Liste
    device_entities: list[SensorEntity] = []
    for dev in (coord.data or {}).get("devices", []):
        dev_id = str(dev.get("_id") or "")
        if not dev_id:
            continue

        # Leistung pro Gerät (W)
        if "power" in dev:
            device_entities.append(DevicePowerSensor(coord, dev_id))

        # Energiezähler pro Gerät (kWh) – falls vorhanden
        if "iWh" in dev:
            device_entities.append(DeviceEnergySensor(coord, dev_id, key="iWh", label="Gerät Netzbezug heute"))
        if "eWh" in dev:
            device_entities.append(DeviceEnergySensor(coord, dev_id, key="eWh", label="Gerät Netzeinspeisung heute"))

        # SOC pro Gerät (falls Gerät Batterie-ähnlich und Feld vorhanden)
        if "soc" in dev:
            device_entities.append(DeviceSocSensor(coord, dev_id))

        # Temperatur (falls vorhanden)
        if "temperature" in dev:
            device_entities.append(DeviceTemperatureSensor(coord, dev_id))

    async_add_entities(site_entities + device_entities, True)


# ---------- Basisklassen ----------

class _Base(CoordinatorEntity[SolarmanagerCoordinator]):
    _attr_has_entity_name = True

    def __init__(self, coordinator: SolarmanagerCoordinator, key: str, name: str):
        super().__init__(coordinator)
        self._key = key
        self._name = name
        sm_id = coordinator.entry.data.get(CONF_SM_ID, "unknown")
        self._attr_unique_id = f"{coordinator.entry.entry_id}_{key}"
        self._attr_name = name
        self._device_info = {
            "identifiers": {(DOMAIN, f"site_{sm_id}")},
            "name": f"Solarmanager {sm_id}",
            "manufacturer": MANUFACTURER if "MANUFACTURER" in globals() else "Solarmanager",
            "model": MODEL if "MODEL" in globals() else "Cloud v3 stream",
        }

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        d = self.coordinator.data or {}
        attrs = {}
        if "t" in d:
            attrs["timestamp"] = d.get("t")
        if "iv" in d:
            attrs["interval_s"] = d.get("iv")
        return attrs


class SolarmanagerPowerSensor(_Base, SensorEntity):
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator: SolarmanagerCoordinator, key: str, name: str, unit: str, device_class):
        super().__init__(coordinator, key, name)
        self._attr_native_unit_of_measurement = unit
        self._attr_device_class = device_class

    @property
    def native_value(self) -> Optional[float]:
        v = (self.coordinator.data or {}).get(self._key)
        return float(v) if isinstance(v, (int, float)) else None


class SolarmanagerEnergySensor(_Base, SensorEntity):
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_device_class = SensorDeviceClass.ENERGY

    def __init__(self, coordinator: SolarmanagerCoordinator, key: str, name: str, unit: str = "kWh"):
        super().__init__(coordinator, key, name)
        self._attr_native_unit_of_measurement = unit

    @property
    def native_value(self) -> Optional[float]:
        # Werte sind bereits kWh
        v = (self.coordinator.data or {}).get(self._key)
        try:
            return float(v) if v is not None else None
        except Exception:
            return None


class SocSensor(_Base, SensorEntity):
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = "%"

    def __init__(self, coordinator: SolarmanagerCoordinator):
        super().__init__(coordinator, "soc", "Batterie-SOC")

    @property
    def native_value(self) -> Optional[float]:
        v = (self.coordinator.data or {}).get("soc")
        try:
            return float(v) if v is not None else None
        except Exception:
            return None


class DevicesOverviewSensor(_Base, SensorEntity):
    """Zeigt Anzahl Geräte und komprimierte Attribute aus devices[]."""
    _attr_icon = "mdi:devices"

    def __init__(self, coordinator: SolarmanagerCoordinator):
        super().__init__(coordinator, "devices_overview", "Geräte (Stream-Übersicht)")

    @property
    def native_value(self):
        devs = (self.coordinator.data or {}).get("devices") or []
        return len(devs)

    @property
    def extra_state_attributes(self):
        base = super().extra_state_attributes
        devs = (self.coordinator.data or {}).get("devices") or []
        compact = []
        for it in devs:
            compact.append({
                "id": it.get("_id"),
                "signal": it.get("signal"),
                "activeDevice": it.get("activeDevice"),
                "power_W": it.get("power"),
                "soc_%": it.get("soc"),
                "iWh_kWh": it.get("iWh"),
                "eWh_kWh": it.get("eWh"),
                "temperature_C": it.get("temperature"),
                "deviceState": it.get("deviceState"),
                "switchState": it.get("switchState"),
            })
        dd = dict(base) if base else {}
        dd["devices"] = compact
        return dd


# ---------- Geräte-Sensoren ----------

def _find_device(data: dict[str, Any] | None, dev_id: str) -> dict[str, Any] | None:
    for it in (data or {}).get("devices", []) or []:
        if str(it.get("_id")) == dev_id:
            return it
    return None


class _DeviceBase(CoordinatorEntity[SolarmanagerCoordinator], SensorEntity):
    _attr_has_entity_name = True

    def __init__(self, coordinator: SolarmanagerCoordinator, dev_id: str, key: str, label: str):
        super().__init__(coordinator)
        self._dev_id = dev_id
        self._key = key
        self._label = label  # z. B. "Leistung", "Netzbezug heute", "SOC", "Temperatur"

        short = dev_id[-6:] if len(dev_id) >= 6 else dev_id
        # Fallback-Name; wird dynamisch via .name übersteuert, sobald Meta da ist
        self._attr_name = f"Gerät {short} {label}"
        self._attr_unique_id = f"{coordinator.entry.entry_id}_dev_{dev_id}_{key}"

    # Dynamischer Anzeigename (zieht aus Coordinator.device_meta, wenn vorhanden)
    @property
    def name(self) -> str:
        friendly = self.coordinator.get_device_name(self._dev_id) if hasattr(self.coordinator, "get_device_name") else None
        if friendly:
            return f"{friendly} {self._label}"
        return super().name  # Fallback auf _attr_name

    # device_info ebenfalls dynamisch, damit „Geräte“-Kachel sauber benannt ist
    @property
    def device_info(self) -> dict[str, Any] | None:
        sm_id = self.coordinator.entry.data.get(CONF_SM_ID, "unknown")
        friendly = self.coordinator.get_device_name(self._dev_id) if hasattr(self.coordinator, "get_device_name") else None
        short = self._dev_id[-6:] if len(self._dev_id) >= 6 else self._dev_id
        base_name = friendly or f"Solarmanager Gerät {short}"
        return {
            "identifiers": {(DOMAIN, f"device_{self._dev_id}")},
            "name": base_name,
            "manufacturer": "Solarmanager",
            "model": "Stream device",
            "via_device": (DOMAIN, f"site_{sm_id}"),
        }

    def _dev(self) -> dict[str, Any] | None:
        return _find_device(self.coordinator.data, self._dev_id)


class DevicePowerSensor(_DeviceBase):
    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = "W"

    def __init__(self, coordinator: SolarmanagerCoordinator, dev_id: str):
        super().__init__(coordinator, dev_id, "power", "Leistung")

    @property
    def native_value(self) -> Optional[float]:
        d = self._dev()
        v = d.get("power") if d else None
        try:
            return float(v) if v is not None else None
        except Exception:
            return None


class DeviceEnergySensor(_DeviceBase):
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_native_unit_of_measurement = "kWh"

    def __init__(self, coordinator: SolarmanagerCoordinator, dev_id: str, *, key: str, label: str):
        super().__init__(coordinator, dev_id, key, label)

    @property
    def native_value(self) -> Optional[float]:
        d = self._dev()
        v = d.get(self._key) if d else None
        try:
            return float(v) if v is not None else None
        except Exception:
            return None


class DeviceSocSensor(_DeviceBase):
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = "%"

    def __init__(self, coordinator: SolarmanagerCoordinator, dev_id: str):
        super().__init__(coordinator, dev_id, "soc", "SOC")

    @property
    def native_value(self) -> Optional[float]:
        d = self._dev()
        v = d.get("soc") if d else None
        try:
            return float(v) if v is not None else None
        except Exception:
            return None


class DeviceTemperatureSensor(_DeviceBase):
    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = "°C"

    def __init__(self, coordinator: SolarmanagerCoordinator, dev_id: str):
        super().__init__(coordinator, dev_id, "temperature", "Temperatur")

    @property
    def native_value(self) -> Optional[float]:
        d = self._dev()
        v = d.get("temperature") if d else None
        try:
            return float(v) if v is not None else None
        except Exception:
            return None

