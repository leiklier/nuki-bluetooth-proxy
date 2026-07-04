"""Button platform for the Nuki Opener."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import NukiOpenerConfigEntry
from .coordinator import NukiOpenerCoordinator
from .entity import NukiOpenerEntity
from .nuki import LockAction


async def async_setup_entry(
    hass: HomeAssistant,
    entry: NukiOpenerConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the button entities."""
    async_add_entities([NukiOpenerOpenButton(entry.runtime_data)])


class NukiOpenerOpenButton(NukiOpenerEntity, ButtonEntity):
    """Fires the electric strike to open the door."""

    _attr_translation_key = "open_door"

    def __init__(self, coordinator: NukiOpenerCoordinator) -> None:
        super().__init__(coordinator, "open_door")

    async def async_press(self) -> None:
        await self._async_execute_lock_action(LockAction.ELECTRIC_STRIKE_ACTUATION)
