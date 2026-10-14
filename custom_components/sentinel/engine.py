"""Moteur Sentinel : suit les entités, applique les modes, notifie."""
from __future__ import annotations

import logging
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
        # entités acquittées individuellement : ignorées jusqu'à leur retour à la normale
        self._silenced: set[str] = set()
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
        self._track_states()
        # Au démarrage on constate l'existant sans notifier.
        self._evaluate_all(silent=True)
        self._notify()

    @callback
    def async_stop(self) -> None:
        if self._unsub_states:
            self._unsub_states()
            self._unsub_states = None
        for timers in (self._delay_timers, self._repeat_timers, self._hold_timers):
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
        self.config = normalize_config(raw)
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
        if ent.get("type") != "motion" or not self._raw_on(ent):
            return
        ent_id = ent["id"]
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
        for other in self.config["ents"]:
            if other.get("id") == ent_id or other.get("id") not in self._hold:
                continue
            orm = room_of(self.config, other)
            if orm and orm.get("id") in near:
                self._release_hold(other["id"])

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
        self._silenced &= known
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
        level = resolve_level(mode, ent, room) if on else "off"
        previous = self._triggered.get(ent_id)

        if ent_id in self._silenced:
            if on:
                return
            self._silenced.discard(ent_id)

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
        self._triggered.clear()
        self._acked.clear()
        self._silenced.clear()
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
        self._silenced.add(ent_id)
        if ent_id in self._hold and not self._raw_on(ent):
            self._release_hold(ent_id)     # présence maintenue : la personne est partie
        zone = (room_of(self.config, ent) or {}).get("name", "")
        self._log(f"Acquitté : {ent.get('name', '')}" + (f" ({zone})" if zone else ""))
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
