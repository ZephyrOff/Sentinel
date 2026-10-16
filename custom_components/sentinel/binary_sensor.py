"""Capteur binaire : alarme (au moins une entité en critical)."""
from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .engine import SentinelEngine
from .entity import SentinelEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    engine: SentinelEngine = hass.data[DOMAIN][entry.entry_id]
    entities = []
    for item in engine.items():
        if item["domain"] != "binary_sensor":
            continue
        entities.append(SentinelAlarm(engine, item) if item["key"] == "alarm" else SentinelFlag(engine, item))
    async_add_entities(entities)


class SentinelFlag(SentinelEntity, BinarySensorEntity):
    """Délais en cours : « arming » (délai de sortie) et « pending » (délai d'entrée)."""

    @property
    def is_on(self) -> bool:
        if self._key == "arming":
            return self.engine.is_arming()
        return self.engine.is_entry_pending()

    @property
    def extra_state_attributes(self) -> dict:
        if self._key == "arming":
            return {"sentinel_key": "arming", "until": self.engine.grace_until_iso()}
        return {"sentinel_key": "pending", "until": self.engine.pending_entry_until()}


class SentinelAlarm(SentinelEntity, BinarySensorEntity):
    _attr_device_class = BinarySensorDeviceClass.SAFETY

    @property
    def is_on(self) -> bool:
        return self.engine.counts()["critical"] > 0

    @property
    def extra_state_attributes(self) -> dict:
        return {"sentinel_key": "alarm"}
