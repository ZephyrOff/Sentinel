"""API websocket utilisée par le panneau Sentinel."""
from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant, callback

from .const import DOMAIN


def _engine(hass: HomeAssistant):
    """Premier (et seul) moteur actif, ou None."""
    for value in hass.data.get(DOMAIN, {}).values():
        if hasattr(value, "snapshot"):
            return value
    return None


@callback
def async_register_websocket(hass: HomeAssistant) -> None:
    websocket_api.async_register_command(hass, ws_get_config)
    websocket_api.async_register_command(hass, ws_save_config)
    websocket_api.async_register_command(hass, ws_subscribe)


@websocket_api.websocket_command({vol.Required("type"): "sentinel/get_config"})
@callback
def ws_get_config(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    engine = _engine(hass)
    if engine is None:
        connection.send_error(msg["id"], "not_loaded", "Sentinel n'est pas chargé")
        return
    connection.send_result(
        msg["id"], {"config": engine.config, "snapshot": engine.snapshot()}
    )


@websocket_api.require_admin
@websocket_api.websocket_command(
    {vol.Required("type"): "sentinel/save_config", vol.Required("config"): dict}
)
@callback
def ws_save_config(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    engine = _engine(hass)
    if engine is None:
        connection.send_error(msg["id"], "not_loaded", "Sentinel n'est pas chargé")
        return
    needs_reload = engine.async_set_config(msg["config"])
    if needs_reload:
        # Les entités exposées changent (modes, zones, noms) : on recrée la plateforme.
        hass.async_create_task(
            hass.config_entries.async_reload(engine.entry.entry_id)
        )
    connection.send_result(msg["id"], {"reloaded": needs_reload})


@websocket_api.websocket_command({vol.Required("type"): "sentinel/subscribe"})
@callback
def ws_subscribe(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    engine = _engine(hass)
    if engine is None:
        connection.send_error(msg["id"], "not_loaded", "Sentinel n'est pas chargé")
        return

    @callback
    def _push() -> None:
        connection.send_message(
            websocket_api.event_message(msg["id"], engine.snapshot())
        )

    connection.subscriptions[msg["id"]] = engine.async_add_listener(_push)
    connection.send_result(msg["id"])
    _push()
