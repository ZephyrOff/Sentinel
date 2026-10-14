"""Sentinel : surveillance de la maison avec modèle 3D, modes et niveaux."""
from __future__ import annotations

import logging
from pathlib import Path

import voluptuous as vol

from homeassistant.components import frontend, panel_custom
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall, callback
try:  # HA récent
    from homeassistant.exceptions import ServiceValidationError
except ImportError:  # pragma: no cover
    from homeassistant.exceptions import HomeAssistantError as ServiceValidationError
from homeassistant.helpers import entity_registry as er
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
from .engine import CodeError, SentinelEngine
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
    # cartes Lovelace (statut, mode, digicode, zones, acquittement, chips, plan, journal)
    frontend.add_extra_js_url(hass, f"{URL_BASE}/sentinel-cards.js?v={VERSION}")
    hass.data[_FLAG_FRONTEND] = True


def _register_services(hass: HomeAssistant) -> None:
    if hass.services.has_service(DOMAIN, SERVICE_SET_MODE):
        return

    def _engines() -> list[SentinelEngine]:
        return [v for v in hass.data.get(DOMAIN, {}).values() if isinstance(v, SentinelEngine)]

    async def _set_mode(call: ServiceCall) -> None:
        code = call.data.get("code")
        for engine in _engines():
            try:
                result = await engine.async_request_mode(call.data["mode"], code)
            except CodeError as err:
                raise ServiceValidationError(str(err)) from err
            if result == "unknown":
                _LOGGER.warning("Mode inconnu : %s", call.data["mode"])

    async def _acknowledge(call: ServiceCall) -> None:
        ref = call.data.get("entity_id")
        found = False
        for engine in _engines():
            found = bool(engine.async_acknowledge(ref)) or found
        if ref and not found:
            _LOGGER.warning("Acquittement : entité inconnue %s", ref)

    async def _reset(call: ServiceCall) -> None:
        for engine in _engines():
            engine.async_reset_counters()

    hass.services.async_register(
        DOMAIN, SERVICE_SET_MODE, _set_mode,
        schema=vol.Schema({vol.Required("mode"): str, vol.Optional("code"): vol.Coerce(str)}),
    )
    hass.services.async_register(
        DOMAIN, SERVICE_ACKNOWLEDGE, _acknowledge,
        schema=vol.Schema({vol.Optional("entity_id"): str}),
    )
    hass.services.async_register(DOMAIN, SERVICE_RESET_COUNTERS, _reset)


@callback
def _async_sync_registry(hass: HomeAssistant, entry: ConfigEntry, engine: SentinelEngine) -> None:
    """Aligne le registre d'entités sur la configuration, avant la création des entités.

    - supprime les entités orphelines (pièce supprimée, entité masquée) qui
      réserveraient des identifiants et feraient apparaître des « _2 », « _8 »… ;
    - applique les identifiants renommés dans le panneau : le registre garde
      sinon l'ancien entity_id, ce qui rendait le renommage sans effet.
    """
    registry = er.async_get(hass)
    items = engine.items()
    wanted = {f"{entry.entry_id}_{it['key']}": it for it in items}

    for reg_entry in er.async_entries_for_config_entry(registry, entry.entry_id):
        if reg_entry.platform == DOMAIN and reg_entry.unique_id not in wanted:
            registry.async_remove(reg_entry.entity_id)

    explicit = (engine.config.get("cfg") or {}).get("ids") or {}
    changed = False
    for unique_id, item in wanted.items():
        current = registry.async_get_entity_id(item["domain"], DOMAIN, unique_id)
        if current is None:
            continue
        key = item["key"]
        desired = f"{item['domain']}.{item['object_id']}"
        # on ne renomme que ce que Sentinel a posé lui-même, ou ce que l'utilisateur
        # a demandé explicitement dans le panneau ; un renommage manuel est respecté
        mine = engine.applied_ids.get(key) in (None, current)
        if current != desired and (explicit.get(key) or mine):
            holder = registry.async_get(desired)
            if holder is None or holder.unique_id == unique_id:
                registry.async_update_entity(current, new_entity_id=desired)
                current = desired
        if (current == desired or mine) and engine.applied_ids.get(key) != current:
            engine.applied_ids[key] = current
            changed = True
    stale = [k for k in engine.applied_ids if f"{entry.entry_id}_{k}" not in wanted]
    for key in stale:
        del engine.applied_ids[key]
        changed = True
    if changed:
        engine.save_now_soon()


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

    _async_sync_registry(hass, entry, engine)
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
