"""Tests for the high-level device wrapper (polling and ring detection)."""

import pytest

from custom_components.nuki_opener_ble.nuki.const import (
    APPLE_MANUFACTURER_ID,
    OPENER_SERVICE_UUID,
    LockAction,
    LockState,
    NukiState,
    Trigger,
)
from custom_components.nuki_opener_ble.nuki.device import NukiOpenerDevice, RingEvent

from .fake_device import FakeEnvironment, FakeOpener, patch_establish_connection
from .test_client import make_client, make_credentials


@pytest.fixture
def environment(monkeypatch: pytest.MonkeyPatch) -> FakeEnvironment:
    env = FakeEnvironment(opener=FakeOpener())
    patch_establish_connection(monkeypatch, env)
    return env


def make_device(environment: FakeEnvironment, security_pin: int | None = None) -> NukiOpenerDevice:
    credentials = make_credentials(environment.opener)
    client = make_client(environment, credentials)
    return NukiOpenerDevice(client, security_pin=security_pin)


def opener_beacon(state_changed: bool) -> dict[int, bytes]:
    uuid = bytes.fromhex(OPENER_SERVICE_UUID.replace("-", ""))
    power = 0xC5 if state_changed else 0xC4
    return {APPLE_MANUFACTURER_ID: bytes.fromhex("0215") + uuid + bytes(4) + bytes([power])}


class TestPolling:
    async def test_update_populates_state_config_battery(
        self, environment: FakeEnvironment
    ) -> None:
        device = make_device(environment)
        await device.update()
        assert device.state is not None
        assert device.state.lock_state == LockState.LOCKED
        assert device.config is not None
        assert device.config.name == "Front Door"
        assert device.battery is not None
        assert device.battery.battery_voltage_mv == 5450
        await device.client.disconnect()

    async def test_config_refetched_on_update_count_change(
        self, environment: FakeEnvironment
    ) -> None:
        device = make_device(environment)
        await device.update()
        environment.opener.config_name = "Renamed"
        await device.update()
        assert device.config is not None
        assert device.config.name == "Front Door"  # count unchanged, not refetched
        environment.opener.state.config_update_count += 1
        await device.update()
        assert device.config.name == "Renamed"
        await device.client.disconnect()

    async def test_poll_needed_follows_advertisements(self, environment: FakeEnvironment) -> None:
        device = make_device(environment)
        assert device.poll_needed()  # no state yet
        await device.update()
        assert not device.poll_needed()
        device.handle_advertisement(opener_beacon(state_changed=False))
        assert not device.poll_needed()
        device.handle_advertisement(opener_beacon(state_changed=True))
        assert device.poll_needed()
        await device.client.disconnect()


class TestRingDetection:
    async def test_plain_ring_via_state_change_advertisement(
        self, environment: FakeEnvironment
    ) -> None:
        device = make_device(environment)
        rings: list[RingEvent] = []
        device.subscribe_ring(rings.append)
        await device.update()

        # Doorbell rings while idle: the state stays LOCKED but the device
        # advertises a state change.
        device.handle_advertisement(opener_beacon(state_changed=True))
        await device.update()
        assert len(rings) == 1
        assert rings[0].detected_by == "state_transition"
        await device.client.disconnect()

    async def test_no_ring_without_state_change_flag(self, environment: FakeEnvironment) -> None:
        device = make_device(environment)
        rings: list[RingEvent] = []
        device.subscribe_ring(rings.append)
        await device.update()
        await device.update()  # plain re-poll, no advertisement flag
        assert rings == []
        await device.client.disconnect()

    async def test_ring_during_rto(self, environment: FakeEnvironment) -> None:
        opener = environment.opener
        device = make_device(environment)
        rings: list[RingEvent] = []
        device.subscribe_ring(rings.append)
        opener.state.lock_state = LockState.RTO_ACTIVE
        await device.update()

        opener.simulate_ring_during_rto()
        device.handle_advertisement(opener_beacon(state_changed=True))
        await device.update()
        assert len(rings) == 1
        await device.client.disconnect()

    async def test_ring_in_continuous_mode(self, environment: FakeEnvironment) -> None:
        opener = environment.opener
        device = make_device(environment)
        rings: list[RingEvent] = []
        device.subscribe_ring(rings.append)
        opener.state.nuki_state = NukiState.CONTINUOUS_MODE
        await device.update()

        opener.state.lock_state = LockState.OPENING
        opener.state.trigger = Trigger.MANUAL
        device.handle_advertisement(opener_beacon(state_changed=True))
        await device.update()
        assert len(rings) == 1
        await device.client.disconnect()

    async def test_own_action_does_not_ring(self, environment: FakeEnvironment) -> None:
        device = make_device(environment)
        rings: list[RingEvent] = []
        device.subscribe_ring(rings.append)
        await device.update()

        # We open the door ourselves: trigger is SYSTEM, not MANUAL.
        await device.client.lock_action(LockAction.ELECTRIC_STRIKE_ACTUATION)
        device.handle_advertisement(opener_beacon(state_changed=True))
        await device.update()
        assert rings == []
        await device.client.disconnect()

    async def test_ring_from_log_entries(self, environment: FakeEnvironment) -> None:
        opener = environment.opener
        opener.add_lock_action_log_entry()
        device = make_device(environment, security_pin=1234)
        rings: list[RingEvent] = []
        device.subscribe_ring(rings.append)
        await device.update()  # establishes the log baseline
        assert rings == []

        opener.add_doorbell_log_entry(suppressed=True)
        await device.update()
        assert len(rings) == 1
        assert rings[0].detected_by == "log"
        assert rings[0].suppressed is True
        await device.client.disconnect()

    async def test_bad_pin_disables_log_detection(self, environment: FakeEnvironment) -> None:
        device = make_device(environment, security_pin=9999)
        await device.update()
        assert device.security_pin is None
        await device.client.disconnect()

    async def test_transition_and_log_ring_debounced(self, environment: FakeEnvironment) -> None:
        opener = environment.opener
        opener.add_lock_action_log_entry()
        device = make_device(environment, security_pin=1234)
        rings: list[RingEvent] = []
        device.subscribe_ring(rings.append)
        await device.update()

        # A plain ring produces both a state-transition detection and a log
        # entry; only one event may fire.
        opener.simulate_plain_ring()
        device.handle_advertisement(opener_beacon(state_changed=True))
        await device.update()
        assert len(rings) == 1
        await device.client.disconnect()


class TestRingSuppressionAfterStrike:
    async def test_own_strike_suppresses_false_ring(self, environment: FakeEnvironment) -> None:
        """A ring detected right after our electric strike is ignored."""
        opener = environment.opener
        opener.add_lock_action_log_entry()
        device = make_device(environment, security_pin=1234)
        rings: list[RingEvent] = []
        device.subscribe_ring(rings.append)
        await device.update()

        await device.execute_lock_action(LockAction.ELECTRIC_STRIKE_ACTUATION)
        # The strike shorts the doorbell lines (e.g. Urmet 1+1 privacy mode):
        # the opener logs a doorbell recognition and flags a state change.
        opener.state.lock_state = LockState.LOCKED
        opener.add_doorbell_log_entry()
        device.handle_advertisement(opener_beacon(state_changed=True))
        await device.update()
        assert rings == []
        await device.client.disconnect()

    async def test_ring_fires_after_suppression_window(self, environment: FakeEnvironment) -> None:
        """Once the window has passed, rings are reported again."""
        device = make_device(environment)
        rings: list[RingEvent] = []
        device.subscribe_ring(rings.append)
        await device.update()

        await device.execute_lock_action(LockAction.ELECTRIC_STRIKE_ACTUATION)
        assert device._suppress_rings_until > 0
        device._suppress_rings_until = 0.0  # simulate window expiry
        opener = environment.opener
        opener.state.lock_state = LockState.LOCKED
        await device.update()  # observe the relatch (OPEN -> LOCKED, no ring)
        assert rings == []
        device.handle_advertisement(opener_beacon(state_changed=True))
        await device.update()
        assert len(rings) == 1
        await device.client.disconnect()

    async def test_rto_action_does_not_suppress(self, environment: FakeEnvironment) -> None:
        """Only the electric strike arms the suppression window."""
        device = make_device(environment)
        await device.update()
        await device.execute_lock_action(LockAction.ACTIVATE_RTO)
        assert device._suppress_rings_until == 0.0
        await device.client.disconnect()

    async def test_window_uses_configured_strike_timing(self, environment: FakeEnvironment) -> None:
        """The window derives from the configured strike delay + duration."""
        import dataclasses
        import time

        opener = environment.opener
        opener.advanced_config = dataclasses.replace(
            opener.advanced_config,
            electric_strike_delay_ms=5000,
            electric_strike_duration_ms=10000,
        )
        device = make_device(environment)
        await device.update()
        await device.execute_lock_action(LockAction.ELECTRIC_STRIKE_ACTUATION)
        remaining = device._suppress_rings_until - time.monotonic()
        assert 20 <= remaining <= 26  # 5s + 10s + 10s margin
        await device.client.disconnect()
