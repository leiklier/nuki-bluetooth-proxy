"""Exceptions raised by the Nuki Opener BLE protocol layer."""

from __future__ import annotations

from .const import Command, ErrorCode


class NukiError(Exception):
    """Base class for all Nuki protocol errors."""


class NukiProtocolError(NukiError):
    """A message could not be parsed or violates the protocol."""


class NukiCrcError(NukiProtocolError):
    """A received message failed its CRC check."""


class NukiDeviceError(NukiError):
    """The device answered with an Error Report."""

    def __init__(self, error_code: ErrorCode, command: Command) -> None:
        self.error_code = error_code
        self.command = command
        super().__init__(f"{error_code.name} (command {command.name})")


class NukiPairingError(NukiError):
    """Pairing with the device failed."""


class NukiNotInPairingModeError(NukiPairingError):
    """The device rejected pairing because pairing mode is not active."""


class NukiBadPinError(NukiError):
    """The device rejected the provided security PIN."""


class NukiConnectionError(NukiError):
    """The BLE connection failed or timed out."""


class NukiResponseTimeoutError(NukiConnectionError):
    """The device did not answer a command in time."""
