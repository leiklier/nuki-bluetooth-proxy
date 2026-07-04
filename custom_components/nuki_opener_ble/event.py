"""Event platform for the Nuki Opener doorbell."""

from __future__ import annotations

from homeassistant.components.event import EventDeviceClass, EventEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import NukiOpenerConfigEntry
from .coordinator import NukiOpenerCoordinator
from .entity import NukiOpenerEntity
from .nuki import RingEvent

EVENT_RING = "ring"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: NukiOpenerConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the doorbell event entity."""
    async_add_entities([NukiOpenerDoorbellEvent(entry.runtime_data)])


class NukiOpenerDoorbellEvent(NukiOpenerEntity, EventEntity):
    """Fires when someone rings the doorbell connected to the Opener."""

    _attr_device_class = EventDeviceClass.DOORBELL
    _attr_event_types = [EVENT_RING]  # noqa: RUF012 (HA entity attribute convention)
    _attr_translation_key = "doorbell"

    def __init__(self, coordinator: NukiOpenerCoordinator) -> None:
        super().__init__(coordinator, "doorbell")

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(self.device.subscribe_ring(self._async_handle_ring))

    @callback
    def _async_handle_ring(self, event: RingEvent) -> None:
        self._trigger_event(
            EVENT_RING,
            {"detected_by": event.detected_by, "suppressed": event.suppressed},
        )
        self.async_write_ha_state()
