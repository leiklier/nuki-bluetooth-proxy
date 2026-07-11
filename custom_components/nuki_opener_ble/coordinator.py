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
from homeassistant.helpers.debounce import Debouncer

from .const import FALLBACK_POLL_INTERVAL
from .nuki import NukiError, NukiOpenerDevice

if TYPE_CHECKING:
    from . import NukiOpenerConfigEntry

_LOGGER = logging.getLogger(__name__)

# Cooldown between beacon-triggered polls. The default of 10 s would delay
# every state change that follows another one within 10 s (a ring opening the
# door, a quick lock after unlock) by up to the full cooldown; polls only
# happen when the beacon signals a change, so a short cooldown stays cheap.
POLL_DEBOUNCE_COOLDOWN = 2.0


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
            poll_debouncer=Debouncer(
                hass,
                _LOGGER,
                cooldown=POLL_DEBOUNCE_COOLDOWN,
                immediate=True,
                background=True,
            ),
            connectable=True,
        )
        self.entry = entry
        self.device = device
        self.rssi: int | None = None
        device.state_listener = self.async_update_listeners

    async def async_refresh_after_action(self) -> None:
        """Read fresh state right after an action we initiated.

        The passive loop (state-change beacon -> debounced poll -> new BLE
        connection) takes 10-20 s; after our own action the connection is
        still warm, so reading directly makes entities reflect the outcome
        immediately.
        """
        try:
            await self.device.update(time.monotonic())
        except NukiError as err:
            # The next beacon-triggered poll will catch up.
            _LOGGER.debug("State refresh after action failed: %s", err)
        self.async_update_listeners()

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
