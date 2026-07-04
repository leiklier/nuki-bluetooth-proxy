"""Constants for the Nuki Opener BLE integration."""

from typing import Final

DOMAIN: Final = "nuki_opener_ble"

CONF_CREDENTIALS: Final = "credentials"
CONF_SECURITY_PIN: Final = "security_pin"

DEFAULT_NAME: Final = "Nuki Opener"
MANUFACTURER: Final = "Nuki Home Solutions GmbH"
MODEL: Final = "Opener"

# Poll at least this often even without a state-change advertisement.
FALLBACK_POLL_INTERVAL: Final = 3 * 3600.0
