"""Number platform for the Nuki Opener (sound volume).

Settings entities require the security PIN (integration options) and are
only created when it is configured.
"""

from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import NukiOpenerConfigEntry
from .coordinator import NukiOpenerCoordinator
from .entity import NukiOpenerEntity
from .nuki import NukiError


async def async_setup_entry(
    hass: HomeAssistant,
    entry: NukiOpenerConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the number entities."""
    if entry.runtime_data.device.security_pin is None:
        return
    async_add_entities([NukiOpenerSoundVolume(entry.runtime_data)])


class NukiOpenerSoundVolume(NukiOpenerEntity, NumberEntity):
    """The opener's sound volume (0-255)."""

    _attr_translation_key = "sound_volume"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_mode = NumberMode.SLIDER
    _attr_native_min_value = 0
    _attr_native_max_value = 255
    _attr_native_step = 1

    def __init__(self, coordinator: NukiOpenerCoordinator) -> None:
        super().__init__(coordinator, "sound_volume")

    @property
    def native_value(self) -> int | None:
        if (config := self.device.advanced_config) is None:
            return None
        return config.sound_level

    async def async_set_native_value(self, value: float) -> None:
        try:
            await self.device.change_advanced_config(sound_level=int(value))
        except NukiError as err:
            raise HomeAssistantError(f"Could not change the sound volume: {err}") from err
        finally:
            self.coordinator.async_update_listeners()
