"""High-level state tracking for a paired Nuki Opener.

Combines passive advertisement data with polled state, keeps the latest
snapshot, and detects doorbell rings from state transitions (the same
heuristics nuki_hub uses) and — when a security PIN is available — from the
device's activity log.
"""

from __future__ import annotations

from collections.abc import Callable
import dataclasses
from dataclasses import dataclass
from datetime import UTC, datetime
import logging
import time
from typing import Any

from .advertisement import NukiAdvertisement, parse_manufacturer_data
from .client import NukiOpenerClient
from .const import LockAction, LockState, LogEntryType, NukiState, StatusCode, Trigger
from .errors import NukiBadPinError, NukiError
from .messages import AdvancedConfig, BatteryReport, LogEntry, OpenerConfig, OpenerState

_LOGGER = logging.getLogger(__name__)

# Re-read the battery report at most this often (seconds).
BATTERY_REPORT_INTERVAL = 3600.0
_OPEN_STATES = (LockState.OPEN, LockState.OPENING)

# Ignore ring detections for this long after firing the electric strike
# ourselves: on some intercom wirings (e.g. Urmet 1+1 in privacy mode) the
# strike shorts the very lines the opener's doorbell detection listens on,
# producing a false ring. Used as fallback/margin around the configured
# strike delay + duration.
RING_SUPPRESSION_FALLBACK = 15.0
RING_SUPPRESSION_MARGIN = 10.0


@dataclass(frozen=True, slots=True)
class RingEvent:
    """A detected doorbell ring."""

    timestamp: datetime
    detected_by: str  # "state_transition" or "log"
    suppressed: bool | None = None


class NukiOpenerDevice:
    """Tracks the state of one Nuki Opener."""

    def __init__(
        self,
        client: NukiOpenerClient,
        security_pin: int | None = None,
    ) -> None:
        self.client = client
        self.security_pin = security_pin
        self.state: OpenerState | None = None
        self.config: OpenerConfig | None = None
        self.advanced_config: AdvancedConfig | None = None
        self.battery: BatteryReport | None = None
        self.last_ring: RingEvent | None = None

        self._poll_pending = True
        self._adv_signaled_change = False
        self._last_battery_monotonic: float | None = None
        self._last_log_index: int | None = None
        self._suppress_rings_until = 0.0
        self._ring_callbacks: list[Callable[[RingEvent], None]] = []

        client.state_callback = self._on_unsolicited_state

    # --- advertisements ----------------------------------------------------

    def handle_advertisement(self, manufacturer_data: dict[int, bytes]) -> None:
        """Process advertisement manufacturer data from the device."""
        adv: NukiAdvertisement | None = parse_manufacturer_data(manufacturer_data)
        if adv is None:
            return
        if adv.state_changed or self.state is None:
            self._poll_pending = True
            self._adv_signaled_change = adv.state_changed

    def poll_needed(self, seconds_since_last_poll: float | None = None) -> bool:
        """Whether the device should be polled now."""
        return self._poll_pending or self.state is None

    # --- polling -----------------------------------------------------------

    async def update(self, now_monotonic: float | None = None) -> None:
        """Poll the device state and derived data."""
        previous = self.state
        adv_signaled_change = self._adv_signaled_change
        state = await self.client.get_state()
        self._poll_pending = False
        self._adv_signaled_change = False
        self.state = state

        self._detect_ring_from_transition(previous, state, adv_signaled_change)

        if self.config is None or (
            previous is not None
            and state.config_update_count is not None
            and previous.config_update_count != state.config_update_count
        ):
            self.config = await self.client.get_config()
            self.advanced_config = await self.client.get_advanced_config()

        if self._should_refresh_battery(now_monotonic):
            try:
                self.battery = await self.client.get_battery_report()
                self._last_battery_monotonic = now_monotonic
            except NukiError as err:
                _LOGGER.debug("Battery report unavailable: %s", err)

        if self.security_pin is not None:
            await self._detect_ring_from_log()

    async def execute_lock_action(
        self, action: LockAction, name_suffix: str | None = None
    ) -> StatusCode:
        """Execute a lock action, tracking self-inflicted ring windows.

        Firing the electric strike can trigger the opener's own doorbell
        detection on wirings where the strike shorts the doorbell lines, so
        ring detections are suppressed while the strike may be active.
        """
        if action == LockAction.ELECTRIC_STRIKE_ACTUATION:
            self._extend_ring_suppression()
        status = await self.client.lock_action(action, name_suffix=name_suffix)
        if action == LockAction.ELECTRIC_STRIKE_ACTUATION:
            # Restart the window from completion; the false ring may only be
            # detected (and logged) while the strike is releasing.
            self._extend_ring_suppression()
        return status

    def _ring_suppression_window(self) -> float:
        if (config := self.advanced_config) is not None:
            return (
                config.electric_strike_delay_ms + config.electric_strike_duration_ms
            ) / 1000 + RING_SUPPRESSION_MARGIN
        return RING_SUPPRESSION_FALLBACK

    def _extend_ring_suppression(self) -> None:
        self._suppress_rings_until = time.monotonic() + self._ring_suppression_window()

    async def change_advanced_config(self, **changes: Any) -> None:
        """Change advanced-config fields (requires the security PIN).

        Read-modify-write against a freshly fetched configuration so that
        concurrent changes made in the Nuki app are never overwritten.
        """
        if self.security_pin is None:
            raise NukiBadPinError("a security PIN is required to change settings")
        current = await self.client.get_advanced_config()
        updated = dataclasses.replace(current, **changes)
        await self.client.set_advanced_config(updated, self.security_pin)
        self.advanced_config = updated

    def _should_refresh_battery(self, now_monotonic: float | None) -> bool:
        if self.battery is None:
            return True
        if now_monotonic is None or self._last_battery_monotonic is None:
            return False
        return now_monotonic - self._last_battery_monotonic >= BATTERY_REPORT_INTERVAL

    def _on_unsolicited_state(self, state: OpenerState) -> None:
        # Sent by the device while an action completes (e.g. RTO activating).
        self.state = state

    # --- ring detection ----------------------------------------------------

    def subscribe_ring(self, callback: Callable[[RingEvent], None]) -> Callable[[], None]:
        """Register a callback fired when a doorbell ring is detected."""
        self._ring_callbacks.append(callback)

        def _unsubscribe() -> None:
            self._ring_callbacks.remove(callback)

        return _unsubscribe

    def _fire_ring(self, event: RingEvent) -> None:
        if time.monotonic() < self._suppress_rings_until:
            _LOGGER.debug("Ignoring ring detected while our own electric strike may be active")
            return
        # Debounce: transition- and log-based detection may see the same ring.
        if (
            self.last_ring is not None
            and (event.timestamp - self.last_ring.timestamp).total_seconds() < 30
            and event.detected_by != self.last_ring.detected_by
        ):
            self.last_ring = event
            return
        self.last_ring = event
        _LOGGER.debug("Doorbell ring detected (%s)", event.detected_by)
        for callback in self._ring_callbacks:
            callback(event)

    def _detect_ring_from_transition(
        self, previous: OpenerState | None, state: OpenerState, adv_signaled_change: bool
    ) -> None:
        """Detect a doorbell ring by comparing consecutive states.

        Mirrors nuki_hub's heuristics: the electric strike firing with a
        manual trigger means someone rang while RTO or continuous mode was
        active; a state-change advertisement without any actual change means
        the bell rang while the opener stayed idle.
        """
        if previous is None:
            return
        ring = False
        if (
            (
                state.nuki_state == NukiState.CONTINUOUS_MODE
                and state.trigger == Trigger.MANUAL
                and state.lock_state in _OPEN_STATES
                and previous.lock_state not in _OPEN_STATES
            )
            or (
                state.trigger == Trigger.MANUAL
                and state.lock_state in _OPEN_STATES
                and previous.lock_state == LockState.RTO_ACTIVE
            )
            or (
                adv_signaled_change
                and state.lock_state == LockState.LOCKED
                and previous.lock_state == LockState.LOCKED
                and previous.nuki_state == state.nuki_state
            )
        ):
            ring = True
        if ring:
            self._fire_ring(RingEvent(timestamp=datetime.now(UTC), detected_by="state_transition"))

    async def _detect_ring_from_log(self) -> None:
        """Detect rings from the device log (requires the security PIN)."""
        assert self.security_pin is not None
        try:
            entries = await self.client.get_log_entries(self.security_pin, count=5)
        except NukiBadPinError:
            _LOGGER.warning("Security PIN rejected; disabling log-based ring detection")
            self.security_pin = None
            return
        except NukiError as err:
            _LOGGER.debug("Could not read log entries: %s", err)
            return
        if not entries:
            return
        newest_index = max(entry.index for entry in entries)
        if self._last_log_index is None:
            # First read: only establish the baseline.
            self._last_log_index = newest_index
            return
        new_rings = [
            entry
            for entry in entries
            if entry.index > self._last_log_index
            and entry.type == LogEntryType.DOORBELL_RECOGNITION
        ]
        self._last_log_index = newest_index
        for entry in sorted(new_rings, key=lambda entry: entry.index):
            if self._is_strike_echo(entry, entries):
                _LOGGER.debug(
                    "Ignoring ring log entry %d: logged right after an electric "
                    "strike (wiring echo)",
                    entry.index,
                )
                continue
            self._fire_ring(
                RingEvent(
                    timestamp=_log_timestamp(entry),
                    detected_by="log",
                    suppressed=entry.doorbell.doorbell_suppressed if entry.doorbell else None,
                )
            )

    def _is_strike_echo(self, ring: LogEntry, entries: list[LogEntry]) -> bool:
        """Whether a ring log entry is the echo of an electric strike.

        On wirings where the strike shorts the doorbell lines (e.g. Urmet 1+1
        in privacy mode), every buzz — also one triggered from the Nuki app or
        a fob — is followed by a false doorbell-recognition entry. Such an
        entry appears at (not before) the strike entry, within the strike's
        active window, measured with the device's own log clock.
        """
        if ring.timestamp is None:
            return False
        window = self._ring_suppression_window()
        for entry in entries:
            if (
                entry.type == LogEntryType.LOCK_ACTION
                and entry.data
                and entry.data[0] == LockAction.ELECTRIC_STRIKE_ACTUATION
                and entry.timestamp is not None
                and 0 <= (ring.timestamp - entry.timestamp).total_seconds() <= window
            ):
                return True
        return False


def _log_timestamp(entry: LogEntry) -> datetime:
    # Log timestamps are device-local; rings we care about are recent, so the
    # wall clock is a better anchor than converting the device timezone.
    return datetime.now(UTC)
