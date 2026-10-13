"""Sentinel : surveillance de la maison avec modèle 3D, modes et niveaux."""
from __future__ import annotations

import logging
from pathlib import Path

import voluptuous as vol

from homeassistant.components import frontend, panel_custom
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers.storage import Store

from .const import (
    DOMAIN,
    PANEL_URL_PATH,
    PLATFORMS,
    SERVICE_ACKNOWLEDGE,
    SERVICE_RESET_COUNTERS,
    SERVICE_SET_MODE,
    STORAGE_KEY,
    STORAGE_VERSION,
    URL_BASE,
    VERSION,
)
from .engine import SentinelEngine
from .websocket_api import async_register_websocket

_LOGGER = logging.getLogger(__name__)

FRONTEND_DIR = Path(__file__).parent / "frontend"
_FLAG_FRONTEND = f"{DOMAIN}_frontend_registered"


async def _async_register_frontend(hass: HomeAssistant) -> None:
    """Sert le panneau et l'ajoute à la barre latérale (une seule fois)."""
    if hass.data.get(_FLAG_FRONTEND):
        return
    try:
        from homeassistant.components.http import StaticPathConfig

        await hass.http.async_register_static_paths(
            [StaticPathConfig(URL_BASE, str(FRONTEND_DIR), False)]
        )
    except ImportError:  # Home Assistant plus ancien
        hass.http.register_static_path(URL_BASE, str(FRONTEND_DIR), False)

    await panel_custom.async_register_panel(
        hass,
        webcomponent_name="sentinel-panel",
        frontend_url_path=PANEL_URL_PATH,
        module_url=f"{URL_BASE}/sentinel-panel.js?v={VERSION}",
        sidebar_title="Sentinel",
        sidebar_icon="mdi:shield-home",
        require_admin=True,
        config={},
    )
    hass.data[_FLAG_FRONTEND] = True


def _register_services(hass: HomeAssistant) -> None:
    if hass.services.has_service(DOMAIN, SERVICE_SET_MODE):
        return

    def _engines() -> list[SentinelEngine]:
        return [v for v in hass.data.get(DOMAIN, {}).values() if isinstance(v, SentinelEngine)]

    async def _set_mode(call: ServiceCall) -> None:
        for engine in _engines():
            if not engine.async_set_mode(call.data["mode"]):
                _LOGGER.warning("Mode inconnu : %s", call.data["mode"])

    async def _acknowledge(call: ServiceCall) -> None:
        for engine in _engines():
            engine.async_acknowledge()

    async def _reset(call: ServiceCall) -> None:
        for engine in _engines():
            engine.async_reset_counters()

    hass.services.async_register(
        DOMAIN, SERVICE_SET_MODE, _set_mode,
        schema=vol.Schema({vol.Required("mode"): str}),
    )
    hass.services.async_register(DOMAIN, SERVICE_ACKNOWLEDGE, _acknowledge)
    hass.services.async_register(DOMAIN, SERVICE_RESET_COUNTERS, _reset)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    hass.data.setdefault(DOMAIN, {})

    store = Store(hass, STORAGE_VERSION, STORAGE_KEY)
    engine = SentinelEngine(hass, entry, store)
    await engine.async_load()
    hass.data[DOMAIN][entry.entry_id] = engine

    await _async_register_frontend(hass)
    if not hass.data.get(f"{DOMAIN}_ws"):
        async_register_websocket(hass)
        hass.data[f"{DOMAIN}_ws"] = True
    _register_services(hass)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    await engine.async_start()
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        engine: SentinelEngine = hass.data[DOMAIN].pop(entry.entry_id)
        engine.async_stop()
        if not any(isinstance(v, SentinelEngine) for v in hass.data[DOMAIN].values()):
            for service in (SERVICE_SET_MODE, SERVICE_ACKNOWLEDGE, SERVICE_RESET_COUNTERS):
                hass.services.async_remove(DOMAIN, service)
    return unloaded


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Retire le panneau quand l'intégration est supprimée."""
    if hass.data.pop(_FLAG_FRONTEND, None):
        frontend.async_remove_panel(hass, PANEL_URL_PATH)
