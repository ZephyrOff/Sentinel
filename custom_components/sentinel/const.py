"""Constantes de l'intégration Sentinel."""
from __future__ import annotations

DOMAIN = "sentinel"
VERSION = "0.1.0"

# Fichiers statiques du panneau (index.html, sentinel-panel.js, logo, three.js)
URL_BASE = "/sentinel_static"
PANEL_URL_PATH = "sentinel"

STORAGE_KEY = "sentinel.config"
STORAGE_VERSION = 1

PLATFORMS = ["select", "sensor", "binary_sensor"]

SIGNAL_UPDATE = "sentinel_update_{}"

EVENT_TRIGGERED = "sentinel_triggered"
EVENT_CLEARED = "sentinel_cleared"

SERVICE_SET_MODE = "set_mode"
SERVICE_ACKNOWLEDGE = "acknowledge"
SERVICE_RESET_COUNTERS = "reset_counters"

JOURNAL_SIZE = 40
