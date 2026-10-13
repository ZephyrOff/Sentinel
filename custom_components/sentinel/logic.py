"""Logique pure de Sentinel (aucune dépendance à Home Assistant).

Tout ce qui décide d'un niveau, d'une zone ou d'un nom d'entité est ici, pour
pouvoir être testé sans instance Home Assistant.

Modèle de configuration (identique à celui du panneau) :

    {
      "floors": [{"id", "name", "short", "level"}],
      "rooms":  [{"id", "floor", "name", "x", "z", "w", "d", "outdoor"?, ...}],
      "ents":   [{"id", "name", "type", "floor", "entity", "x", "z",
                  "room"?, "delay"?, "invert"?}],
      "mode":   "<id du mode actif>",
      "modes":  [{"id", "name", "def", "types", "rooms", "ents", "notify",
                  "entryDelay", "exitDelay", ...}],
      "cfg":    {"prefix": "sentinel", "ids": {}, "expose": {}}
    }
"""
from __future__ import annotations

import copy
import re
from typing import Any

LEVELS = ("off", "info", "warning", "critical")
STATUS_RANK = {"ok": 0, "info": 1, "warning": 2, "critical": 3}

# États Home Assistant considérés comme « déclenché » (hors inversion).
ON_STATES = frozenset(
    {
        "on", "open", "opening", "unlocked", "detected", "triggered", "wet",
        "true", "active", "motion", "occupied", "alarm", "problem", "unsafe",
    }
)

# Les entry delay ne s'appliquent jamais à ces types (sécurité des personnes).
SAFETY_TYPES = frozenset({"smoke", "leak"})

# Libellés d'état utilisés dans les messages ({state}).
STATE_TEXT = {
    "door": ("Fermée", "Ouverte"),
    "window": ("Fermée", "Ouverte"),
    "bay": ("Fermée", "Ouverte"),
    "garage": ("Fermée", "Ouverte"),
    "gate": ("Fermé", "Ouvert"),
    "motion": ("Aucun mouvement", "Mouvement détecté"),
    "presence": ("Absence", "Présence"),
    "smoke": ("Normal", "Fumée détectée"),
    "leak": ("Sec", "Fuite détectée"),
    "wardrobe": ("Fermée", "Ouverte"),
    "cabinet": ("Fermé", "Ouvert"),
    "fridge": ("Fermé", "Porte ouverte"),
    "safe": ("Fermé", "Ouvert"),
    "medicine": ("Fermée", "Ouverte"),
}


# --------------------------------------------------------------------------- #
# Configuration par défaut
# --------------------------------------------------------------------------- #
def _notify(info: bool, warning: bool, critical: bool) -> dict[str, dict[str, Any]]:
    return {
        "info": {"on": info, "target": "", "repeat": 0,
                 "msg": "{name} ({zone}) : {state}"},
        "warning": {"on": warning, "target": "", "repeat": 0,
                    "msg": "Attention : {name} ({zone}) – {state}"},
        "critical": {"on": critical, "target": "", "repeat": 5,
                     "msg": "ALERTE {mode} : {name} ({zone}) – {state}"},
    }


def default_modes() -> list[dict[str, Any]]:
    """Quatre modes de départ, uniquement par type (aucune entité précise)."""
    return [
        {
            "id": "m_off", "name": "Désarmé", "color": "#8591a2",
            "desc": "Maison occupée : seules les sécurités sont actives.",
            "def": "off", "entryDelay": 0, "exitDelay": 0,
            "types": {"smoke": "critical", "leak": "critical",
                      "fridge": "warning", "safe": "warning"},
            "rooms": {}, "ents": {}, "notify": _notify(False, False, True),
        },
        {
            "id": "m_home", "name": "Présence", "color": "#3fa56f",
            "desc": "Dans la maison : les ouvertures sont surveillées.",
            "def": "off", "entryDelay": 0, "exitDelay": 0,
            "types": {"door": "warning", "window": "warning", "bay": "warning",
                      "garage": "warning", "gate": "info", "smoke": "critical",
                      "leak": "critical", "fridge": "warning", "safe": "critical"},
            "rooms": {}, "ents": {}, "notify": _notify(False, True, True),
        },
        {
            "id": "m_away", "name": "Absence", "color": "#d3302c",
            "desc": "Personne à la maison : tout déclenche.",
            "def": "critical", "entryDelay": 30, "exitDelay": 60,
            "types": {"smoke": "critical", "leak": "critical",
                      "fridge": "warning", "medicine": "off", "gate": "critical"},
            "rooms": {}, "ents": {}, "notify": _notify(True, True, True),
        },
        {
            "id": "m_night", "name": "Nuit", "color": "#6f78d8",
            "desc": "Ouvertures surveillées, sécurités actives.",
            "def": "off", "entryDelay": 15, "exitDelay": 0,
            "types": {"door": "critical", "window": "critical", "bay": "critical",
                      "garage": "critical", "gate": "warning", "smoke": "critical",
                      "leak": "critical", "fridge": "warning", "safe": "critical"},
            "rooms": {}, "ents": {}, "notify": _notify(False, True, True),
        },
    ]


def default_config() -> dict[str, Any]:
    """Configuration d'un Sentinel vide (aucune pièce, aucun capteur)."""
    return {
        "floors": [{"id": "f0", "name": "Rez-de-chaussée", "short": "RDC", "level": 0}],
        "rooms": [],
        "ents": [],
        "mode": "m_off",
        "modes": default_modes(),
        "cfg": {"prefix": "sentinel", "ids": {}, "expose": {}},
    }


def normalize_config(raw: Any) -> dict[str, Any]:
    """Rend une configuration utilisable même si elle est partielle ou ancienne."""
    base = default_config()
    if not isinstance(raw, dict):
        return base
    cfg = copy.deepcopy(raw)
    for key in ("floors", "rooms", "ents", "modes"):
        if not isinstance(cfg.get(key), list):
            cfg[key] = base[key]
    if not cfg["floors"]:
        cfg["floors"] = base["floors"]
    if not cfg["modes"]:
        cfg["modes"] = base["modes"]
    if not isinstance(cfg.get("cfg"), dict):
        cfg["cfg"] = base["cfg"]
    cfg["cfg"].setdefault("prefix", "sentinel")
    cfg["cfg"].setdefault("ids", {})
    cfg["cfg"].setdefault("expose", {})
    for mode in cfg["modes"]:
        mode.setdefault("def", "off")
        for bag in ("types", "rooms", "ents"):
            if not isinstance(mode.get(bag), dict):
                mode[bag] = {}
        if not isinstance(mode.get("notify"), dict):
            mode["notify"] = _notify(False, False, True)
    if not any(m.get("id") == cfg.get("mode") for m in cfg["modes"]):
        cfg["mode"] = cfg["modes"][0]["id"]
    return cfg


# --------------------------------------------------------------------------- #
# Résolution des zones et des niveaux
# --------------------------------------------------------------------------- #
def current_mode(config: dict[str, Any]) -> dict[str, Any]:
    for mode in config["modes"]:
        if mode.get("id") == config.get("mode"):
            return mode
    return config["modes"][0]


def find_mode(config: dict[str, Any], ref: str) -> dict[str, Any] | None:
    """Retrouve un mode par identifiant ou par nom (insensible à la casse)."""
    ref_l = str(ref).strip().lower()
    for mode in config["modes"]:
        if mode.get("id") == ref:
            return mode
    for mode in config["modes"]:
        if str(mode.get("name", "")).strip().lower() == ref_l:
            return mode
    return None


def room_of(config: dict[str, Any], ent: dict[str, Any]) -> dict[str, Any] | None:
    """Zone d'une entité : ouverture rattachée à sa pièce, sinon par position."""
    rooms = config["rooms"]
    if ent.get("room"):
        return next((r for r in rooms if r.get("id") == ent["room"]), None)
    x, z = ent.get("x"), ent.get("z")
    if x is None or z is None:
        return None
    for room in rooms:
        if room.get("floor") != ent.get("floor"):
            continue
        try:
            if (room["x"] <= x <= room["x"] + room["w"]
                    and room["z"] <= z <= room["z"] + room["d"]):
                return room
        except (KeyError, TypeError):
            continue
    return None


def resolve_level(mode: dict[str, Any], ent: dict[str, Any],
                  room: dict[str, Any] | None) -> str:
    """Niveau d'une entité dans un mode : entité > zone > type > défaut."""
    level = (mode.get("ents") or {}).get(ent.get("id"))
    if level is None and room is not None:
        level = (mode.get("rooms") or {}).get(room.get("id"))
    if level is None:
        level = (mode.get("types") or {}).get(ent.get("type"))
    if level is None:
        level = mode.get("def", "off")
    return level if level in LEVELS else "off"


def state_is_on(state: str | None, invert: bool = False) -> bool:
    """Traduit un état Home Assistant en « déclenché » (faux si indisponible)."""
    if state is None or state in ("unavailable", "unknown", ""):
        return False
    on = str(state).lower() in ON_STATES
    return (not on) if invert else on


def effective_delay(mode: dict[str, Any], ent: dict[str, Any], level: str) -> int:
    """Temporisation avant déclenchement : celle de l'entité + délai d'entrée.

    Le délai d'entrée du mode ne s'applique qu'au niveau critical et jamais aux
    capteurs de sécurité (fumée, fuite).
    """
    delay = int(ent.get("delay") or 0)
    if level == "critical" and ent.get("type") not in SAFETY_TYPES:
        delay += int(mode.get("entryDelay") or 0)
    return max(0, delay)


def state_text(ent: dict[str, Any], on: bool) -> str:
    off_t, on_t = STATE_TEXT.get(ent.get("type"), ("Inactif", "Actif"))
    return on_t if on else off_t


_TPL = re.compile(r"\{(\w+)\}")


def fill_template(template: str, ent: dict[str, Any], room: dict[str, Any] | None,
                  mode: dict[str, Any], level: str, on: bool) -> str:
    values = {
        "name": ent.get("name", ""),
        "zone": room.get("name", "—") if room else "—",
        "state": state_text(ent, on),
        "level": level,
        "mode": mode.get("name", ""),
        "entity": ent.get("entity", ""),
    }
    return _TPL.sub(lambda m: str(values.get(m.group(1), m.group(0))), template or "")


def slugify(text: str) -> str:
    import unicodedata

    norm = unicodedata.normalize("NFD", str(text))
    norm = "".join(c for c in norm if unicodedata.category(c) != "Mn").lower()
    return re.sub(r"[^a-z0-9]+", "_", norm).strip("_") or "x"


# --------------------------------------------------------------------------- #
# Entités exposées à Home Assistant
# --------------------------------------------------------------------------- #
def exposed_items(config: dict[str, Any]) -> list[dict[str, Any]]:
    """Description des entités Home Assistant publiées par Sentinel."""
    prefix = config["cfg"].get("prefix") or "sentinel"
    ids = config["cfg"].get("ids") or {}
    expose = config["cfg"].get("expose") or {}

    raw = [
        ("mode", "select", "mode", "Mode", "mdi:shield-home"),
        ("status", "sensor", "status", "Status", "mdi:shield-alert"),
        ("trigger_counter", "sensor", "trigger_counter", "Trigger counter", "mdi:counter"),
        ("critical_count", "sensor", "critical_count", "Critical count", "mdi:alert-octagon"),
        ("warning_count", "sensor", "warning_count", "Warning count", "mdi:alert"),
        ("info_count", "sensor", "info_count", "Info count", "mdi:information"),
        ("alarm", "binary_sensor", "alarm", "Alarm", "mdi:alarm-light"),
        ("last_trigger", "sensor", "last_trigger", "Last trigger", "mdi:history"),
    ]
    for room in config["rooms"]:
        raw.append(
            (f"zone:{room['id']}", "sensor", f"zone_{slugify(room.get('name', ''))}_status",
             f"Zone {room.get('name', '')}", "mdi:floor-plan")
        )

    items = []
    for key, domain, suffix, name, icon in raw:
        if expose.get(key) is False:
            continue
        object_id = ids.get(key) or f"{prefix}_{suffix}"
        items.append(
            {"key": key, "domain": domain, "name": name, "icon": icon,
             "object_id": slugify(object_id) if ids.get(key) else object_id}
        )
    return items


def structure_signature(config: dict[str, Any]) -> str:
    """Change quand il faut recréer les entités exposées."""
    import json

    return json.dumps(
        [
            config["cfg"],
            [(m.get("id"), m.get("name")) for m in config["modes"]],
            [(r.get("id"), r.get("name")) for r in config["rooms"]],
        ],
        sort_keys=True,
    )
