"""Binary sensor platform for the Nuki Opener."""

from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import NukiOpenerConfigEntry
from .coordinator import NukiOpenerCoordinator
from .entity import NukiOpenerEntity
from .nuki import DoorSensorState


async def async_setup_entry(
    hass: HomeAssistant,
    entry: NukiOpenerConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the binary sensor entities."""
    coordinator = entry.runtime_data
    entities: list[NukiOpenerEntity] = [NukiOpenerBatteryCritical(coordinator)]
    state = coordinator.device.state
    if state is not None and state.door_sensor_state not in (
        None,
        DoorSensorState.UNAVAILABLE,
        DoorSensorState.DEACTIVATED,
    ):
        entities.append(NukiOpenerDoorSensor(coordinator))
    async_add_entities(entities)


class NukiOpenerBatteryCritical(NukiOpenerEntity, BinarySensorEntity):
    """Reports a critically low opener battery."""

    _attr_device_class = BinarySensorDeviceClass.BATTERY
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_translation_key = "battery_critical"

    def __init__(self, coordinator: NukiOpenerCoordinator) -> None:
        super().__init__(coordinator, "battery_critical")

    @property
    def is_on(self) -> bool | None:
        if (state := self.device.state) is None:
            return None
        return state.battery_critical


class NukiOpenerDoorSensor(NukiOpenerEntity, BinarySensorEntity):
    """Reports the state of an attached door sensor."""

    _attr_device_class = BinarySensorDeviceClass.DOOR

    def __init__(self, coordinator: NukiOpenerCoordinator) -> None:
        super().__init__(coordinator, "door")

    @property
    def is_on(self) -> bool | None:
        if (state := self.device.state) is None:
            return None
        if state.door_sensor_state == DoorSensorState.DOOR_OPENED:
            return True
        if state.door_sensor_state == DoorSensorState.DOOR_CLOSED:
            return False
        return None
