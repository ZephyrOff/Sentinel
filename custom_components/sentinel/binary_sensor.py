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
    async_add_entities(
        SentinelAlarm(engine, item)
        for item in engine.items()
        if item["domain"] == "binary_sensor"
    )


class SentinelAlarm(SentinelEntity, BinarySensorEntity):
    _attr_device_class = BinarySensorDeviceClass.SAFETY

    @property
    def is_on(self) -> bool:
        return self.engine.counts()["critical"] > 0
