"""Tests de la logique pure (sans Home Assistant).

Lancer :  python -m pytest tests   (ou)   python tests/test_logic.py
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_PATH = Path(__file__).parent.parent / "custom_components" / "sentinel" / "logic.py"
_spec = importlib.util.spec_from_file_location("sentinel_logic", _PATH)
logic = importlib.util.module_from_spec(_spec)
sys.modules["sentinel_logic"] = logic
_spec.loader.exec_module(logic)


def _config():
    cfg = logic.default_config()
    cfg["rooms"] = [
        {"id": "r1", "floor": "f0", "name": "Salon", "x": 0, "z": 0, "w": 6, "d": 5},
        {"id": "r2", "floor": "f0", "name": "Jardin", "x": 0, "z": 5, "w": 6, "d": 4,
         "outdoor": True},
    ]
    cfg["ents"] = [
        {"id": "e1", "name": "Mouvement salon", "type": "motion", "floor": "f0",
         "entity": "binary_sensor.salon", "x": 2, "z": 2},
        {"id": "e2", "name": "Fenêtre salon", "type": "window", "floor": "f0",
         "entity": "binary_sensor.fenetre", "room": "r1", "side": "N", "x": 1, "z": 0},
        {"id": "e3", "name": "Fumée", "type": "smoke", "floor": "f0",
         "entity": "binary_sensor.fumee", "x": 3, "z": 3},
    ]
    return cfg


def test_room_of_by_position_and_attachment():
    cfg = _config()
    assert logic.room_of(cfg, cfg["ents"][0])["id"] == "r1"
    assert logic.room_of(cfg, cfg["ents"][1])["id"] == "r1"
    assert logic.room_of(cfg, {"floor": "f0", "x": 50, "z": 50}) is None
    assert logic.room_of(cfg, {"floor": "f9", "x": 2, "z": 2}) is None


def test_level_priority_entity_zone_type_default():
    mode = {"def": "off", "types": {"motion": "warning"},
            "rooms": {"r1": "info"}, "ents": {"e1": "critical"}}
    cfg = _config()
    room = logic.room_of(cfg, cfg["ents"][0])
    assert logic.resolve_level(mode, cfg["ents"][0], room) == "critical"   # entité
    mode["ents"] = {}
    assert logic.resolve_level(mode, cfg["ents"][0], room) == "info"       # zone
    mode["rooms"] = {}
    assert logic.resolve_level(mode, cfg["ents"][0], room) == "warning"    # type
    mode["types"] = {}
    assert logic.resolve_level(mode, cfg["ents"][0], room) == "off"        # défaut
    mode["def"] = "critical"
    assert logic.resolve_level(mode, cfg["ents"][0], room) == "critical"
    mode["def"] = "n'importe quoi"
    assert logic.resolve_level(mode, cfg["ents"][0], room) == "off"


def test_state_mapping():
    assert logic.state_is_on("on") and logic.state_is_on("open")
    assert not logic.state_is_on("off") and not logic.state_is_on("closed")
    assert not logic.state_is_on("unavailable") and not logic.state_is_on(None)
    assert logic.state_is_on("off", invert=True)
    assert not logic.state_is_on("unknown", invert=True)


def test_effective_delay():
    mode = {"entryDelay": 30}
    assert logic.effective_delay(mode, {"type": "door", "delay": 5}, "critical") == 35
    assert logic.effective_delay(mode, {"type": "door"}, "warning") == 0
    assert logic.effective_delay(mode, {"type": "smoke"}, "critical") == 0


def test_template():
    cfg = _config()
    ent, mode = cfg["ents"][1], {"name": "Absence"}
    room = logic.room_of(cfg, ent)
    out = logic.fill_template("{mode} : {name} ({zone}) – {state} [{inconnu}]",
                              ent, room, mode, "critical", True)
    assert out == "Absence : Fenêtre salon (Salon) – Ouverte [{inconnu}]"


def test_normalize_config_repairs_partial_data():
    cfg = logic.normalize_config({"floors": [], "modes": [{"id": "x", "name": "X"}]})
    assert cfg["floors"] and cfg["modes"][0]["def"] == "off"
    assert cfg["mode"] == "x"
    assert cfg["cfg"]["prefix"] == "sentinel"
    assert logic.normalize_config("pas un dict")["modes"]


def test_exposed_items_prefix_ids_and_expose():
    cfg = _config()
    keys = {i["key"]: i for i in logic.exposed_items(cfg)}
    assert keys["status"]["object_id"] == "sentinel_status"
    assert keys["zone:r1"]["object_id"] == "sentinel_zone_salon_status"
    cfg["cfg"]["prefix"] = "maison"
    cfg["cfg"]["ids"] = {"status": "Alarme Maison"}
    cfg["cfg"]["expose"] = {"info_count": False}
    keys = {i["key"]: i for i in logic.exposed_items(cfg)}
    assert keys["mode"]["object_id"] == "maison_mode"
    assert keys["status"]["object_id"] == "alarme_maison"
    assert "info_count" not in keys


def test_find_mode_by_id_or_name():
    cfg = logic.default_config()
    assert logic.find_mode(cfg, "m_away")["name"] == "Absence"
    assert logic.find_mode(cfg, "absence")["id"] == "m_away"
    assert logic.find_mode(cfg, "inconnu") is None


if __name__ == "__main__":
    tests = [v for k, v in dict(globals()).items() if k.startswith("test_")]
    for fn in tests:
        fn()
        print("ok  ", fn.__name__)
    print(f"{len(tests)} tests passés")
