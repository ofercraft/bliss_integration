"""Config flow to configure the Bliss blinds integration."""
from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.components.bluetooth import BluetoothServiceInfoBleak
from homeassistant.config_entries import ConfigEntry, ConfigFlow, OptionsFlow
from homeassistant.const import CONF_DEVICES
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import (
    config_validation as cv,
    device_registry as dr,
    entity_registry as er,
    selector,
)

from .bliss_bt_client import BlissBlindClient
from .const import (
    CONF_MAC,
    CONF_NAME,
    CONF_PASSWORD,
    CONF_RANGE_MAX,
    DEFAULT_PASSWORD,
    DEFAULT_RANGE_MAX,
    DOMAIN,
    LOGGER,
)

CONFIG_ENTRY_TITLE = "Bliss Blinds"
SELECTED_DEVICE = "selected_device"

DEVICE_DATA = {
    CONF_NAME: "",
    CONF_MAC: "",
    CONF_PASSWORD: DEFAULT_PASSWORD,
    CONF_RANGE_MAX: DEFAULT_RANGE_MAX,
}


class BlissConfigFlowHandler(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        self.device_data = DEVICE_DATA.copy()
        self.config_entry: ConfigEntry | None = None

    @staticmethod
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return BlissOptionsFlowHandler(config_entry)

    def _get_existing_entry(self) -> ConfigEntry | None:
        if self.hass is None:
            return None
        for entry in self.hass.config_entries.async_entries(DOMAIN):
            if entry.title == CONFIG_ENTRY_TITLE:
                return entry
        return None

    def _device_exists(self, mac: str) -> bool:
        entry = self._get_existing_entry()
        if entry and mac in entry.data.get(CONF_DEVICES, {}):
            return True
        return False

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        await self.async_set_unique_id(CONFIG_ENTRY_TITLE)
        self._abort_if_unique_id_configured()
        return self.async_create_entry(title=CONFIG_ENTRY_TITLE, data={CONF_DEVICES: {}})

    async def async_step_bluetooth(
        self, discovery_info: BluetoothServiceInfoBleak
    ) -> FlowResult:
        LOGGER.info("Discovered Bliss blind via Bluetooth: %s", discovery_info.address)
        formatted_mac = dr.format_mac(discovery_info.address)

        await self.async_set_unique_id(formatted_mac)
        self._abort_if_unique_id_configured()

        if self._device_exists(formatted_mac):
            return self.async_abort(
                reason="device_already_configured",
                description_placeholders={"dev_name": formatted_mac},
            )

        self.device_data = DEVICE_DATA.copy()
        self.device_data[CONF_MAC] = formatted_mac
        self.device_data[CONF_NAME] = discovery_info.name or formatted_mac
        return await self.async_step_add_device()

    async def async_step_add_device(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            formatted_mac = dr.format_mac(user_input[CONF_MAC])
            if self._device_exists(formatted_mac):
                return self.async_abort(
                    reason="device_already_configured",
                    description_placeholders={"dev_name": formatted_mac},
                )

            client = BlissBlindClient(
                self.hass,
                formatted_mac,
                user_input[CONF_PASSWORD],
                int(user_input[CONF_RANGE_MAX]),
            )
            try:
                await client.ensure_connected()
                await client.disconnect()
            except Exception:
                errors["base"] = "cannot_connect"

            if not errors:
                entry = self._get_existing_entry()
                new_device_data = dict(user_input)
                new_device_data[CONF_MAC] = formatted_mac

                if entry is None:
                    await self.async_set_unique_id(CONFIG_ENTRY_TITLE)
                    return self.async_create_entry(
                        title=CONFIG_ENTRY_TITLE,
                        data={CONF_DEVICES: {formatted_mac: new_device_data}},
                        description_placeholders={"dev_name": new_device_data[CONF_NAME]},
                    )

                new_data = {CONF_DEVICES: dict(entry.data.get(CONF_DEVICES, {}))}
                new_data[CONF_DEVICES][formatted_mac] = new_device_data

                self.hass.config_entries.async_update_entry(entry, data=new_data)
                self.hass.config_entries._async_schedule_save()
                await self.hass.config_entries.async_reload(entry.entry_id)

                return self.async_abort(
                    reason="add_success",
                    description_placeholders={"dev_name": new_device_data[CONF_NAME]},
                )

        data_schema = get_device_schema_add(user_input or self.device_data)
        return self.async_show_form(
            step_id="add_device", data_schema=data_schema, errors=errors
        )


class BlissOptionsFlowHandler(OptionsFlow):
    def __init__(self, config_entry: ConfigEntry) -> None:
        self.config_entry = config_entry
        self.selected_device: str | None = None
        self.device_data = DEVICE_DATA.copy()

    async def async_step_init(self, user_input: dict[str, Any] | None = None):
        if user_input is not None:
            action = user_input.get("action")
            if action == "add_device":
                return await self.async_step_add_device()
            if action == "edit_device":
                return await self.async_step_select_edit_device()
            if action == "remove_device":
                return await self.async_step_remove_device()

        return self.async_show_form(step_id="init", data_schema=CONFIGURE_SCHEMA)

    def _device_exists(self, mac: str) -> bool:
        return mac in self.config_entry.data.get(CONF_DEVICES, {})

    async def async_step_add_device(self, user_input: dict[str, Any] | None = None):
        errors: dict[str, str] = {}

        if user_input is not None:
            formatted_mac = dr.format_mac(user_input[CONF_MAC])
            if self._device_exists(formatted_mac):
                return self.async_abort(
                    reason="device_already_configured",
                    description_placeholders={"dev_name": formatted_mac},
                )

            client = BlissBlindClient(
                self.hass,
                formatted_mac,
                user_input[CONF_PASSWORD],
                int(user_input[CONF_RANGE_MAX]),
            )
            try:
                await client.ensure_connected()
                await client.disconnect()
            except Exception:
                errors["base"] = "cannot_connect"

            if not errors:
                new_data = {CONF_DEVICES: dict(self.config_entry.data.get(CONF_DEVICES, {}))}
                device_data = dict(user_input)
                device_data[CONF_MAC] = formatted_mac
                new_data[CONF_DEVICES][formatted_mac] = device_data

                self.hass.config_entries.async_update_entry(
                    self.config_entry, data=new_data
                )
                self.hass.config_entries._async_schedule_save()
                await self.hass.config_entries.async_reload(self.config_entry.entry_id)

                return self.async_abort(
                    reason="add_success",
                    description_placeholders={"dev_name": device_data[CONF_NAME]},
                )

        data_schema = get_device_schema_add(user_input or self.device_data)
        return self.async_show_form(
            step_id="add_device", data_schema=data_schema, errors=errors
        )

    async def async_step_select_edit_device(
        self, user_input: dict[str, Any] | None = None
    ):
        if user_input is not None:
            self.selected_device = user_input[SELECTED_DEVICE]
            self.device_data = dict(
                self.config_entry.data.get(CONF_DEVICES, {})[self.selected_device]
            )
            return await self.async_step_edit_device()

        devices = {
            mac: data[CONF_NAME]
            for mac, data in self.config_entry.data.get(CONF_DEVICES, {}).items()
        }
        return self.async_show_form(
            step_id="select_edit_device",
            data_schema=get_device_schema_select(devices),
        )

    async def async_step_edit_device(self, user_input: dict[str, Any] | None = None):
        errors: dict[str, str] = {}
        if user_input is not None and self.selected_device is not None:
            new_data = {CONF_DEVICES: dict(self.config_entry.data.get(CONF_DEVICES, {}))}
            entry = new_data[CONF_DEVICES][self.selected_device]
            entry[CONF_NAME] = user_input[CONF_NAME]
            entry[CONF_PASSWORD] = user_input[CONF_PASSWORD]
            entry[CONF_RANGE_MAX] = int(user_input[CONF_RANGE_MAX])

            self.hass.config_entries.async_update_entry(
                self.config_entry, data=new_data
            )
            self.hass.config_entries._async_schedule_save()
            await self.hass.config_entries.async_reload(self.config_entry.entry_id)

            return self.async_abort(
                reason="edit_success",
                description_placeholders={"dev_name": entry[CONF_NAME]},
            )

        return self.async_show_form(
            step_id="edit_device",
            data_schema=get_device_schema_edit(self.device_data),
            description_placeholders={"dev_name": self.device_data[CONF_NAME]},
            errors=errors,
        )

    async def async_step_remove_device(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        if user_input is not None:
            mac = user_input[SELECTED_DEVICE]
            devices = {CONF_DEVICES: dict(self.config_entry.data.get(CONF_DEVICES, {}))}
            device_name = devices[CONF_DEVICES][mac][CONF_NAME]
            devices[CONF_DEVICES].pop(mac)

            self.hass.config_entries.async_update_entry(
                self.config_entry, data=devices
            )
            self.hass.config_entries._async_schedule_save()
            await self._async_remove_device(mac)

            return self.async_abort(
                reason="remove_success",
                description_placeholders={"dev_name": device_name},
            )

        devices = {
            mac: data[CONF_NAME]
            for mac, data in self.config_entry.data.get(CONF_DEVICES, {}).items()
        }
        return self.async_show_form(
            step_id="remove_device",
            data_schema=get_device_schema_select(devices),
        )

    async def _async_remove_device(self, mac: str) -> None:
        device_registry = dr.async_get(self.hass)
        entity_registry = er.async_get(self.hass)
        device_entry = device_registry.async_get_device({(DOMAIN, mac)})
        if device_entry is None:
            return

        for entry in er.async_entries_for_device(entity_registry, device_entry.id, include_disabled_entities=True):
            entity_registry.async_remove(entry.entity_id)

        device_registry.async_remove_device(device_entry.id)


CONF_ACTION = "action"
CONF_ACTIONS = ["add_device", "edit_device", "remove_device"]
CONFIGURE_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_ACTION): selector.SelectSelector(
            selector.SelectSelectorConfig(
                options=CONF_ACTIONS,
                translation_key=CONF_ACTION,
            )
        )
    }
)


def get_device_schema_add(user_input: dict[str, Any]) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(CONF_NAME, default=user_input[CONF_NAME]): cv.string,
            vol.Required(CONF_MAC, default=user_input[CONF_MAC]): cv.string,
            vol.Required(CONF_PASSWORD, default=user_input[CONF_PASSWORD]): cv.string,
            vol.Optional(
                CONF_RANGE_MAX,
                default=user_input[CONF_RANGE_MAX],
            ): vol.All(vol.Coerce(int), vol.Range(min=10, max=10000)),
        }
    )


def get_device_schema_edit(user_input: dict[str, Any]) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(CONF_NAME, default=user_input[CONF_NAME]): cv.string,
            vol.Required(CONF_PASSWORD, default=user_input[CONF_PASSWORD]): cv.string,
            vol.Optional(
                CONF_RANGE_MAX,
                default=user_input[CONF_RANGE_MAX],
            ): vol.All(vol.Coerce(int), vol.Range(min=10, max=10000)),
        }
    )


def get_device_schema_select(devices: dict[str, Any]) -> vol.Schema:
    options = {mac: f"{name} ({mac})" for mac, name in devices.items()}
    return vol.Schema({vol.Required(SELECTED_DEVICE): vol.In(options)})
