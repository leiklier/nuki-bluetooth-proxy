"""Lock platform for the Nuki Opener.

The lock supports two behaviors:

- **ring-to-open**: unlocking arms RTO (the door buzzes open on the next
  ring), locking disarms it, and ``lock.open`` fires the electric strike.
- **buzzer**: unlocking fires the electric strike directly and the entity
  returns to locked once the opener relatches. Useful when the intercom
  wiring does not support ring-to-open, and for HomeKit (which only knows
  lock/unlock): unlock buzzes the door open.

The default resolves automatically from the opener's reported capabilities.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.lock import LockEntity, LockEntityFeature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import NukiOpenerConfigEntry
from .const import (
    CONF_LOCK_BEHAVIOR,
    LOCK_BEHAVIOR_AUTO,
    LOCK_BEHAVIOR_BUZZER,
)
from .coordinator import NukiOpenerCoordinator
from .entity import NukiOpenerEntity
from .nuki import Capability, LockAction, LockState


def resolve_buzzer_mode(entry: NukiOpenerConfigEntry) -> bool:
    """Whether the lock entity should act as a buzzer."""
    behavior = entry.options.get(CONF_LOCK_BEHAVIOR, LOCK_BEHAVIOR_AUTO)
    if behavior == LOCK_BEHAVIOR_AUTO:
        config = entry.runtime_data.device.config
        return config is not None and config.capabilities == Capability.DOOR_OPENING_ONLY
    return behavior == LOCK_BEHAVIOR_BUZZER


async def async_setup_entry(
    hass: HomeAssistant,
    entry: NukiOpenerConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the lock entity."""
    async_add_entities([NukiOpenerLock(entry.runtime_data, resolve_buzzer_mode(entry))])


class NukiOpenerLock(NukiOpenerEntity, LockEntity):
    """Ring-to-open or buzzer control of the Nuki Opener."""

    # This is the main entity of the device: use the device name directly.
    _attr_name = None
    _attr_supported_features = LockEntityFeature.OPEN

    def __init__(self, coordinator: NukiOpenerCoordinator, buzzer_mode: bool) -> None:
        super().__init__(coordinator, "lock")
        self._buzzer_mode = buzzer_mode

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
        """Buzz the door open (buzzer mode) or activate ring-to-open."""
        if self._buzzer_mode:
            await self._async_execute_lock_action(LockAction.ELECTRIC_STRIKE_ACTUATION)
        else:
            await self._async_execute_lock_action(LockAction.ACTIVATE_RTO)

    async def async_lock(self, **kwargs: Any) -> None:
        """Deactivate ring-to-open; a no-op in buzzer mode.

        In buzzer mode the opener relatches by itself after the strike
        duration, so there is nothing to lock.
        """
        if self._buzzer_mode:
            return
        await self._async_execute_lock_action(LockAction.DEACTIVATE_RTO)

    async def async_open(self, **kwargs: Any) -> None:
        """Fire the electric strike to open the door now."""
        await self._async_execute_lock_action(LockAction.ELECTRIC_STRIKE_ACTUATION)
