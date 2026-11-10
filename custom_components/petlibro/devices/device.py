# Error Mode - Used for pulling API for new devices. Enable Error Mode and Disable Debug Mode.

from logging import getLogger
from typing import cast

from homeassistant.exceptions import ConfigEntryAuthFailed

from ..api import PetLibroAPI
from ..exceptions import PetLibroAPIError
from .event import Event, EVENT_UPDATE
from ..member import Member


_LOGGER = getLogger(__name__)


class Device(Event):
    def __init__(self, data: dict, member: Member, api: PetLibroAPI):
        super().__init__()
        self._data: dict = {}
        self.api = api
        self.member = member
        # Outcome of the last coordinator poll. refresh() itself never raises
        # for API errors (dozens of setters call it after a successful
        # command), so failures are recorded here and surfaced by poll().
        self.last_poll_ok: bool = True
        self._refresh_error: Exception | None = None

        self.update_data(data)

    def update_data(self, data: dict) -> None:
        """Save the device info from a data dictionary."""
        try:
            # Log at debug level instead of error level
            _LOGGER.debug("Updating data with new information.")
            self._data.update(data)
            self.emit(EVENT_UPDATE)
            _LOGGER.debug("Data updated successfully.")
        except Exception as e:
            _LOGGER.error(f"Error updating data: {e}")
            # Optionally log specific fields instead of the entire data
            _LOGGER.debug(f"Partial data: {data.get('deviceSn', 'Unknown Serial')}")
    async def refresh(self):
        """Refresh the device data from the API."""
        try:
            data = {}
            data.update(await self.api.device_base_info(self.serial))
            data.update(await self.api.device_real_info(self.serial))
            data.update(await self.api.device_attribute_settings(self.serial))
            self.update_data(data)
        except ConfigEntryAuthFailed:
            # Must reach the coordinator so Home Assistant can start the reauth
            # flow; swallowing it here left the integration retrying a dead
            # token forever without ever prompting the user.
            raise
        except PetLibroAPIError:
            # Let the subclass handler (and ultimately the coordinator) see it.
            raise
        except Exception as e:
            _LOGGER.error(f"Failed to refresh device data: {e}")
            self._record_refresh_error(e)

    def _record_refresh_error(self, err: Exception) -> None:
        """Remember a refresh failure so poll() can report it."""
        self._refresh_error = err

    async def poll(self) -> None:
        """Refresh for the coordinator, raising if the refresh failed.

        Subclass refresh() handlers log and swallow PetLibroAPIError, which
        used to make a cloud outage look like a successful poll: entities
        stayed available with frozen values. poll() re-raises the recorded
        error so the hub can count the failure and entities can go
        unavailable.
        """
        self._refresh_error = None
        try:
            await self.refresh()
        except Exception:
            self.last_poll_ok = False
            raise
        err, self._refresh_error = self._refresh_error, None
        if err is not None:
            self.last_poll_ok = False
            raise err
        self.last_poll_ok = True

    @property
    def serial(self) -> str:
        return cast(str, self._data.get("deviceSn"))

    @property
    def model(self) -> str:
        return cast(str, self._data.get("productIdentifier"))

    @property
    def model_name(self) -> str:
        return cast(str, self._data.get("productName"))

    @property
    def name(self) -> str:
        return cast(str, self._data.get("name"))

    @property
    def mac(self) -> str:
        return cast(str, self._data.get("mac"))

    @property
    def software_version(self) -> str:
        return cast(str, self._data.get("softwareVersion"))

    @property
    def hardware_version(self) -> str:
        return cast(str, self._data.get("hardwareVersion"))
