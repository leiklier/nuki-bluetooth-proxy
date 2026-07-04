"""Bluetooth test utilities.

Minimal port of Home Assistant core's ``tests.components.bluetooth`` helpers,
which are not shipped with pytest-homeassistant-custom-component.
"""

from __future__ import annotations

import time
from typing import Any

from bleak.backends.scanner import AdvertisementData, BLEDevice
from homeassistant.components.bluetooth import (
    SOURCE_LOCAL,
    BluetoothServiceInfoBleak,
    async_get_advertisement_callback,
)
from homeassistant.core import HomeAssistant

from custom_components.nuki_opener_ble.nuki.const import (
    APPLE_MANUFACTURER_ID,
    OPENER_SERVICE_UUID,
)

from .nuki.fake_device import DEFAULT_ADDRESS

ADVERTISEMENT_DATA_DEFAULTS = {
    "local_name": "",
    "manufacturer_data": {},
    "service_data": {},
    "service_uuids": [],
    "rssi": -60,
    "platform_data": ((),),
    "tx_power": -127,
}


def generate_advertisement_data(**kwargs: Any) -> AdvertisementData:
    """Generate advertisement data with defaults."""
    new = kwargs.copy()
    for key, value in ADVERTISEMENT_DATA_DEFAULTS.items():
        new.setdefault(key, value)
    return AdvertisementData(**new)


def opener_manufacturer_data(state_changed: bool = False) -> dict[int, bytes]:
    """Build the iBeacon manufacturer data a Nuki Opener broadcasts."""
    uuid = bytes.fromhex(OPENER_SERVICE_UUID.replace("-", ""))
    measured_power = 0xC5 if state_changed else 0xC4
    return {
        APPLE_MANUFACTURER_ID: bytes.fromhex("0215") + uuid + bytes(4) + bytes([measured_power])
    }


def make_opener_service_info(
    address: str = DEFAULT_ADDRESS,
    name: str = "Nuki_Opener_11223344",
    state_changed: bool = False,
) -> BluetoothServiceInfoBleak:
    """Build a discovery info for a Nuki Opener beacon."""
    manufacturer_data = opener_manufacturer_data(state_changed)
    advertisement = generate_advertisement_data(
        local_name=name, manufacturer_data=manufacturer_data
    )
    device = BLEDevice(address=address, name=name, details={})
    return BluetoothServiceInfoBleak(
        name=name,
        address=address,
        rssi=-60,
        manufacturer_data=manufacturer_data,
        service_data={},
        service_uuids=[],
        source=SOURCE_LOCAL,
        device=device,
        advertisement=advertisement,
        connectable=True,
        time=time.monotonic(),
        tx_power=-127,
        raw=None,
    )


def inject_service_info(hass: HomeAssistant, info: BluetoothServiceInfoBleak) -> None:
    """Inject a bluetooth advertisement into the manager."""
    async_get_advertisement_callback(hass)(info)


def inject_opener_advertisement(
    hass: HomeAssistant,
    address: str = DEFAULT_ADDRESS,
    state_changed: bool = False,
) -> None:
    """Inject a Nuki Opener beacon advertisement."""
    inject_service_info(hass, make_opener_service_info(address, state_changed=state_changed))
