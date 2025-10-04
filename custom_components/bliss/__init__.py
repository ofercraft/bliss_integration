"""Support for Bliss blinds."""
from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_DEVICES
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.helpers.device_registry import DeviceEntry

from .const import CONF_MAC, CONF_NAME, DOMAIN, LOGGER, PLATFORMS
from .coordinator import BlissBlindCoordinator


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up a Bliss config entry."""
    LOGGER.debug("Setting up configuration for Bliss blinds")
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN].setdefault(CONF_DEVICES, {})

    for device_id, conf in entry.data.get(CONF_DEVICES, {}).items():
        device_registry = dr.async_get(hass)
        device = device_registry.async_get_or_create(
            config_entry_id=entry.entry_id,
            identifiers={(DOMAIN, conf[CONF_MAC])},
            name=conf[CONF_NAME],
        )

        coordinator = BlissBlindCoordinator(hass, device.id, conf)
        await coordinator.async_config_entry_first_refresh()
        hass.data[DOMAIN][CONF_DEVICES][device_id] = coordinator

    hass.async_create_task(
        hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    )

    entry.async_on_unload(entry.add_update_listener(update_listener))
    return True


async def async_migrate_entry(hass: HomeAssistant, config_entry: ConfigEntry) -> bool:
    """Migrate old config entries if required."""
    if config_entry.version == 1:
        LOGGER.error("Unsupported configuration version. Please re-add the integration.")
        return False
    return True


async def update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    LOGGER.debug("Updating Bliss BLE entry")
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    LOGGER.debug("Unloading Bliss BLE entry")

    for coordinator in hass.data.get(DOMAIN, {}).get(CONF_DEVICES, {}).values():
        await coordinator.async_shutdown()

    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok and DOMAIN in hass.data:
        hass.data[DOMAIN].pop(CONF_DEVICES, None)

    return unload_ok


async def async_remove_config_entry_device(
    hass: HomeAssistant, config_entry: ConfigEntry, device_entry: DeviceEntry
) -> bool:
    """Remove entities and device from Home Assistant when the device is removed."""
    device_id = device_entry.id
    ent_reg = er.async_get(hass)
    reg_entities: dict[str, str] = {}
    for ent in er.async_entries_for_config_entry(ent_reg, config_entry.entry_id):
        if device_id == ent.device_id:
            reg_entities[ent.unique_id] = ent.entity_id
    for entity_id in reg_entities.values():
        ent_reg.async_remove(entity_id)

    dev_reg = dr.async_get(hass)
    dev_reg.async_remove_device(device_id)

    devices_to_remove: list[str] = []
    for dev_id, dev_config in config_entry.data.get(CONF_DEVICES, {}).items():
        if dev_config[CONF_NAME] == device_entry.name:
            devices_to_remove.append(dev_config[CONF_MAC])

    new_data = {CONF_DEVICES: dict(config_entry.data.get(CONF_DEVICES, {}))}
    for mac in devices_to_remove:
        new_data[CONF_DEVICES].pop(mac, None)

    hass.config_entries.async_update_entry(config_entry, data=new_data)
    hass.config_entries._async_schedule_save()

    domain_data = hass.data.get(DOMAIN, {}).get(CONF_DEVICES, {})
    for mac in devices_to_remove:
        domain_data.pop(mac, None)

    return True
