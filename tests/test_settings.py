"""Tests for the advanced-configuration settings entities."""

from homeassistant.const import ATTR_ENTITY_ID, STATE_OFF, STATE_ON
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.nuki_opener_ble.const import CONF_SECURITY_PIN

from .conftest import setup_entry
from .nuki.fake_device import FakeEnvironment

VOLUME_ENTITY = "number.front_door_sound_volume"
SUPPRESS_RING = "switch.front_door_suppress_ring_sound"


async def _setup_with_pin(hass: HomeAssistant, config_entry: MockConfigEntry) -> None:
    config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(config_entry, options={CONF_SECURITY_PIN: 1234})
    await setup_entry(hass, config_entry)


async def test_settings_entities_require_pin(
    hass: HomeAssistant,
    enable_bluetooth: None,
    environment: FakeEnvironment,
    config_entry: MockConfigEntry,
) -> None:
    """Without a security PIN, no settings entities are created."""
    await setup_entry(hass, config_entry)
    assert hass.states.get(VOLUME_ENTITY) is None
    assert hass.states.get(SUPPRESS_RING) is None


async def test_sound_volume(
    hass: HomeAssistant,
    enable_bluetooth: None,
    environment: FakeEnvironment,
    config_entry: MockConfigEntry,
) -> None:
    """The sound volume reads and writes the advanced configuration."""
    await _setup_with_pin(hass, config_entry)
    assert hass.states.get(VOLUME_ENTITY).state == "80"

    await hass.services.async_call(
        "number",
        "set_value",
        {ATTR_ENTITY_ID: VOLUME_ENTITY, "value": 0},
        blocking=True,
    )
    assert environment.opener.advanced_config.sound_level == 0
    assert hass.states.get(VOLUME_ENTITY).state == "0"
    # Only the changed field differs; everything else is preserved.
    assert environment.opener.advanced_config.rto_timeout_minutes == 20
    assert environment.opener.advanced_config.electric_strike_duration_ms == 3000


async def test_doorbell_suppression_switch(
    hass: HomeAssistant,
    enable_bluetooth: None,
    environment: FakeEnvironment,
    config_entry: MockConfigEntry,
) -> None:
    """The suppression switch flips only its own bit."""
    await _setup_with_pin(hass, config_entry)
    assert hass.states.get(SUPPRESS_RING).state == STATE_OFF

    await hass.services.async_call(
        "switch", "turn_on", {ATTR_ENTITY_ID: SUPPRESS_RING}, blocking=True
    )
    assert environment.opener.advanced_config.doorbell_suppression == 0x04
    assert hass.states.get(SUPPRESS_RING).state == STATE_ON

    await hass.services.async_call(
        "switch", "turn_off", {ATTR_ENTITY_ID: SUPPRESS_RING}, blocking=True
    )
    assert environment.opener.advanced_config.doorbell_suppression == 0x00
    assert hass.states.get(SUPPRESS_RING).state == STATE_OFF


async def test_write_preserves_concurrent_app_changes(
    hass: HomeAssistant,
    enable_bluetooth: None,
    environment: FakeEnvironment,
    config_entry: MockConfigEntry,
) -> None:
    """Writes are read-modify-write against the device, not a stale cache."""
    import dataclasses

    await _setup_with_pin(hass, config_entry)
    # Someone changes the RTO timeout in the Nuki app behind HA's back.
    environment.opener.advanced_config = dataclasses.replace(
        environment.opener.advanced_config, rto_timeout_minutes=45
    )
    await hass.services.async_call(
        "number",
        "set_value",
        {ATTR_ENTITY_ID: VOLUME_ENTITY, "value": 120},
        blocking=True,
    )
    assert environment.opener.advanced_config.sound_level == 120
    assert environment.opener.advanced_config.rto_timeout_minutes == 45
