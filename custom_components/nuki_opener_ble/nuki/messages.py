"""Payload parsing and building for Nuki Opener BLE commands.

Only the payloads are handled here; message framing (CRC, encryption) lives in
``protocol.py``. Parsers are tolerant of extra trailing bytes because Nuki
appends new fields to existing messages in firmware updates.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import struct

from .const import (
    MAX_NAME_LENGTH,
    Capability,
    ClientType,
    Command,
    CompletionStatus,
    DoorbellSource,
    DoorSensorState,
    ErrorCode,
    LockAction,
    LockState,
    LogEntryType,
    NukiState,
    OperatingMode,
    StatusCode,
    Trigger,
)
from .crypto import hmac_sha256
from .errors import NukiProtocolError


def _parse_datetime(data: bytes) -> datetime | None:
    """Parse a 7-byte Nuki timestamp; returns None if it is not a valid date."""
    year, month, day, hour, minute, second = struct.unpack_from("<HBBBBB", data)
    try:
        return datetime(year, month, day, hour, minute, second)
    except ValueError:
        return None


def _build_datetime(value: datetime) -> bytes:
    return struct.pack(
        "<HBBBBB", value.year, value.month, value.day, value.hour, value.minute, value.second
    )


def _decode_name(data: bytes) -> str:
    return data.rstrip(b"\x00").decode("utf-8", errors="replace")


def encode_name(name: str, length: int = MAX_NAME_LENGTH) -> bytes:
    """UTF-8 encode and zero-pad a name field, truncating if necessary."""
    encoded = name.encode("utf-8")[:length]
    return encoded.ljust(length, b"\x00")


def _require(payload: bytes, size: int, what: str) -> None:
    if len(payload) < size:
        raise NukiProtocolError(f"{what} payload too short: {len(payload)} < {size} bytes")


@dataclass(frozen=True, slots=True)
class OpenerState:
    """Payload of Opener States (0x000C)."""

    nuki_state: NukiState
    lock_state: LockState
    trigger: Trigger
    current_time: datetime | None
    timezone_offset_minutes: int
    battery_critical: bool
    config_update_count: int | None = None
    ring_to_open_timer: int | None = None
    last_lock_action: LockAction | None = None
    last_lock_action_trigger: Trigger | None = None
    last_lock_action_completion_status: CompletionStatus | None = None
    door_sensor_state: DoorSensorState | None = None

    @property
    def ring_to_open_active(self) -> bool:
        return self.lock_state == LockState.RTO_ACTIVE

    @property
    def continuous_mode_active(self) -> bool:
        return self.nuki_state == NukiState.CONTINUOUS_MODE

    @classmethod
    def parse(cls, payload: bytes) -> OpenerState:
        # The 2016-era spec example is 13 bytes; current firmware sends 19+.
        _require(payload, 13, "Opener States")
        return cls(
            nuki_state=NukiState(payload[0]),
            lock_state=LockState(payload[1]),
            trigger=Trigger(payload[2]),
            current_time=_parse_datetime(payload[3:10]),
            timezone_offset_minutes=int.from_bytes(payload[10:12], "little", signed=True),
            battery_critical=bool(payload[12] & 0x01),
            config_update_count=payload[13] if len(payload) > 13 else None,
            ring_to_open_timer=payload[14] if len(payload) > 14 else None,
            last_lock_action=LockAction(payload[15]) if len(payload) > 15 else None,
            last_lock_action_trigger=Trigger(payload[16]) if len(payload) > 16 else None,
            last_lock_action_completion_status=(
                CompletionStatus(payload[17]) if len(payload) > 17 else None
            ),
            door_sensor_state=DoorSensorState(payload[18]) if len(payload) > 18 else None,
        )


@dataclass(frozen=True, slots=True)
class OpenerConfig:
    """Payload of Config (0x0015)."""

    nuki_id: int
    name: str
    latitude: float
    longitude: float
    capabilities: Capability
    pairing_enabled: bool
    button_enabled: bool
    led_enabled: bool
    current_time: datetime | None
    timezone_offset_minutes: int
    dst_mode: int
    has_fob: bool
    fob_action_1: int
    fob_action_2: int
    fob_action_3: int
    operating_mode: OperatingMode
    advertising_mode: int
    has_keypad: bool
    firmware_version: str
    hardware_revision: str
    timezone_id: int

    _SIZE = 72

    @classmethod
    def parse(cls, payload: bytes) -> OpenerConfig:
        _require(payload, cls._SIZE, "Config")
        (nuki_id,) = struct.unpack_from("<I", payload, 0)
        latitude, longitude = struct.unpack_from("<ff", payload, 36)
        return cls(
            nuki_id=nuki_id,
            name=_decode_name(payload[4:36]),
            latitude=latitude,
            longitude=longitude,
            capabilities=Capability(payload[44]),
            pairing_enabled=bool(payload[45]),
            button_enabled=bool(payload[46]),
            led_enabled=bool(payload[47]),
            current_time=_parse_datetime(payload[48:55]),
            timezone_offset_minutes=int.from_bytes(payload[55:57], "little", signed=True),
            dst_mode=payload[57],
            has_fob=bool(payload[58]),
            fob_action_1=payload[59],
            fob_action_2=payload[60],
            fob_action_3=payload[61],
            operating_mode=OperatingMode(payload[62]),
            advertising_mode=payload[63],
            has_keypad=bool(payload[64]),
            firmware_version=".".join(str(b) for b in payload[65:68]),
            hardware_revision=".".join(str(b) for b in payload[68:70]),
            timezone_id=int.from_bytes(payload[70:72], "little"),
        )


@dataclass(frozen=True, slots=True)
class BatteryReport:
    """Payload of Battery Report (0x0011)."""

    battery_drain: int
    battery_voltage_mv: int
    battery_critical: bool
    lock_action: LockAction
    start_voltage_mv: int
    lowest_voltage_mv: int
    lock_distance: int
    start_temperature: int
    max_turn_current: int
    battery_resistance: int

    _SIZE = 17

    @classmethod
    def parse(cls, payload: bytes) -> BatteryReport:
        _require(payload, cls._SIZE, "Battery Report")
        (
            drain,
            voltage,
            critical,
            action,
            start_voltage,
            lowest_voltage,
            distance,
            temperature,
            max_current,
            resistance,
        ) = struct.unpack_from("<HHBBHHHbHH", payload)
        return cls(
            battery_drain=drain,
            battery_voltage_mv=voltage,
            battery_critical=bool(critical & 0x01),
            lock_action=LockAction(action),
            start_voltage_mv=start_voltage,
            lowest_voltage_mv=lowest_voltage,
            lock_distance=distance,
            start_temperature=temperature,
            max_turn_current=max_current,
            battery_resistance=resistance,
        )


@dataclass(frozen=True, slots=True)
class ErrorReport:
    """Payload of Error Report (0x0012)."""

    error_code: ErrorCode
    command: Command

    @classmethod
    def parse(cls, payload: bytes) -> ErrorReport:
        _require(payload, 3, "Error Report")
        return cls(
            error_code=ErrorCode(payload[0]),
            command=Command(int.from_bytes(payload[1:3], "little")),
        )


@dataclass(frozen=True, slots=True)
class DoorbellRecognition:
    """Data of a doorbell-recognition log entry (type 0x06)."""

    ring_to_open_activated: bool
    continuous_mode_activated: bool
    source: DoorbellSource
    geofence_active: bool
    doorbell_suppressed: bool
    sound_id: int
    completion_status: CompletionStatus
    code_id: int


@dataclass(frozen=True, slots=True)
class LogEntry:
    """Payload of Log Entry (0x0032)."""

    index: int
    timestamp: datetime | None
    auth_id: int
    name: str
    type: LogEntryType
    data: bytes
    doorbell: DoorbellRecognition | None = None

    _HEADER_SIZE = 4 + 7 + 4 + 32 + 1

    @classmethod
    def parse(cls, payload: bytes) -> LogEntry:
        _require(payload, cls._HEADER_SIZE, "Log Entry")
        entry_type = LogEntryType(payload[47])
        data = payload[cls._HEADER_SIZE :]
        doorbell = None
        if entry_type == LogEntryType.DOORBELL_RECOGNITION and len(data) >= 8:
            doorbell = DoorbellRecognition(
                ring_to_open_activated=bool(data[0] & 0x01),
                continuous_mode_activated=bool(data[0] & 0x02),
                source=DoorbellSource(data[1]),
                geofence_active=bool(data[2]),
                doorbell_suppressed=bool(data[3]),
                sound_id=data[4],
                completion_status=CompletionStatus(data[5]),
                code_id=int.from_bytes(data[6:8], "little"),
            )
        return cls(
            index=int.from_bytes(payload[0:4], "little"),
            timestamp=_parse_datetime(payload[4:11]),
            auth_id=int.from_bytes(payload[11:15], "little"),
            name=_decode_name(payload[15:47]),
            type=entry_type,
            data=data,
            doorbell=doorbell,
        )


@dataclass(frozen=True, slots=True)
class LogEntryCount:
    """Payload of Log Entry Count (0x0033)."""

    logging_enabled: bool
    count: int

    @classmethod
    def parse(cls, payload: bytes) -> LogEntryCount:
        _require(payload, 3, "Log Entry Count")
        return cls(
            logging_enabled=bool(payload[0]),
            count=int.from_bytes(payload[1:3], "little"),
        )


@dataclass(frozen=True, slots=True)
class AuthorizationId:
    """Payload of Authorization-ID (0x0007), received while pairing."""

    authenticator: bytes
    auth_id: bytes
    uuid: bytes
    nonce: bytes

    @classmethod
    def parse(cls, payload: bytes) -> AuthorizationId:
        _require(payload, 84, "Authorization-ID")
        return cls(
            authenticator=payload[0:32],
            auth_id=payload[32:36],
            uuid=payload[36:52],
            nonce=payload[52:84],
        )

    def verify(self, shared_key: bytes, client_nonce: bytes) -> bool:
        """Check the device's authenticator over (auth_id, uuid, nonce, client_nonce)."""
        expected = hmac_sha256(shared_key, self.auth_id + self.uuid + self.nonce + client_nonce)
        return expected == self.authenticator


def parse_status(payload: bytes) -> StatusCode:
    """Parse the payload of Status (0x000E)."""
    _require(payload, 1, "Status")
    return StatusCode(payload[0])


def parse_challenge(payload: bytes) -> bytes:
    """Parse the payload of Challenge (0x0004); returns the 32-byte nonce."""
    _require(payload, 32, "Challenge")
    return payload[0:32]


def parse_public_key(payload: bytes) -> bytes:
    """Parse the payload of Public Key (0x0003)."""
    _require(payload, 32, "Public Key")
    return payload[0:32]


def build_request_data(command: Command) -> bytes:
    """Payload of Request Data (0x0001)."""
    return struct.pack("<H", command)


def build_public_key(public_key: bytes) -> bytes:
    """Payload of Public Key (0x0003)."""
    return public_key


def build_authorization_authenticator(
    shared_key: bytes, client_public_key: bytes, device_public_key: bytes, challenge: bytes
) -> bytes:
    """Payload of Authorization Authenticator (0x0005)."""
    return hmac_sha256(shared_key, client_public_key + device_public_key + challenge)


def build_authorization_data(
    shared_key: bytes,
    client_type: ClientType,
    app_id: int,
    name: str,
    client_nonce: bytes,
    challenge: bytes,
) -> bytes:
    """Payload of Authorization Data (0x0006)."""
    inner = bytes((client_type,)) + struct.pack("<I", app_id) + encode_name(name) + client_nonce
    authenticator = hmac_sha256(shared_key, inner + challenge)
    return authenticator + inner


def build_authorization_id_confirmation(
    shared_key: bytes, auth_id: bytes, challenge: bytes
) -> bytes:
    """Payload of Authorization-ID Confirmation (0x001E)."""
    return hmac_sha256(shared_key, auth_id + challenge) + auth_id


def build_lock_action(
    action: LockAction,
    app_id: int,
    challenge: bytes,
    flags: int = 0,
    name_suffix: str | None = None,
) -> bytes:
    """Payload of Lock Action (0x000D)."""
    suffix = encode_name(name_suffix, 20) if name_suffix else b""
    return bytes((action,)) + struct.pack("<I", app_id) + bytes((flags,)) + suffix + challenge


def build_request_config(challenge: bytes) -> bytes:
    """Payload of Request Config (0x0014)."""
    return challenge


def build_verify_security_pin(challenge: bytes, pin: int) -> bytes:
    """Payload of Verify Security PIN (0x0020)."""
    return challenge + struct.pack("<H", pin)


def build_request_log_entries(
    challenge: bytes,
    pin: int,
    start_index: int = 0,
    count: int = 1,
    sort_descending: bool = True,
    request_total_count: bool = False,
) -> bytes:
    """Payload of Request Log Entries (0x0031)."""
    return (
        struct.pack(
            "<IHBB",
            start_index,
            count,
            0x01 if sort_descending else 0x00,
            0x01 if request_total_count else 0x00,
        )
        + challenge
        + struct.pack("<H", pin)
    )


def build_update_time(time: datetime, challenge: bytes, pin: int) -> bytes:
    """Payload of Update Time (0x0021)."""
    return _build_datetime(time) + challenge + struct.pack("<H", pin)
