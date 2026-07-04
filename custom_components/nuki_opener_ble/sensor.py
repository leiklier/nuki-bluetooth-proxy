"""Sensor platform for the Nuki Opener."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
)
from homeassistant.const import (
    SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
    EntityCategory,
    UnitOfElectricPotential,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import NukiOpenerConfigEntry
from .coordinator import NukiOpenerCoordinator
from .entity import NukiOpenerEntity
from .nuki import LockState, NukiState


@dataclass(frozen=True, kw_only=True)
class NukiOpenerSensorDescription(SensorEntityDescription):
    """Describes a Nuki Opener sensor."""

    value_fn: Callable[[NukiOpenerCoordinator], str | int | float | datetime | None]


def _enum_value(value: LockState | NukiState | None) -> str | None:
    """Map a tolerant enum to its translation option, or None if unknown."""
    if value is None or value.name.startswith("UNKNOWN_"):
        return None
    return value.name.lower()


SENSORS: tuple[NukiOpenerSensorDescription, ...] = (
    NukiOpenerSensorDescription(
        key="lock_state",
        translation_key="lock_state",
        device_class=SensorDeviceClass.ENUM,
        options=[state.name.lower() for state in LockState],
        value_fn=lambda coordinator: _enum_value(
            coordinator.device.state.lock_state if coordinator.device.state else None
        ),
    ),
    NukiOpenerSensorDescription(
        key="nuki_state",
        translation_key="nuki_state",
        device_class=SensorDeviceClass.ENUM,
        options=[state.name.lower() for state in NukiState],
        value_fn=lambda coordinator: _enum_value(
            coordinator.device.state.nuki_state if coordinator.device.state else None
        ),
    ),
    NukiOpenerSensorDescription(
        key="last_ring",
        translation_key="last_ring",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda coordinator: (
            coordinator.device.last_ring.timestamp if coordinator.device.last_ring else None
        ),
    ),
    NukiOpenerSensorDescription(
        key="battery_voltage",
        translation_key="battery_voltage",
        device_class=SensorDeviceClass.VOLTAGE,
        native_unit_of_measurement=UnitOfElectricPotential.MILLIVOLT,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda coordinator: (
            coordinator.device.battery.battery_voltage_mv if coordinator.device.battery else None
        ),
    ),
    NukiOpenerSensorDescription(
        key="rssi",
        translation_key="rssi",
        device_class=SensorDeviceClass.SIGNAL_STRENGTH,
        native_unit_of_measurement=SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda coordinator: coordinator.rssi,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: NukiOpenerConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the sensor entities."""
    async_add_entities(NukiOpenerSensor(entry.runtime_data, description) for description in SENSORS)


class NukiOpenerSensor(NukiOpenerEntity, SensorEntity):
    """A sensor exposing part of the opener state."""

    entity_description: NukiOpenerSensorDescription

    def __init__(
        self,
        coordinator: NukiOpenerCoordinator,
        description: NukiOpenerSensorDescription,
    ) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> str | int | float | datetime | None:
        return self.entity_description.value_fn(self.coordinator)
