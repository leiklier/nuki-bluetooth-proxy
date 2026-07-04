"""High-level state tracking for a paired Nuki Opener.

Combines passive advertisement data with polled state, keeps the latest
snapshot, and detects doorbell rings from state transitions (the same
heuristics nuki_hub uses) and — when a security PIN is available — from the
device's activity log.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
import logging

from .advertisement import NukiAdvertisement, parse_manufacturer_data
from .client import NukiOpenerClient
from .const import LockState, LogEntryType, NukiState, Trigger
from .errors import NukiBadPinError, NukiError
from .messages import BatteryReport, LogEntry, OpenerConfig, OpenerState

_LOGGER = logging.getLogger(__name__)

# Re-read the battery report at most this often (seconds).
BATTERY_REPORT_INTERVAL = 3600.0
_OPEN_STATES = (LockState.OPEN, LockState.OPENING)


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
        self.battery: BatteryReport | None = None
        self.last_ring: RingEvent | None = None

        self._poll_pending = True
        self._adv_signaled_change = False
        self._last_battery_monotonic: float | None = None
        self._last_log_index: int | None = None
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

        if self._should_refresh_battery(now_monotonic):
            try:
                self.battery = await self.client.get_battery_report()
                self._last_battery_monotonic = now_monotonic
            except NukiError as err:
                _LOGGER.debug("Battery report unavailable: %s", err)

        if self.security_pin is not None:
            await self._detect_ring_from_log()

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
            self._fire_ring(
                RingEvent(
                    timestamp=_log_timestamp(entry),
                    detected_by="log",
                    suppressed=entry.doorbell.doorbell_suppressed if entry.doorbell else None,
                )
            )


def _log_timestamp(entry: LogEntry) -> datetime:
    # Log timestamps are device-local; rings we care about are recent, so the
    # wall clock is a better anchor than converting the device timezone.
    return datetime.now(UTC)
