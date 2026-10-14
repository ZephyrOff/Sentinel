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
from homeassistant.exceptions import HomeAssistantError
from .engine import CodeError


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
            "sentinel_key": "mode",
            "mode_id": mode.get("id"),
            "description": mode.get("desc", ""),
            "entry_delay": mode.get("entryDelay", 0),
            "exit_delay": mode.get("exitDelay", 0),
            "grace_until": self.engine.grace_until_iso(),
            "code_set": self.engine.code_is_set(),
            "modes": [
                {"id": m.get("id"), "name": m.get("name"), "color": m.get("color"),
                 "desc": m.get("desc", ""),
                 "need_code": self.engine.mode_needs_code(m),
                 "need_confirm": self.engine.mode_needs_confirm(m)}
                for m in self.engine.config["modes"]
            ],
            "pending_change": self.engine.request_info(),
            "last_request": self.engine.last_request(),
        }

    async def async_select_option(self, option: str) -> None:
        try:
            await self.engine.async_request_mode(option, None)
        except CodeError as err:
            raise HomeAssistantError(f"{err} : utilise le digicode ou le service sentinel.set_mode") from err
