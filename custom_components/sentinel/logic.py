"""Logique pure de Sentinel (aucune dépendance à Home Assistant).

Tout ce qui décide d'un niveau, d'une zone ou d'un nom d'entité est ici, pour
pouvoir être testé sans instance Home Assistant.

Modèle de configuration (identique à celui du panneau) :

    {
      "floors": [{"id", "name", "short", "level"}],
      "rooms":  [{"id", "floor", "name", "pts": [[x, z], ...], "wl": [0|1, ...],
                  "x", "z", "w", "d" (boîte englobante), "outdoor"?, ...}],
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
        # états francisés (capteurs template, intégrations localisées)
        "ouvert", "ouverte", "allumé", "allumée", "détecté", "détectée",
    }
)

# Les entry delay ne s'appliquent jamais à ces types (sécurité des personnes).
SAFETY_TYPES = frozenset({"smoke", "leak"})

# Évènements d'un mode auxquels on peut rattacher une notification et des actions.
# enter : le mode vient d'être activé ; clear : une entité redevient normale.
EVENTS = ("enter", "info", "warning", "critical", "clear")
ZONE_EVENTS = ("info", "warning", "critical", "clear")

TYPE_LABELS = {
    "door": "Porte", "window": "Fenêtre", "bay": "Baie vitrée", "garage": "Porte de garage",
    "gate": "Portail", "wardrobe": "Armoire", "cabinet": "Placard / buffet",
    "fridge": "Réfrigérateur", "safe": "Coffre-fort", "medicine": "Armoire à pharmacie",
    "motion": "Mouvement", "presence": "Présence", "smoke": "Fumée", "leak": "Fuite d'eau",
}

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
    # mémorisation jusqu'à acquittement : statut/alarme/compteurs, et zones
    cfg["cfg"]["latch_alarm"] = bool(cfg["cfg"].get("latch_alarm"))
    cfg["cfg"]["latch_zones"] = bool(cfg["cfg"].get("latch_zones"))
    try:
        hold = int(cfg["cfg"].get("hold_minutes", 30))
    except (TypeError, ValueError):
        hold = 30
    cfg["cfg"]["hold_minutes"] = max(1, min(hold, 720))
    for mode in cfg["modes"]:
        mode.setdefault("def", "off")
        mode.setdefault("ackDelay", 60)
        mode.setdefault("handoffDelay", 10)
        if not isinstance(mode.get("actions"), dict):
            mode["actions"] = {}
        if not isinstance(mode.get("zones"), dict):
            mode["zones"] = {}
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
        if _room_contains(room, x, z):
            return room
    return None


def _room_contains(room: dict[str, Any], x: float, z: float) -> bool:
    """Point dans la pièce : polygone `pts` si présent, sinon rectangle x/z/w/d."""
    pts = room.get("pts")
    try:
        if isinstance(pts, list) and len(pts) >= 3:
            return _in_polygon(pts, x, z)
        return (room["x"] <= x <= room["x"] + room["w"]
                and room["z"] <= z <= room["z"] + room["d"])
    except (KeyError, TypeError, ValueError, IndexError):
        return False


def _in_polygon(pts: list[Any], x: float, z: float) -> bool:
    """Lancer de rayon ; un point sur le bord est dedans."""
    eps = 1e-9
    inside = False
    j = len(pts) - 1
    for i in range(len(pts)):
        ax, az = float(pts[i][0]), float(pts[i][1])
        bx, bz = float(pts[j][0]), float(pts[j][1])
        cross = (bx - ax) * (z - az) - (bz - az) * (x - ax)
        if (abs(cross) < eps
                and min(ax, bx) - eps <= x <= max(ax, bx) + eps
                and min(az, bz) - eps <= z <= max(az, bz) + eps):
            return True
        if (az > z) != (bz > z) and x < (bx - ax) * (z - az) / (bz - az) + ax:
            inside = not inside
        j = i
    return inside


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


def _polygon(room: dict[str, Any]) -> list[tuple[float, float]]:
    pts = room.get("pts")
    if isinstance(pts, list) and len(pts) >= 3:
        return [(float(p[0]), float(p[1])) for p in pts]
    x, z, w, d = (float(room.get(k, 0)) for k in ("x", "z", "w", "d"))
    return [(x, z), (x + w, z), (x + w, z + d), (x, z + d)]


def _touching(pa: list[tuple[float, float]], pb: list[tuple[float, float]],
              tol: float = 0.25, min_overlap: float = 0.3) -> bool:
    """Deux polygones se touchent s'ils ont deux arêtes colinéaires qui se recouvrent."""
    for i in range(len(pa)):
        a1, a2 = pa[i], pa[(i + 1) % len(pa)]
        ax, az = a2[0] - a1[0], a2[1] - a1[1]
        length = (ax * ax + az * az) ** 0.5
        if length < 1e-6:
            continue
        for j in range(len(pb)):
            b1, b2 = pb[j], pb[(j + 1) % len(pb)]
            bx, bz = b2[0] - b1[0], b2[1] - b1[1]
            blen = (bx * bx + bz * bz) ** 0.5
            if blen < 1e-6 or abs(ax * bz - az * bx) / (length * blen) > 0.03:
                continue
            d1 = abs(ax * (b1[1] - a1[1]) - az * (b1[0] - a1[0])) / length
            d2 = abs(ax * (b2[1] - a1[1]) - az * (b2[0] - a1[0])) / length
            if d1 > tol or d2 > tol:
                continue
            t1 = ((b1[0] - a1[0]) * ax + (b1[1] - a1[1]) * az) / length
            t2 = ((b2[0] - a1[0]) * ax + (b2[1] - a1[1]) * az) / length
            if min(length, max(t1, t2)) - max(0.0, min(t1, t2)) >= min_overlap:
                return True
    return False


def adjacent_room_ids(config: dict[str, Any], room_id: str) -> set[str]:
    """Pièces ou zones du même étage qui partagent un bord avec la pièce donnée."""
    rooms = config.get("rooms", [])
    base = next((r for r in rooms if r.get("id") == room_id), None)
    if base is None:
        return set()
    poly = _polygon(base)
    return {
        r["id"] for r in rooms
        if r.get("id") != room_id and r.get("floor") == base.get("floor")
        and _touching(poly, _polygon(r))
    }


def entity_key(ent: dict[str, Any]) -> str:
    """Identifiant d'entité nettoyé (espaces, casse) ; "" si l'entité n'est pas liée."""
    return str(ent.get("entity") or "").strip().lower()


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


def template_values(ent: dict[str, Any] | None, room: dict[str, Any] | None,
                    mode: dict[str, Any], level: str, on: bool,
                    floor: str = "", when: Any = None) -> dict[str, str]:
    """Variables disponibles dans les messages et les données d'action."""
    ent = ent or {}
    return {
        "name": ent.get("name", ""),
        "zone": room.get("name", "—") if room else "—",
        "floor": floor,
        "type": TYPE_LABELS.get(ent.get("type"), ent.get("type") or ""),
        "state": state_text(ent, on) if ent else "",
        "level": level,
        "mode": mode.get("name", ""),
        "entity": ent.get("entity", ""),
        "time": when.strftime("%H:%M") if when else "",
        "date": when.strftime("%d/%m/%Y") if when else "",
    }


def fill_template(template: str, ent: dict[str, Any], room: dict[str, Any] | None,
                  mode: dict[str, Any], level: str, on: bool,
                  floor: str = "", when: Any = None) -> str:
    values = template_values(ent, room, mode, level, on, floor, when)
    return _TPL.sub(lambda m: str(values.get(m.group(1), m.group(0))), template or "")


def fill_data(obj: Any, values: dict[str, str]) -> Any:
    """Applique les variables {name}… à toutes les chaînes d'une structure."""
    if isinstance(obj, str):
        return _TPL.sub(lambda m: str(values.get(m.group(1), m.group(0))), obj)
    if isinstance(obj, list):
        return [fill_data(v, values) for v in obj]
    if isinstance(obj, dict):
        return {k: fill_data(v, values) for k, v in obj.items()}
    return obj


def reactions_for(mode: dict[str, Any], room: dict[str, Any] | None,
                  event: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Notifications et actions d'un évènement.

    Le réglage de la zone remplace celui du mode, sauf si la zone demande de
    s'y ajouter (`zones[id].add.notify[event]` / `.add.actions[event]`)."""
    mn = (mode.get("notify") or {}).get(event)
    ma = (mode.get("actions") or {}).get(event)
    notifies = [mn] if isinstance(mn, dict) and mn else []
    actions = [a for a in ma if isinstance(a, dict)] if isinstance(ma, list) else []
    zone = (mode.get("zones") or {}).get(room.get("id")) if room else None
    if isinstance(zone, dict) and event in ZONE_EVENTS:
        add = zone.get("add") if isinstance(zone.get("add"), dict) else {}
        zn = zone.get("notify") if isinstance(zone.get("notify"), dict) else {}
        za = zone.get("actions") if isinstance(zone.get("actions"), dict) else {}
        if isinstance(zn.get(event), dict):
            keep = notifies if (add.get("notify") or {}).get(event) else []
            notifies = keep + [zn[event]]
        if isinstance(za.get(event), list):
            keep = actions if (add.get("actions") or {}).get(event) else []
            actions = keep + [a for a in za[event] if isinstance(a, dict)]
    return notifies, actions


def action_call(action: dict[str, Any], values: dict[str, str]) -> tuple[str, str, dict[str, Any]] | None:
    """(domaine, service, données) d'une action, ou None si elle est incomplète."""
    service = str(action.get("service") or "").strip()
    domain, _, name = service.partition(".")
    if not domain or not name:
        return None
    data = action.get("data")
    data = fill_data(data, values) if isinstance(data, dict) else {}
    target = action.get("entity_id")
    if isinstance(target, str):
        ids = [i.strip() for i in target.split(",") if i.strip()]
    elif isinstance(target, list):
        ids = [str(i).strip() for i in target if str(i).strip()]
    else:
        ids = []
    if ids and "entity_id" not in data:
        data["entity_id"] = ids if len(ids) > 1 else ids[0]
    return domain, name, data


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
    used: set[tuple[str, str]] = set()
    for key, domain, suffix, name, icon in raw:
        if expose.get(key) is False:
            continue
        object_id = ids.get(key) or f"{prefix}_{suffix}"
        if ids.get(key):
            object_id = slugify(object_id)
        # Deux pièces au même nom (« Nouvelle pièce », « Jardin »…) ne doivent pas
        # produire le même identifiant : on numérote dans l'ordre de la configuration.
        base, n = object_id, 2
        while (domain, object_id) in used:
            object_id = f"{base}_{n}"
            n += 1
        used.add((domain, object_id))
        items.append(
            {"key": key, "domain": domain, "name": name, "icon": icon, "object_id": object_id}
        )
    return items


def structure_signature(config: dict[str, Any]) -> str:
    """Change quand il faut recréer les entités exposées."""
    import json

    # les options de mémorisation n'ont aucun effet sur les entités créées
    cfg = {k: v for k, v in config["cfg"].items()
           if not k.startswith("latch_") and k != "hold_minutes"}
    return json.dumps(
        [
            cfg,
            [(m.get("id"), m.get("name")) for m in config["modes"]],
            [(r.get("id"), r.get("name")) for r in config["rooms"]],
        ],
        sort_keys=True,
    )
