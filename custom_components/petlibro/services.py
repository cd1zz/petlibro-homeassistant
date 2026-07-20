"""PetLibro services."""
from __future__ import annotations
import logging
from typing import Any
import voluptuous as vol

from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from .const import DOMAIN, MAX_FEED_PORTIONS

_LOGGER = logging.getLogger(__name__)

SERVICE_MANUAL_FEED = "manual_feed"

SERVICE_SCHEMA_MANUAL_FEED = vol.Schema({
    vol.Required("device_id"): cv.string,
    # Bounded by the same constant the select/number entities use; this was
    # capped at 12 while those exposed all 48.
    vol.Required("portions", default=1): vol.All(
        vol.Coerce(int), vol.Range(min=1, max=MAX_FEED_PORTIONS)
    ),
})


def _find_device(hass: HomeAssistant, device_id: str):
    """Return the feeder matching a serial or name, or None."""
    for hub in hass.data.get(DOMAIN, {}).values():
        if not hub:
            continue
        for device in getattr(hub, "devices", []):
            if device.serial == device_id or device.name == device_id:
                return device
    return None


async def async_setup_services(hass: HomeAssistant) -> None:
    """Set up PetLibro services."""

    # Check if services are already registered (to handle multiple config entries)
    if hass.services.has_service(DOMAIN, SERVICE_MANUAL_FEED):
        _LOGGER.debug("PetLibro services already registered, skipping")
        return

    _LOGGER.info("Setting up PetLibro services")

    async def handle_manual_feed(call: ServiceCall) -> None:
        """Handle manual feed service call."""
        device_id = call.data["device_id"]
        portions = call.data["portions"]

        # hass.data[DOMAIN] was indexed directly, raising KeyError when the
        # service was called before any entry had finished setting up.
        device = _find_device(hass, device_id)

        if device is None:
            # Previously this only logged, so the service reported success to
            # the caller and automations could not detect the failure.
            raise ServiceValidationError(
                f"No PetLibro device found matching '{device_id}'"
            )

        if not hasattr(device, "set_manual_feed"):
            raise ServiceValidationError(
                f"{device.name} does not support manual feeding"
            )

        _LOGGER.info("Manual feeding %s portions for device %s", portions, device.name)
        try:
            await device.api.set_manual_feed(device.serial, portions)
            await device.refresh()
        except Exception as err:
            # An aiohttp error here previously escaped as a raw traceback.
            raise HomeAssistantError(
                f"Failed to manually feed {device.name}: {err}"
            ) from err

    hass.services.async_register(
        DOMAIN,
        SERVICE_MANUAL_FEED,
        handle_manual_feed,
        schema=SERVICE_SCHEMA_MANUAL_FEED,
    )
    _LOGGER.info(f"Registered service: {DOMAIN}.{SERVICE_MANUAL_FEED}")
    _LOGGER.info("PetLibro services registered")


@callback
def async_unload_services(hass: HomeAssistant) -> None:
    """Remove PetLibro services once the last config entry is unloaded.

    Without this the service stayed in the registry after the integration was
    removed, and calling it raised KeyError.
    """
    if hass.services.has_service(DOMAIN, SERVICE_MANUAL_FEED):
        hass.services.async_remove(DOMAIN, SERVICE_MANUAL_FEED)
        _LOGGER.debug("Removed service: %s.%s", DOMAIN, SERVICE_MANUAL_FEED)
