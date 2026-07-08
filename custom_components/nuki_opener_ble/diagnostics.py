"""Diagnostics support for the Nuki Opener BLE integration."""

from __future__ import annotations

import dataclasses
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from . import NukiOpenerConfigEntry
from .nuki import NukiError

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
    recent_log: list[Any] | str = "unavailable without a security PIN"
    if device.security_pin is not None:
        try:
            entries = await device.client.get_log_entries(device.security_pin, count=15)
            recent_log = [
                {**_asdict(log_entry), "data": log_entry.data.hex()} for log_entry in entries
            ]
        except NukiError as err:
            recent_log = f"could not read: {err}"
    return {
        "entry": {
            "data": async_redact_data(dict(entry.data), TO_REDACT),
            "options": dict(entry.options),
        },
        "state": _asdict(device.state),
        "config": _asdict(device.config),
        "advanced_config": _asdict(device.advanced_config),
        "battery": _asdict(device.battery),
        "last_ring": _asdict(device.last_ring),
        "recent_log_entries": recent_log,
        "rssi": entry.runtime_data.rssi,
        "security_pin_configured": device.security_pin is not None,
    }
