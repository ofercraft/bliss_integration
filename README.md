# Bliss Blinds for Home Assistant

Local Bluetooth control and status monitoring for Bliss blinds.

This fork keeps the BLE connection active with a three-second status heartbeat, retries failed status and battery reads across clean reconnects, detects unsolicited disconnects, and continuously refreshes position so Home Assistant tracks remote and app-driven movement.

## Installation

Copy `custom_components/bliss` into Home Assistant's `custom_components` directory and restart Home Assistant. The integration discovers supported blinds advertising the Bliss service UUID.

## Reliability behavior

- Persistent BLE connection through a Home Assistant Bluetooth adapter or proxy
- Three-second read heartbeat to keep the blind awake and state current
- Three attempts for failed or timed-out reads
- Automatic clean reconnect between attempts
- One-second movement tracking while a blind is changing position
- Immediate unavailable state only after all retry attempts fail

This repository is based on the original [`donandren/bliss_integration`](https://github.com/donandren/bliss_integration) project and contains local reliability and battery-status improvements.
