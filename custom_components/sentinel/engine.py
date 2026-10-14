"""Moteur Sentinel : suit les entités, applique les modes, notifie."""
from __future__ import annotations

import logging
import secrets
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CALLBACK_TYPE, Event, HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import (
    async_call_later,
    async_track_state_change_event,
    async_track_time_interval,
)
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import (
    EVENT_CLEARED,
    EVENT_TRIGGERED,
    JOURNAL_SIZE,
    SIGNAL_UPDATE,
)
from .logic import (
    new_code_record,
    verify_code,
    STATUS_RANK,
    current_mode,
    default_config,
    effective_delay,
    entity_key,
    exposed_items,
    fill_template,
    fill_data,
    find_mode,
    action_call,
    adjacent_room_ids,
    reactions_for,
    template_values,
    normalize_config,
    resolve_level,
    room_of,
    state_is_on,
    structure_signature,
)

_LOGGER = logging.getLogger(__name__)


class CodeError(Exception):
    """Code d'armement manquant, incorrect ou verrouillé."""


def _fill(template: str, values: dict[str, str]) -> str:
    return fill_data(str(template or ""), values)


class SentinelEngine:
    """Évalue les entités configurées et publie statuts, compteurs et alertes."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, store: Store) -> None:
        self.hass = hass
        self.entry = entry
        self.store = store
        self.config: dict[str, Any] = default_config()
        self.signal = SIGNAL_UPDATE.format(entry.entry_id)

        self._unsub_states: CALLBACK_TYPE | None = None
        self._triggered: dict[str, str] = {}      # id entité -> niveau déclenché
        self._on_since: dict[str, datetime] = {}  # id entité -> début d'activation
        self._delay_timers: dict[str, CALLBACK_TYPE] = {}
        self._repeat_timers: dict[str, CALLBACK_TYPE] = {}
        self._acked: set[str] = set()
        self._grace_until: datetime | None = None
        self._listeners: list[Callable[[], None]] = []

        self.journal: list[dict[str, Any]] = []
        self.last_trigger: dict[str, Any] | None = None
        self.total_since_reset = 0
        # clé exposée -> entity_id que Sentinel a lui-même attribué (pour ne pas
        # écraser un renommage fait à la main dans Home Assistant)
        self.applied_ids: dict[str, str] = {}
        # id entité -> pire niveau atteint depuis le dernier acquittement
        # (affiché dans le statut / l'alarme / les zones si la mémorisation est activée)
        self._latched: dict[str, str] = {}
        # id entité -> {"until": datetime, "kind": "exit" | "entry"} : déclenchement en attente
        self._pending: dict[str, dict[str, Any]] = {}
        # détecteurs de mouvement : une personne est considérée présente tant qu'aucun
        # mouvement n'est vu dans une pièce voisine (ou jusqu'à expiration)
        self._hold: dict[str, datetime] = {}
        # demande de changement de mode en attente de validation sur un appareil de confiance
        self._mode_request: dict[str, Any] | None = None
        self._request_timer: CALLBACK_TYPE | None = None
        self._unsub_action: CALLBACK_TYPE | None = None
        # essais de code incorrects consécutifs et verrouillage temporaire
        self._code_fails = 0
        self._code_locked_until: datetime | None = None
        # entités acquittées individuellement : détection suspendue jusqu'à `until`
        # (durée réglable dans le mode : ackDelay)
        self._silenced: dict[str, datetime] = {}
        self._silence_timers: dict[str, CALLBACK_TYPE] = {}
        # détecteurs de mouvement acquittés : on attend une nouvelle détection (retour
        # à « off » puis « on ») avant de les réarmer, au lieu de reprendre la même
        self._rearm: set[str] = set()
        # zone quittée : désactivation différée quand un mouvement apparaît dans une zone voisine
        self._handoff_timers: dict[str, CALLBACK_TYPE] = {}
        self._hold_timers: dict[str, CALLBACK_TYPE] = {}
        self._signature = ""

    # ------------------------------------------------------------------ #
    # Cycle de vie
    # ------------------------------------------------------------------ #
    async def async_load(self) -> None:
        data = await self.store.async_load()
        if isinstance(data, dict):
            self.config = normalize_config(data.get("config"))
            self.total_since_reset = int(data.get("total_since_reset", 0))
            latched = data.get("latched")
            self._latched = (
                {k: v for k, v in latched.items() if v in STATUS_RANK}
                if isinstance(latched, dict) else {}
            )
            applied = data.get("applied_ids")
            self.applied_ids = dict(applied) if isinstance(applied, dict) else {}
        else:
            self.config = default_config()
        self._signature = structure_signature(self.config)

    async def async_start(self) -> None:
        self._unsub_action = self.hass.bus.async_listen(
            "mobile_app_notification_action", self._handle_action
        )
        self._track_states()
        # Au démarrage on constate l'existant sans notifier.
        self._evaluate_all(silent=True)
        self._notify()

    @callback
    def async_stop(self) -> None:
        if self._unsub_action:
            self._unsub_action()
            self._unsub_action = None
        if self._request_timer:
            self._request_timer()
            self._request_timer = None
        if self._unsub_states:
            self._unsub_states()
            self._unsub_states = None
        for timers in (self._delay_timers, self._repeat_timers, self._hold_timers,
                       self._silence_timers, self._handoff_timers):
            for cancel in timers.values():
                cancel()
            timers.clear()
        self._listeners.clear()

    def save_now_soon(self) -> None:
        self._save_soon()

    def _save_soon(self) -> None:
        self.store.async_delay_save(
            lambda: {
                "config": self.config,
                "total_since_reset": self.total_since_reset,
                "applied_ids": self.applied_ids,
                "latched": self._latched,
            },
            1.0,
        )

    # ------------------------------------------------------------------ #
    # Configuration
    # ------------------------------------------------------------------ #
    @callback
    def async_set_config(self, raw: dict[str, Any]) -> bool:
        """Applique une nouvelle configuration. Retourne True si un rechargement
        de l'intégration est nécessaire (entités exposées modifiées)."""
        previous = self.config.get("cfg") or {}
        previous_modes = {m.get("id"): m for m in self.config.get("modes", [])}
        self.config = normalize_config(raw)
        for mode in self.config["modes"]:
            old = previous_modes.get(mode.get("id")) or {}
            for key in ("code_hash", "code_salt"):
                if old.get(key) and not mode.get(key):
                    mode[key] = old[key]
        for key in ("code_hash", "code_salt"):
            if previous.get(key) and not (raw.get("cfg") or {}).get(key):
                self.config["cfg"][key] = previous[key]
        if not (self._flag("latch_alarm") or self._flag("latch_zones")):
            self._latched.clear()
        self._save_soon()
        signature = structure_signature(self.config)
        needs_reload = signature != self._signature
        self._signature = signature
        if not needs_reload:
            self._track_states()
            self._evaluate_all(silent=False)
            self._notify()
        return needs_reload

    def items(self) -> list[dict[str, Any]]:
        return exposed_items(self.config)

    # ------------------------------------------------------------------ #
    # Suivi des états
    # ------------------------------------------------------------------ #
    def _track_states(self) -> None:
        if self._unsub_states:
            self._unsub_states()
            self._unsub_states = None
        entity_ids = sorted({entity_key(e) for e in self.config["ents"] if entity_key(e)})
        if entity_ids:
            self._unsub_states = async_track_state_change_event(
                self.hass, entity_ids, self._handle_state_event
            )

    @callback
    def _handle_state_event(self, event: Event) -> None:
        entity_id = event.data.get("entity_id")
        for ent in self.config["ents"]:
            if entity_key(ent) == entity_id:
                self._track_motion(ent)
                self._evaluate(ent, silent=False)
        self._notify()

    def _raw_on(self, ent: dict[str, Any]) -> bool:
        entity_id = entity_key(ent)
        if not entity_id:
            return False
        state = self.hass.states.get(entity_id)
        return state_is_on(state.state if state else None, bool(ent.get("invert")))

    def _is_on(self, ent: dict[str, Any]) -> bool:
        return self._raw_on(ent) or ent.get("id") in self._hold

    # -- présence maintenue des détecteurs de mouvement --
    def _hold_minutes(self) -> int:
        return int((self.config.get("cfg") or {}).get("hold_minutes") or 30)

    def _track_motion(self, ent: dict[str, Any]) -> None:
        """Un mouvement détecté maintient la présence ; il libère les pièces voisines."""
        if ent.get("type") != "motion":
            return
        raw = self._raw_on(ent)
        if ent["id"] in self._rearm:
            if not raw:
                self._rearm.discard(ent["id"])   # le capteur est retombé : prêt pour la suite
            return                               # même détection : on n'y revient pas
        if not raw:
            return
        ent_id = ent["id"]
        self._cancel_handoff(ent_id)          # nouveau mouvement ici : la zone reste occupée
        self._hold[ent_id] = dt_util.utcnow()
        old = self._hold_timers.pop(ent_id, None)
        if old:
            old()

        @callback
        def _expire(_now: datetime) -> None:
            self._hold_timers.pop(ent_id, None)
            self._release_hold(ent_id)
            self._notify()

        self._hold_timers[ent_id] = async_call_later(
            self.hass, self._hold_minutes() * 60, _expire
        )
        room = room_of(self.config, ent)
        near = adjacent_room_ids(self.config, room["id"]) if room else set()
        delay = self._handoff_delay()
        for other in self.config["ents"]:
            oid = other.get("id")
            if oid == ent_id or other.get("type") != "motion":
                continue
            orm = room_of(self.config, other)
            if not orm or orm.get("id") not in near:
                continue
            active = (oid in self._hold or self._raw_on(other)
                      or oid in self._triggered or oid in self._pending)
            if active:
                self._schedule_handoff(oid, delay)

    # -- zone quittée --
    def _handoff_delay(self) -> int:
        try:
            value = int(current_mode(self.config).get("handoffDelay", 10))
        except (TypeError, ValueError):
            value = 10
        return max(0, min(value, 3600))

    def _schedule_handoff(self, ent_id: str, seconds: int) -> None:
        if ent_id in self._handoff_timers:
            return                            # déjà programmé : le premier mouvement compte
        if seconds <= 0:
            self._handoff(ent_id)
            return

        @callback
        def _fire(_now: datetime) -> None:
            self._handoff_timers.pop(ent_id, None)
            self._handoff(ent_id)
            self._notify()

        self._handoff_timers[ent_id] = async_call_later(self.hass, seconds, _fire)

    def _cancel_handoff(self, ent_id: str) -> None:
        cancel = self._handoff_timers.pop(ent_id, None)
        if cancel:
            cancel()

    def _handoff(self, ent_id: str) -> None:
        """La personne est passée dans la zone voisine : cette zone n'est plus occupée,
        même si son détecteur reste « on » (longue temporisation d'extinction)."""
        ent = next((e for e in self.config["ents"] if e["id"] == ent_id), None)
        if ent is None:
            return
        if self._raw_on(ent):
            self._rearm.add(ent_id)           # on attend une nouvelle détection
        self._hold.pop(ent_id, None)
        cancel = self._hold_timers.pop(ent_id, None)
        if cancel:
            cancel()
        self._on_since.pop(ent_id, None)
        self._cancel_timers(ent_id)
        self._evaluate(ent, silent=False)     # retour à la normale si elle était déclenchée

    def _release_hold(self, ent_id: str) -> None:
        if self._hold.pop(ent_id, None) is None:
            return
        cancel = self._hold_timers.pop(ent_id, None)
        if cancel:
            cancel()
        ent = next((e for e in self.config["ents"] if e["id"] == ent_id), None)
        if ent is not None:
            self._evaluate(ent, silent=False)

    def _clear_holds(self) -> None:
        for cancel in self._hold_timers.values():
            cancel()
        self._hold_timers.clear()
        self._hold.clear()

    # ------------------------------------------------------------------ #
    # Évaluation
    # ------------------------------------------------------------------ #
    def _evaluate_all(self, silent: bool) -> None:
        known = {e["id"] for e in self.config["ents"]}
        for ent_id in [i for i in self._latched if i not in known]:
            del self._latched[ent_id]
        for ent_id in [i for i in self._hold if i not in known]:
            self._release_hold(ent_id)
        for ent_id in [i for i in self._silenced if i not in known]:
            self._end_silence(ent_id, evaluate=False)
        for ent_id in [i for i in self._triggered if i not in known]:
            self._triggered.pop(ent_id, None)
            self._cancel_timers(ent_id)
        for ent in self.config["ents"]:
            self._evaluate(ent, silent=silent)

    def _cancel_timers(self, ent_id: str) -> None:
        self._pending.pop(ent_id, None)
        for timers in (self._delay_timers, self._repeat_timers):
            cancel = timers.pop(ent_id, None)
            if cancel:
                cancel()

    def _evaluate(self, ent: dict[str, Any], silent: bool) -> None:
        ent_id = ent["id"]
        mode = current_mode(self.config)
        room = room_of(self.config, ent)
        on = self._is_on(ent)
        if ent_id in self._rearm and self._raw_on(ent):
            on = False
        level = resolve_level(mode, ent, room) if on else "off"
        previous = self._triggered.get(ent_id)

        if ent_id in self._silenced and on:
            return      # détection suspendue après acquittement

        if not on or level == "off":
            if not on:
                self._on_since.pop(ent_id, None)
            self._cancel_timers(ent_id)
            self._acked.discard(ent_id)
            if previous is not None:
                del self._triggered[ent_id]
                if not silent:
                    self._on_clear(ent, room, mode, previous)
            return

        now = dt_util.utcnow()
        since = self._on_since.setdefault(ent_id, now)
        remaining = effective_delay(mode, ent, level) - (now - since).total_seconds()
        kind = "entry"
        if self._grace_until and now < self._grace_until:
            grace_left = (self._grace_until - now).total_seconds()
            if grace_left >= remaining:
                kind = "exit"
            remaining = max(remaining, grace_left)

        if remaining > 0 and not silent:
            self._pending[ent_id] = {"until": now + timedelta(seconds=remaining), "kind": kind}
            self._schedule_recheck(ent_id, remaining)
            return
        if previous == level:
            return

        self._triggered[ent_id] = level
        memo = self._latched.get(ent_id)
        if memo is None or STATUS_RANK.get(level, 0) > STATUS_RANK.get(memo, 0):
            self._latched[ent_id] = level
        self._cancel_timers(ent_id)
        self._acked.discard(ent_id)
        if not silent:
            self._on_trigger(ent, room, mode, level)

    def _schedule_recheck(self, ent_id: str, seconds: float) -> None:
        old = self._delay_timers.pop(ent_id, None)
        if old:
            old()

        @callback
        def _fire(_now: datetime) -> None:
            self._delay_timers.pop(ent_id, None)
            ent = next((e for e in self.config["ents"] if e["id"] == ent_id), None)
            if ent is not None:
                self._evaluate(ent, silent=False)
                self._notify()

        self._delay_timers[ent_id] = async_call_later(self.hass, seconds + 0.05, _fire)

    # ------------------------------------------------------------------ #
    # Réactions
    # ------------------------------------------------------------------ #
    def _log(self, text: str, level: str = "", note: dict[str, str] | None = None) -> None:
        self.journal.insert(
            0,
            {"t": dt_util.now().strftime("%H:%M:%S"), "text": text, "lv": level, "note": note},
        )
        del self.journal[JOURNAL_SIZE:]

    # -- réactions : notification + actions, au niveau du mode ou propres à une zone --
    def _floor_name(self, room: dict[str, Any] | None) -> str:
        if not room:
            return ""
        for floor in self.config.get("floors", []):
            if floor.get("id") == room.get("floor"):
                return str(floor.get("name", ""))
        return ""

    def _values(self, ent: dict[str, Any] | None, room: dict[str, Any] | None,
                mode: dict[str, Any], level: str, on: bool = True) -> dict[str, str]:
        return template_values(ent, room, mode, level, on, self._floor_name(room), dt_util.now())

    def _react(self, event: str, ent: dict[str, Any] | None, room: dict[str, Any] | None,
               mode: dict[str, Any], level: str, on: bool = True
               ) -> tuple[dict[str, str] | None, list[dict[str, Any]]]:
        """Envoie les notifications et lance les actions de l'évènement.

        Retourne (note pour le journal, configurations de notification actives)."""
        notifies, actions = reactions_for(mode, room, event)
        values = self._values(ent, room, mode, level, on)
        sent: list[dict[str, str]] = []
        for cfg in notifies:
            if cfg.get("on") and cfg.get("target"):
                message = _fill(cfg.get("msg", ""), values)
                sent.append({"target": cfg["target"], "msg": message})
                self.hass.async_create_task(self._send(cfg["target"], message, mode))
        for action in actions:
            call = action_call(action, values)
            if call is None:
                continue
            domain, service, data = call
            self._log(f"Action : {domain}.{service}" + (f" ({data['entity_id']})" if isinstance(data.get("entity_id"), str) else ""))
            self.hass.async_create_task(self._call(domain, service, data))
        note = None
        if sent:
            note = {"target": ", ".join(dict.fromkeys(n["target"] for n in sent)), "msg": sent[0]["msg"]}
        return note, [c for c in notifies if c.get("on") and c.get("target")]

    def _on_trigger(self, ent: dict[str, Any], room: dict[str, Any] | None,
                    mode: dict[str, Any], level: str) -> None:
        zone = room.get("name", "") if room else ""
        self.total_since_reset += 1
        self._save_soon()
        self.last_trigger = {
            "name": ent.get("name", ""), "level": level, "zone": zone,
            "time": dt_util.now().strftime("%H:%M:%S"), "entity_id": ent.get("entity"),
        }
        note, active = self._react(level, ent, room, mode, level)
        repeats = [int(c.get("repeat") or 0) for c in active if int(c.get("repeat") or 0) > 0]
        if repeats:
            self._start_repeat(ent, room, mode, level, min(repeats))
        label = f"{ent.get('name', '')} ({zone})" if zone else ent.get("name", "")
        self._log(f"{label} : déclenché", level, note)
        self.hass.bus.async_fire(
            EVENT_TRIGGERED,
            {"entity_id": ent.get("entity"), "sentinel_id": ent["id"],
             "name": ent.get("name"), "level": level, "zone": zone, "mode": mode.get("name")},
        )

    def _on_clear(self, ent: dict[str, Any], room: dict[str, Any] | None,
                  mode: dict[str, Any], level: str = "") -> None:
        zone = room.get("name", "") if room else ""
        label = f"{ent.get('name', '')} ({zone})" if zone else ent.get("name", "")
        note, _ = self._react("clear", ent, room, mode, level, on=False)
        self._log(f"{label} : retour à la normale", "", note)
        self.hass.bus.async_fire(
            EVENT_CLEARED,
            {"entity_id": ent.get("entity"), "sentinel_id": ent["id"],
             "name": ent.get("name"), "zone": zone, "mode": mode.get("name")},
        )

    def _start_repeat(self, ent: dict[str, Any], room: dict[str, Any] | None,
                      mode: dict[str, Any], level: str, minutes: int) -> None:
        ent_id = ent["id"]

        @callback
        def _again(_now: datetime) -> None:
            if self._triggered.get(ent_id) != level or ent_id in self._acked:
                return
            current = current_mode(self.config)
            notifies, _actions = reactions_for(current, room, level)
            values = self._values(ent, room, current, level)
            for cfg in notifies:
                if int(cfg.get("repeat") or 0) > 0 and cfg.get("on") and cfg.get("target"):
                    self.hass.async_create_task(
                        self._send(cfg["target"], _fill(cfg.get("msg", ""), values), mode)
                    )

        self._repeat_timers[ent_id] = async_track_time_interval(
            self.hass, _again, timedelta(minutes=minutes)
        )

    async def _call(self, domain: str, service: str, data: dict[str, Any]) -> None:
        if not self.hass.services.has_service(domain, service):
            _LOGGER.warning("Service d'action introuvable : %s.%s", domain, service)
            return
        try:
            await self.hass.services.async_call(domain, service, data, blocking=False)
        except Exception:  # noqa: BLE001 - une action ratée ne doit rien casser
            _LOGGER.exception("Échec de l'action %s.%s", domain, service)

    async def _send(self, target: str, message: str, mode: dict[str, Any]) -> None:
        domain, _, service = target.partition(".")
        if not service or not self.hass.services.has_service(domain, service):
            _LOGGER.warning("Service de notification introuvable : %s", target)
            return
        try:
            await self.hass.services.async_call(
                domain, service,
                {"message": message, "title": f"Sentinel · {mode.get('name', '')}"},
                blocking=False,
            )
        except Exception:  # noqa: BLE001 - une notification ratée ne doit rien casser
            _LOGGER.exception("Échec de l'envoi de la notification vers %s", target)

    # ------------------------------------------------------------------ #
    # Actions publiques (services, entités, websocket)
    # ------------------------------------------------------------------ #
    # -- code d'armement --
    def code_is_set(self) -> bool:
        return bool((self.config.get("cfg") or {}).get("code_hash"))

    def _code_record(self, mode: dict[str, Any]) -> dict[str, Any] | None:
        """Code propre au mode, sinon code global (ancienne configuration)."""
        if mode.get("code_hash"):
            return mode
        cfg = self.config.get("cfg") or {}
        return cfg if cfg.get("code_hash") else None

    def mode_needs_code(self, mode: dict[str, Any]) -> bool:
        return bool(mode.get("needCode")) and self._code_record(mode) is not None

    def confirm_target(self, mode: dict[str, Any]) -> str:
        return str(mode.get("confirmTarget") or (self.config.get("cfg") or {}).get("confirm_target") or "")

    def mode_needs_confirm(self, mode: dict[str, Any]) -> bool:
        return bool(mode.get("needConfirm")) and bool(self.confirm_target(mode))

    @callback
    def async_set_code(self, code: str, mode_id: str | None = None) -> None:
        holder: dict[str, Any] | None
        if mode_id:
            holder = next((m for m in self.config["modes"] if m.get("id") == mode_id), None)
            if holder is None:
                return
        else:
            holder = self.config.setdefault("cfg", {})
        if code:
            holder.update(new_code_record(code))
        else:
            holder.pop("code_hash", None)
            holder.pop("code_salt", None)
        self._code_fails = 0
        self._code_locked_until = None
        self._save_soon()
        self._notify()

    def check_mode_code(self, ref: str, code: str | None) -> None:
        """Lève CodeError si le mode demande un code et qu'il n'est pas valable."""
        mode = find_mode(self.config, ref)
        if mode is None or not self.mode_needs_code(mode):
            return
        record = self._code_record(mode) or {}
        now = dt_util.utcnow()
        if self._code_locked_until and now < self._code_locked_until:
            left = int((self._code_locked_until - now).total_seconds()) + 1
            raise CodeError(f"Trop d'essais, réessaie dans {left} s")
        if verify_code(record, code):
            self._code_fails = 0
            return
        self._code_fails += 1
        if self._code_fails >= 5:
            self._code_fails = 0
            self._code_locked_until = now + timedelta(seconds=60)
            raise CodeError("Trop d'essais, verrouillé 60 s")
        raise CodeError("Code incorrect" if code else "Code requis")

    # -- demande de changement de mode (code, puis validation sur un appareil de confiance) --
    async def async_request_mode(self, ref: str, code: str | None = None) -> str:
        """Retourne "applied", "pending" (validation attendue) ou "unknown".

        Lève CodeError si le mode demande un code absent ou incorrect."""
        mode = find_mode(self.config, ref)
        if mode is None:
            return "unknown"
        if mode.get("id") == self.config.get("mode"):
            return "applied"
        self.check_mode_code(ref, code)
        if self.mode_needs_confirm(mode):
            self._start_request(mode)
            return "pending"
        self.async_set_mode(mode["id"])
        return "applied"

    def request_info(self) -> dict[str, Any] | None:
        req = self._mode_request
        if not req:
            return None
        return {"mode_id": req["mode_id"], "mode": req["name"], "until": req["until"].isoformat()}

    def _clear_request(self) -> None:
        self._mode_request = None
        if self._request_timer:
            self._request_timer()
            self._request_timer = None

    def _start_request(self, mode: dict[str, Any]) -> None:
        self._clear_request()
        token = secrets.token_hex(6)
        seconds = int(mode.get("confirmTimeout") or 120)
        target = self.confirm_target(mode)
        self._mode_request = {
            "token": token, "mode_id": mode["id"], "name": mode.get("name", ""),
            "until": dt_util.utcnow() + timedelta(seconds=seconds),
        }

        @callback
        def _expire(_now: datetime) -> None:
            self._request_timer = None
            if self._mode_request and self._mode_request["token"] == token:
                self._mode_request = None
                self._log(f"Demande « {mode.get('name', '')} » expirée")
                self._notify()

        self._request_timer = async_call_later(self.hass, seconds, _expire)
        self._log(f"Validation demandée : « {mode.get('name', '')} »")
        self.hass.async_create_task(self._send_confirmation(target, token, mode))
        self._notify()

    async def _send_confirmation(self, target: str, token: str, mode: dict[str, Any]) -> None:
        domain, _, service = target.partition(".")
        if not service or not self.hass.services.has_service(domain, service):
            _LOGGER.warning("Service de validation introuvable : %s", target)
            return
        data = {
            "title": "Sentinel",
            "message": f"Activer le mode « {mode.get('name', '')} » ?",
            "data": {
                "tag": "sentinel_confirm",
                "actions": [
                    {"action": f"SENTINEL_CONFIRM_{token}", "title": "Valider"},
                    {"action": f"SENTINEL_REJECT_{token}", "title": "Refuser", "destructive": True},
                ],
                "push": {"interruption-level": "time-sensitive"},
            },
        }
        try:
            await self.hass.services.async_call(domain, service, data, blocking=False)
        except Exception:  # noqa: BLE001
            _LOGGER.exception("Échec de l'envoi de la demande de validation vers %s", target)

    @callback
    def _handle_action(self, event: Event) -> None:
        action = str((event.data or {}).get("action") or "")
        req = self._mode_request
        if not req or not action.startswith("SENTINEL_"):
            return
        confirm = action == f"SENTINEL_CONFIRM_{req['token']}"
        reject = action == f"SENTINEL_REJECT_{req['token']}"
        if not (confirm or reject) or dt_util.utcnow() > req["until"]:
            return
        mode_id, name = req["mode_id"], req["name"]
        self._clear_request()
        if confirm:
            self._log(f"Validé sur l'appareil : « {name} »")
            self.async_set_mode(mode_id)
        else:
            self._log(f"Refusé sur l'appareil : « {name} »")
            self._notify()

    @callback
    def async_set_mode(self, ref: str) -> bool:
        mode = find_mode(self.config, ref)
        if mode is None:
            return False
        if mode.get("id") == self.config.get("mode"):
            return True
        self.config["mode"] = mode["id"]
        grace = int(mode.get("exitDelay") or 0)
        self._grace_until = dt_util.utcnow() + timedelta(seconds=grace) if grace else None
        # Nouveau mode : on repart de zéro, ce qui est déjà ouvert est réévalué
        # (et notifié) selon les règles du nouveau mode, après le délai de sortie.
        for ent_id in list(self._triggered) + list(self._delay_timers):
            self._cancel_timers(ent_id)
        self._clear_request()
        self._triggered.clear()
        self._acked.clear()
        self._clear_silences()
        self._rearm.clear()
        for cancel in self._handoff_timers.values():
            cancel()
        self._handoff_timers.clear()
        self._clear_holds()
        self._log(f"Mode « {mode.get('name', '')} » activé")
        note, _cfg = self._react("enter", None, None, mode, "")
        if note:
            self._log(f"Notification : {note['target']}", "", note)
        self._save_soon()
        self._evaluate_all(silent=False)
        if grace:
            # Réévalue à la fin du délai de sortie.
            async_call_later(self.hass, grace + 0.1, self._after_grace)
        self._notify()
        return True

    @callback
    def _after_grace(self, _now: datetime) -> None:
        self._grace_until = None
        self._evaluate_all(silent=False)
        self._notify()

    @callback
    def async_acknowledge(self, ref: str | None = None) -> bool:
        """Acquitte tout, ou une seule entité (identifiant Sentinel ou entité Home Assistant)."""
        if ref:
            return self._acknowledge_entity(str(ref).strip())
        self._acked.update(self._triggered)
        for cancel in self._repeat_timers.values():
            cancel()
        self._repeat_timers.clear()
        self._latched.clear()
        # la présence maintenue des détecteurs de mouvement est aussi acquittée
        for ent_id in list(self._hold):
            self._release_hold(ent_id)
        self._save_soon()
        self._log("Alertes acquittées")
        self._notify()
        return True

    def _ack_delay(self) -> int:
        try:
            value = int(current_mode(self.config).get("ackDelay", 60))
        except (TypeError, ValueError):
            value = 60
        return max(0, min(value, 3600))

    def _start_silence(self, ent_id: str, seconds: int) -> None:
        old = self._silence_timers.pop(ent_id, None)
        if old:
            old()
        if seconds <= 0:
            self._silenced.pop(ent_id, None)
            return
        self._silenced[ent_id] = dt_util.utcnow() + timedelta(seconds=seconds)

        @callback
        def _done(_now: datetime) -> None:
            self._silence_timers.pop(ent_id, None)
            self._end_silence(ent_id, evaluate=True)
            self._notify()

        self._silence_timers[ent_id] = async_call_later(self.hass, seconds, _done)

    def _end_silence(self, ent_id: str, evaluate: bool) -> None:
        self._silenced.pop(ent_id, None)
        cancel = self._silence_timers.pop(ent_id, None)
        if cancel:
            cancel()
        if evaluate:
            ent = next((e for e in self.config["ents"] if e["id"] == ent_id), None)
            if ent is not None:
                self._evaluate(ent, silent=False)   # toujours actif : le délai repart

    def _clear_silences(self) -> None:
        for cancel in self._silence_timers.values():
            cancel()
        self._silence_timers.clear()
        self._silenced.clear()

    def _acknowledge_entity(self, ref: str) -> bool:
        """Acquitte une entité en cours de détection (délai) ou déclenchée."""
        key = ref.lower()
        ent = next(
            (e for e in self.config["ents"] if e.get("id") == ref or entity_key(e) == key),
            None,
        )
        if ent is None:
            return False
        ent_id = ent["id"]
        self._cancel_timers(ent_id)        # délai en cours et rappels
        self._triggered.pop(ent_id, None)
        self._latched.pop(ent_id, None)
        self._acked.discard(ent_id)
        self._on_since.pop(ent_id, None)
        if ent_id in self._hold and not self._raw_on(ent):
            self._release_hold(ent_id)     # présence maintenue : la personne est partie
        if ent.get("type") == "motion":
            # on remet la zone à zéro : plus de présence maintenue pour ses détecteurs,
            # et la détection en cours n'est pas reprise à la fin de la pause
            room = room_of(self.config, ent)
            group = [ent]
            if room:
                group += [
                    o for o in self.config["ents"]
                    if o.get("id") != ent_id and o.get("type") == "motion"
                    and (room_of(self.config, o) or {}).get("id") == room.get("id")
                ]
            for o in group:
                if self._raw_on(o):
                    self._rearm.add(o["id"])
            for o in group:
                if o["id"] != ent_id:
                    self._triggered.pop(o["id"], None)
                    self._cancel_timers(o["id"])
                self._release_hold(o["id"])
        seconds = self._ack_delay()
        self._start_silence(ent_id, seconds)
        zone = (room_of(self.config, ent) or {}).get("name", "")
        self._log(
            f"Acquitté : {ent.get('name', '')}" + (f" ({zone})" if zone else "")
            + (f" · détection suspendue {seconds} s" if seconds else "")
        )
        self._save_soon()
        self._notify()
        return True

    @callback
    def async_reset_counters(self) -> None:
        self.total_since_reset = 0
        self.last_trigger = None
        self._save_soon()
        self._notify()

    # ------------------------------------------------------------------ #
    # Lecture
    # ------------------------------------------------------------------ #
    def _flag(self, name: str) -> bool:
        return bool((self.config.get("cfg") or {}).get(name))

    def _effective(self, ent_id: str, latch: bool) -> str | None:
        """Niveau courant, ou le pire niveau mémorisé si la mémorisation est active."""
        current = self._triggered.get(ent_id)
        if latch:
            memo = self._latched.get(ent_id)
            if memo and (current is None
                         or STATUS_RANK.get(memo, 0) > STATUS_RANK.get(current, 0)):
                return memo
        return current

    def counts(self) -> dict[str, Any]:
        counts = {"info": 0, "warning": 0, "critical": 0}
        latch = self._flag("latch_alarm")
        ids = set(self._triggered) | (set(self._latched) if latch else set())
        for ent_id in ids:
            level = self._effective(ent_id, latch)
            if level in counts:
                counts[level] += 1
        counts["total"] = counts["info"] + counts["warning"] + counts["critical"]
        counts["status"] = (
            "critical" if counts["critical"] else "warning" if counts["warning"]
            else "info" if counts["info"] else "ok"
        )
        return counts

    def zone_status(self, room_id: str) -> tuple[str, int]:
        status, n = "ok", 0
        latch = self._flag("latch_zones")
        for ent in self.config["ents"]:
            level = self._effective(ent["id"], latch)
            if not level:
                continue
            room = room_of(self.config, ent)
            if room and room.get("id") == room_id:
                n += 1
                if STATUS_RANK[level] > STATUS_RANK[status]:
                    status = level
        return status, n

    def active_items(self) -> list[dict[str, Any]]:
        """Entités déclenchées, en cours de détection ou acquittées (pour les cartes)."""
        now = dt_util.utcnow()
        mode = current_mode(self.config)
        order = {"critical": 0, "warning": 1, "info": 2}
        items: list[dict[str, Any]] = []
        for ent in self.config["ents"]:
            ent_id = ent["id"]
            room = room_of(self.config, ent)
            status = until = None
            level = ""
            if ent_id in self._triggered:
                status, level = "triggered", self._triggered[ent_id]
            elif ent_id in self._pending:
                status = "pending"
                level = resolve_level(mode, ent, room)
                until = self._pending[ent_id]["until"]
            elif ent_id in self._silenced:
                status, until = "acked", self._silenced[ent_id]
                level = resolve_level(mode, ent, room)
            if status is None:
                continue
            items.append({
                "id": ent_id, "name": ent.get("name", ""), "type": ent.get("type", ""),
                "zone": (room or {}).get("name", ""), "room_id": (room or {}).get("id", ""),
                "entity_id": entity_key(ent), "status": status, "level": level,
                "until": until.isoformat() if until else None,
                "kind": (self._pending.get(ent_id) or {}).get("kind"),
            })
        items.sort(key=lambda i: (i["status"] != "triggered", order.get(i["level"], 3), i["name"]))
        return items

    def grace_until_iso(self) -> str | None:
        now = dt_util.utcnow()
        if self._grace_until and self._grace_until > now:
            return self._grace_until.isoformat()
        return None

    def _pending_info(self, ent_id: str, now: datetime) -> dict[str, Any]:
        pending = self._pending.get(ent_id)
        if not pending:
            return {}
        left = (pending["until"] - now).total_seconds()
        if left <= 0:
            return {}
        return {"pending": int(left) + 1, "pending_kind": pending["kind"]}

    def snapshot(self) -> dict[str, Any]:
        mode = current_mode(self.config)
        ents: dict[str, Any] = {}
        now = dt_util.utcnow()
        for ent in self.config["ents"]:
            entity_id = entity_key(ent)
            state = self.hass.states.get(entity_id) if entity_id else None
            ents[ent["id"]] = {
                "state": state.state if state else None,
                "on": self._is_on(ent),
                "level": self._triggered.get(ent["id"], ""),
                "latched": self._latched.get(ent["id"], ""),
                "held": ent["id"] in self._hold and not self._raw_on(ent),
                "acked": ent["id"] in self._silenced,
                **(
                    {"acked_left": int((self._silenced[ent["id"]] - now).total_seconds()) + 1}
                    if ent["id"] in self._silenced else {}
                ),
                # déclenchement différé par un délai de sortie ou d'entrée
                **self._pending_info(ent["id"], now),
            }
        return {
            "mode_id": mode.get("id"),
            "mode": mode.get("name"),
            "counts": self.counts(),
            "last_trigger": self.last_trigger,
            "total_since_reset": self.total_since_reset,
            "ents": ents,
            "latched_count": len(self._latched),
            "grace": (
                max(0, int((self._grace_until - now).total_seconds()))
                if self._grace_until else 0
            ),
            "journal": self.journal,
        }

    # ------------------------------------------------------------------ #
    # Diffusion
    # ------------------------------------------------------------------ #
    @callback
    def async_add_listener(self, listener: Callable[[], None]) -> CALLBACK_TYPE:
        self._listeners.append(listener)

        @callback
        def _remove() -> None:
            if listener in self._listeners:
                self._listeners.remove(listener)

        return _remove

    @callback
    def _notify(self) -> None:
        async_dispatcher_send(self.hass, self.signal)
        for listener in list(self._listeners):
            listener()
