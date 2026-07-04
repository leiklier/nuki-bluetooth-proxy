"""Lock platform for the Nuki Opener.

The lock maps the opener's ring-to-open feature: unlocking activates RTO
(the door buzzes open on the next ring), locking deactivates it, and
``lock.open`` fires the electric strike immediately.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.lock import LockEntity, LockEntityFeature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import NukiOpenerConfigEntry
from .coordinator import NukiOpenerCoordinator
from .entity import NukiOpenerEntity
from .nuki import LockAction, LockState


async def async_setup_entry(
    hass: HomeAssistant,
    entry: NukiOpenerConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the lock entity."""
    async_add_entities([NukiOpenerLock(entry.runtime_data)])


class NukiOpenerLock(NukiOpenerEntity, LockEntity):
    """Ring-to-open control of the Nuki Opener."""

    # This is the main entity of the device: use the device name directly.
    _attr_name = None
    _attr_supported_features = LockEntityFeature.OPEN

    def __init__(self, coordinator: NukiOpenerCoordinator) -> None:
        super().__init__(coordinator, "lock")

    @property
    def is_locked(self) -> bool | None:
        if (state := self.device.state) is None:
            return None
        if state.lock_state == LockState.UNDEFINED:
            return None
        return state.lock_state == LockState.LOCKED

    @property
    def is_open(self) -> bool | None:
        if (state := self.device.state) is None:
            return None
        return state.lock_state == LockState.OPEN

    @property
    def is_opening(self) -> bool | None:
        if (state := self.device.state) is None:
            return None
        return state.lock_state == LockState.OPENING

    @property
    def is_jammed(self) -> bool | None:
        if (state := self.device.state) is None:
            return None
        return state.lock_state == LockState.UNCALIBRATED

    async def async_unlock(self, **kwargs: Any) -> None:
        """Activate ring-to-open."""
        await self._async_execute_lock_action(LockAction.ACTIVATE_RTO)

    async def async_lock(self, **kwargs: Any) -> None:
        """Deactivate ring-to-open."""
        await self._async_execute_lock_action(LockAction.DEACTIVATE_RTO)

    async def async_open(self, **kwargs: Any) -> None:
        """Fire the electric strike to open the door now."""
        await self._async_execute_lock_action(LockAction.ELECTRIC_STRIKE_ACTUATION)
