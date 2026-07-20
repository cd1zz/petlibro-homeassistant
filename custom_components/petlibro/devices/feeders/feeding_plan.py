"""Shared logic for deciding which scheduled feeding a manual feed should skip.

Kept in one place because the selection rules are subtle and were previously
duplicated (and drifting) across feeder classes:

* Times are compared in Home Assistant's *configured* timezone, not the
  container's system clock. Many installs run the container as UTC while HA is
  configured to a local zone; `datetime.now()` there is hours off, which the old
  "pick the next enabled plan" search absorbed silently but a proximity window
  cannot.
* Only plans that are still enabled for today are eligible, so a plan that was
  already skipped is never skipped twice.
* Selection is bounded by a proximity window. Without one the search walks
  forward to whatever plan happens to be next, so deleting or disabling a
  morning plan silently redirects the skip onto an unrelated evening meal.
"""

from __future__ import annotations

from datetime import datetime
from logging import getLogger
from typing import Any, Iterable

import aiohttp
from homeassistant.util import dt as dt_util

from ...const import (
    DEFAULT_SKIP_WINDOW_MINUTES,
    MIN_SKIP_WINDOW_MINUTES,
    MAX_SKIP_WINDOW_MINUTES,
)
from ...exceptions import PetLibroAPIError

_LOGGER = getLogger(__name__)

# Plan state as reported by /device/feedingPlan/todayNew.
# 1 = scheduled/pending, 2 = skipped, 3 = completed, 4 = skipped & time passed.
PLAN_STATE_SCHEDULED = 1
PLAN_STATE_SKIPPED = 2


def parse_plan_minutes(plan: dict[str, Any]) -> int | None:
    """Return a plan's "HH:MM" as minutes since local midnight, or None if unusable."""
    raw = plan.get("time")
    if not isinstance(raw, str):
        return None
    parts = raw.split(":")
    if len(parts) != 2:
        return None
    try:
        hours, minutes = int(parts[0]), int(parts[1])
    except ValueError:
        return None
    if not (0 <= hours < 24 and 0 <= minutes < 60):
        return None
    return hours * 60 + minutes


def _find_next_plan_in_state(
    plans: Iterable[dict[str, Any]] | None,
    state: int,
    now: datetime | None = None,
) -> tuple[dict[str, Any] | None, int | None]:
    """Find the soonest plan later today in the given state."""
    if not plans:
        return None, None

    if now is None:
        now = dt_util.now()
    now_minutes = now.hour * 60 + now.minute

    best_plan: dict[str, Any] | None = None
    best_minutes: int | None = None

    for plan in plans:
        if not isinstance(plan, dict):
            continue
        if plan.get("state") != state:
            continue
        plan_minutes = parse_plan_minutes(plan)
        if plan_minutes is None:
            _LOGGER.debug("Ignoring feeding plan with unparseable time: %s", plan)
            continue
        if plan_minutes <= now_minutes:
            # Today only; no wrap into tomorrow's schedule.
            continue
        if best_minutes is None or plan_minutes < best_minutes:
            best_plan, best_minutes = plan, plan_minutes

    if best_plan is None or best_minutes is None:
        return None, None
    return best_plan, best_minutes - now_minutes


def find_next_skipped_plan(
    plans: Iterable[dict[str, Any]] | None,
    now: datetime | None = None,
) -> tuple[dict[str, Any] | None, int | None]:
    """Find the soonest plan later today that is currently skipped.

    Used to undo a skip. Deliberately unbounded by the proximity window: putting
    a cancelled meal back should always be possible, however far off it is.
    """
    return _find_next_plan_in_state(plans, PLAN_STATE_SKIPPED, now=now)


def find_next_scheduled_plan(
    plans: Iterable[dict[str, Any]] | None,
    now: datetime | None = None,
) -> tuple[dict[str, Any] | None, int | None]:
    """Find the soonest still-enabled feeding plan later today.

    Returns (plan, minutes_until_it_fires), or (None, None) if there is none.

    `now` defaults to dt_util.now(), which is Home Assistant's configured
    timezone -- the same zone the integration reports to PetLibro, so plan
    times and the current time are always expressed in one frame.

    Plans that are already skipped or already fired are not candidates, so a
    meal is never skipped twice.
    """
    return _find_next_plan_in_state(plans, PLAN_STATE_SCHEDULED, now=now)


def select_plan_to_skip(
    plans: Iterable[dict[str, Any]] | None,
    window_minutes: float,
    serial: str = "?",
    now: datetime | None = None,
) -> dict[str, Any] | None:
    """Return the plan a manual feed should skip, or None if none qualifies.

    A plan qualifies only when it fires within `window_minutes` of now, i.e. the
    manual feed is plausibly standing in for it. Anything further out is left
    alone and the reason is logged at WARNING, since a silently-skipped meal is
    exactly the failure this guard exists to prevent.
    """
    plan, minutes_away = find_next_scheduled_plan(plans, now=now)

    if plan is None or minutes_away is None:
        _LOGGER.warning(
            "No upcoming scheduled feeding left today for %s; nothing to skip.", serial
        )
        return None

    if minutes_away > window_minutes:
        _LOGGER.warning(
            "Not skipping the %s feeding for %s: it is %d minutes away, beyond the "
            "%g minute skip window. Raise the 'Skip Next Feeding Window' number "
            "entity if this feed was meant to replace it.",
            plan.get("time"),
            serial,
            minutes_away,
            window_minutes,
        )
        return None

    _LOGGER.debug(
        "Selected feeding plan %s at %s for %s (%d minutes away, within %g minute window)",
        plan.get("planId"),
        plan.get("time"),
        serial,
        minutes_away,
        window_minutes,
    )
    return plan


class FeedingPlanSkipMixin:
    """Skip/restore scheduled feedings, shared by every feeder that supports it.

    Mix in *before* Device so these implementations win:

        class AirSmartFeeder(FeedingPlanSkipMixin, Device):

    Requires the host class to provide `api`, `serial` and `refresh()`.
    """

    @property
    def skip_window_minutes(self) -> float:
        """How close a scheduled feeding must be for a manual feed to skip it."""
        return getattr(self, "_skip_window_minutes", DEFAULT_SKIP_WINDOW_MINUTES)

    @skip_window_minutes.setter
    def skip_window_minutes(self, value: float) -> None:
        self._skip_window_minutes = value

    async def set_skip_window_minutes(self, value: float) -> None:
        """Set the skip window, clamped to a sane range."""
        clamped = max(MIN_SKIP_WINDOW_MINUTES, min(value, MAX_SKIP_WINDOW_MINUTES))
        _LOGGER.debug(
            "Setting skip window: serial=%s, value=%s (clamped to %s)",
            self.serial, value, clamped,
        )
        self.skip_window_minutes = clamped

    async def _fetch_today_plans(self) -> list:
        """Return today's feeding plans, or an empty list."""
        data = await self.api.device_feeding_plan_today_new(self.serial)
        if not data or "plans" not in data:
            _LOGGER.warning("No feeding plan data available for %s", self.serial)
            return []
        return data.get("plans") or []

    async def _skip_upcoming_plan(self) -> bool:
        """Disable the next scheduled feeding if it is inside the skip window.

        Returns True only if a plan was actually skipped.
        """
        plans = await self._fetch_today_plans()
        plan = select_plan_to_skip(plans, self.skip_window_minutes, self.serial)
        if plan is None:
            return False

        plan_id = plan.get("planId")
        if not plan_id:
            _LOGGER.warning("Selected feeding plan has no planId: %s", plan)
            return False

        await self.api.set_feeding_plan_enable_today_single(self.serial, plan_id, False)
        _LOGGER.info(
            "Skipped the %s feeding for %s (plan %s) FOR TODAY ONLY",
            plan.get("time"), self.serial, plan_id,
        )
        return True

    async def manual_feed_and_skip_next(self, amount: int | None = None) -> None:
        """Dispense food now and skip the scheduled feeding it stands in for.

        The scheduled feeding is only skipped when it falls within
        skip_window_minutes of now. Without that bound, removing or disabling a
        nearby plan silently redirected the skip onto an unrelated later meal.
        """
        if amount is None:
            amount = getattr(self, "manual_feed_quantity", 1)

        _LOGGER.info(
            "Manual feed and skip next for %s: dispensing %s portions",
            self.serial, amount,
        )
        try:
            await self.api.set_manual_feed(self.serial, amount)
            await self._skip_upcoming_plan()
            await self.refresh()
        except aiohttp.ClientError as err:
            _LOGGER.error(
                "Failed manual feed and skip next for %s: %s", self.serial, err
            )
            raise PetLibroAPIError(f"Error in manual feed and skip next: {err}")

    async def disable_next_feeding(self) -> None:
        """Skip the next scheduled feeding for today only. Dispenses no food."""
        _LOGGER.info(
            "Disabling next scheduled feeding for %s (no food dispensed)", self.serial
        )
        try:
            await self._skip_upcoming_plan()
            await self.refresh()
        except aiohttp.ClientError as err:
            _LOGGER.error("Failed to disable next feeding for %s: %s", self.serial, err)
            raise PetLibroAPIError(f"Error disabling next feeding: {err}")

    async def enable_next_feeding(self) -> None:
        """Re-enable the next skipped feeding for today, undoing a skip.

        Not bounded by the skip window: restoring a cancelled meal should always
        be possible, however far away it is.
        """
        _LOGGER.info("Enabling next skipped feeding for %s", self.serial)
        try:
            plans = await self._fetch_today_plans()
            plan, minutes_away = find_next_skipped_plan(plans)

            if plan is None:
                _LOGGER.warning(
                    "No upcoming skipped feeding to re-enable for %s", self.serial
                )
                return

            plan_id = plan.get("planId")
            if not plan_id:
                _LOGGER.warning("Skipped feeding plan has no planId: %s", plan)
                return

            await self.api.set_feeding_plan_enable_today_single(
                self.serial, plan_id, True
            )
            _LOGGER.info(
                "Re-enabled the %s feeding for %s (plan %s, %d minutes away)",
                plan.get("time"), self.serial, plan_id, minutes_away,
            )
            await self.refresh()
        except aiohttp.ClientError as err:
            _LOGGER.error("Failed to enable next feeding for %s: %s", self.serial, err)
            raise PetLibroAPIError(f"Error enabling next feeding: {err}")
