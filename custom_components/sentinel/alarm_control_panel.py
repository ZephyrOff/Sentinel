"""Entité alarm_control_panel : l'alarme vue comme dans Alarmo (désarmée, armée, délai, déclenchée)."""
from __future__ import annotations

from typing import Any

from homeassistant.components.alarm_control_panel import (
    AlarmControlPanelEntity,
    AlarmControlPanelEntityFeature,
    CodeFormat,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

try:  # Home Assistant 2024.11 et suivants
    from homeassistant.components.alarm_control_panel import AlarmControlPanelState
except ImportError:  # pragma: no cover - anciennes versions
    AlarmControlPanelState = None

try:
    from homeassistant.exceptions import ServiceValidationError
except ImportError:  # pragma: no cover
    ServiceValidationError = HomeAssistantError

from .const import DOMAIN
from .engine import CodeError, SentinelEngine
from .entity import SentinelEntity

_FEATURES = {
    "armed_home": AlarmControlPanelEntityFeature.ARM_HOME,
    "armed_away": AlarmControlPanelEntityFeature.ARM_AWAY,
    "armed_night": AlarmControlPanelEntityFeature.ARM_NIGHT,
    "armed_vacation": AlarmControlPanelEntityFeature.ARM_VACATION,
    "armed_custom_bypass": AlarmControlPanelEntityFeature.ARM_CUSTOM_BYPASS,
}


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    engine: SentinelEngine = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        SentinelAlarmPanel(engine, item)
        for item in engine.items()
        if item["domain"] == "alarm_control_panel"
    )


class SentinelAlarmPanel(SentinelEntity, AlarmControlPanelEntity):
    """Armer / désarmer : chaque action correspond à un mode Sentinel (réglage « armement »)."""

    _attr_code_arm_required = False

    @property
    def supported_features(self) -> AlarmControlPanelEntityFeature:
        features = AlarmControlPanelEntityFeature(0)
        for kind, feature in _FEATURES.items():
            if self.engine.mode_for_kind(kind) is not None:
                features |= feature
        return features

    @property
    def code_format(self) -> CodeFormat | None:
        needs = any(self.engine.mode_needs_code(m) for m in self.engine.config["modes"])
        return CodeFormat.NUMBER if needs else None

    if AlarmControlPanelState is not None:

        @property
        def alarm_state(self):  # type: ignore[override]
            return AlarmControlPanelState(self.engine.panel_state())

    else:  # pragma: no cover

        @property
        def state(self) -> str:  # type: ignore[override]
            return self.engine.panel_state()

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        engine = self.engine
        mode = next((m for m in engine.config["modes"] if m.get("id") == engine.config.get("mode")), {})
        triggered = [
            e.get("name", "") for e in engine.config["ents"] if e["id"] in engine._triggered
        ]
        modes = []
        for m in engine.config["modes"]:
            kind = engine.armed_kind(m)
            canonical = engine.mode_for_kind(kind)
            modes.append({
                "id": m.get("id"), "name": m.get("name"), "color": m.get("color"),
                "desc": m.get("desc", ""), "armed_as": kind,
                "need_code": engine.mode_needs_code(m),
                "need_confirm": engine.mode_needs_confirm(m),
                # les services alarm_control_panel.* activent le premier mode de chaque type
                "via_panel": bool(canonical and canonical.get("id") == m.get("id")),
            })
        return {
            "sentinel_key": "panel",
            "modes": modes,
            "grace_until": engine.grace_until_iso(),
            "last_request": engine.last_request(),
            "code_set": engine.code_is_set(),
            "arm_mode": mode.get("name"),
            "mode_id": mode.get("id"),
            "armed_as": engine.armed_kind(mode) if mode else None,
            "triggered_by": triggered,
            "open_sensors": [e.get("name", "") for e in engine.open_entities()],
            "exit_delay_until": engine.grace_until_iso(),
            "entry_delay_until": engine.pending_entry_until(),
            "pending_change": engine.request_info(),
        }

    async def _go(self, kind: str, code: str | None) -> None:
        mode = self.engine.mode_for_kind(kind)
        if mode is None:
            raise HomeAssistantError(
                f"Aucun mode Sentinel n'est associé à « {kind} » (réglage « Type d'armement » du mode)"
            )
        try:
            await self.engine.async_request_mode(mode["id"], code)
        except CodeError as err:
            raise ServiceValidationError(str(err)) from err

    async def async_alarm_disarm(self, code: str | None = None) -> None:
        await self._go("disarmed", code)

    async def async_alarm_arm_home(self, code: str | None = None) -> None:
        await self._go("armed_home", code)

    async def async_alarm_arm_away(self, code: str | None = None) -> None:
        await self._go("armed_away", code)

    async def async_alarm_arm_night(self, code: str | None = None) -> None:
        await self._go("armed_night", code)

    async def async_alarm_arm_vacation(self, code: str | None = None) -> None:
        await self._go("armed_vacation", code)

    async def async_alarm_arm_custom_bypass(self, code: str | None = None) -> None:
        await self._go("armed_custom_bypass", code)
