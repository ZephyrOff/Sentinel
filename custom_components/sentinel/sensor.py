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
from .logic import room_of

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
        if key == "open_sensors":
            return len(self.engine.open_entities())
        if key == "active":
            return len([i for i in self.engine.active_items() if i["status"] != "acked"])
        if key == "journal":
            return self.engine.journal[0]["text"][:250] if self.engine.journal else "none"
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
        base: dict[str, Any] = {"sentinel_key": key}
        if key == "open_sensors":
            return {**base, "sensors": [
                {"name": e.get("name", ""), "entity_id": e.get("entity", ""), "type": e.get("type", "")}
                for e in self.engine.open_entities()
            ]}
        if key == "active":
            return {**base, "items": self.engine.active_items()}
        if key == "journal":
            return {**base, "events": [
                {"time": e.get("t"), "text": e.get("text"), "level": e.get("lv") or ""}
                for e in self.engine.journal
            ]}
        if key == "trigger_counter":
            return {**base,
                "info": counts["info"],
                "warning": counts["warning"],
                "critical": counts["critical"],
                "total_since_reset": self.engine.total_since_reset,
            }
        if key == "last_trigger":
            return {**base, **dict(self.engine.last_trigger or {})}
        if key.startswith("zone:"):
            room_id = key.split(":", 1)[1]
            room = next((r for r in self.engine.config["rooms"] if r.get("id") == room_id), {})
            ents = [e for e in self.engine.config["ents"] if (room_of(self.engine.config, e) or {}).get("id") == room_id]
            return {**base, "triggered": self.engine.zone_status(room_id)[1],
                    "room_id": room_id, "zone_name": room.get("name", ""), "floor": room.get("floor", ""),
                    "outdoor": bool(room.get("outdoor")), "total": len(ents)}
        return base
