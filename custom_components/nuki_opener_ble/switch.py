"""Switch platform for the Nuki Opener (ring-to-open and continuous mode)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.switch import SwitchEntity, SwitchEntityDescription
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import NukiOpenerConfigEntry
from .coordinator import NukiOpenerCoordinator
from .entity import NukiOpenerEntity
from .nuki import Capability, LockAction, OpenerState


@dataclass(frozen=True, kw_only=True)
class NukiOpenerSwitchDescription(SwitchEntityDescription):
    """Describes a Nuki Opener switch."""

    is_on_fn: Callable[[OpenerState], bool]
    turn_on_action: LockAction
    turn_off_action: LockAction


SWITCHES: tuple[NukiOpenerSwitchDescription, ...] = (
    NukiOpenerSwitchDescription(
        key="ring_to_open",
        translation_key="ring_to_open",
        is_on_fn=lambda state: state.ring_to_open_active,
        turn_on_action=LockAction.ACTIVATE_RTO,
        turn_off_action=LockAction.DEACTIVATE_RTO,
    ),
    NukiOpenerSwitchDescription(
        key="continuous_mode",
        translation_key="continuous_mode",
        is_on_fn=lambda state: state.continuous_mode_active,
        turn_on_action=LockAction.ACTIVATE_CM,
        turn_off_action=LockAction.DEACTIVATE_CM,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: NukiOpenerConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the switch entities."""
    coordinator = entry.runtime_data
    config = coordinator.device.config
    rto_unavailable = config is not None and config.capabilities == Capability.DOOR_OPENING_ONLY
    async_add_entities(
        NukiOpenerSwitch(coordinator, description)
        for description in SWITCHES
        if not (description.key == "ring_to_open" and rto_unavailable)
    )


class NukiOpenerSwitch(NukiOpenerEntity, SwitchEntity):
    """A switch backed by a pair of opener lock actions."""

    entity_description: NukiOpenerSwitchDescription

    def __init__(
        self,
        coordinator: NukiOpenerCoordinator,
        description: NukiOpenerSwitchDescription,
    ) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def is_on(self) -> bool | None:
        if (state := self.device.state) is None:
            return None
        return self.entity_description.is_on_fn(state)

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._async_execute_lock_action(self.entity_description.turn_on_action)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._async_execute_lock_action(self.entity_description.turn_off_action)
