"""Parsing of Nuki BLE advertisements.

A paired Nuki Opener advertises an Apple iBeacon whose proximity UUID is the
opener service UUID. The beacon's trailing "measured power" byte doubles as a
signal flag: an odd value means the device state changed and should be polled.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from .const import APPLE_MANUFACTURER_ID, IBEACON_PREFIX, OPENER_SERVICE_UUID

_IBEACON_LENGTH = 23


@dataclass(frozen=True, slots=True)
class NukiAdvertisement:
    """A parsed Nuki iBeacon frame."""

    service_uuid: str
    major: int
    minor: int
    measured_power: int

    @property
    def is_opener(self) -> bool:
        return self.service_uuid == OPENER_SERVICE_UUID

    @property
    def state_changed(self) -> bool:
        """True if the device signals an unfetched state change."""
        return bool(self.measured_power & 0x01)


def parse_manufacturer_data(manufacturer_data: dict[int, bytes]) -> NukiAdvertisement | None:
    """Parse Nuki's iBeacon from advertisement manufacturer data.

    Returns None for non-iBeacon frames (e.g. HomeKit advertisements, which
    Nuki devices also broadcast under the Apple manufacturer ID).
    """
    data = manufacturer_data.get(APPLE_MANUFACTURER_ID)
    if data is None or len(data) < _IBEACON_LENGTH or data[0:2] != IBEACON_PREFIX:
        return None
    return NukiAdvertisement(
        service_uuid=str(UUID(bytes=bytes(data[2:18]))),
        major=int.from_bytes(data[18:20], "big"),
        minor=int.from_bytes(data[20:22], "big"),
        measured_power=data[22],
    )
