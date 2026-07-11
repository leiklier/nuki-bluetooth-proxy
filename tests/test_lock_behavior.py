"""Tests for the lock entity's buzzer behavior."""

from homeassistant.const import ATTR_ENTITY_ID, STATE_ON
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.nuki_opener_ble.const import (
    CONF_LOCK_BEHAVIOR,
    LOCK_BEHAVIOR_BUZZER,
    LOCK_BEHAVIOR_RING_TO_OPEN,
)
from custom_components.nuki_opener_ble.nuki.const import LockAction, LockState

from .bluetooth_utils import inject_opener_advertisement
from .conftest import setup_entry
from .nuki.fake_device import FakeEnvironment

LOCK_ENTITY = "lock.front_door"
RTO_SWITCH = "switch.front_door_ring_to_open"
CM_SWITCH = "switch.front_door_continuous_mode"


async def test_buzzer_mode_via_option(
    hass: HomeAssistant,
    enable_bluetooth: None,
    environment: FakeEnvironment,
    config_entry: MockConfigEntry,
) -> None:
    """With the buzzer option set, unlock fires the electric strike."""
    config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        config_entry, options={CONF_LOCK_BEHAVIOR: LOCK_BEHAVIOR_BUZZER}
    )
    await setup_entry(hass, config_entry)

    assert hass.states.get(LOCK_ENTITY).state == "locked"
    await hass.services.async_call("lock", "unlock", {ATTR_ENTITY_ID: LOCK_ENTITY}, blocking=True)
    assert environment.opener.received_lock_actions == [LockAction.ELECTRIC_STRIKE_ACTUATION]
    # The strike is running; the opener reports the door as open.
    assert hass.states.get(LOCK_ENTITY).state == "open"

    # The opener relatches by itself and advertises the state change.
    environment.opener.state.lock_state = LockState.LOCKED
    inject_opener_advertisement(hass, state_changed=True)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert hass.states.get(LOCK_ENTITY).state == "locked"


async def test_buzzer_mode_lock_is_noop(
    hass: HomeAssistant,
    enable_bluetooth: None,
    environment: FakeEnvironment,
    config_entry: MockConfigEntry,
) -> None:
    """Locking in buzzer mode sends nothing to the opener."""
    config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        config_entry, options={CONF_LOCK_BEHAVIOR: LOCK_BEHAVIOR_BUZZER}
    )
    await setup_entry(hass, config_entry)
    await hass.services.async_call("lock", "lock", {ATTR_ENTITY_ID: LOCK_ENTITY}, blocking=True)
    assert environment.opener.received_lock_actions == []
    assert hass.states.get(LOCK_ENTITY).state == "locked"


async def test_auto_mode_without_rto_capability(
    hass: HomeAssistant,
    enable_bluetooth: None,
    environment: FakeEnvironment,
    config_entry: MockConfigEntry,
) -> None:
    """With capability door-opening-only, auto resolves to buzzer and hides RTO."""
    environment.opener.capabilities = 0x00  # only door opening possible
    await setup_entry(hass, config_entry)

    await hass.services.async_call("lock", "unlock", {ATTR_ENTITY_ID: LOCK_ENTITY}, blocking=True)
    assert environment.opener.received_lock_actions == [LockAction.ELECTRIC_STRIKE_ACTUATION]
    # The pointless ring-to-open switch is not created; continuous mode stays.
    assert hass.states.get(RTO_SWITCH) is None
    assert hass.states.get(CM_SWITCH) is not None


async def test_forced_ring_to_open_despite_capability(
    hass: HomeAssistant,
    enable_bluetooth: None,
    environment: FakeEnvironment,
    config_entry: MockConfigEntry,
) -> None:
    """An explicit ring_to_open option overrides the capability heuristic."""
    environment.opener.capabilities = 0x00
    config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        config_entry, options={CONF_LOCK_BEHAVIOR: LOCK_BEHAVIOR_RING_TO_OPEN}
    )
    await setup_entry(hass, config_entry)
    await hass.services.async_call("lock", "unlock", {ATTR_ENTITY_ID: LOCK_ENTITY}, blocking=True)
    assert environment.opener.received_lock_actions == [LockAction.ACTIVATE_RTO]


async def test_auto_mode_with_rto_capability_keeps_rto_semantics(
    hass: HomeAssistant,
    enable_bluetooth: None,
    environment: FakeEnvironment,
    config_entry: MockConfigEntry,
) -> None:
    """With RTO available, auto keeps the ring-to-open lock semantics."""
    await setup_entry(hass, config_entry)
    await hass.services.async_call("lock", "unlock", {ATTR_ENTITY_ID: LOCK_ENTITY}, blocking=True)
    assert environment.opener.received_lock_actions == [LockAction.ACTIVATE_RTO]
    assert hass.states.get(RTO_SWITCH).state == STATE_ON


async def test_state_is_fresh_right_after_service_call(
    hass: HomeAssistant,
    enable_bluetooth: None,
    environment: FakeEnvironment,
    config_entry: MockConfigEntry,
) -> None:
    """Entities reflect an action's outcome without waiting for a beacon poll.

    Real hardware does not always push OPENER_STATES while an action runs;
    without the post-action refresh the entity would show the old state until
    the next beacon-triggered poll (10+ seconds with the debouncer cooldown).
    """
    environment.opener.omit_lock_state_update = True
    await setup_entry(hass, config_entry)

    await hass.services.async_call("lock", "unlock", {ATTR_ENTITY_ID: LOCK_ENTITY}, blocking=True)
    assert hass.states.get(LOCK_ENTITY).state == "unlocked"
    assert hass.states.get(RTO_SWITCH).state == STATE_ON

    await hass.services.async_call("lock", "lock", {ATTR_ENTITY_ID: LOCK_ENTITY}, blocking=True)
    assert hass.states.get(LOCK_ENTITY).state == "locked"
    assert environment.opener.received_lock_actions == [
        LockAction.ACTIVATE_RTO,
        LockAction.DEACTIVATE_RTO,
    ]
