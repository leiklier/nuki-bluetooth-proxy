"""Tests for command payload parsing and building."""

from datetime import datetime

import pytest

from custom_components.nuki_opener_ble.nuki import messages, protocol
from custom_components.nuki_opener_ble.nuki.const import (
    Capability,
    ClientType,
    Command,
    CompletionStatus,
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
from custom_components.nuki_opener_ble.nuki.errors import NukiProtocolError

from . import vectors
from .fake_device import FakeOpener


class TestOpenerState:
    def test_parse_spec_payload(self) -> None:
        state = messages.OpenerState.parse(vectors.STATES_RESPONSE_PAYLOAD)
        assert state.nuki_state == NukiState.DOOR_MODE
        assert state.lock_state == LockState.LOCKED
        assert state.trigger == Trigger.SYSTEM
        assert state.current_time == datetime(2016, 3, 7, 8, 15, 30)
        assert state.timezone_offset_minutes == 60
        assert state.battery_critical is False
        # 2016-era firmware: fields after the battery flag are absent/unknown.
        assert state.last_lock_action is None
        assert state.door_sensor_state is None

    def test_parse_minimal_13_byte_payload(self) -> None:
        state = messages.OpenerState.parse(vectors.STATES_RESPONSE_PAYLOAD[:13])
        assert state.lock_state == LockState.LOCKED
        assert state.config_update_count is None

    def test_parse_modern_payload(self) -> None:
        state = messages.OpenerState.parse(FakeOpener().state_payload())
        assert state.nuki_state == NukiState.DOOR_MODE
        assert state.lock_state == LockState.LOCKED
        assert state.config_update_count == 7
        assert state.ring_to_open_timer == 0
        assert state.last_lock_action == LockAction.DEACTIVATE_RTO
        assert state.last_lock_action_trigger == Trigger.SYSTEM
        assert state.last_lock_action_completion_status == CompletionStatus.SUCCESS
        assert state.door_sensor_state == DoorSensorState.UNAVAILABLE

    def test_parse_tolerates_trailing_bytes(self) -> None:
        payload = FakeOpener().state_payload() + bytes(8)
        state = messages.OpenerState.parse(payload)
        assert state.lock_state == LockState.LOCKED

    def test_parse_rejects_truncated_payload(self) -> None:
        with pytest.raises(NukiProtocolError):
            messages.OpenerState.parse(bytes(12))

    def test_convenience_properties(self) -> None:
        payload = bytearray(FakeOpener().state_payload())
        payload[0] = NukiState.CONTINUOUS_MODE
        payload[1] = LockState.RTO_ACTIVE
        state = messages.OpenerState.parse(bytes(payload))
        assert state.ring_to_open_active is True
        assert state.continuous_mode_active is True

    def test_unknown_enum_values_are_tolerated(self) -> None:
        payload = bytearray(FakeOpener().state_payload())
        payload[1] = 0x42
        state = messages.OpenerState.parse(bytes(payload))
        assert state.lock_state == 0x42
        assert state.lock_state.name == "UNKNOWN_0X42"


class TestOpenerConfig:
    def test_parse(self) -> None:
        config = messages.OpenerConfig.parse(FakeOpener().config_payload())
        assert config.nuki_id == 0x11223344
        assert config.name == "Front Door"
        assert config.latitude == pytest.approx(59.91, abs=1e-4)
        assert config.longitude == pytest.approx(10.75, abs=1e-4)
        assert config.capabilities == Capability.DOOR_OPENING_AND_RTO
        assert config.pairing_enabled is False
        assert config.button_enabled is True
        assert config.operating_mode == OperatingMode.DIGITAL_INTERCOM
        assert config.firmware_version == "1.8.0"
        assert config.hardware_revision == "5.2"
        assert config.timezone_id == 37

    def test_parse_rejects_truncated_payload(self) -> None:
        with pytest.raises(NukiProtocolError):
            messages.OpenerConfig.parse(bytes(50))


class TestBatteryReport:
    def test_parse(self) -> None:
        report = messages.BatteryReport.parse(FakeOpener().battery_payload())
        assert report.battery_voltage_mv == 5450
        assert report.battery_critical is False
        assert report.start_voltage_mv == 5500
        assert report.lowest_voltage_mv == 5300
        assert report.start_temperature == 21


class TestErrorReport:
    def test_parse(self) -> None:
        report = messages.ErrorReport.parse(bytes([0x10, 0x01, 0x00]))
        assert report.error_code == ErrorCode.P_NOT_PAIRING
        assert report.command == Command.REQUEST_DATA


class TestLogEntry:
    def test_parse_doorbell_recognition(self) -> None:
        opener = FakeOpener()
        opener.add_doorbell_log_entry(suppressed=True)
        entry = messages.LogEntry.parse(opener.log_entry_payload(opener.log_entries[0]))
        assert entry.index == 1
        assert entry.type == LogEntryType.DOORBELL_RECOGNITION
        assert entry.doorbell is not None
        assert entry.doorbell.doorbell_suppressed is True
        assert entry.doorbell.completion_status == CompletionStatus.SUCCESS

    def test_parse_lock_action_entry(self) -> None:
        opener = FakeOpener()
        opener.add_lock_action_log_entry()
        entry = messages.LogEntry.parse(opener.log_entry_payload(opener.log_entries[0]))
        assert entry.type == LogEntryType.LOCK_ACTION
        assert entry.doorbell is None


class TestSimpleParsers:
    def test_parse_status(self) -> None:
        assert messages.parse_status(b"\x00") == StatusCode.COMPLETE
        assert messages.parse_status(b"\x01") == StatusCode.ACCEPTED

    def test_parse_challenge_and_public_key(self) -> None:
        assert messages.parse_challenge(vectors.CHALLENGE_1) == vectors.CHALLENGE_1
        assert messages.parse_public_key(vectors.DEVICE_PUBLIC_KEY) == (vectors.DEVICE_PUBLIC_KEY)
        with pytest.raises(NukiProtocolError):
            messages.parse_challenge(bytes(31))


class TestPairingBuilders:
    def test_authorization_data_spec_vector(self) -> None:
        payload = messages.build_authorization_data(
            vectors.SHARED_KEY,
            ClientType.APP,
            app_id=0,
            name=vectors.AUTHORIZATION_DATA_NAME,
            client_nonce=vectors.CL_NONCE,
            challenge=vectors.CHALLENGE_2,
        )
        message = protocol.encode_plain(Command.AUTHORIZATION_DATA, payload)
        assert message == vectors.AUTHORIZATION_DATA_MESSAGE

    def test_authorization_id_verification_spec_vector(self) -> None:
        _, payload = protocol.decode_plain(vectors.AUTHORIZATION_ID_MESSAGE)
        authorization = messages.AuthorizationId.parse(payload)
        assert authorization.auth_id == vectors.AUTH_ID
        assert authorization.uuid == vectors.DEVICE_UUID
        assert authorization.verify(vectors.SHARED_KEY, vectors.CL_NONCE)
        assert not authorization.verify(vectors.SHARED_KEY, bytes(32))

    def test_authorization_id_confirmation_spec_vector(self) -> None:
        payload = messages.build_authorization_id_confirmation(
            vectors.SHARED_KEY, vectors.AUTH_ID, vectors.CHALLENGE_3
        )
        message = protocol.encode_plain(Command.AUTHORIZATION_ID_CONFIRMATION, payload)
        assert message == vectors.AUTHORIZATION_ID_CONFIRMATION_MESSAGE


class TestCommandBuilders:
    def test_lock_action_spec_vector(self) -> None:
        payload = messages.build_lock_action(
            LockAction.ACTIVATE_RTO,
            app_id=0,
            challenge=vectors.LOCK_ACTION_CHALLENGE,
        )
        # PDATA from the spec: auth_id | command | payload | crc
        assert vectors.LOCK_ACTION_PLAINTEXT[6:-2] == payload

    def test_lock_action_with_name_suffix(self) -> None:
        payload = messages.build_lock_action(
            LockAction.ELECTRIC_STRIKE_ACTUATION,
            app_id=1,
            challenge=bytes(32),
            name_suffix="Leik",
        )
        assert len(payload) == 1 + 4 + 1 + 20 + 32
        assert payload[6:26] == b"Leik" + bytes(16)

    def test_encode_name_truncates_multibyte(self) -> None:
        assert len(messages.encode_name("ø" * 40)) == 32

    def test_build_request_log_entries(self) -> None:
        payload = messages.build_request_log_entries(bytes(32), pin=1234, count=5)
        assert len(payload) == 4 + 2 + 1 + 1 + 32 + 2
        assert payload[4:6] == (5).to_bytes(2, "little")
