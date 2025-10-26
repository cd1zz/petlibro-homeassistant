"""Generic PETLIBRO feeder"""
from typing import Optional, cast
from logging import getLogger
from . import Device
from ..device import Device

_LOGGER = getLogger(__name__)


UNITS = {
    1: "cup",
    2: "oz",
    3: "g",
    4: "mL"
}

UNITS_RATIO = {
    1: 1/12,
    2: 0.35,
    3: 10,
    4: 20
}

class Feeder(Device):
    """Generic PETLIBRO feeder device"""

    async def refresh(self):
        await super().refresh()
        self.update_data({
            "feedingPlanTodayNew": await self.api.device_feeding_plan_today_new(self.serial)
        })

    @property
    def unit_id(self) -> int | None:
        """The device unit type identifier"""
        return self._data.get("unitType")

    @property
    def unit_type(self) -> str | None:
        """The device unit type"""
        unit : Optional[str] = None

        if unit_id := self.unit_id:
            unit = UNITS.get(unit_id)

        return unit

    @property
    def feeding_plan(self) -> bool:
        return self._data.get("enableFeedingPlan", False)

    async def set_feeding_plan(self, value: bool):
        await self.api.set_device_feeding_plan(self.serial, value)
        await self.refresh()

    @property
    def feeding_plan_today_all(self) -> bool:
        return not cast(bool, self._data.get("feedingPlanTodayNew", {}).get("allSkipped"))

    async def set_feeding_plan_today_all(self, value: bool):
        await self.api.set_device_feeding_plan_today_all(self.serial, value)
        await self.refresh()

    async def set_manual_feed(self):
        await self.api.set_device_manual_feeding(self.serial)
        await self.refresh()

    async def set_manual_feed_amount(self, amount: int = None):
        """Trigger manual feeding with specific amount"""
        # If no amount provided, try to get from device attributes or default
        if amount is None:
            amount = self._data.get("default_portion_size", 1)
        await self.api.set_manual_feed(self.serial, amount)
        await self.refresh()

    async def manual_feed_and_skip_next(self, amount: int = None):
        """Feed specified amount and skip the next scheduled feeding for today"""
        from datetime import datetime

        # If no amount provided, try to get from device attributes or default
        if amount is None:
            amount = self._data.get("default_portion_size", 1)

        # First, dispense the food
        await self.api.set_manual_feed(self.serial, amount)

        # Get today's feeding plans
        today_plans_data = await self.api.device_feeding_plan_today_new(self.serial)

        if today_plans_data and 'plans' in today_plans_data:
            plans = today_plans_data['plans']

            # Get current time in 24-hour format
            now = datetime.now()
            current_time = now.strftime("%H:%M")

            # Find the next scheduled feeding (state == 1 and time > current_time)
            next_feeding = None
            for plan in sorted(plans, key=lambda p: p.get('time', '')):
                # State 1 = SCHEDULED (toggled on)
                if plan.get('state') == 1 and plan.get('time', '') > current_time:
                    next_feeding = plan
                    break

            # Skip the next feeding for today only
            if next_feeding:
                plan_id = next_feeding.get('planId')
                if plan_id:
                    await self.api.set_feeding_plan_enable_today_single(self.serial, plan_id, False)
                    _LOGGER.info(f"Skipped next feeding at {next_feeding.get('time')} (ID: {plan_id})")

        await self.refresh()

    def convert_unit(self, value: int) -> int:
        """
        Convert a value to the device unit

        :param unit: Value to convert
        :return: Converted value or unchanged if no unit
        """
        if self.unit_id:
            return value * UNITS_RATIO.get(self.unit_id, 1)
        return value
