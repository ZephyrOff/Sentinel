"""Capteurs Sentinel : statut, compteurs, dernier déclenchement, zones."""
from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .engine import SentinelEngine
from .entity import SentinelEntity

STATUS_OPTIONS = ["ok", "info", "warning", "critical"]


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    engine: SentinelEngine = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        SentinelSensor(engine, item)
        for item in engine.items()
        if item["domain"] == "sensor"
    )


class SentinelSensor(SentinelEntity, SensorEntity):
    """Un capteur dont la valeur dépend de la clé."""

    def __init__(self, engine: SentinelEngine, item: dict[str, Any]) -> None:
        super().__init__(engine, item)
        key = self._key
        if key == "status" or key.startswith("zone:"):
            self._attr_device_class = SensorDeviceClass.ENUM
            self._attr_options = STATUS_OPTIONS
        elif key.endswith("_count") or key == "trigger_counter":
            self._attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def native_value(self) -> str | int | None:
        key, counts = self._key, self.engine.counts()
        if key == "status":
            return counts["status"]
        if key == "trigger_counter":
            return counts["total"]
        if key in ("critical_count", "warning_count", "info_count"):
            return counts[key.removesuffix("_count")]
        if key == "last_trigger":
            last = self.engine.last_trigger
            return last["name"] if last else "none"
        if key.startswith("zone:"):
            return self.engine.zone_status(key.split(":", 1)[1])[0]
        return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        key, counts = self._key, self.engine.counts()
        if key == "trigger_counter":
            return {
                "info": counts["info"],
                "warning": counts["warning"],
                "critical": counts["critical"],
                "total_since_reset": self.engine.total_since_reset,
            }
        if key == "last_trigger":
            return dict(self.engine.last_trigger or {})
        if key.startswith("zone:"):
            return {"triggered": self.engine.zone_status(key.split(":", 1)[1])[1]}
        return {}
