"""Base entity for the Nuki Opener BLE integration."""

from __future__ import annotations

from homeassistant.components.bluetooth.passive_update_coordinator import (
    PassiveBluetoothCoordinatorEntity,
)
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import DeviceInfo, format_mac

from .const import DEFAULT_NAME, MANUFACTURER, MODEL
from .coordinator import NukiOpenerCoordinator
from .nuki import LockAction, NukiError, NukiOpenerDevice


class NukiOpenerEntity(PassiveBluetoothCoordinatorEntity[NukiOpenerCoordinator]):
    """Common behaviour for all Nuki Opener entities."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: NukiOpenerCoordinator, key: str | None = None) -> None:
        super().__init__(coordinator)
        base_unique_id = format_mac(coordinator.address)
        self._attr_unique_id = f"{base_unique_id}-{key}" if key else base_unique_id
        config = coordinator.device.config
        self._attr_device_info = DeviceInfo(
            connections={(dr.CONNECTION_BLUETOOTH, coordinator.address)},
            name=config.name if config else DEFAULT_NAME,
            manufacturer=MANUFACTURER,
            model=MODEL,
            sw_version=config.firmware_version if config else None,
            hw_version=config.hardware_revision if config else None,
            serial_number=f"{config.nuki_id:08X}" if config else None,
        )

    @property
    def device(self) -> NukiOpenerDevice:
        return self.coordinator.device

    async def _async_execute_lock_action(self, action: LockAction) -> None:
        """Run a lock action, logging the HA user in the opener's activity log."""
        name_suffix = None
        if (
            self._context is not None
            and self._context.user_id is not None
            and (user := await self.hass.auth.async_get_user(self._context.user_id))
        ):
            name_suffix = user.name
        try:
            await self.device.execute_lock_action(action, name_suffix=name_suffix)
        except NukiError as err:
            self.coordinator.async_update_listeners()
            raise HomeAssistantError(f"Nuki Opener action {action.name} failed: {err}") from err
        await self.coordinator.async_refresh_after_action()
