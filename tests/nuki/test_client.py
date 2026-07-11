"""Tests for the BLE client against a simulated Opener."""

import asyncio

import pytest

from custom_components.nuki_opener_ble.nuki import crypto
from custom_components.nuki_opener_ble.nuki.client import (
    NukiOpenerClient,
    NukiOpenerCredentials,
)
from custom_components.nuki_opener_ble.nuki.const import (
    ClientType,
    Command,
    LockAction,
    LockState,
    StatusCode,
)
from custom_components.nuki_opener_ble.nuki.errors import (
    NukiBadPinError,
    NukiConnectionError,
    NukiDeviceError,
    NukiNotInPairingModeError,
)

from .fake_device import (
    FakeEnvironment,
    FakeOpener,
    make_ble_device,
    patch_establish_connection,
)


@pytest.fixture
def environment(monkeypatch: pytest.MonkeyPatch) -> FakeEnvironment:
    env = FakeEnvironment(opener=FakeOpener())
    patch_establish_connection(monkeypatch, env)
    return env


def make_client(
    environment: FakeEnvironment, credentials: NukiOpenerCredentials | None = None
) -> NukiOpenerClient:
    return NukiOpenerClient(
        ble_device_getter=lambda: make_ble_device(environment.address),
        credentials=credentials,
        disconnect_delay=0.01,
    )


def make_credentials(opener: FakeOpener) -> NukiOpenerCredentials:
    private_key, public_key = crypto.generate_keypair()
    credentials = NukiOpenerCredentials(
        private_key=private_key,
        public_key=public_key,
        device_public_key=opener.public_key,
        auth_id=opener.auth_id,
        app_id=42,
    )
    opener.install_credentials(credentials)
    return credentials


class TestPairing:
    async def test_pair_success(self, environment: FakeEnvironment) -> None:
        client = make_client(environment)
        credentials = await client.pair(name="Home Assistant")
        assert credentials.auth_id == environment.opener.auth_id
        assert credentials.device_public_key == environment.opener.public_key
        assert credentials.shared_key == environment.opener.shared_key
        assert environment.opener.paired_name == "Home Assistant"
        assert environment.opener.paired_client_type == ClientType.BRIDGE
        assert not environment.opener.pairing_mode
        # The client must disconnect after pairing.
        assert all(not fake.is_connected for fake in environment.clients)

    async def test_pair_not_in_pairing_mode(self, environment: FakeEnvironment) -> None:
        environment.opener.pairing_mode = False
        client = make_client(environment)
        with pytest.raises(NukiNotInPairingModeError):
            await client.pair()

    async def test_credentials_roundtrip(self, environment: FakeEnvironment) -> None:
        client = make_client(environment)
        credentials = await client.pair()
        restored = NukiOpenerCredentials.from_dict(credentials.to_dict())
        assert restored == credentials
        assert restored.shared_key == credentials.shared_key


class TestCommands:
    async def test_get_state(self, environment: FakeEnvironment) -> None:
        credentials = make_credentials(environment.opener)
        client = make_client(environment, credentials)
        state = await client.get_state()
        assert state.lock_state == LockState.LOCKED
        assert state.config_update_count == 7
        await client.disconnect()

    async def test_get_config(self, environment: FakeEnvironment) -> None:
        credentials = make_credentials(environment.opener)
        client = make_client(environment, credentials)
        config = await client.get_config()
        assert config.name == "Front Door"
        assert config.nuki_id == 0x11223344
        await client.disconnect()

    async def test_get_battery_report(self, environment: FakeEnvironment) -> None:
        credentials = make_credentials(environment.opener)
        client = make_client(environment, credentials)
        report = await client.get_battery_report()
        assert report.battery_voltage_mv == 5450
        await client.disconnect()

    async def test_lock_action_waits_for_completion(self, environment: FakeEnvironment) -> None:
        credentials = make_credentials(environment.opener)
        client = make_client(environment, credentials)
        status = await client.lock_action(LockAction.ACTIVATE_RTO)
        assert status == StatusCode.COMPLETE
        assert environment.opener.received_lock_actions == [LockAction.ACTIVATE_RTO]
        assert environment.opener.state.lock_state == LockState.RTO_ACTIVE
        await client.disconnect()

    async def test_lock_action_updates_state_callback(self, environment: FakeEnvironment) -> None:
        credentials = make_credentials(environment.opener)
        client = make_client(environment, credentials)
        states = []
        client.state_callback = states.append
        await client.lock_action(LockAction.ELECTRIC_STRIKE_ACTUATION)
        # The intermediate OPENER_STATES notification must reach the callback.
        assert [state.lock_state for state in states] == [LockState.OPEN]
        await client.disconnect()

    async def test_verify_security_pin(self, environment: FakeEnvironment) -> None:
        credentials = make_credentials(environment.opener)
        client = make_client(environment, credentials)
        assert await client.verify_security_pin(1234) is True
        assert await client.verify_security_pin(1111) is False
        await client.disconnect()

    async def test_get_log_entries(self, environment: FakeEnvironment) -> None:
        opener = environment.opener
        opener.add_lock_action_log_entry()
        opener.add_doorbell_log_entry()
        credentials = make_credentials(opener)
        client = make_client(environment, credentials)
        entries = await client.get_log_entries(pin=1234, count=5)
        assert [entry.index for entry in entries] == [2, 1]
        await client.disconnect()

    async def test_get_log_entries_bad_pin(self, environment: FakeEnvironment) -> None:
        environment.opener.add_doorbell_log_entry()
        credentials = make_credentials(environment.opener)
        client = make_client(environment, credentials)
        with pytest.raises(NukiBadPinError):
            await client.get_log_entries(pin=1, count=5)
        await client.disconnect()

    async def test_device_error_is_raised(self, environment: FakeEnvironment) -> None:
        credentials = make_credentials(environment.opener)
        # Force a decryption mismatch on the device by changing its key.
        environment.opener.shared_key = bytes(32)
        client = make_client(environment, credentials)
        with pytest.raises((NukiDeviceError, NukiConnectionError, Exception)):
            await client.get_state()
        await client.disconnect()

    async def test_no_ble_device_available(self, environment: FakeEnvironment) -> None:
        credentials = make_credentials(environment.opener)
        client = NukiOpenerClient(ble_device_getter=lambda: None, credentials=credentials)
        with pytest.raises(NukiConnectionError):
            await client.get_state()

    async def test_connection_reused_within_operations(self, environment: FakeEnvironment) -> None:
        credentials = make_credentials(environment.opener)
        client = make_client(environment, credentials)
        await client.get_state()
        await client.get_config()
        # Both operations ran before the idle disconnect fired.
        assert len(environment.clients) == 1
        await client.disconnect()


class TestRequestRetry:
    async def test_request_retries_after_connection_drop(
        self, environment: FakeEnvironment
    ) -> None:
        """A request whose connection drops mid-flight is retried."""
        credentials = make_credentials(environment.opener)
        client = make_client(environment, credentials)
        original = client._request_once
        calls = 0

        async def flaky(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise NukiConnectionError("disconnected while waiting for response")
            return await original(*args, **kwargs)

        client._request_once = flaky
        state = await client.get_state()
        assert state.lock_state == LockState.LOCKED
        assert calls == 2
        await client.disconnect()

    async def test_request_gives_up_after_attempts(self, environment: FakeEnvironment) -> None:
        """Persistent connection failures surface after the retries."""
        credentials = make_credentials(environment.opener)
        client = make_client(environment, credentials)
        calls = 0

        async def always_fails(*args, **kwargs):
            nonlocal calls
            calls += 1
            raise NukiConnectionError("disconnected while waiting for response")

        client._request_once = always_fails
        with pytest.raises(NukiConnectionError):
            await client.get_state()
        assert calls == 3
        await client.disconnect()


class TestCompletionWait:
    async def test_disconnect_during_completion_wait_returns_fast(
        self, environment: FakeEnvironment
    ) -> None:
        """A link drop after ACCEPTED must not stall for the full timeout."""
        environment.opener.omit_lock_completion = True
        credentials = make_credentials(environment.opener)
        client = make_client(environment, credentials)
        task = asyncio.ensure_future(client.lock_action(LockAction.ACTIVATE_RTO))
        # Let the ACCEPTED response arrive and the completion wait start.
        for _ in range(20):
            await asyncio.sleep(0)
        assert environment.opener.received_lock_actions == [LockAction.ACTIVATE_RTO]
        assert not task.done()
        environment.clients[-1].simulate_unexpected_disconnect()
        status = await asyncio.wait_for(task, timeout=1)
        assert status == StatusCode.ACCEPTED
        await client.disconnect()

    async def test_interim_status_is_ignored_until_complete(
        self, environment: FakeEnvironment
    ) -> None:
        """A duplicate ACCEPTED does not end the completion wait."""
        environment.opener.duplicate_lock_accepted = True
        credentials = make_credentials(environment.opener)
        client = make_client(environment, credentials)
        status = await client.lock_action(LockAction.ACTIVATE_RTO)
        assert status == StatusCode.COMPLETE
        await client.disconnect()

    async def test_stale_status_is_dropped(self, environment: FakeEnvironment) -> None:
        """A status with no armed waiter must not leak into a later action."""
        credentials = make_credentials(environment.opener)
        client = make_client(environment, credentials)
        # A late completion from a previous, already-finished action.
        client._handle_message(Command.STATUS, bytes([StatusCode.ACCEPTED]))
        status = await client.lock_action(LockAction.ACTIVATE_RTO)
        assert status == StatusCode.COMPLETE
        await client.disconnect()


class TestDeadAirRecovery:
    async def test_request_recovers_from_dead_air(self, environment: FakeEnvironment) -> None:
        """A write whose response never arrives is retried on a new connection
        after the short request watchdog, not the full completion timeout."""
        environment.opener.swallow_usdio_responses = 1
        credentials = make_credentials(environment.opener)
        client = NukiOpenerClient(
            ble_device_getter=lambda: make_ble_device(environment.address),
            credentials=credentials,
            disconnect_delay=0.01,
            request_timeout=0.1,
        )
        state = await asyncio.wait_for(client.get_state(), timeout=5)
        assert state.lock_state == LockState.LOCKED
        # The deaf connection was abandoned and a fresh one established.
        assert len(environment.clients) == 2
        await client.disconnect()

    async def test_lock_action_redoes_challenge_on_stale_nonce(
        self, environment: FakeEnvironment
    ) -> None:
        """A replayed action rejected with K_BAD_NONCE is redone from the
        challenge instead of failing the service call."""
        environment.opener.reject_lock_action_nonce = 1
        credentials = make_credentials(environment.opener)
        client = make_client(environment, credentials)
        status = await client.lock_action(LockAction.ACTIVATE_RTO)
        assert status == StatusCode.COMPLETE
        assert environment.opener.received_lock_actions == [LockAction.ACTIVATE_RTO]
        await client.disconnect()

    async def test_lock_action_persistent_bad_nonce_raises(
        self, environment: FakeEnvironment
    ) -> None:
        """A second K_BAD_NONCE in a row surfaces as a device error."""
        environment.opener.reject_lock_action_nonce = 2
        credentials = make_credentials(environment.opener)
        client = make_client(environment, credentials)
        with pytest.raises(NukiDeviceError):
            await client.lock_action(LockAction.ACTIVATE_RTO)
        await client.disconnect()
