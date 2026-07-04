"""Tests for integration setup and teardown."""

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from .conftest import setup_entry
from .nuki.fake_device import FakeEnvironment


async def test_setup_and_unload(
    hass: HomeAssistant,
    enable_bluetooth: None,
    environment: FakeEnvironment,
    config_entry: MockConfigEntry,
) -> None:
    """The entry sets up, creates entities, and unloads cleanly."""
    await setup_entry(hass, config_entry)
    assert config_entry.state is ConfigEntryState.LOADED

    coordinator = config_entry.runtime_data
    assert coordinator.device.state is not None
    assert coordinator.device.config is not None
    assert coordinator.device.config.name == "Front Door"

    assert await hass.config_entries.async_unload(config_entry.entry_id)
    await hass.async_block_till_done()
    assert config_entry.state is ConfigEntryState.NOT_LOADED
    # The BLE connection must be closed on unload.
    assert all(not client.is_connected for client in environment.clients)


async def test_setup_retries_when_device_absent(
    hass: HomeAssistant,
    enable_bluetooth: None,
    environment: FakeEnvironment,
    config_entry: MockConfigEntry,
) -> None:
    """Setup retries if the opener has not been seen over bluetooth."""
    config_entry.add_to_hass(hass)
    # No advertisement injected: no connectable device available.
    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert config_entry.state is ConfigEntryState.SETUP_RETRY


async def test_device_registry_entry(
    hass: HomeAssistant,
    enable_bluetooth: None,
    environment: FakeEnvironment,
    config_entry: MockConfigEntry,
    device_registry,
) -> None:
    """The device is registered with Nuki metadata."""
    await setup_entry(hass, config_entry)
    device = device_registry.async_get_device(connections={("bluetooth", "AA:BB:CC:DD:EE:FF")})
    assert device is not None
    assert device.manufacturer == "Nuki Home Solutions GmbH"
    assert device.model == "Opener"
    assert device.name == "Front Door"
    assert device.sw_version == "1.8.0"
    assert device.hw_version == "5.2"
    assert device.serial_number == "11223344"
