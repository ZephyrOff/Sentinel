"""Test du moteur avec un Home Assistant simulé (modules `homeassistant.*` factices).

Lancer :  python -m pytest tests   (ou)   python tests/test_engine.py
"""
from __future__ import annotations

import asyncio
import importlib
import sys
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).parent.parent


# --------------------------------------------------------------------------- #
# Faux modules Home Assistant
# --------------------------------------------------------------------------- #
class _Clock:
    now = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)


class _Timers:
    """Minuteries enregistrées par le moteur ; le test les déclenche à la main."""

    calls: list[dict] = []
    intervals: list[dict] = []


def _install_stubs() -> None:
    def mod(name: str) -> types.ModuleType:
        m = types.ModuleType(name)
        sys.modules[name] = m
        return m

    ha = mod("homeassistant")
    ce = mod("homeassistant.config_entries")
    ce.ConfigEntry = object
    core = mod("homeassistant.core")
    core.CALLBACK_TYPE = object
    core.Event = object
    core.HomeAssistant = object
    core.callback = lambda f: f
    helpers = mod("homeassistant.helpers")
    disp = mod("homeassistant.helpers.dispatcher")
    disp.sent = []
    disp.async_dispatcher_send = lambda hass, signal: disp.sent.append(signal)
    ev = mod("homeassistant.helpers.event")

    def call_later(hass, delay, action):
        item = {"delay": delay, "action": action, "cancelled": False}
        _Timers.calls.append(item)

        def cancel():
            item["cancelled"] = True

        return cancel

    def track_state(hass, ids, action):
        hass.tracked = list(ids)
        hass.state_cb = action
        return lambda: None

    def track_interval(hass, action, interval):
        item = {"interval": interval, "action": action, "cancelled": False}
        _Timers.intervals.append(item)
        return lambda: item.__setitem__("cancelled", True)

    ev.async_call_later = call_later
    ev.async_track_state_change_event = track_state
    ev.async_track_time_interval = track_interval
    st = mod("homeassistant.helpers.storage")
    st.Store = object
    util = mod("homeassistant.util")
    dt = mod("homeassistant.util.dt")
    dt.utcnow = lambda: _Clock.now
    dt.now = lambda: _Clock.now
    for sub, parent in ((helpers, ha), (util, ha)):
        parent.__dict__.setdefault(sub.__name__.split(".")[-1], sub)
    util.dt = dt

    # paquet « sentinel » chargé depuis le dépôt sans exécuter son __init__
    pkg = types.ModuleType("sentinel")
    pkg.__path__ = [str(ROOT / "custom_components" / "sentinel")]
    sys.modules["sentinel"] = pkg


class _State:
    def __init__(self, state: str) -> None:
        self.state = state


class _Services:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def has_service(self, domain, service) -> bool:
        return (domain, service) in {
            ("notify", "mobile_app_test"), ("notify", "zone_only"),
            ("script", "turn_on"), ("light", "turn_on"),
        }

    async def async_call(self, domain, service, data, blocking=False) -> None:
        self.calls.append((domain, service, data))


class _Bus:
    def __init__(self) -> None:
        self.events: list[tuple] = []

    def async_fire(self, name, data) -> None:
        self.events.append((name, data))


class _Hass:
    def __init__(self) -> None:
        self.states_map: dict[str, _State] = {}
        self.services = _Services()
        self.bus = _Bus()
        self.tasks: list = []

    @property
    def states(self):
        outer = self

        class _S:
            @staticmethod
            def get(entity_id):
                return outer.states_map.get(entity_id)

        return _S

    def async_create_task(self, coro):
        self.tasks.append(coro)

    def set(self, entity_id: str, state: str) -> None:
        self.states_map[entity_id] = _State(state)
        self.state_cb(types.SimpleNamespace(data={"entity_id": entity_id}))

    def run_tasks(self) -> None:
        pending, self.tasks = self.tasks, []
        for coro in pending:
            asyncio.run(coro)


class _Entry:
    entry_id = "abc"


class _FakeStore:
    async def async_load(self):
        return None

    def async_delay_save(self, *_a):
        pass


def _build():
    _Timers.calls.clear()
    _Timers.intervals.clear()
    _Clock.now = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    _install_stubs()
    engine_mod = importlib.import_module("sentinel.engine")
    hass = _Hass()
    engine = engine_mod.SentinelEngine(hass, _Entry(), _FakeStore())
    cfg = {
        "floors": [{"id": "f0", "name": "RDC", "short": "RDC", "level": 0}],
        "rooms": [{"id": "r1", "floor": "f0", "name": "Salon", "x": 0, "z": 0, "w": 6, "d": 5}],
        "ents": [
            {"id": "e1", "name": "Fenêtre", "type": "window", "floor": "f0",
             "entity": "binary_sensor.fenetre", "room": "r1", "side": "N", "x": 1, "z": 0},
            {"id": "e2", "name": "Fumée", "type": "smoke", "floor": "f0",
             "entity": "binary_sensor.fumee", "x": 3, "z": 3},
        ],
        "mode": "off",
        "modes": [
            {"id": "off", "name": "Désarmé", "def": "off", "types": {"smoke": "critical"},
             "rooms": {}, "ents": {}, "entryDelay": 0, "exitDelay": 0,
             "notify": {"critical": {"on": True, "target": "notify.mobile_app_test",
                                     "repeat": 5, "msg": "{mode} {name} {zone}"}}},
            {"id": "away", "name": "Absence", "def": "critical", "types": {},
             "rooms": {}, "ents": {}, "entryDelay": 30, "exitDelay": 0,
             "notify": {"critical": {"on": True, "target": "notify.mobile_app_test",
                                     "repeat": 0, "msg": "{mode} {name} {zone}"}}},
        ],
        "cfg": {"prefix": "sentinel", "ids": {}, "expose": {}},
    }
    engine.config = engine_mod.normalize_config(cfg)
    hass.states_map = {"binary_sensor.fenetre": _State("off"), "binary_sensor.fumee": _State("off")}
    asyncio.run(engine.async_start())
    return hass, engine


def test_smoke_triggers_immediately_and_notifies():
    hass, engine = _build()
    hass.set("binary_sensor.fumee", "on")
    assert engine.counts()["critical"] == 1
    assert engine.counts()["status"] == "critical"
    assert hass.bus.events[-1][0] == "sentinel_triggered"
    hass.run_tasks()
    assert hass.services.calls[-1][:2] == ("notify", "mobile_app_test")
    assert "Désarmé Fumée" in hass.services.calls[-1][2]["message"]
    assert _Timers.intervals and not _Timers.intervals[-1]["cancelled"]  # rappel armé
    hass.set("binary_sensor.fumee", "off")
    assert engine.counts()["total"] == 0
    assert _Timers.intervals[-1]["cancelled"]
    assert hass.bus.events[-1][0] == "sentinel_cleared"


def test_window_ignored_when_disarmed_then_delayed_critical_when_away():
    hass, engine = _build()
    hass.set("binary_sensor.fenetre", "on")
    assert engine.counts()["total"] == 0                     # défaut off dans « Désarmé »

    assert engine.async_set_mode("Absence")                  # fenêtre déjà ouverte
    assert engine.counts()["total"] == 0                     # entryDelay 30 s en attente
    timer = [t for t in _Timers.calls if not t["cancelled"]][-1]
    assert 29 < timer["delay"] <= 30.1

    _Clock.now += timedelta(seconds=31)
    timer["action"](_Clock.now)
    assert engine.counts()["critical"] == 1
    assert engine.last_trigger["name"] == "Fenêtre"


def test_delay_cancelled_if_closed_before_end():
    hass, engine = _build()
    engine.async_set_mode("Absence")
    hass.set("binary_sensor.fenetre", "on")
    timer = [t for t in _Timers.calls if not t["cancelled"]][-1]
    hass.set("binary_sensor.fenetre", "off")
    assert timer["cancelled"]
    assert engine.counts()["total"] == 0


def test_unavailable_never_triggers():
    hass, engine = _build()
    hass.set("binary_sensor.fumee", "unavailable")
    assert engine.counts()["total"] == 0


def test_snapshot_shape():
    hass, engine = _build()
    hass.set("binary_sensor.fumee", "on")
    snap = engine.snapshot()
    assert snap["mode_id"] == "off" and snap["counts"]["critical"] == 1
    assert snap["ents"]["e2"] == {"state": "on", "on": True, "level": "critical", "latched": "critical", "held": False, "acked": False}
    assert engine.zone_status("r1") == ("ok", 0) or engine.zone_status("r1")[0] in ("ok", "critical")


def test_mode_actions_run_with_variables():
    hass, engine = _build()
    mode = engine.config["modes"][0]
    mode["actions"] = {"critical": [
        {"service": "script.turn_on", "entity_id": "script.sirene",
         "data": {"variables": {"msg": "{name} - {zone} ({floor}, {type})"}}},
        {"service": "light.turn_on", "entity_id": "light.a, light.b"},
        {"service": "pas_un_service"},
    ]}
    hass.set("binary_sensor.fumee", "on")
    hass.run_tasks()
    calls = [c for c in hass.services.calls if c[0] != "notify"]
    assert calls[0] == ("script", "turn_on", {
        "variables": {"msg": "Fumée - Salon (RDC, Fumée)"},
        "entity_id": "script.sirene"})
    assert calls[1][2]["entity_id"] == ["light.a", "light.b"]
    assert len(calls) == 2


def test_zone_override_replaces_mode_reaction():
    hass, engine = _build()
    # la fumée est dans la pièce r1 (Salon) : sa zone a ses propres notification et action
    mode = engine.config["modes"][0]
    mode["actions"] = {"critical": [{"service": "light.turn_on", "entity_id": "light.global"}]}
    mode["zones"] = {"r1": {
        "notify": {"critical": {"on": True, "target": "notify.zone_only", "msg": "Salon {name}"}},
        "actions": {"critical": [{"service": "script.turn_on", "entity_id": "script.salon"}]},
    }}
    # e2 n'est rattaché à aucune pièce par `room` : on le place dans le Salon par position
    hass.set("binary_sensor.fumee", "on")
    hass.run_tasks()
    targets = [(c[0], c[1], c[2].get("entity_id")) for c in hass.services.calls]
    assert ("notify", "zone_only", None) in targets
    assert ("script", "turn_on", "script.salon") in targets
    assert ("light", "turn_on", "light.global") not in targets
    assert ("notify", "mobile_app_test", None) not in targets


def test_enter_and_clear_reactions():
    hass, engine = _build()
    away = engine.config["modes"][1]
    away["notify"]["enter"] = {"on": True, "target": "notify.mobile_app_test", "msg": "Mode {mode} à {time}"}
    away["actions"] = {"enter": [{"service": "light.turn_on", "entity_id": "light.hall"}]}
    engine.async_set_mode("away")
    hass.run_tasks()
    assert any(c[0] == "light" and c[2]["entity_id"] == "light.hall" for c in hass.services.calls)
    assert any(c[0] == "notify" and c[2]["message"].startswith("Mode Absence à ") for c in hass.services.calls)
    # retour à la normale : notification « clear »
    engine.config["modes"][0]["notify"]["clear"] = {"on": True, "target": "notify.mobile_app_test", "msg": "OK {name}"}
    engine.async_set_mode("off")
    hass.set("binary_sensor.fumee", "on")
    hass.set("binary_sensor.fumee", "off")
    hass.run_tasks()
    assert any(c[0] == "notify" and c[2]["message"] == "OK Fumée" for c in hass.services.calls)


def test_zone_reaction_can_add_to_mode():
    hass, engine = _build()
    mode = engine.config["modes"][0]
    mode["actions"] = {"critical": [{"service": "light.turn_on", "entity_id": "light.global"}]}
    mode["zones"] = {"r1": {
        "notify": {"critical": {"on": True, "target": "notify.zone_only", "msg": "Salon {name}"}},
        "actions": {"critical": [{"service": "script.turn_on", "entity_id": "script.salon"}]},
        "add": {"notify": {"critical": True}, "actions": {"critical": True}},
    }}
    hass.set("binary_sensor.fumee", "on")
    hass.run_tasks()
    targets = [(c[0], c[1], c[2].get("entity_id")) for c in hass.services.calls]
    # zone ET mode : les deux notifications et les deux actions partent
    assert ("notify", "zone_only", None) in targets
    assert ("notify", "mobile_app_test", None) in targets
    assert ("script", "turn_on", "script.salon") in targets
    assert ("light", "turn_on", "light.global") in targets


def test_pending_entry_and_exit_delay_visible_in_snapshot():
    hass, engine = _build()
    engine.async_set_mode("away")          # entryDelay 30 s sur critical
    hass.set("binary_sensor.fenetre", "on")
    ent = engine.snapshot()["ents"]["e1"]
    assert ent["level"] == "" and ent["pending_kind"] == "entry" and 1 <= ent["pending"] <= 31
    # délai de sortie : la fenêtre est déjà ouverte quand on arme
    hass2, engine2 = _build()
    hass2.set("binary_sensor.fenetre", "on")
    engine2.config["modes"][1]["exitDelay"] = 60
    engine2.async_set_mode("away")
    snap = engine2.snapshot()
    assert snap["grace"] > 0
    assert snap["ents"]["e1"]["pending_kind"] == "exit"
    assert snap["ents"]["e1"]["pending"] > 30


def _motion_house():
    """Deux pièces voisines (Salon | Cuisine) avec un détecteur de mouvement chacune."""
    hass, engine = _build()
    cfg = engine.config
    cfg["rooms"] = [
        {"id": "r1", "floor": "f0", "name": "Salon", "x": 0, "z": 0, "w": 5, "d": 5},
        {"id": "r2", "floor": "f0", "name": "Cuisine", "x": 5, "z": 0, "w": 4, "d": 5},
        {"id": "r3", "floor": "f0", "name": "Garage", "x": 20, "z": 0, "w": 4, "d": 5},
    ]
    cfg["ents"] = [
        {"id": "m1", "name": "Mvt salon", "type": "motion", "floor": "f0",
         "entity": "binary_sensor.m1", "x": 2, "z": 2},
        {"id": "m2", "name": "Mvt cuisine", "type": "motion", "floor": "f0",
         "entity": "binary_sensor.m2", "x": 7, "z": 2},
        {"id": "m3", "name": "Mvt garage", "type": "motion", "floor": "f0",
         "entity": "binary_sensor.m3", "x": 22, "z": 2},
    ]
    cfg["modes"][1]["def"] = "critical"
    for ent_id in ("m1", "m2", "m3"):
        hass.states_map["binary_sensor." + ent_id] = _State("off")
    engine.async_set_mode("away")
    return hass, engine


def test_motion_presence_is_held_until_neighbour_motion():
    hass, engine = _motion_house()
    hass.set("binary_sensor.m1", "on")
    hass.set("binary_sensor.m1", "off")          # le capteur ne voit plus personne
    snap = engine.snapshot()["ents"]["m1"]
    assert snap["on"] is True and snap["held"] is True   # mais la présence est maintenue
    # mouvement dans la pièce voisine : la personne a changé de pièce
    hass.set("binary_sensor.m2", "on")
    assert engine.snapshot()["ents"]["m1"]["on"] is False
    assert engine.snapshot()["ents"]["m1"]["held"] is False


def test_motion_hold_ignores_non_adjacent_rooms_and_mode_change():
    hass, engine = _motion_house()
    hass.set("binary_sensor.m1", "on")
    hass.set("binary_sensor.m1", "off")
    hass.set("binary_sensor.m3", "on")           # garage : pas voisin du salon
    assert engine.snapshot()["ents"]["m1"]["on"] is True
    engine.async_set_mode("off")                 # changer de mode repart de zéro
    assert engine.snapshot()["ents"]["m1"]["on"] is False


def test_adjacent_rooms_for_polygons():
    from sentinel import logic as lg
    cfg = {"rooms": [
        {"id": "a", "floor": "f0", "pts": [[0, 0], [4, 0], [4, 4], [0, 4]]},
        {"id": "b", "floor": "f0", "pts": [[4, 1], [8, 1], [8, 3], [4, 3]]},
        {"id": "c", "floor": "f0", "pts": [[4, 4], [8, 4], [8, 8], [4, 8]]},   # coin seulement
        {"id": "d", "floor": "f1", "pts": [[0, 0], [4, 0], [4, 4], [0, 4]]},   # autre étage
    ]}
    assert lg.adjacent_room_ids(cfg, "a") == {"b"}


def test_acknowledge_single_entity_pending_and_triggered():
    hass, engine = _build()
    engine.config["modes"][1]["ackDelay"] = 45
    engine.async_set_mode("away")                      # entryDelay 30 s sur critical
    hass.set("binary_sensor.fenetre", "on")
    assert engine.snapshot()["ents"]["e1"]["pending_kind"] == "entry"
    assert engine.async_acknowledge("binary_sensor.fenetre") is True
    snap = engine.snapshot()["ents"]["e1"]
    assert snap["acked"] is True and "pending" not in snap and snap["level"] == ""
    assert 40 <= snap["acked_left"] <= 46
    # pendant la suspension : un nouveau changement d'état ne déclenche rien
    hass.set("binary_sensor.fenetre", "off")
    hass.set("binary_sensor.fenetre", "on")
    assert "pending" not in engine.snapshot()["ents"]["e1"]
    # fin de la suspension (minuterie à 45 s) : toujours ouverte, la détection repart
    timer = [t for t in _Timers.calls if not t["cancelled"] and abs(t["delay"] - 45) < 1][-1]
    timer["action"](None)
    snap = engine.snapshot()["ents"]["e1"]
    assert snap["acked"] is False and snap["pending_kind"] == "entry"


def test_acknowledge_triggered_entity_clears_status_only_for_it():
    hass, engine = _build()
    hass.set("binary_sensor.fumee", "on")             # critical immédiat (mode Désarmé)
    assert engine.counts()["critical"] == 1
    assert engine.async_acknowledge("e2") is True     # par identifiant Sentinel
    assert engine.counts()["critical"] == 0
    assert engine.async_acknowledge("inconnu") is False


def test_acknowledged_motion_waits_for_a_new_detection():
    hass, engine = _motion_house()
    hass.set("binary_sensor.m1", "on")
    assert engine.snapshot()["ents"]["m1"]["pending_kind"] == "entry"
    assert engine.async_acknowledge("binary_sensor.m1") is True
    # fin de la pause alors que le capteur est toujours « on » : on ne reprend pas
    timer = [t for t in _Timers.calls if not t["cancelled"] and abs(t["delay"] - 60) < 1][-1]
    timer["action"](None)
    snap = engine.snapshot()["ents"]["m1"]
    assert "pending" not in snap and snap["level"] == "" and snap["held"] is False
    # le capteur retombe puis détecte de nouveau : nouvelle détection, le délai repart
    hass.set("binary_sensor.m1", "off")
    hass.set("binary_sensor.m1", "on")
    assert engine.snapshot()["ents"]["m1"]["pending_kind"] == "entry"


def test_acknowledge_motion_resets_zone_presence():
    hass, engine = _motion_house()
    hass.states_map["binary_sensor.m4"] = _State("off")
    engine.config["ents"].append({"id": "m4", "name": "Mvt salon 2", "type": "motion",
                                  "floor": "f0", "entity": "binary_sensor.m4", "x": 3, "z": 3})
    engine._track_states()
    hass.set("binary_sensor.m4", "on")
    hass.set("binary_sensor.m4", "off")
    assert engine.snapshot()["ents"]["m4"]["held"] is True        # présence maintenue
    hass.set("binary_sensor.m1", "on")                             # autre détecteur du salon
    assert engine.async_acknowledge("binary_sensor.m1") is True
    assert engine.snapshot()["ents"]["m4"]["held"] is False        # zone remise à zéro
    assert engine.snapshot()["ents"]["m4"]["on"] is False


if __name__ == "__main__":
    tests = [v for k, v in dict(globals()).items() if k.startswith("test_")]
    for fn in tests:
        fn()
        print("ok  ", fn.__name__)
    print(f"{len(tests)} tests passés")


def test_latch_keeps_status_until_acknowledge():
    hass, engine = _build()
    engine.config["cfg"]["latch_alarm"] = True
    hass.set("binary_sensor.fumee", "on")
    assert engine.counts()["status"] == "critical"
    hass.set("binary_sensor.fumee", "off")
    # le capteur est revenu normal mais le statut reste mémorisé
    assert engine.counts()["critical"] == 1
    assert engine.snapshot()["ents"]["e2"]["latched"] == "critical"
    # zones non mémorisées : revient à ok
    assert engine.zone_status("r1") == ("ok", 0)
    engine.async_acknowledge()
    assert engine.counts()["status"] == "ok"
    assert engine.snapshot()["latched_count"] == 0


def test_latch_zones_independent_of_alarm():
    hass, engine = _build()
    engine.config["cfg"]["latch_zones"] = True
    hass.set("binary_sensor.fumee", "on")
    hass.set("binary_sensor.fumee", "off")
    assert engine.counts()["status"] == "ok"
    assert engine.zone_status("r1") == ("critical", 1)
    engine.async_acknowledge()
    assert engine.zone_status("r1") == ("ok", 0)


def test_no_latch_by_default():
    hass, engine = _build()
    hass.set("binary_sensor.fumee", "on")
    hass.set("binary_sensor.fumee", "off")
    assert engine.counts()["status"] == "ok"
