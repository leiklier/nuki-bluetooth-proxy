"""Switch platform for the Nuki Opener.

Action switches (ring-to-open, continuous mode) drive lock actions.
Settings switches (doorbell suppression) write the advanced configuration
and require the security PIN, so they are only created when it is set.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.switch import SwitchEntity, SwitchEntityDescription
from homeassistant.const import STATE_ON, EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from . import NukiOpenerConfigEntry
from .coordinator import NukiOpenerCoordinator
from .entity import NukiOpenerEntity
from .nuki import AdvancedConfig, Capability, LockAction, NukiError, OpenerState


@dataclass(frozen=True, kw_only=True)
class NukiOpenerSwitchDescription(SwitchEntityDescription):
    """Describes a switch backed by a pair of opener lock actions."""

    is_on_fn: Callable[[OpenerState], bool]
    turn_on_action: LockAction
    turn_off_action: LockAction


@dataclass(frozen=True, kw_only=True)
class NukiOpenerSuppressionDescription(SwitchEntityDescription):
    """Describes a doorbell-suppression bit of the advanced configuration."""

    bit: int


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

# Bitmask layout per the Opener BLE spec: bit0 CM, bit1 RTO, bit2 ring.
SUPPRESSION_SWITCHES: tuple[NukiOpenerSuppressionDescription, ...] = (
    NukiOpenerSuppressionDescription(
        key="suppress_ring",
        translation_key="suppress_ring",
        entity_category=EntityCategory.CONFIG,
        bit=0x04,
    ),
    NukiOpenerSuppressionDescription(
        key="suppress_rto",
        translation_key="suppress_rto",
        entity_category=EntityCategory.CONFIG,
        bit=0x02,
    ),
    NukiOpenerSuppressionDescription(
        key="suppress_cm",
        translation_key="suppress_cm",
        entity_category=EntityCategory.CONFIG,
        bit=0x01,
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
    entities: list[NukiOpenerEntity] = [
        NukiOpenerSwitch(coordinator, description)
        for description in SWITCHES
        if not (description.key == "ring_to_open" and rto_unavailable)
    ]
    entities.append(NukiOpenerDoorbellNotificationsSwitch(coordinator))
    if coordinator.device.security_pin is not None:
        entities.extend(
            NukiOpenerSuppressionSwitch(coordinator, description)
            for description in SUPPRESSION_SWITCHES
        )
    async_add_entities(entities)


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


class NukiOpenerDoorbellNotificationsSwitch(NukiOpenerEntity, SwitchEntity, RestoreEntity):
    """Whether a doorbell ring surfaces as the doorbell event.

    A Home-Assistant-local preference, not an Opener setting: it does not
    touch the intercom chime (that is the suppression switches). Turning it
    off stops the doorbell event entity from firing, which silences Apple
    HomeKit doorbell notifications — the Home app cannot mute those for a
    camera-less doorbell — as well as any automations that trigger on the
    event. The state is restored across restarts.
    """

    _attr_entity_category = EntityCategory.CONFIG
    _attr_translation_key = "doorbell_notifications"

    def __init__(self, coordinator: NukiOpenerCoordinator) -> None:
        super().__init__(coordinator, "doorbell_notifications")

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if (last_state := await self.async_get_last_state()) is not None:
            self.coordinator.doorbell_notifications_enabled = last_state.state == STATE_ON

    @property
    def is_on(self) -> bool:
        return self.coordinator.doorbell_notifications_enabled

    async def async_turn_on(self, **kwargs: Any) -> None:
        self.coordinator.doorbell_notifications_enabled = True
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        self.coordinator.doorbell_notifications_enabled = False
        self.async_write_ha_state()


class NukiOpenerSuppressionSwitch(NukiOpenerEntity, SwitchEntity):
    """A doorbell-suppression flag in the opener's advanced configuration."""

    entity_description: NukiOpenerSuppressionDescription

    def __init__(
        self,
        coordinator: NukiOpenerCoordinator,
        description: NukiOpenerSuppressionDescription,
    ) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def _advanced_config(self) -> AdvancedConfig | None:
        return self.device.advanced_config

    @property
    def is_on(self) -> bool | None:
        if (config := self._advanced_config) is None:
            return None
        return bool(config.doorbell_suppression & self.entity_description.bit)

    async def _async_set_bit(self, enabled: bool) -> None:
        if (config := self._advanced_config) is None:
            raise HomeAssistantError("The opener's configuration has not been read yet")
        bit = self.entity_description.bit
        mask = (
            config.doorbell_suppression | bit if enabled else (config.doorbell_suppression & ~bit)
        )
        try:
            await self.device.change_advanced_config(doorbell_suppression=mask)
        except NukiError as err:
            raise HomeAssistantError(f"Could not change doorbell suppression: {err}") from err
        finally:
            self.coordinator.async_update_listeners()

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._async_set_bit(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._async_set_bit(False)
