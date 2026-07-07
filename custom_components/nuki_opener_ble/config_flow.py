"""Config flow for the Nuki Opener BLE integration."""

from __future__ import annotations

import logging
from typing import Any

from bleak_retry_connector import close_stale_connections_by_address
from homeassistant.components import bluetooth
from homeassistant.components.bluetooth import (
    BluetoothServiceInfoBleak,
    async_discovered_service_info,
)
from homeassistant.config_entries import (
    ConfigEntryState,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import callback
from homeassistant.helpers.device_registry import format_mac
from homeassistant.helpers.selector import (
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)
import voluptuous as vol

from .const import (
    CONF_CREDENTIALS,
    CONF_LOCK_BEHAVIOR,
    CONF_SECURITY_PIN,
    DEFAULT_NAME,
    DOMAIN,
    LOCK_BEHAVIOR_AUTO,
    LOCK_BEHAVIORS,
)
from .nuki import (
    INITIALIZATION_SERVICE_UUIDS,
    PAIRING_SERVICE_UUID,
    NukiConnectionError,
    NukiError,
    NukiNotInPairingModeError,
    NukiOpenerClient,
    NukiOpenerCredentials,
    parse_manufacturer_data,
)
from .nuki.crypto import public_key_from_private

_LOGGER = logging.getLogger(__name__)

PAIRING_CLIENT_NAME = "Home Assistant"

MANUAL_SCHEMA = vol.Schema(
    {
        vol.Required("private_key"): str,
        vol.Required("device_public_key"): str,
        vol.Required("auth_id"): str,
        vol.Required("app_id"): str,
    }
)


def _is_nuki_opener(discovery_info: BluetoothServiceInfoBleak) -> bool:
    """Check whether a discovery looks like a Nuki Opener."""
    adv = parse_manufacturer_data(discovery_info.manufacturer_data)
    if adv is not None and adv.is_opener:
        return True
    service_uuids = {uuid.lower() for uuid in discovery_info.service_uuids}
    return PAIRING_SERVICE_UUID in service_uuids or bool(
        service_uuids.intersection(INITIALIZATION_SERVICE_UUIDS)
    )


def _parse_manual_credentials(user_input: dict[str, Any]) -> NukiOpenerCredentials:
    """Build credentials from manually entered values.

    Raises ValueError on malformed input.
    """
    private_key = bytes.fromhex(user_input["private_key"].strip())
    device_public_key = bytes.fromhex(user_input["device_public_key"].strip())
    auth_id = bytes.fromhex(user_input["auth_id"].strip())
    app_id = int(user_input["app_id"].strip())
    if len(private_key) != 32 or len(device_public_key) != 32 or len(auth_id) != 4:
        raise ValueError("wrong key or authorization id length")
    if not 0 <= app_id < 2**32:
        raise ValueError("app id out of range")
    return NukiOpenerCredentials(
        private_key=private_key,
        public_key=public_key_from_private(private_key),
        device_public_key=device_public_key,
        auth_id=auth_id,
        app_id=app_id,
    )


class NukiOpenerConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the config flow."""

    VERSION = 1

    def __init__(self) -> None:
        self._address: str | None = None
        self._title: str = DEFAULT_NAME

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: Any) -> NukiOpenerOptionsFlow:
        """Return the options flow."""
        return NukiOpenerOptionsFlow()

    async def async_step_bluetooth(
        self, discovery_info: BluetoothServiceInfoBleak
    ) -> ConfigFlowResult:
        """Handle a device discovered over bluetooth."""
        await self.async_set_unique_id(format_mac(discovery_info.address))
        self._abort_if_unique_id_configured()
        if not _is_nuki_opener(discovery_info):
            return self.async_abort(reason="not_supported")
        self._address = discovery_info.address
        self._title = discovery_info.name or DEFAULT_NAME
        self.context["title_placeholders"] = {
            "name": self._title,
            "address": self._address,
        }
        return await self.async_step_choose_method()

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Let the user pick a discovered Nuki Opener."""
        if user_input is not None:
            address: str = user_input[CONF_ADDRESS]
            await self.async_set_unique_id(format_mac(address), raise_on_progress=False)
            self._abort_if_unique_id_configured()
            self._address = address
            return await self.async_step_choose_method()

        current_ids = self._async_current_ids()
        devices: dict[str, str] = {}
        for info in async_discovered_service_info(self.hass, connectable=True):
            if format_mac(info.address) in current_ids or not _is_nuki_opener(info):
                continue
            devices[info.address] = f"{info.name or DEFAULT_NAME} ({info.address})"
        if not devices:
            return self.async_abort(reason="no_devices_found")
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({vol.Required(CONF_ADDRESS): vol.In(devices)}),
        )

    async def async_step_choose_method(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Choose between pairing and manual credential entry."""
        assert self._address is not None
        return self.async_show_menu(
            step_id="choose_method",
            menu_options=["pair", "manual"],
            description_placeholders={"name": self._title, "address": self._address},
        )

    async def async_step_pair(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Pair with the opener (it must be in pairing mode)."""
        assert self._address is not None
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                credentials = await self._async_pair()
            except NukiNotInPairingModeError:
                errors["base"] = "not_in_pairing_mode"
            except NukiConnectionError:
                errors["base"] = "cannot_connect"
            except NukiError:
                _LOGGER.exception("Unexpected error while pairing")
                errors["base"] = "pairing_failed"
            else:
                return self._async_create(credentials)
        return self.async_show_form(
            step_id="pair",
            data_schema=vol.Schema({}),
            errors=errors,
            description_placeholders={"name": self._title, "address": self._address},
        )

    async def async_step_manual(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Enter existing credentials (e.g. exported from nuki_hub)."""
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                credentials = _parse_manual_credentials(user_input)
            except ValueError:
                errors["base"] = "invalid_credentials"
            else:
                return self._async_create(credentials)
        return self.async_show_form(step_id="manual", data_schema=MANUAL_SCHEMA, errors=errors)

    async def _async_pair(self) -> NukiOpenerCredentials:
        assert self._address is not None
        address = self._address

        def _ble_device_getter() -> bluetooth.BLEDevice | None:
            return bluetooth.async_ble_device_from_address(self.hass, address, connectable=True)

        if _ble_device_getter() is None:
            raise NukiConnectionError("device not present")
        await close_stale_connections_by_address(address)
        client = NukiOpenerClient(_ble_device_getter)
        return await client.pair(name=PAIRING_CLIENT_NAME)

    def _async_create(self, credentials: NukiOpenerCredentials) -> ConfigFlowResult:
        assert self._address is not None
        return self.async_create_entry(
            title=self._title,
            data={
                CONF_ADDRESS: self._address,
                CONF_CREDENTIALS: credentials.to_dict(),
            },
        )


class NukiOpenerOptionsFlow(OptionsFlow):
    """Options flow: security PIN and lock entity behavior."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Manage the options."""
        errors: dict[str, str] = {}
        if user_input is not None:
            options: dict[str, Any] = {
                CONF_LOCK_BEHAVIOR: user_input[CONF_LOCK_BEHAVIOR],
            }
            pin_text = (user_input.get(CONF_SECURITY_PIN) or "").strip()
            if not pin_text:
                return self.async_create_entry(data=options)
            if not pin_text.isdigit() or not 0 <= int(pin_text) <= 0xFFFF:
                errors[CONF_SECURITY_PIN] = "invalid_pin"
            else:
                pin = int(pin_text)
                error = await self._async_verify_pin(pin)
                if error is None:
                    return self.async_create_entry(data={**options, CONF_SECURITY_PIN: pin})
                errors["base" if error == "cannot_connect" else CONF_SECURITY_PIN] = error
        current_pin = self.config_entry.options.get(CONF_SECURITY_PIN)
        current_behavior = self.config_entry.options.get(CONF_LOCK_BEHAVIOR, LOCK_BEHAVIOR_AUTO)
        schema = vol.Schema(
            {
                vol.Optional(
                    CONF_SECURITY_PIN,
                    description={
                        "suggested_value": "" if current_pin is None else str(current_pin)
                    },
                ): str,
                vol.Required(CONF_LOCK_BEHAVIOR, default=current_behavior): SelectSelector(
                    SelectSelectorConfig(
                        options=LOCK_BEHAVIORS,
                        mode=SelectSelectorMode.DROPDOWN,
                        translation_key=CONF_LOCK_BEHAVIOR,
                    )
                ),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema, errors=errors)

    async def _async_verify_pin(self, pin: int) -> str | None:
        """Check the PIN against the device; returns an error key or None."""
        if self.config_entry.state is not ConfigEntryState.LOADED:
            return None  # cannot verify now; accept and verify on first use
        client = self.config_entry.runtime_data.device.client
        try:
            if not await client.verify_security_pin(pin):
                return "invalid_pin"
        except NukiError:
            return "cannot_connect"
        return None
