"""Bluetooth coordinator for the Nuki Opener.

Listens passively to the Opener's iBeacon advertisements and polls over a
proxy connection when the beacon signals a state change (or as a periodic
fallback).
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING

from homeassistant.components import bluetooth
from homeassistant.components.bluetooth.active_update_coordinator import (
    ActiveBluetoothDataUpdateCoordinator,
)
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import CoreState, HomeAssistant, callback

from .const import FALLBACK_POLL_INTERVAL
from .nuki import NukiOpenerDevice

if TYPE_CHECKING:
    from . import NukiOpenerConfigEntry

_LOGGER = logging.getLogger(__name__)


class NukiOpenerCoordinator(ActiveBluetoothDataUpdateCoordinator[None]):
    """Coordinates advertisement handling and polling for one Opener."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: NukiOpenerConfigEntry,
        device: NukiOpenerDevice,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            address=entry.data[CONF_ADDRESS],
            mode=bluetooth.BluetoothScanningMode.PASSIVE,
            needs_poll_method=self._needs_poll,
            poll_method=self._async_poll_device,
            connectable=True,
        )
        self.entry = entry
        self.device = device
        self.rssi: int | None = None

    @callback
    def _needs_poll(
        self,
        service_info: bluetooth.BluetoothServiceInfoBleak,
        seconds_since_last_poll: float | None,
    ) -> bool:
        if self.hass.state is not CoreState.running:
            return False
        # A connectable path must exist (a proxy or local adapter in range).
        if (
            bluetooth.async_ble_device_from_address(
                self.hass, service_info.device.address, connectable=True
            )
            is None
        ):
            return False
        if seconds_since_last_poll is not None and seconds_since_last_poll > (
            FALLBACK_POLL_INTERVAL
        ):
            return True
        return self.device.poll_needed(seconds_since_last_poll)

    async def _async_poll_device(self, service_info: bluetooth.BluetoothServiceInfoBleak) -> None:
        await self.device.update(time.monotonic())

    @callback
    def _async_handle_bluetooth_event(
        self,
        service_info: bluetooth.BluetoothServiceInfoBleak,
        change: bluetooth.BluetoothChange,
    ) -> None:
        self.rssi = service_info.rssi
        self.device.handle_advertisement(service_info.manufacturer_data)
        super()._async_handle_bluetooth_event(service_info, change)
