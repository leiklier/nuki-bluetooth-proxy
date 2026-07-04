# Nuki Opener BLE for Home Assistant

Control a [Nuki Opener](https://nuki.io/en/opener/) from Home Assistant **directly over
Bluetooth** — no Nuki Bridge and no dedicated ESP32 required. If you have
[ESPHome Bluetooth proxies](https://esphome.io/components/bluetooth_proxy.html) (or a local
Bluetooth adapter) within range of the Opener, this integration speaks the native Nuki BLE
protocol through them.

Inspired by [nuki_hub](https://github.com/technyon/nuki_hub); the protocol is implemented
from the official Nuki Opener BLE API v1.1.0 specification and unit-tested against the test
vectors published in it. Prior art that helped along the way:
[pyNukiBT](https://github.com/ronengr/pyNukiBT) and
[NukiBleEsp32](https://github.com/I-Connect/NukiBleEsp32).

## Features

- **Lock entity** — the main control:
  - `lock.unlock` activates **ring-to-open** (the door buzzes open on the next ring)
  - `lock.lock` deactivates ring-to-open
  - `lock.open` fires the **electric strike** and opens the door immediately
- **Switches** for ring-to-open and **continuous mode**
- **Open door button** for dashboards
- **Doorbell event entity** — fires when someone rings, so you can trigger automations
  (notifications, announcements, conditional auto-open, …)
- **Sensors**: opener state, mode, last ring timestamp, battery voltage and Bluetooth
  signal (diagnostic), battery-critical and door-sensor binary sensors
- **Diagnostics download** with secrets redacted

State updates are near-instant without draining the Opener's batteries: the integration
listens passively to the Opener's Bluetooth beacons and only connects when the beacon
signals that something changed.

### Doorbell ring detection

Rings are detected from state transitions (the same heuristics nuki_hub uses). If you also
enter your Nuki **security PIN** in the integration options, rings are additionally
confirmed from the Opener's activity log, which is more reliable and reports whether the
ring was suppressed (Ring Suppression).

## Requirements

- A Nuki Opener, set up and calibrated with the Nuki app
- Home Assistant 2025.3 or newer
- An [ESPHome Bluetooth proxy](https://esphome.io/components/bluetooth_proxy.html)
  (`active: true` is required) or a local Bluetooth adapter within range of the Opener

## Installation

### HACS (recommended)

1. HACS → three-dot menu → *Custom repositories* → add this repository as type
   *Integration*.
2. Install **Nuki Opener BLE** and restart Home Assistant.

### Manual

Copy `custom_components/nuki_opener_ble` into your Home Assistant `custom_components`
directory and restart.

## Setup

1. Home Assistant should discover the Opener automatically (*Settings → Devices &
   services*). Otherwise add the **Nuki Opener BLE** integration manually.
2. Make sure pairing is enabled in the Nuki app (*Settings → Features & configuration →
   Button & LED*).
3. Put the Opener into pairing mode: **press and hold its button for 5 seconds** until the
   LED ring lights up.
4. Choose **Pair a new authorization** in the setup dialog and submit.

Home Assistant registers itself as a *bridge-type* authorization, which allows switching
continuous mode without a PIN. The Nuki app will show "Home Assistant" in the Opener's
user list; you can revoke access there at any time.

If you already have credentials (for example exported from nuki_hub or pyNukiBT), choose
**Enter existing credentials** instead — no re-pairing needed.

### Options

- **Security PIN** — the PIN configured in the Nuki app. Optional; enables log-based ring
  detection. The PIN is verified against the device when you save it.

## How it works

The Nuki Opener advertises an iBeacon whose last byte flags unfetched state changes. The
integration subscribes passively to those advertisements (essentially free over ESPHome
proxies), and when the flag is set — or an action is requested — it connects, exchanges
encrypted commands, and disconnects again. Staying disconnected matters: while a client is
connected, the Opener stops advertising, which would break ring detection and drain its
batteries.

The BLE protocol layer (`custom_components/nuki_opener_ble/nuki/`) is transport-agnostic
and Home-Assistant-free: X25519 key exchange, XSalsa20-Poly1305 message encryption,
HMAC-SHA256 authenticators and CRC-16/CCITT framing, verified against the official
specification's test vectors.

## Troubleshooting

- **The opener is not discovered** — check that a proxy/adapter is in range
  (*Settings → Devices & services → Bluetooth* lists reachable devices), and that the
  proxy has `bluetooth_proxy: active: true`.
- **Pairing fails with "not in pairing mode"** — hold the Opener's button for a full
  5 seconds; the LED ring must light up continuously. Also confirm pairing is enabled in
  the Nuki app.
- **Actions are slow or time out** — BLE connections through a proxy typically take 1–3
  seconds. If it is consistently worse, check the Bluetooth signal diagnostic sensor and
  consider moving a proxy closer.

## Development

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements_test.txt ruff mypy
.venv/bin/pytest          # full test suite
.venv/bin/ruff check .    # lint
.venv/bin/mypy            # strict typing on the protocol library
```

The test suite includes a full device-side simulation of the Opener's BLE protocol, so
config flow, pairing, polling, actions, and doorbell events are tested end-to-end without
hardware.

## Disclaimer

This is a third-party project, not affiliated with or endorsed by Nuki Home Solutions
GmbH. Use at your own risk.
