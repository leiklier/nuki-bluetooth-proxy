"""Async BLE client for the Nuki Opener.

Works with any bleak backend, including Home Assistant's ESPHome Bluetooth
proxies. Connections are opened on demand and closed after a short idle
period: while connected, the Opener stops advertising, which would break
state-change detection (and drain its batteries).
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
import contextlib
from dataclasses import dataclass, field
import logging
from typing import Any

from bleak import BleakClient
from bleak.backends.characteristic import BleakGATTCharacteristic
from bleak.backends.device import BLEDevice
from bleak.exc import BleakError
from bleak_retry_connector import BleakClientWithServiceCache, establish_connection

from . import messages, protocol
from .const import (
    OPENER_SERVICE_UUID,
    PAIRING_GDIO_UUID,
    PAIRING_SERVICE_UUID,
    USDIO_UUID,
    ClientType,
    Command,
    ErrorCode,
    LockAction,
    StatusCode,
)
from .crypto import (
    CHALLENGE_NONCE_SIZE,
    derive_shared_key,
    generate_keypair,
    random_bytes,
)
from .errors import (
    NukiBadPinError,
    NukiConnectionError,
    NukiDeviceError,
    NukiNotInPairingModeError,
    NukiPairingError,
    NukiProtocolError,
    NukiResponseTimeoutError,
)
from .messages import (
    AdvancedConfig,
    AuthorizationId,
    BatteryReport,
    LogEntry,
    OpenerConfig,
    OpenerState,
)

_LOGGER = logging.getLogger(__name__)

RESPONSE_TIMEOUT = 15.0
PAIRING_TIMEOUT = 30.0
DISCONNECT_DELAY = 3.0
REQUEST_ATTEMPTS = 3
RETRY_DELAY = 0.5


@dataclass(frozen=True)
class NukiOpenerCredentials:
    """Everything needed to talk to a paired Opener."""

    private_key: bytes
    public_key: bytes
    device_public_key: bytes
    auth_id: bytes
    app_id: int
    client_type: ClientType = ClientType.BRIDGE

    @property
    def shared_key(self) -> bytes:
        return derive_shared_key(self.private_key, self.device_public_key)

    def to_dict(self) -> dict[str, Any]:
        """Serialize for config entry storage."""
        return {
            "private_key": self.private_key.hex(),
            "public_key": self.public_key.hex(),
            "device_public_key": self.device_public_key.hex(),
            "auth_id": self.auth_id.hex(),
            "app_id": self.app_id,
            "client_type": int(self.client_type),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> NukiOpenerCredentials:
        return cls(
            private_key=bytes.fromhex(data["private_key"]),
            public_key=bytes.fromhex(data["public_key"]),
            device_public_key=bytes.fromhex(data["device_public_key"]),
            auth_id=bytes.fromhex(data["auth_id"]),
            app_id=int(data["app_id"]),
            client_type=ClientType(data.get("client_type", ClientType.BRIDGE)),
        )


@dataclass
class _PendingCommand:
    """Bookkeeping for a command awaiting its response."""

    expected: Command
    future: asyncio.Future[tuple[Command, bytes]]
    aggregate: tuple[Command, ...] = ()
    aggregated: list[bytes] = field(default_factory=list)


class NukiOpenerClient:
    """BLE client implementing the Nuki Opener protocol."""

    def __init__(
        self,
        ble_device_getter: Callable[[], BLEDevice | None],
        credentials: NukiOpenerCredentials | None = None,
        disconnect_delay: float = DISCONNECT_DELAY,
        response_timeout: float = RESPONSE_TIMEOUT,
    ) -> None:
        self._ble_device_getter = ble_device_getter
        self.credentials = credentials
        self._disconnect_delay = disconnect_delay
        self._response_timeout = response_timeout

        self._client: BleakClient | None = None
        self._connect_lock = asyncio.Lock()
        self._operation_lock = asyncio.Lock()
        self._pending: _PendingCommand | None = None
        # Armed by lock_action before sending: buffers the action's status
        # notifications (several can arrive in one event-loop tick), an error
        # report, or None when the link drops. One queue per action, so a
        # stale status can never be taken for a later action's completion.
        self._completion: asyncio.Queue[bytes | NukiDeviceError | None] | None = None
        self._plain_reassembler = protocol.MessageReassembler(encrypted=False)
        self._encrypted_reassembler = protocol.MessageReassembler(encrypted=True)
        self._disconnect_timer: asyncio.TimerHandle | None = None
        self._disconnect_task: asyncio.Future[None] | None = None
        self._expected_disconnect = False
        self.state_callback: Callable[[OpenerState], None] | None = None

    @property
    def is_connected(self) -> bool:
        return self._client is not None and self._client.is_connected

    async def connect(self) -> None:
        """Connect and subscribe to indications."""
        async with self._connect_lock:
            self._cancel_disconnect_timer()
            if self.is_connected:
                return
            ble_device = self._ble_device_getter()
            if ble_device is None:
                raise NukiConnectionError("no BLE device available (device not seen recently)")
            self._plain_reassembler.reset()
            self._encrypted_reassembler.reset()
            self._expected_disconnect = False

            def _fresh_ble_device() -> BLEDevice:
                return self._ble_device_getter() or ble_device

            try:
                self._client = await establish_connection(
                    BleakClientWithServiceCache,
                    ble_device,
                    f"Nuki Opener {ble_device.address}",
                    disconnected_callback=self._on_disconnect,
                    ble_device_callback=_fresh_ble_device,
                )
            except (BleakError, TimeoutError) as err:
                raise NukiConnectionError(f"failed to connect: {err}") from err
            try:
                services = self._client.services
                if services.get_characteristic(PAIRING_GDIO_UUID):
                    await self._client.start_notify(PAIRING_GDIO_UUID, self._on_notification)
                if self.credentials and services.get_characteristic(USDIO_UUID):
                    await self._client.start_notify(USDIO_UUID, self._on_notification)
            except (BleakError, TimeoutError) as err:
                await self.disconnect()
                raise NukiConnectionError(f"failed to subscribe: {err}") from err
            _LOGGER.debug("Connected to %s", ble_device.address)

    async def disconnect(self) -> None:
        """Disconnect immediately."""
        self._cancel_disconnect_timer()
        client, self._client = self._client, None
        self._expected_disconnect = True
        if client is not None and client.is_connected:
            with contextlib.suppress(BleakError, TimeoutError):
                await client.disconnect()

    def _on_disconnect(self, _client: BleakClient) -> None:
        if not self._expected_disconnect:
            _LOGGER.debug("Unexpected disconnect from Nuki Opener")
        if self._pending is not None and not self._pending.future.done():
            self._pending.future.set_exception(
                NukiConnectionError("disconnected while waiting for response")
            )
        if self._completion is not None:
            self._completion.put_nowait(None)

    def _schedule_disconnect(self) -> None:
        self._cancel_disconnect_timer()
        if not self.is_connected:
            return
        loop = asyncio.get_running_loop()
        self._disconnect_timer = loop.call_later(self._disconnect_delay, self._idle_disconnect)

    def _idle_disconnect(self) -> None:
        # Never tear down a connection an operation has started using in the
        # meantime; that operation will schedule a fresh timer when it ends.
        if self._operation_lock.locked() or (
            self._pending is not None and not self._pending.future.done()
        ):
            return
        self._disconnect_task = asyncio.ensure_future(self.disconnect())

    def _cancel_disconnect_timer(self) -> None:
        if self._disconnect_timer is not None:
            self._disconnect_timer.cancel()
            self._disconnect_timer = None

    # --- receiving ---------------------------------------------------------

    def _on_notification(self, sender: BleakGATTCharacteristic, data: bytearray) -> None:
        try:
            if sender.uuid == PAIRING_GDIO_UUID:
                raw_messages = self._plain_reassembler.feed(bytes(data))
                parsed = [protocol.decode_plain(raw) for raw in raw_messages]
            else:
                raw_messages = self._encrypted_reassembler.feed(bytes(data))
                if self.credentials is None:
                    return
                key = self.credentials.shared_key
                parsed = [protocol.decode_encrypted(raw, key)[1:] for raw in raw_messages]
        except NukiProtocolError as err:
            _LOGGER.warning("Dropping undecodable notification: %s", err)
            if self._pending is not None and not self._pending.future.done():
                self._pending.future.set_exception(err)
            return
        for command, payload in parsed:
            self._handle_message(command, payload)

    def _handle_message(self, command: Command, payload: bytes) -> None:
        _LOGGER.debug("Received %s (%d bytes)", command.name, len(payload))
        pending = self._pending
        if command == Command.ERROR_REPORT:
            report = messages.ErrorReport.parse(payload)
            error = NukiDeviceError(report.error_code, report.command)
            if pending is not None and not pending.future.done():
                pending.future.set_exception(error)
            elif self._completion is not None:
                # The accepted action failed while executing.
                self._completion.put_nowait(error)
            else:
                _LOGGER.warning("Unsolicited error report: %s", error)
            return
        if pending is not None and not pending.future.done():
            if command == pending.expected:
                pending.future.set_result((command, payload))
                return
            if command in pending.aggregate:
                pending.aggregated.append(payload)
                return
        if command == Command.OPENER_STATES:
            try:
                state = OpenerState.parse(payload)
            except NukiProtocolError as err:
                _LOGGER.warning("Failed to parse unsolicited opener state: %s", err)
                return
            if self.state_callback is not None:
                self.state_callback(state)
            return
        if command == Command.STATUS:
            # Completion of an ACCEPTED action; consumed by _wait_for_completion.
            # A status with no armed waiter is stale (e.g. the late completion
            # of an action whose wait already ended) and must not leak into a
            # later action.
            if self._completion is not None:
                if self._completion.qsize() < 4:
                    self._completion.put_nowait(payload)
            else:
                _LOGGER.debug("Dropping stale status notification")
            return
        _LOGGER.debug("Ignoring unsolicited %s", command.name)

    # --- sending -----------------------------------------------------------

    async def _write(self, characteristic: str, message: bytes) -> None:
        await self.connect()
        assert self._client is not None
        try:
            await self._client.write_gatt_char(characteristic, message, response=True)
        except (BleakError, TimeoutError) as err:
            raise NukiConnectionError(f"failed to write: {err}") from err

    async def _request(
        self,
        characteristic: str,
        message: bytes,
        expected: Command,
        aggregate: tuple[Command, ...] = (),
        response_timeout: float | None = None,
    ) -> tuple[bytes, list[bytes]]:
        """Send a message and wait for the expected response command.

        Retries when the BLE link drops mid-request (common over Bluetooth
        proxies); replaying is safe because Nuki's challenge nonces are
        single-use, so a command the device already executed is rejected
        rather than executed twice. Returns ``(payload, aggregated_payloads)``.
        """
        last_error: NukiConnectionError | None = None
        for attempt in range(REQUEST_ATTEMPTS):
            if attempt:
                _LOGGER.debug(
                    "Retrying %s request (attempt %d/%d)",
                    expected.name,
                    attempt + 1,
                    REQUEST_ATTEMPTS,
                )
                await self.disconnect()
                await asyncio.sleep(RETRY_DELAY)
            try:
                return await self._request_once(
                    characteristic, message, expected, aggregate, response_timeout
                )
            except NukiConnectionError as err:
                last_error = err
        assert last_error is not None
        raise last_error

    async def _request_once(
        self,
        characteristic: str,
        message: bytes,
        expected: Command,
        aggregate: tuple[Command, ...] = (),
        response_timeout: float | None = None,
    ) -> tuple[bytes, list[bytes]]:
        if self._pending is not None and not self._pending.future.done():
            raise NukiProtocolError("another command is already in flight")
        pending = _PendingCommand(
            expected=expected,
            future=asyncio.get_running_loop().create_future(),
            aggregate=aggregate,
        )
        self._pending = pending
        try:
            await self._write(characteristic, message)
            async with asyncio.timeout(response_timeout or self._response_timeout):
                _, payload = await pending.future
        except TimeoutError as err:
            raise NukiResponseTimeoutError(f"no {expected.name} response within timeout") from err
        finally:
            self._pending = None
        return payload, pending.aggregated

    async def _request_plain(
        self,
        command: Command,
        payload: bytes,
        expected: Command,
        response_timeout: float | None = None,
    ) -> bytes:
        message = protocol.encode_plain(command, payload)
        response, _ = await self._request(
            PAIRING_GDIO_UUID, message, expected, response_timeout=response_timeout
        )
        return response

    async def _request_encrypted(
        self,
        command: Command,
        payload: bytes,
        expected: Command,
        aggregate: tuple[Command, ...] = (),
    ) -> tuple[bytes, list[bytes]]:
        if self.credentials is None:
            raise NukiProtocolError("not paired: no credentials available")
        message = protocol.encode_encrypted(
            self.credentials.auth_id, command, payload, self.credentials.shared_key
        )
        return await self._request(USDIO_UUID, message, expected, aggregate)

    async def _request_challenge(self) -> bytes:
        payload, _ = await self._request_encrypted(
            Command.REQUEST_DATA,
            messages.build_request_data(Command.CHALLENGE),
            expected=Command.CHALLENGE,
        )
        return messages.parse_challenge(payload)

    # --- public API --------------------------------------------------------

    async def get_state(self) -> OpenerState:
        """Read the current opener state."""
        async with self._operation_lock:
            try:
                payload, _ = await self._request_encrypted(
                    Command.REQUEST_DATA,
                    messages.build_request_data(Command.OPENER_STATES),
                    expected=Command.OPENER_STATES,
                )
                return OpenerState.parse(payload)
            finally:
                self._schedule_disconnect()

    async def get_config(self) -> OpenerConfig:
        """Read the opener configuration."""
        async with self._operation_lock:
            try:
                challenge = await self._request_challenge()
                payload, _ = await self._request_encrypted(
                    Command.REQUEST_CONFIG,
                    messages.build_request_config(challenge),
                    expected=Command.CONFIG,
                )
                return OpenerConfig.parse(payload)
            finally:
                self._schedule_disconnect()

    async def get_advanced_config(self) -> AdvancedConfig:
        """Read the opener's advanced configuration."""
        async with self._operation_lock:
            try:
                challenge = await self._request_challenge()
                payload, _ = await self._request_encrypted(
                    Command.REQUEST_ADVANCED_CONFIG,
                    messages.build_request_advanced_config(challenge),
                    expected=Command.ADVANCED_CONFIG,
                )
                return AdvancedConfig.parse(payload)
            finally:
                self._schedule_disconnect()

    async def set_advanced_config(self, config: AdvancedConfig, pin: int) -> None:
        """Write the advanced configuration (requires the security PIN)."""
        async with self._operation_lock:
            try:
                challenge = await self._request_challenge()
                payload, _ = await self._request_encrypted(
                    Command.SET_ADVANCED_CONFIG,
                    messages.build_set_advanced_config(config, challenge, pin),
                    expected=Command.STATUS,
                )
                if messages.parse_status(payload) != StatusCode.COMPLETE:
                    raise NukiProtocolError("device did not confirm the configuration")
            except NukiDeviceError as err:
                if err.error_code == ErrorCode.K_BAD_PIN:
                    raise NukiBadPinError("security PIN rejected") from err
                raise
            finally:
                self._schedule_disconnect()

    async def get_battery_report(self) -> BatteryReport:
        """Read the battery report."""
        async with self._operation_lock:
            try:
                payload, _ = await self._request_encrypted(
                    Command.REQUEST_DATA,
                    messages.build_request_data(Command.BATTERY_REPORT),
                    expected=Command.BATTERY_REPORT,
                )
                return BatteryReport.parse(payload)
            finally:
                self._schedule_disconnect()

    async def lock_action(
        self,
        action: LockAction,
        name_suffix: str | None = None,
        wait_for_completion: bool = True,
    ) -> StatusCode:
        """Execute a lock action (RTO, continuous mode, electric strike)."""
        if self.credentials is None:
            raise NukiProtocolError("not paired: no credentials available")
        async with self._operation_lock:
            try:
                challenge = await self._request_challenge()
                # Armed before sending so a completion that arrives while the
                # ACCEPTED response is still being processed is not lost.
                completion: asyncio.Queue[bytes | NukiDeviceError | None] = asyncio.Queue()
                self._completion = completion
                try:
                    payload, _ = await self._request_encrypted(
                        Command.LOCK_ACTION,
                        messages.build_lock_action(
                            action, self.credentials.app_id, challenge, name_suffix=name_suffix
                        ),
                        expected=Command.STATUS,
                    )
                    status = messages.parse_status(payload)
                    if status == StatusCode.ACCEPTED and wait_for_completion:
                        status = await self._wait_for_completion(completion)
                    return status
                finally:
                    self._completion = None
            finally:
                self._schedule_disconnect()

    async def _wait_for_completion(
        self, completion: asyncio.Queue[bytes | NukiDeviceError | None]
    ) -> StatusCode:
        """Wait for the final status of an accepted action.

        Returns ACCEPTED when the link drops or nothing arrives in time: the
        action was already accepted and is executing on the device, so
        retrying would be wrong and waiting longer pointless.
        """
        try:
            async with asyncio.timeout(self._response_timeout):
                while True:
                    item = await completion.get()
                    if item is None:
                        _LOGGER.debug(
                            "Link lost while waiting for action completion; assuming accepted"
                        )
                        return StatusCode.ACCEPTED
                    if isinstance(item, NukiDeviceError):
                        raise item
                    status = messages.parse_status(item)
                    if status == StatusCode.COMPLETE:
                        return status
                    _LOGGER.debug(
                        "Ignoring interim status %s while waiting for completion", status.name
                    )
        except TimeoutError:
            _LOGGER.warning("Timed out waiting for action completion; assuming accepted")
            return StatusCode.ACCEPTED

    async def verify_security_pin(self, pin: int) -> bool:
        """Check a security PIN against the device."""
        async with self._operation_lock:
            try:
                challenge = await self._request_challenge()
                payload, _ = await self._request_encrypted(
                    Command.VERIFY_SECURITY_PIN,
                    messages.build_verify_security_pin(challenge, pin),
                    expected=Command.STATUS,
                )
                return messages.parse_status(payload) == StatusCode.COMPLETE
            except NukiDeviceError as err:
                if err.error_code == ErrorCode.K_BAD_PIN:
                    return False
                raise
            finally:
                self._schedule_disconnect()

    async def get_log_entries(
        self, pin: int, count: int = 1, start_index: int = 0
    ) -> list[LogEntry]:
        """Read the most recent log entries (requires the security PIN)."""
        async with self._operation_lock:
            try:
                challenge = await self._request_challenge()
                _, aggregated = await self._request_encrypted(
                    Command.REQUEST_LOG_ENTRIES,
                    messages.build_request_log_entries(
                        challenge, pin, start_index=start_index, count=count
                    ),
                    expected=Command.STATUS,
                    aggregate=(Command.LOG_ENTRY,),
                )
            except NukiDeviceError as err:
                if err.error_code == ErrorCode.K_BAD_PIN:
                    raise NukiBadPinError("security PIN rejected") from err
                raise
            finally:
                self._schedule_disconnect()
        return [LogEntry.parse(payload) for payload in aggregated]

    async def pair(
        self,
        app_id: int | None = None,
        name: str = "Home Assistant",
        client_type: ClientType = ClientType.BRIDGE,
    ) -> NukiOpenerCredentials:
        """Pair with an Opener that is in pairing mode.

        The user must have put the Opener into pairing mode first (hold its
        button for 5 seconds until the LED ring lights up).
        """
        if app_id is None:
            app_id = int.from_bytes(random_bytes(4), "little")
        private_key, public_key = generate_keypair()
        async with self._operation_lock:
            try:
                return await self._pair(private_key, public_key, app_id, name, client_type)
            except NukiDeviceError as err:
                if err.error_code == ErrorCode.P_NOT_PAIRING:
                    raise NukiNotInPairingModeError("the Opener is not in pairing mode") from err
                raise NukiPairingError(f"pairing failed: {err}") from err
            finally:
                await self.disconnect()

    async def _pair(
        self,
        private_key: bytes,
        public_key: bytes,
        app_id: int,
        name: str,
        client_type: ClientType,
    ) -> NukiOpenerCredentials:
        await self.connect()
        assert self._client is not None
        if not self._client.services.get_characteristic(PAIRING_GDIO_UUID):
            raise NukiPairingError("device does not expose the Opener pairing service")

        response = await self._request_plain(
            Command.REQUEST_DATA,
            messages.build_request_data(Command.PUBLIC_KEY),
            expected=Command.PUBLIC_KEY,
            response_timeout=PAIRING_TIMEOUT,
        )
        device_public_key = messages.parse_public_key(response)
        shared_key = derive_shared_key(private_key, device_public_key)

        response = await self._request_plain(
            Command.PUBLIC_KEY,
            messages.build_public_key(public_key),
            expected=Command.CHALLENGE,
            response_timeout=PAIRING_TIMEOUT,
        )
        challenge = messages.parse_challenge(response)

        response = await self._request_plain(
            Command.AUTHORIZATION_AUTHENTICATOR,
            messages.build_authorization_authenticator(
                shared_key, public_key, device_public_key, challenge
            ),
            expected=Command.CHALLENGE,
            response_timeout=PAIRING_TIMEOUT,
        )
        challenge = messages.parse_challenge(response)

        client_nonce = random_bytes(CHALLENGE_NONCE_SIZE)
        response = await self._request_plain(
            Command.AUTHORIZATION_DATA,
            messages.build_authorization_data(
                shared_key, client_type, app_id, name, client_nonce, challenge
            ),
            expected=Command.AUTHORIZATION_ID,
            response_timeout=PAIRING_TIMEOUT,
        )
        authorization = AuthorizationId.parse(response)
        if not authorization.verify(shared_key, client_nonce):
            raise NukiPairingError("device sent an invalid authenticator")

        response = await self._request_plain(
            Command.AUTHORIZATION_ID_CONFIRMATION,
            messages.build_authorization_id_confirmation(
                shared_key, authorization.auth_id, authorization.nonce
            ),
            expected=Command.STATUS,
            response_timeout=PAIRING_TIMEOUT,
        )
        if messages.parse_status(response) != StatusCode.COMPLETE:
            raise NukiPairingError("device did not confirm the authorization")

        return NukiOpenerCredentials(
            private_key=private_key,
            public_key=public_key,
            device_public_key=device_public_key,
            auth_id=authorization.auth_id,
            app_id=app_id,
            client_type=client_type,
        )


def is_opener_device(client: BleakClient) -> bool:
    """Check whether a connected BLE device exposes the Opener services."""
    services = client.services
    return bool(
        services.get_characteristic(USDIO_UUID)
        or services.get_characteristic(PAIRING_GDIO_UUID)
        or services.get_service(OPENER_SERVICE_UUID)
        or services.get_service(PAIRING_SERVICE_UUID)
    )
