"""A device-side simulation of a Nuki Opener for tests.

``FakeOpener`` implements the peripheral side of the Nuki BLE protocol on top
of the same framing primitives used by the client (whose wire format is
independently verified against the official spec vectors in
``test_protocol.py``). ``FakeBleakClient`` exposes it through the bleak API,
delivering indications split into 20-byte chunks like real hardware with a
small MTU.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import struct
from typing import Any

from custom_components.nuki_opener_ble.nuki import crypto, messages, protocol
from custom_components.nuki_opener_ble.nuki.const import (
    PAIRING_GDIO_UUID,
    USDIO_UUID,
    ClientType,
    Command,
    ErrorCode,
    LockAction,
    LockState,
    LogEntryType,
    NukiState,
    StatusCode,
    Trigger,
)

DEFAULT_ADDRESS = "AA:BB:CC:DD:EE:FF"


@dataclass
class FakeOpenerState:
    nuki_state: NukiState = NukiState.DOOR_MODE
    lock_state: LockState = LockState.LOCKED
    trigger: Trigger = Trigger.SYSTEM
    battery_critical: bool = False
    config_update_count: int = 7
    ring_to_open_timer: int = 0
    last_lock_action: LockAction = LockAction.DEACTIVATE_RTO
    last_trigger: Trigger = Trigger.SYSTEM
    last_completion_status: int = 0
    door_sensor_state: int = 0


@dataclass
class FakeLogEntry:
    index: int
    type: LogEntryType
    data: bytes = b""
    name: str = "Fake"
    timestamp: tuple[int, int, int, int, int, int] = (2026, 7, 4, 12, 0, 0)


class FakeOpener:
    """Protocol state machine for the peripheral side."""

    def __init__(self, security_pin: int = 1234) -> None:
        self.private_key, self.public_key = crypto.generate_keypair()
        self.uuid = crypto.random_bytes(16)
        self.auth_id = bytes.fromhex("02000000")
        self.security_pin = security_pin
        self.pairing_mode = True
        self.state = FakeOpenerState()
        self.shared_key: bytes | None = None
        self.challenge: bytes | None = None
        self.client_public_key: bytes | None = None
        self.paired_name: str | None = None
        self.paired_client_type: ClientType | None = None
        self.received_lock_actions: list[LockAction] = []
        self.log_entries: list[FakeLogEntry] = []
        # Response shaping for lock actions, mimicking real-hardware quirks.
        self.omit_lock_completion = False
        self.omit_lock_state_update = False
        self.duplicate_lock_accepted = False
        self.config_name = "Front Door"
        self.capabilities = 0x01  # door opening and ring-to-open
        self.firmware_version = (1, 8, 0)
        self.hardware_revision = (5, 2)
        self.nuki_id = 0x11223344
        self.advanced_config = messages.AdvancedConfig(
            intercom_id=42,
            bus_mode_switch=0,
            short_circuit_duration_ms=0,
            electric_strike_delay_ms=0,
            random_electric_strike_delay=False,
            electric_strike_duration_ms=3000,
            disable_rto_after_ring=False,
            rto_timeout_minutes=20,
            doorbell_suppression=0x00,
            doorbell_suppression_duration_ms=500,
            sound_ring=1,
            sound_open=1,
            sound_rto=1,
            sound_cm=1,
            sound_confirmation=1,
            sound_level=80,
            single_button_press_action=1,
            double_button_press_action=4,
            battery_type=0,
            automatic_battery_type_detection=True,
        )

    # --- helpers ------------------------------------------------------------

    def install_credentials(self, credentials: Any) -> None:
        """Mark the device as paired with the given client credentials."""
        self.shared_key = crypto.derive_shared_key(self.private_key, credentials.public_key)
        self.pairing_mode = False

    def add_doorbell_log_entry(
        self,
        suppressed: bool = False,
        timestamp: tuple[int, int, int, int, int, int] = (2026, 7, 4, 12, 0, 0),
    ) -> None:
        index = (self.log_entries[-1].index + 1) if self.log_entries else 1
        data = bytes([0x00, 0x00, 0x00, 0x01 if suppressed else 0x00, 0x01, 0x00]) + struct.pack(
            "<H", 0
        )
        self.log_entries.append(
            FakeLogEntry(
                index=index,
                type=LogEntryType.DOORBELL_RECOGNITION,
                data=data,
                timestamp=timestamp,
            )
        )

    def add_lock_action_log_entry(
        self,
        action: int = 1,
        timestamp: tuple[int, int, int, int, int, int] = (2026, 7, 4, 12, 0, 0),
    ) -> None:
        index = (self.log_entries[-1].index + 1) if self.log_entries else 1
        self.log_entries.append(
            FakeLogEntry(
                index=index,
                type=LogEntryType.LOCK_ACTION,
                data=bytes([action, 0, 0, 0]),
                timestamp=timestamp,
            )
        )

    def state_payload(self) -> bytes:
        state = self.state
        return (
            bytes([state.nuki_state, state.lock_state, state.trigger])
            + struct.pack("<HBBBBB", 2026, 7, 4, 12, 0, 0)
            + struct.pack("<h", 120)
            + bytes(
                [
                    0x01 if state.battery_critical else 0x00,
                    state.config_update_count,
                    state.ring_to_open_timer,
                    state.last_lock_action,
                    state.last_trigger,
                    state.last_completion_status,
                    state.door_sensor_state,
                ]
            )
        )

    def config_payload(self) -> bytes:
        return (
            struct.pack("<I", self.nuki_id)
            + messages.encode_name(self.config_name)
            + struct.pack("<ff", 59.91, 10.75)
            + bytes([self.capabilities, 0x00, 0x01, 0x01])  # capabilities, pairing, button, led
            + struct.pack("<HBBBBB", 2026, 7, 4, 12, 0, 0)
            + struct.pack("<h", 120)
            + bytes([0x01, 0x00, 0x07, 0x07, 0x07, 0x02, 0x00, 0x00])
            + bytes(self.firmware_version)
            + bytes(self.hardware_revision)
            + struct.pack("<H", 37)  # Europe/Berlin
        )

    def battery_payload(self) -> bytes:
        return struct.pack("<HHBBHHHbHH", 84, 5450, 0, 0, 5500, 5300, 0, 21, 150, 300)

    def log_entry_payload(self, entry: FakeLogEntry) -> bytes:
        return (
            struct.pack("<I", entry.index)
            + struct.pack("<HBBBBB", *entry.timestamp)
            + self.auth_id
            + messages.encode_name(entry.name)
            + bytes([entry.type])
            + entry.data
        )

    # --- pairing characteristic ---------------------------------------------

    def handle_pairing_write(self, data: bytes) -> list[bytes]:
        command, payload = protocol.decode_plain(data)

        def error(code: ErrorCode) -> list[bytes]:
            return [
                protocol.encode_plain(
                    Command.ERROR_REPORT, bytes([code]) + struct.pack("<H", command)
                )
            ]

        if command == Command.REQUEST_DATA:
            if int.from_bytes(payload[0:2], "little") != Command.PUBLIC_KEY:
                return error(ErrorCode.P_BAD_PARAMETER)
            if not self.pairing_mode:
                return error(ErrorCode.P_NOT_PAIRING)
            return [protocol.encode_plain(Command.PUBLIC_KEY, self.public_key)]

        if command == Command.PUBLIC_KEY:
            self.client_public_key = payload
            self.shared_key = crypto.derive_shared_key(self.private_key, payload)
            self.challenge = crypto.random_bytes(32)
            return [protocol.encode_plain(Command.CHALLENGE, self.challenge)]

        assert self.shared_key is not None and self.challenge is not None

        if command == Command.AUTHORIZATION_AUTHENTICATOR:
            assert self.client_public_key is not None
            expected = crypto.hmac_sha256(
                self.shared_key, self.client_public_key + self.public_key + self.challenge
            )
            if payload != expected:
                return error(ErrorCode.P_BAD_AUTHENTICATOR)
            self.challenge = crypto.random_bytes(32)
            return [protocol.encode_plain(Command.CHALLENGE, self.challenge)]

        if command == Command.AUTHORIZATION_DATA:
            authenticator, inner = payload[0:32], payload[32:]
            if crypto.hmac_sha256(self.shared_key, inner + self.challenge) != authenticator:
                return error(ErrorCode.P_BAD_AUTHENTICATOR)
            self.paired_client_type = ClientType(inner[0])
            self.paired_name = inner[5:37].rstrip(b"\x00").decode()
            client_nonce = inner[37:69]
            self.challenge = crypto.random_bytes(32)
            response_authenticator = crypto.hmac_sha256(
                self.shared_key, self.auth_id + self.uuid + self.challenge + client_nonce
            )
            return [
                protocol.encode_plain(
                    Command.AUTHORIZATION_ID,
                    response_authenticator + self.auth_id + self.uuid + self.challenge,
                )
            ]

        if command == Command.AUTHORIZATION_ID_CONFIRMATION:
            authenticator, auth_id = payload[0:32], payload[32:36]
            expected = crypto.hmac_sha256(self.shared_key, auth_id + self.challenge)
            if authenticator != expected or auth_id != self.auth_id:
                return error(ErrorCode.P_BAD_AUTHENTICATOR)
            self.pairing_mode = False
            return [protocol.encode_plain(Command.STATUS, bytes([StatusCode.COMPLETE]))]

        return error(ErrorCode.UNKNOWN)

    # --- USDIO characteristic -----------------------------------------------

    def handle_usdio_write(self, data: bytes) -> list[bytes]:
        assert self.shared_key is not None, "device is not paired"
        _, command, payload = protocol.decode_encrypted(data, self.shared_key)

        def encrypted(command: Command, payload: bytes) -> bytes:
            assert self.shared_key is not None
            return protocol.encode_encrypted(self.auth_id, command, payload, self.shared_key)

        def error(code: ErrorCode) -> list[bytes]:
            return [encrypted(Command.ERROR_REPORT, bytes([code]) + struct.pack("<H", command))]

        if command == Command.REQUEST_DATA:
            target = Command(int.from_bytes(payload[0:2], "little"))
            if target == Command.OPENER_STATES:
                return [encrypted(Command.OPENER_STATES, self.state_payload())]
            if target == Command.CHALLENGE:
                self.challenge = crypto.random_bytes(32)
                return [encrypted(Command.CHALLENGE, self.challenge)]
            if target == Command.BATTERY_REPORT:
                return [encrypted(Command.BATTERY_REPORT, self.battery_payload())]
            return error(ErrorCode.K_BAD_PARAMETER)

        if command == Command.LOCK_ACTION:
            if payload[-32:] != self.challenge:
                return error(ErrorCode.K_BAD_NONCE)
            action = LockAction(payload[0])
            self.received_lock_actions.append(action)
            self._apply_lock_action(action)
            responses = [encrypted(Command.STATUS, bytes([StatusCode.ACCEPTED]))]
            if self.duplicate_lock_accepted:
                responses.append(encrypted(Command.STATUS, bytes([StatusCode.ACCEPTED])))
            if not self.omit_lock_state_update:
                responses.append(encrypted(Command.OPENER_STATES, self.state_payload()))
            if not self.omit_lock_completion:
                responses.append(encrypted(Command.STATUS, bytes([StatusCode.COMPLETE])))
            return responses

        if command == Command.REQUEST_CONFIG:
            if payload[0:32] != self.challenge:
                return error(ErrorCode.K_BAD_NONCE)
            return [encrypted(Command.CONFIG, self.config_payload())]

        if command == Command.REQUEST_ADVANCED_CONFIG:
            if payload[0:32] != self.challenge:
                return error(ErrorCode.K_BAD_NONCE)
            return [encrypted(Command.ADVANCED_CONFIG, self.advanced_config.serialize())]

        if command == Command.SET_ADVANCED_CONFIG:
            size = messages.AdvancedConfig._SIZE
            if payload[size : size + 32] != self.challenge:
                return error(ErrorCode.K_BAD_NONCE)
            (pin,) = struct.unpack("<H", payload[size + 32 : size + 34])
            if pin != self.security_pin:
                return error(ErrorCode.K_BAD_PIN)
            self.advanced_config = messages.AdvancedConfig.parse(payload[:size])
            self.state.config_update_count = (self.state.config_update_count + 1) % 256
            return [encrypted(Command.STATUS, bytes([StatusCode.COMPLETE]))]

        if command == Command.VERIFY_SECURITY_PIN:
            if payload[0:32] != self.challenge:
                return error(ErrorCode.K_BAD_NONCE)
            (pin,) = struct.unpack("<H", payload[32:34])
            if pin != self.security_pin:
                return error(ErrorCode.K_BAD_PIN)
            return [encrypted(Command.STATUS, bytes([StatusCode.COMPLETE]))]

        if command == Command.REQUEST_LOG_ENTRIES:
            start_index, count, _sort_order, _total = struct.unpack_from("<IHBB", payload)
            if payload[8:40] != self.challenge:
                return error(ErrorCode.K_BAD_NONCE)
            (pin,) = struct.unpack("<H", payload[40:42])
            if pin != self.security_pin:
                return error(ErrorCode.K_BAD_PIN)
            entries = sorted(self.log_entries, key=lambda entry: entry.index, reverse=True)
            if start_index:
                entries = [entry for entry in entries if entry.index < start_index]
            responses = [
                encrypted(Command.LOG_ENTRY, self.log_entry_payload(entry))
                for entry in entries[:count]
            ]
            responses.append(encrypted(Command.STATUS, bytes([StatusCode.COMPLETE])))
            return responses

        return error(ErrorCode.K_BAD_PARAMETER)

    def _apply_lock_action(self, action: LockAction) -> None:
        state = self.state
        state.trigger = Trigger.SYSTEM
        state.last_lock_action = action
        if action == LockAction.ACTIVATE_RTO:
            state.lock_state = LockState.RTO_ACTIVE
        elif action == LockAction.DEACTIVATE_RTO:
            state.lock_state = LockState.LOCKED
        elif action == LockAction.ELECTRIC_STRIKE_ACTUATION:
            state.lock_state = LockState.OPEN
        elif action == LockAction.ACTIVATE_CM:
            state.nuki_state = NukiState.CONTINUOUS_MODE
        elif action == LockAction.DEACTIVATE_CM:
            state.nuki_state = NukiState.DOOR_MODE

    # --- doorbell simulation --------------------------------------------------

    def simulate_plain_ring(self) -> None:
        """A ring while idle: nothing changes, only the advertisement flag."""
        self.add_doorbell_log_entry(suppressed=False)

    def simulate_ring_during_rto(self) -> None:
        """A ring while RTO is active: the strike fires with a manual trigger."""
        self.state.lock_state = LockState.OPEN
        self.state.trigger = Trigger.MANUAL
        self.add_doorbell_log_entry(suppressed=False)


@dataclass
class _FakeCharacteristic:
    uuid: str


class _FakeServices:
    def __init__(self, characteristics: list[str]) -> None:
        self._characteristics = characteristics

    def get_characteristic(self, uuid: str) -> _FakeCharacteristic | None:
        if uuid in self._characteristics:
            return _FakeCharacteristic(uuid)
        return None

    def get_service(self, uuid: str) -> None:
        return None


class FakeBleakClient:
    """Minimal bleak client backed by a FakeOpener."""

    chunk_size = 20

    def __init__(
        self,
        opener: FakeOpener,
        address: str = DEFAULT_ADDRESS,
        disconnected_callback: Any = None,
    ) -> None:
        self.opener = opener
        self.address = address
        self._connected = True
        self._callbacks: dict[str, Any] = {}
        self._disconnected_callback = disconnected_callback
        self.services = _FakeServices([PAIRING_GDIO_UUID, USDIO_UUID])
        self.disconnect_count = 0

    @property
    def is_connected(self) -> bool:
        return self._connected

    async def start_notify(self, uuid: str, callback: Any) -> None:
        self._callbacks[uuid] = callback

    async def stop_notify(self, uuid: str) -> None:
        self._callbacks.pop(uuid, None)

    async def disconnect(self) -> None:
        self._connected = False
        self.disconnect_count += 1

    def simulate_unexpected_disconnect(self) -> None:
        """Drop the link from the device side (e.g. a proxy hiccup)."""
        self._connected = False
        if self._disconnected_callback is not None:
            self._disconnected_callback(self)

    async def write_gatt_char(self, uuid: str, data: bytes, response: bool = True) -> None:
        assert self._connected, "write on a disconnected client"
        if uuid == PAIRING_GDIO_UUID:
            responses = self.opener.handle_pairing_write(bytes(data))
        elif uuid == USDIO_UUID:
            responses = self.opener.handle_usdio_write(bytes(data))
        else:  # pragma: no cover - unexpected characteristic
            raise AssertionError(f"write to unexpected characteristic {uuid}")
        callback = self._callbacks.get(uuid)
        if callback is None:
            return
        loop = asyncio.get_running_loop()
        characteristic = _FakeCharacteristic(uuid)
        for message in responses:
            for offset in range(0, len(message), self.chunk_size):
                chunk = bytearray(message[offset : offset + self.chunk_size])
                loop.call_soon(callback, characteristic, chunk)


@dataclass
class FakeEnvironment:
    """A FakeOpener wired up behind a patched establish_connection."""

    opener: FakeOpener
    clients: list[FakeBleakClient] = field(default_factory=list)
    address: str = DEFAULT_ADDRESS

    def make_client(self, *args: Any, **kwargs: Any) -> FakeBleakClient:
        client = FakeBleakClient(
            self.opener,
            self.address,
            disconnected_callback=kwargs.get("disconnected_callback"),
        )
        self.clients.append(client)
        return client


def patch_establish_connection(monkeypatch: Any, environment: FakeEnvironment) -> None:
    """Route all establish_connection call sites to the fake environment."""

    async def _establish_connection(
        client_class: Any, device: Any, name: str, **kwargs: Any
    ) -> FakeBleakClient:
        return environment.make_client(**kwargs)

    monkeypatch.setattr(
        "custom_components.nuki_opener_ble.nuki.client.establish_connection",
        _establish_connection,
    )
    # The integration's removal sweep imports it separately.
    monkeypatch.setattr(
        "custom_components.nuki_opener_ble.establish_connection",
        _establish_connection,
    )


def make_ble_device(address: str = DEFAULT_ADDRESS) -> Any:
    """Create a BLEDevice for tests."""
    from bleak.backends.device import BLEDevice

    try:
        return BLEDevice(address=address, name="Nuki_Opener", details=None)
    except TypeError:
        return BLEDevice(address, "Nuki_Opener", None)
