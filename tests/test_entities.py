"""Tests for the entity platforms against the simulated opener."""

from homeassistant.const import (
    ATTR_ENTITY_ID,
    SERVICE_TURN_OFF,
    SERVICE_TURN_ON,
    STATE_OFF,
    STATE_ON,
    STATE_UNKNOWN,
)
from homeassistant.core import HomeAssistant, State
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    mock_restore_cache,
)

from custom_components.nuki_opener_ble.nuki.const import (
    LockAction,
    LockState,
    NukiState,
)

from .bluetooth_utils import inject_opener_advertisement
from .conftest import setup_entry
from .nuki.fake_device import FakeEnvironment

LOCK_ENTITY = "lock.front_door"
RTO_SWITCH = "switch.front_door_ring_to_open"
CM_SWITCH = "switch.front_door_continuous_mode"
OPEN_BUTTON = "button.front_door_open_door"
STATE_SENSOR = "sensor.front_door_state"
MODE_SENSOR = "sensor.front_door_mode"
LAST_RING_SENSOR = "sensor.front_door_last_ring"
BATTERY_CRITICAL = "binary_sensor.front_door_battery_critical"
DOORBELL_EVENT = "event.front_door_doorbell"
DOORBELL_SWITCH = "switch.front_door_doorbell_notifications"


async def test_entity_states_after_setup(
    hass: HomeAssistant,
    enable_bluetooth: None,
    environment: FakeEnvironment,
    config_entry: MockConfigEntry,
) -> None:
    """All entities reflect the initial opener state."""
    await setup_entry(hass, config_entry)

    assert hass.states.get(LOCK_ENTITY).state == "locked"
    assert hass.states.get(RTO_SWITCH).state == STATE_OFF
    assert hass.states.get(CM_SWITCH).state == STATE_OFF
    assert hass.states.get(STATE_SENSOR).state == "locked"
    assert hass.states.get(MODE_SENSOR).state == "door_mode"
    assert hass.states.get(BATTERY_CRITICAL).state == STATE_OFF
    assert hass.states.get(DOORBELL_EVENT).state == STATE_UNKNOWN
    assert hass.states.get(DOORBELL_SWITCH).state == STATE_ON
    assert hass.states.get(LAST_RING_SENSOR).state == STATE_UNKNOWN


async def test_lock_unlock_activates_rto(
    hass: HomeAssistant,
    enable_bluetooth: None,
    environment: FakeEnvironment,
    config_entry: MockConfigEntry,
) -> None:
    """Unlocking the lock activates ring-to-open."""
    await setup_entry(hass, config_entry)
    await hass.services.async_call("lock", "unlock", {ATTR_ENTITY_ID: LOCK_ENTITY}, blocking=True)
    assert environment.opener.received_lock_actions == [LockAction.ACTIVATE_RTO]
    assert hass.states.get(LOCK_ENTITY).state == "unlocked"
    assert hass.states.get(RTO_SWITCH).state == STATE_ON
    assert hass.states.get(STATE_SENSOR).state == "rto_active"

    await hass.services.async_call("lock", "lock", {ATTR_ENTITY_ID: LOCK_ENTITY}, blocking=True)
    assert environment.opener.received_lock_actions[-1] == LockAction.DEACTIVATE_RTO
    assert hass.states.get(LOCK_ENTITY).state == "locked"


async def test_lock_open_fires_electric_strike(
    hass: HomeAssistant,
    enable_bluetooth: None,
    environment: FakeEnvironment,
    config_entry: MockConfigEntry,
) -> None:
    """lock.open triggers the electric strike."""
    await setup_entry(hass, config_entry)
    await hass.services.async_call("lock", "open", {ATTR_ENTITY_ID: LOCK_ENTITY}, blocking=True)
    assert environment.opener.received_lock_actions == [LockAction.ELECTRIC_STRIKE_ACTUATION]
    assert hass.states.get(LOCK_ENTITY).state == "open"


async def test_open_button(
    hass: HomeAssistant,
    enable_bluetooth: None,
    environment: FakeEnvironment,
    config_entry: MockConfigEntry,
) -> None:
    """The open-door button fires the electric strike."""
    await setup_entry(hass, config_entry)
    await hass.services.async_call("button", "press", {ATTR_ENTITY_ID: OPEN_BUTTON}, blocking=True)
    assert environment.opener.received_lock_actions == [LockAction.ELECTRIC_STRIKE_ACTUATION]


async def test_continuous_mode_switch(
    hass: HomeAssistant,
    enable_bluetooth: None,
    environment: FakeEnvironment,
    config_entry: MockConfigEntry,
) -> None:
    """The continuous mode switch sends CM lock actions."""
    await setup_entry(hass, config_entry)
    await hass.services.async_call(
        "switch", SERVICE_TURN_ON, {ATTR_ENTITY_ID: CM_SWITCH}, blocking=True
    )
    assert environment.opener.received_lock_actions == [LockAction.ACTIVATE_CM]
    assert hass.states.get(CM_SWITCH).state == STATE_ON
    assert hass.states.get(MODE_SENSOR).state == "continuous_mode"

    await hass.services.async_call(
        "switch", SERVICE_TURN_OFF, {ATTR_ENTITY_ID: CM_SWITCH}, blocking=True
    )
    assert environment.opener.received_lock_actions[-1] == LockAction.DEACTIVATE_CM
    assert hass.states.get(CM_SWITCH).state == STATE_OFF


async def test_doorbell_ring_event(
    hass: HomeAssistant,
    enable_bluetooth: None,
    environment: FakeEnvironment,
    config_entry: MockConfigEntry,
) -> None:
    """A plain ring (state-change beacon without state change) fires the event."""
    await setup_entry(hass, config_entry)
    assert hass.states.get(DOORBELL_EVENT).state == STATE_UNKNOWN

    inject_opener_advertisement(hass, state_changed=True)
    await hass.async_block_till_done(wait_background_tasks=True)

    event_state = hass.states.get(DOORBELL_EVENT)
    assert event_state.state != STATE_UNKNOWN
    assert event_state.attributes["event_type"] == "ring"
    assert hass.states.get(LAST_RING_SENSOR).state != STATE_UNKNOWN


async def test_doorbell_notifications_switch_swallows_ring(
    hass: HomeAssistant,
    enable_bluetooth: None,
    environment: FakeEnvironment,
    config_entry: MockConfigEntry,
) -> None:
    """With doorbell notifications off, a ring does not fire the event."""
    await setup_entry(hass, config_entry)
    await hass.services.async_call(
        "switch", SERVICE_TURN_OFF, {ATTR_ENTITY_ID: DOORBELL_SWITCH}, blocking=True
    )
    assert hass.states.get(DOORBELL_SWITCH).state == STATE_OFF

    inject_opener_advertisement(hass, state_changed=True)
    await hass.async_block_till_done(wait_background_tasks=True)

    # The ring is still detected (the last-ring sensor updates) but the
    # doorbell event stays silent, so no HomeKit doorbell notification fires.
    assert hass.states.get(LAST_RING_SENSOR).state != STATE_UNKNOWN
    assert hass.states.get(DOORBELL_EVENT).state == STATE_UNKNOWN


async def test_doorbell_notifications_state_restored(
    hass: HomeAssistant,
    enable_bluetooth: None,
    environment: FakeEnvironment,
    config_entry: MockConfigEntry,
) -> None:
    """The switch restores its off state across a restart and keeps gating."""
    mock_restore_cache(hass, [State(DOORBELL_SWITCH, STATE_OFF)])
    await setup_entry(hass, config_entry)
    assert hass.states.get(DOORBELL_SWITCH).state == STATE_OFF

    inject_opener_advertisement(hass, state_changed=True)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert hass.states.get(DOORBELL_EVENT).state == STATE_UNKNOWN


async def test_advertisement_triggered_poll_updates_state(
    hass: HomeAssistant,
    enable_bluetooth: None,
    environment: FakeEnvironment,
    config_entry: MockConfigEntry,
) -> None:
    """A state-change beacon triggers a poll that picks up external changes."""
    await setup_entry(hass, config_entry)
    # RTO activated externally, e.g. from the Nuki app.
    environment.opener.state.lock_state = LockState.RTO_ACTIVE
    environment.opener.state.nuki_state = NukiState.DOOR_MODE

    inject_opener_advertisement(hass, state_changed=True)
    await hass.async_block_till_done(wait_background_tasks=True)

    assert hass.states.get(LOCK_ENTITY).state == "unlocked"
    assert hass.states.get(RTO_SWITCH).state == STATE_ON


@pytest.mark.usefixtures("entity_registry_enabled_by_default")
async def test_diagnostic_sensors(
    hass: HomeAssistant,
    enable_bluetooth: None,
    environment: FakeEnvironment,
    config_entry: MockConfigEntry,
) -> None:
    """Battery voltage and RSSI diagnostic sensors report values."""
    await setup_entry(hass, config_entry)
    assert hass.states.get("sensor.front_door_battery_voltage").state == "5450"
    assert hass.states.get("sensor.front_door_bluetooth_signal").state == "-60"
