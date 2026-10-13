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
        return (domain, service) == ("notify", "mobile_app_test")

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
    assert snap["ents"]["e2"] == {"state": "on", "on": True, "level": "critical", "latched": "critical"}
    assert engine.zone_status("r1") == ("ok", 0) or engine.zone_status("r1")[0] in ("ok", "critical")


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
