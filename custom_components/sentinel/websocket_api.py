"""API websocket utilisée par le panneau Sentinel."""
from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect

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
    websocket_api.async_register_command(hass, ws_set_code)
    websocket_api.async_register_command(hass, ws_set_mode)


@websocket_api.websocket_command({vol.Required("type"): "sentinel/get_config"})
@callback
def ws_get_config(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    engine = _engine(hass)
    if engine is None:
        connection.send_error(msg["id"], "not_loaded", "Sentinel n'est pas chargé")
        return
    # le code d'armement (haché) ne quitte jamais le serveur
    config = {**engine.config, "cfg": {k: v for k, v in engine.config["cfg"].items()
                                       if k not in ("code_hash", "code_salt")}}
    config["cfg"]["code_set"] = engine.code_is_set()
    config["modes"] = [
        {**{k: v for k, v in m.items() if k not in ("code_hash", "code_salt")},
         "code_set": bool(m.get("code_hash"))}
        for m in engine.config["modes"]
    ]
    connection.send_result(msg["id"], {"config": config, "snapshot": engine.snapshot()})


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


@websocket_api.require_admin
@websocket_api.websocket_command(
    {vol.Required("type"): "sentinel/set_code", vol.Required("code"): vol.Coerce(str),
     vol.Optional("mode"): str}
)
@callback
def ws_set_code(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    engine = _engine(hass)
    if engine is None:
        connection.send_error(msg["id"], "not_loaded", "Sentinel n'est pas chargé")
        return
    code = msg["code"].strip()
    if code and not (4 <= len(code) <= 12 and code.isdigit()):
        connection.send_error(msg["id"], "invalid_code", "Le code doit faire 4 à 12 chiffres")
        return
    engine.async_set_code(code, msg.get("mode"))
    connection.send_result(msg["id"], {"code_set": bool(code)})


@websocket_api.require_admin
@websocket_api.websocket_command({vol.Required("type"): "sentinel/set_mode", vol.Required("mode"): str})
@callback
def ws_set_mode(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    """Changement de mode depuis le panneau (administrateur) : sans code."""
    engine = _engine(hass)
    if engine is None or not engine.async_set_mode(msg["mode"]):
        connection.send_error(msg["id"], "unknown_mode", "Mode inconnu")
        return
    connection.send_result(msg["id"], {})


@websocket_api.websocket_command({vol.Required("type"): "sentinel/subscribe"})
@callback
def ws_subscribe(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    engine = _engine(hass)
    if engine is None:
        connection.send_error(msg["id"], "not_loaded", "Sentinel n'est pas chargé")
        return

    # Abonné au signal de mise à jour plutôt qu'à l'objet moteur : quand la
    # configuration change et que l'intégration est rechargée, un nouveau moteur
    # prend la suite sous le même signal et le panneau continue de recevoir les états.
    @callback
    def _push() -> None:
        current = _engine(hass)
        if current is None:
            return
        connection.send_message(
            websocket_api.event_message(msg["id"], current.snapshot())
        )

    connection.subscriptions[msg["id"]] = async_dispatcher_connect(
        hass, engine.signal, _push
    )
    connection.send_result(msg["id"])
    _push()
