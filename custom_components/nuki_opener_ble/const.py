"""Constants for the Nuki Opener BLE integration."""

from typing import Final

DOMAIN: Final = "nuki_opener_ble"

CONF_CREDENTIALS: Final = "credentials"
CONF_SECURITY_PIN: Final = "security_pin"
CONF_LOCK_BEHAVIOR: Final = "lock_behavior"

LOCK_BEHAVIOR_AUTO: Final = "auto"
LOCK_BEHAVIOR_RING_TO_OPEN: Final = "ring_to_open"
LOCK_BEHAVIOR_BUZZER: Final = "buzzer"
LOCK_BEHAVIORS: Final = [
    LOCK_BEHAVIOR_AUTO,
    LOCK_BEHAVIOR_RING_TO_OPEN,
    LOCK_BEHAVIOR_BUZZER,
]

DEFAULT_NAME: Final = "Nuki Opener"
MANUFACTURER: Final = "Nuki Home Solutions GmbH"
MODEL: Final = "Opener"

# Poll at least this often even without a state-change advertisement.
FALLBACK_POLL_INTERVAL: Final = 3 * 3600.0
