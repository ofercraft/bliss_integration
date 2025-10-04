# iLink Light Integration for Home Assistant

This repository provides the **iLink Light** custom integration for Home Assistant. It enables local
Bluetooth Low Energy (BLE) control of supported iLink lighting devices via Home Assistant's light
platform, exposing on/off control, brightness management, and built-in lighting scenes.

## Installation

1. Copy the `custom_components/ilink_light` directory into your Home Assistant configuration's
   `custom_components` folder.
2. Restart Home Assistant to load the new integration.
3. From *Settings → Devices & Services*, click **Add Integration** and search for **iLink Light**.

Alternatively, add this repository as a custom repository in HACS and install the integration from
there.

## Configuration

The integration provides a config flow that guides you through pairing new devices. You will be
asked for each light's Bluetooth MAC address and can optionally adjust the standard and fast polling
intervals.

Once configured, entities are created under the `light` platform. You can control brightness as well
as switch between the device's built-in scenes from Home Assistant automations, dashboards, or voice
assistants.

## Troubleshooting

- Ensure your Home Assistant host has Bluetooth hardware that can reach the lights.
- If updates appear delayed, consider lowering the *fast* scan interval in the integration options.
- Re-run the config flow's **Add device** option to pair additional lights.

## License

This project is distributed under the terms of the MIT License. See [`LICENSE`](LICENSE) for details.
