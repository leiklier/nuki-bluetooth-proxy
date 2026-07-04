"""Diagnostics support for the Nuki Opener BLE integration."""

from __future__ import annotations

import dataclasses
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from . import NukiOpenerConfigEntry

TO_REDACT = {"credentials", "address", "serial_number"}


def _asdict(obj: Any) -> Any:
    if obj is None:
        return None
    return dataclasses.asdict(
        obj,
        dict_factory=lambda items: {
            key: value if isinstance(value, (int, float, bool, str, type(None))) else str(value)
            for key, value in items
        },
    )


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: NukiOpenerConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    device = entry.runtime_data.device
    return {
        "entry": {
            "data": async_redact_data(dict(entry.data), TO_REDACT),
            "options": dict(entry.options),
        },
        "state": _asdict(device.state),
        "config": _asdict(device.config),
        "battery": _asdict(device.battery),
        "last_ring": _asdict(device.last_ring),
        "rssi": entry.runtime_data.rssi,
        "security_pin_configured": device.security_pin is not None,
    }
