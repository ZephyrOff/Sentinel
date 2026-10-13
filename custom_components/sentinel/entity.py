"""Entité de base Sentinel."""
from __future__ import annotations

from typing import Any

from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import Entity

from .const import DOMAIN, VERSION
from .engine import SentinelEngine


class SentinelEntity(Entity):
    """Base : se met à jour à chaque changement du moteur."""

    _attr_should_poll = False
    _attr_has_entity_name = True

    def __init__(self, engine: SentinelEngine, item: dict[str, Any]) -> None:
        self.engine = engine
        self._key: str = item["key"]
        self._attr_name = item["name"]
        self._attr_icon = item["icon"]
        self._attr_unique_id = f"{engine.entry.entry_id}_{item['key']}"
        # Identifiant voulu (préfixe + éventuel renommage défini dans le panneau).
        # Le registre d'entités garde la main si l'entité existe déjà.
        self.entity_id = f"{item['domain']}.{item['object_id']}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, engine.entry.entry_id)},
            name="Sentinel",
            manufacturer="Sentinel",
            model="Surveillance",
            sw_version=VERSION,
        )

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(
            async_dispatcher_connect(self.hass, self.engine.signal, self._handle_update)
        )

    @callback
    def _handle_update(self) -> None:
        self.async_write_ha_state()
