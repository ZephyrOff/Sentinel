"""Sélecteur du mode actif."""
from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .engine import SentinelEngine
from .entity import SentinelEntity
from .logic import current_mode


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    engine: SentinelEngine = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        SentinelModeSelect(engine, item)
        for item in engine.items()
        if item["domain"] == "select"
    )


class SentinelModeSelect(SentinelEntity, SelectEntity):
    """Choix du mode (Désarmé, Présence, Absence, Nuit, ...)."""

    @property
    def options(self) -> list[str]:
        return [m.get("name", m.get("id", "")) for m in self.engine.config["modes"]]

    @property
    def current_option(self) -> str | None:
        return current_mode(self.engine.config).get("name")

    @property
    def extra_state_attributes(self) -> dict:
        mode = current_mode(self.engine.config)
        return {
            "mode_id": mode.get("id"),
            "description": mode.get("desc", ""),
            "entry_delay": mode.get("entryDelay", 0),
            "exit_delay": mode.get("exitDelay", 0),
        }

    async def async_select_option(self, option: str) -> None:
        self.engine.async_set_mode(option)
