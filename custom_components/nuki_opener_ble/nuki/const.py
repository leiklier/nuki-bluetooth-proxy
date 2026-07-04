"""Constants and enums for the Nuki Opener BLE protocol.

Values are taken from the official Nuki Opener BLE API v1.1.0. The GATT UUIDs
in that document are a copy-paste error from the Smart Lock spec (``a92e``);
real Opener hardware uses the ``a92a`` prefix, as implemented by NukiBleEsp32
and pyNukiBT.
"""

from __future__ import annotations

from enum import IntEnum
from typing import Self

# GATT services / characteristics of the Nuki Opener.
PAIRING_SERVICE_UUID = "a92ae100-5501-11e4-916c-0800200c9a66"
PAIRING_GDIO_UUID = "a92ae101-5501-11e4-916c-0800200c9a66"
OPENER_SERVICE_UUID = "a92ae200-5501-11e4-916c-0800200c9a66"
USDIO_UUID = "a92ae202-5501-11e4-916c-0800200c9a66"
# Advertised while the device is factory-new / uninitialized.
INITIALIZATION_SERVICE_UUIDS = (
    "a92ae000-5501-11e4-916c-0800200c9a66",
    "a92ee000-5501-11e4-916c-0800200c9a66",
)

# Apple manufacturer ID; Nuki devices advertise their state as an iBeacon.
APPLE_MANUFACTURER_ID = 76
IBEACON_PREFIX = bytes((0x02, 0x15))

MAX_NAME_LENGTH = 32


class _TolerantEnum(IntEnum):
    """IntEnum that synthesizes members for unknown values.

    Nuki extends payloads and value ranges across firmware versions; parsing
    must not break on values this implementation does not know about.
    """

    @classmethod
    def _missing_(cls, value: object) -> Self | None:
        if not isinstance(value, int):
            return None
        member = int.__new__(cls, value)
        member._name_ = f"UNKNOWN_0X{value:02X}"
        member._value_ = value
        return member


class Command(_TolerantEnum):
    """Nuki BLE command identifiers."""

    REQUEST_DATA = 0x0001
    PUBLIC_KEY = 0x0003
    CHALLENGE = 0x0004
    AUTHORIZATION_AUTHENTICATOR = 0x0005
    AUTHORIZATION_DATA = 0x0006
    AUTHORIZATION_ID = 0x0007
    REMOVE_AUTHORIZATION_ENTRY = 0x0008
    REQUEST_AUTHORIZATION_ENTRIES = 0x0009
    AUTHORIZATION_ENTRY = 0x000A
    AUTHORIZATION_DATA_INVITE = 0x000B
    OPENER_STATES = 0x000C
    LOCK_ACTION = 0x000D
    STATUS = 0x000E
    MOST_RECENT_COMMAND = 0x000F
    BATTERY_REPORT = 0x0011
    ERROR_REPORT = 0x0012
    SET_CONFIG = 0x0013
    REQUEST_CONFIG = 0x0014
    CONFIG = 0x0015
    SET_SECURITY_PIN = 0x0019
    SET_CALIBRATED = 0x001A
    REQUEST_REBOOT = 0x001D
    AUTHORIZATION_ID_CONFIRMATION = 0x001E
    AUTHORIZATION_ID_INVITE = 0x001F
    VERIFY_SECURITY_PIN = 0x0020
    UPDATE_TIME = 0x0021
    UPDATE_AUTHORIZATION_ENTRY = 0x0025
    AUTHORIZATION_ENTRY_COUNT = 0x0027
    START_BUS_SIGNAL_RECORDING = 0x002F
    REQUEST_LOG_ENTRIES = 0x0031
    LOG_ENTRY = 0x0032
    LOG_ENTRY_COUNT = 0x0033
    ENABLE_LOGGING = 0x0034
    SET_ADVANCED_CONFIG = 0x0035
    REQUEST_ADVANCED_CONFIG = 0x0036
    ADVANCED_CONFIG = 0x0037
    ADD_TIME_CONTROL_ENTRY = 0x0039
    TIME_CONTROL_ENTRY_ID = 0x003A
    REMOVE_TIME_CONTROL_ENTRY = 0x003B
    REQUEST_TIME_CONTROL_ENTRIES = 0x003C
    TIME_CONTROL_ENTRY_COUNT = 0x003D
    TIME_CONTROL_ENTRY = 0x003E
    UPDATE_TIME_CONTROL_ENTRY = 0x003F
    ADD_KEYPAD_CODE = 0x0041
    KEYPAD_CODE_ID = 0x0042
    REQUEST_KEYPAD_CODES = 0x0043
    KEYPAD_CODE_COUNT = 0x0044
    KEYPAD_CODE = 0x0045
    UPDATE_KEYPAD_CODE = 0x0046
    REMOVE_KEYPAD_CODE = 0x0047
    KEYPAD_ACTION = 0x0048
    CONTINUOUS_MODE_ACTION = 0x0057
    SIMPLE_LOCK_ACTION = 0x0100


class ErrorCode(_TolerantEnum):
    """Nuki error codes (general, pairing and opener service)."""

    BAD_CRC = 0xFD
    BAD_LENGTH = 0xFE
    UNKNOWN = 0xFF

    P_NOT_PAIRING = 0x10
    P_BAD_AUTHENTICATOR = 0x11
    P_BAD_PARAMETER = 0x12
    P_MAX_USER = 0x13

    K_NOT_AUTHORIZED = 0x20
    K_BAD_PIN = 0x21
    K_BAD_NONCE = 0x22
    K_BAD_PARAMETER = 0x23
    K_INVALID_AUTH_ID = 0x24
    K_DISABLED = 0x25
    K_REMOTE_NOT_ALLOWED = 0x26
    K_TIME_NOT_ALLOWED = 0x27
    K_TOO_MANY_PIN_ATTEMPTS = 0x28
    K_TOO_MANY_ENTRIES = 0x29
    K_CODE_ALREADY_EXISTS = 0x2A
    K_CODE_INVALID = 0x2B
    K_CODE_INVALID_TIMEOUT_1 = 0x2C
    K_CODE_INVALID_TIMEOUT_2 = 0x2D
    K_CODE_INVALID_TIMEOUT_3 = 0x2E
    K_AUTO_UNLOCK_TOO_RECENT = 0x40
    K_BUSY = 0x45
    K_CANCELED = 0x46
    K_NOT_CALIBRATED = 0x47
    K_RECORDING_TIMEOUT = 0x48
    K_LOW_VOLTAGE = 0x49
    K_OPERATING_MODE_UNKNOWN = 0x50


class StatusCode(_TolerantEnum):
    """Completion status of a command."""

    COMPLETE = 0x00
    ACCEPTED = 0x01


class NukiState(_TolerantEnum):
    """Operation mode of the Opener ("Nuki state")."""

    UNINITIALIZED = 0x00
    PAIRING_MODE = 0x01
    DOOR_MODE = 0x02
    CONTINUOUS_MODE = 0x03
    MAINTENANCE_MODE = 0x04


class LockState(_TolerantEnum):
    """State of the intercom control within the Opener ("lock state")."""

    UNCALIBRATED = 0x00
    LOCKED = 0x01
    RTO_ACTIVE = 0x03
    OPEN = 0x05
    OPENING = 0x07
    UNDEFINED = 0xFF


class LockAction(_TolerantEnum):
    """Executable opener actions."""

    ACTIVATE_RTO = 0x01
    DEACTIVATE_RTO = 0x02
    ELECTRIC_STRIKE_ACTUATION = 0x03
    ACTIVATE_CM = 0x04
    DEACTIVATE_CM = 0x05
    FOB_ACTION_1 = 0x81
    FOB_ACTION_2 = 0x82
    FOB_ACTION_3 = 0x83


class Trigger(_TolerantEnum):
    """What caused a state change."""

    SYSTEM = 0x00
    MANUAL = 0x01
    BUTTON = 0x02
    AUTOMATIC = 0x03


class CompletionStatus(_TolerantEnum):
    """Completion status of the most recent lock action."""

    SUCCESS = 0x00
    CANCELED = 0x02
    TOO_RECENT = 0x03
    BUSY = 0x04
    INCOMPLETE = 0x08
    INVALID_CODE = 0xE0
    OTHER_ERROR = 0xFE
    UNKNOWN = 0xFF


class DoorSensorState(_TolerantEnum):
    """State of an attached door sensor."""

    UNAVAILABLE = 0x00
    DEACTIVATED = 0x01
    DOOR_CLOSED = 0x02
    DOOR_OPENED = 0x03
    DOOR_STATE_UNKNOWN = 0x04
    CALIBRATING = 0x05


class ClientType(_TolerantEnum):
    """Type of the authorized client.

    BRIDGE is the default for this integration: bridge authorizations may
    execute the continuous-mode lock actions (0x04/0x05) without a security
    PIN.
    """

    APP = 0x00
    BRIDGE = 0x01
    FOB = 0x02
    KEYPAD = 0x03


class Capability(_TolerantEnum):
    """What the opener can do, depending on the intercom wiring."""

    DOOR_OPENING_ONLY = 0x00
    DOOR_OPENING_AND_RTO = 0x01
    RTO_ONLY = 0x02


class OperatingMode(_TolerantEnum):
    """Intercom type the Opener is configured for."""

    GENERIC_DOOR_OPENER = 0x00
    ANALOGUE_INTERCOM = 0x01
    DIGITAL_INTERCOM = 0x02
    SIEDLE = 0x03
    TCS = 0x04
    BTICINO = 0x05
    SIEDLE_HTS = 0x06
    STR = 0x07
    RITTO = 0x08
    FERMAX = 0x09
    COMELIT = 0x0A
    URMET_BIBUS = 0x0B
    URMET_2VOICE = 0x0C
    GOLMAR = 0x0D
    SKS = 0x0E
    SPARE = 0x0F


class LogEntryType(_TolerantEnum):
    """Type of a log entry."""

    LOGGING_ENABLED_DISABLED = 0x01
    LOCK_ACTION = 0x02
    CALIBRATION = 0x03
    KEYPAD_ACTION = 0x05
    DOORBELL_RECOGNITION = 0x06


class DoorbellSource(_TolerantEnum):
    """Cause of a doorbell-recognition log entry."""

    DOORBELL = 0x00
    TIME_CONTROL = 0x01
    APP = 0x02
    BUTTON = 0x03
    FOB = 0x04
    BRIDGE = 0x05
    KEYPAD = 0x06
