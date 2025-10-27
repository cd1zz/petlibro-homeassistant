"""PetLibro services."""
from __future__ import annotations
import logging
from typing import Any
import voluptuous as vol

from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

SERVICE_MANUAL_FEED = "manual_feed"

SERVICE_SCHEMA_MANUAL_FEED = vol.Schema({
    vol.Required("device_id"): cv.string,
    vol.Required("portions", default=1): vol.All(
        vol.Coerce(int), vol.Range(min=1, max=12)
    ),
})

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

        # Get the device from the hub
        hub = None
        for entry_id in hass.data[DOMAIN]:
            hub = hass.data[DOMAIN][entry_id]
            if hub:
                for device in hub.devices:
                    if device.serial == device_id or device.name == device_id:
                        _LOGGER.info(f"Manual feeding {portions} portions for device {device.name}")
                        await device.api.set_manual_feed(device.serial, portions)
                        await device.refresh()
                        return

        _LOGGER.error(f"Device {device_id} not found")

    hass.services.async_register(
        DOMAIN,
        SERVICE_MANUAL_FEED,
        handle_manual_feed,
        schema=SERVICE_SCHEMA_MANUAL_FEED,
    )
    _LOGGER.info(f"Registered service: {DOMAIN}.{SERVICE_MANUAL_FEED}")
    _LOGGER.info("PetLibro services registered")