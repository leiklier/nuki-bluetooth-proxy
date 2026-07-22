"""Tests for the diagnostics download."""

from homeassistant.core import HomeAssistant

from custom_components.nuki_opener_ble.diagnostics import (
    async_get_config_entry_diagnostics,
)

from .conftest import setup_entry
from .nuki.fake_device import FakeEnvironment


async def test_diagnostics(
    hass: HomeAssistant,
    enable_bluetooth: None,
    environment: FakeEnvironment,
    config_entry,
) -> None:
    """Diagnostics include device state and redact secrets."""
    await setup_entry(hass, config_entry)
    diagnostics = await async_get_config_entry_diagnostics(hass, config_entry)

    assert diagnostics["entry"]["data"]["credentials"] == "**REDACTED**"
    assert diagnostics["entry"]["data"]["address"] == "**REDACTED**"
    assert diagnostics["state"]["lock_state"] == 1
    assert diagnostics["config"]["name"] == "Front Door"
    assert diagnostics["battery"]["battery_voltage_mv"] == 5450
    assert diagnostics["security_pin_configured"] is False
    assert diagnostics["recent_log_entries"] == "unavailable without a security PIN"


async def test_diagnostics_includes_log_with_pin(
    hass: HomeAssistant,
    enable_bluetooth: None,
    environment: FakeEnvironment,
    config_entry,
) -> None:
    """With a PIN, recent log entries are included raw."""
    environment.opener.add_doorbell_log_entry()
    config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(config_entry, options={"security_pin": 1234})
    await setup_entry(hass, config_entry)
    diagnostics = await async_get_config_entry_diagnostics(hass, config_entry)
    assert diagnostics["security_pin_configured"] is True
    assert diagnostics["entry"]["options"]["security_pin"] == "**REDACTED**"
    entries = diagnostics["recent_log_entries"]
    assert entries[0]["type"] == 6  # doorbell recognition
    assert isinstance(entries[0]["data"], str)
