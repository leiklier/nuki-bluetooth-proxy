"""Tests for the options flow (security PIN)."""

from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.nuki_opener_ble.const import (
    CONF_LOCK_BEHAVIOR,
    CONF_SECURITY_PIN,
    LOCK_BEHAVIOR_AUTO,
    LOCK_BEHAVIOR_BUZZER,
)

from .conftest import setup_entry
from .nuki.fake_device import FakeEnvironment


async def test_set_valid_pin(
    hass: HomeAssistant,
    enable_bluetooth: None,
    environment: FakeEnvironment,
    config_entry: MockConfigEntry,
) -> None:
    """A valid PIN is verified against the device and stored."""
    await setup_entry(hass, config_entry)
    result = await hass.config_entries.options.async_init(config_entry.entry_id)
    assert result["type"] is FlowResultType.FORM

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_SECURITY_PIN: "1234"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert config_entry.options == {
        CONF_SECURITY_PIN: 1234,
        CONF_LOCK_BEHAVIOR: LOCK_BEHAVIOR_AUTO,
    }
    await hass.async_block_till_done()


async def test_wrong_pin_is_rejected(
    hass: HomeAssistant,
    enable_bluetooth: None,
    environment: FakeEnvironment,
    config_entry: MockConfigEntry,
) -> None:
    """A PIN the device rejects shows an error."""
    await setup_entry(hass, config_entry)
    result = await hass.config_entries.options.async_init(config_entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_SECURITY_PIN: "1111"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {CONF_SECURITY_PIN: "invalid_pin"}


async def test_malformed_pin_is_rejected(
    hass: HomeAssistant,
    enable_bluetooth: None,
    environment: FakeEnvironment,
    config_entry: MockConfigEntry,
) -> None:
    """A non-numeric or out-of-range PIN shows an error."""
    await setup_entry(hass, config_entry)
    result = await hass.config_entries.options.async_init(config_entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_SECURITY_PIN: "not-a-pin"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {CONF_SECURITY_PIN: "invalid_pin"}


async def test_clearing_pin(
    hass: HomeAssistant,
    enable_bluetooth: None,
    environment: FakeEnvironment,
    config_entry: MockConfigEntry,
) -> None:
    """Submitting an empty PIN clears the option."""
    await setup_entry(hass, config_entry)
    hass.config_entries.async_update_entry(config_entry, options={CONF_SECURITY_PIN: 1234})
    await hass.async_block_till_done()

    result = await hass.config_entries.options.async_init(config_entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_SECURITY_PIN: ""}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert CONF_SECURITY_PIN not in config_entry.options
    await hass.async_block_till_done()


async def test_set_lock_behavior(
    hass: HomeAssistant,
    enable_bluetooth: None,
    environment: FakeEnvironment,
    config_entry: MockConfigEntry,
) -> None:
    """The lock behavior option is stored without requiring a PIN."""
    await setup_entry(hass, config_entry)
    result = await hass.config_entries.options.async_init(config_entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {CONF_SECURITY_PIN: "", CONF_LOCK_BEHAVIOR: LOCK_BEHAVIOR_BUZZER},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert config_entry.options == {CONF_LOCK_BEHAVIOR: LOCK_BEHAVIOR_BUZZER}
    await hass.async_block_till_done()
