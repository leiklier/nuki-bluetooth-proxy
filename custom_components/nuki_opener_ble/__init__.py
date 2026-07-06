"""The Nuki Opener BLE integration.

Controls a Nuki Opener directly over Bluetooth, e.g. through ESPHome
Bluetooth proxies — no Nuki Bridge required.
"""

from __future__ import annotations

import logging
import time

from bleak.backends.device import BLEDevice
from bleak_retry_connector import close_stale_connections_by_address
from homeassistant.components import bluetooth
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_ADDRESS, EVENT_HOMEASSISTANT_STOP, Platform
from homeassistant.core import Event, HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady

from .const import CONF_CREDENTIALS, CONF_SECURITY_PIN
from .coordinator import NukiOpenerCoordinator
from .nuki import NukiError, NukiOpenerClient, NukiOpenerCredentials, NukiOpenerDevice

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.EVENT,
    Platform.LOCK,
    Platform.SENSOR,
    Platform.SWITCH,
]

type NukiOpenerConfigEntry = ConfigEntry[NukiOpenerCoordinator]


async def async_setup_entry(hass: HomeAssistant, entry: NukiOpenerConfigEntry) -> bool:
    """Set up a Nuki Opener from a config entry."""
    address: str = entry.data[CONF_ADDRESS]
    credentials = NukiOpenerCredentials.from_dict(entry.data[CONF_CREDENTIALS])

    def _ble_device_getter() -> BLEDevice | None:
        return bluetooth.async_ble_device_from_address(hass, address, connectable=True)

    if _ble_device_getter() is None:
        raise ConfigEntryNotReady(
            f"Nuki Opener {address} is not present; make sure a Bluetooth proxy or "
            "adapter within range is connected to Home Assistant"
        )

    # The Opener accepts a single BLE connection and stops advertising while
    # connected; make sure no stale local-adapter connection blocks it.
    await close_stale_connections_by_address(address)

    client = NukiOpenerClient(_ble_device_getter, credentials)
    device = NukiOpenerDevice(client, security_pin=entry.options.get(CONF_SECURITY_PIN))
    coordinator = NukiOpenerCoordinator(hass, entry, device)

    try:
        await device.update(time.monotonic())
    except NukiError as err:
        await client.disconnect()
        raise ConfigEntryNotReady(f"Could not connect to Nuki Opener {address}: {err}") from err
    except BaseException:
        # Never leave a connection (or its idle-disconnect timer) behind.
        await client.disconnect()
        raise

    async def _async_disconnect_on_stop(_event: Event) -> None:
        await client.disconnect()

    entry.runtime_data = coordinator
    entry.async_on_unload(
        hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, _async_disconnect_on_stop)
    )
    entry.async_on_unload(coordinator.async_start())
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    return True


async def _async_options_updated(hass: HomeAssistant, entry: NukiOpenerConfigEntry) -> None:
    """Reload the entry when options change."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: NukiOpenerConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    await entry.runtime_data.device.client.disconnect()
    return unload_ok
