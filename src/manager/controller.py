"""Rolling controller. All writes, including OFF, share a durable lease."""

from dataclasses import asdict, replace
import asyncio
import time
from datetime import datetime, timedelta
from uuid import uuid4

from .device import AlphaDevice, DeviceUnavailable
from .domain import PRAGUE, utc_now, next_boundary
from .planner import optimize
from .providers import Providers
from .tariff import schedule_verified
from .store import BusyError
from .daily import plan_daily
from .legacy_device import LegacyDevice
from .energy_day import cache_power_history, record_measurement
from .soc_tracking import capture_forecast


class Controller:
    def __init__(self, settings, store, live=False, device_factory=None):
        self.settings = settings
        self.store = store
        self.live = live
        self.device_factory = device_factory or (
            lambda: (
                LegacyDevice(store, settings.legacy_min_write_interval_seconds)
                if settings.control_mode == "legacy_daily"
                else AlphaDevice()
            )
        )
        self.providers = Providers(settings, store)
        self._emhass = None

    async def standard_plan(self, slots, telemetry, current_schedule):
        """One writer; optional independent EMHASS proposal uses identical inputs."""
        active = self.settings.standard_planner
        candidate = None
        candidate_error = None
        if active == "candidate" or self.settings.emhass_shadow:
            try:
                candidate = plan_daily(
                    slots,
                    telemetry,
                    self.settings,
                    minimum_slots=4,
                    current_schedule=current_schedule,
                )
            except Exception as exc:
                if active == "candidate":
                    raise
                candidate_error = safe_error(exc)
        comparison = None
        emhass_plan = None
        if active == "emhass" or self.settings.emhass_shadow:
            started = time.monotonic()
            try:
                from .emhass_mpc import EmhassPlanner
                from .emhass_schedule import schedule_from_decisions

                if self._emhass is None:
                    self._emhass = EmhassPlanner(self.settings)
                decisions = await asyncio.to_thread(self._emhass.plan, slots, telemetry)
                emhass_plan = schedule_from_decisions(
                    decisions, slots, telemetry, self.settings, current_schedule
                )
                comparison = {
                    "status": "partial"
                    if candidate_error
                    else "compared"
                    if candidate
                    else "planned",
                    "timestamp": utc_now().isoformat(),
                    "active_planner": active,
                    "shadow_controls_device": False,
                    "measured_soc_percent": telemetry.soc_percent,
                    "emhass_solver": self._emhass.last_solve,
                    "emhass": {
                        "charge": emhass_plan["charge"],
                        "discharge": emhass_plan["discharge"],
                        "next_action": emhass_plan["committed_first_action"],
                        "requested_next_action": decisions[0].action,
                        "first_action_projection": emhass_plan[
                            "first_action_projection"
                        ],
                        "forecast_benefit_eur": emhass_plan["forecast_benefit_eur"],
                    },
                    "candidate": {
                        "charge": candidate["charge"],
                        "discharge": candidate["discharge"],
                        "next_action": candidate["decisions"][0]["action"],
                        "forecast_benefit_eur": candidate["forecast_benefit_eur"],
                    }
                    if candidate
                    else None,
                    "candidate_error": candidate_error,
                    "note": "Same measured SOC, prices and forecasts. Forecast differences are not measured savings.",
                }
            except Exception as exc:
                if active == "emhass":
                    raise
                comparison = {
                    "status": "unavailable",
                    "timestamp": utc_now().isoformat(),
                    "active_planner": active,
                    "shadow_controls_device": False,
                    "message": safe_error(exc),
                }
            comparison["solve_seconds"] = round(time.monotonic() - started, 3)
        chosen = emhass_plan if active == "emhass" else candidate
        return {**chosen, "planner": active}, comparison

    def control(self):
        return self.store.get(
            "control", {"enabled": False, "restore_pending": False, "revision": 0}
        )

    def readiness(self):
        gaps = []
        if not self.live:
            gaps.append("Observation mode: device writes are disabled.")
        if not schedule_verified(utc_now(), self.settings):
            gaps.append(
                "HDO high/low tariff times are missing or outside their verified date range."
            )
        if not self.settings.device_control_verified:
            gaps.append(
                "Controlled schedule writes, 10% reserve enforcement and appliance import protection are not yet commissioned on this inverter."
                if self.settings.control_mode == "legacy_daily"
                else "Native self-consumption and reserve controls are not verified on this inverter."
            )
        if self.settings.control_mode == "periodic" and not self.settings.hold_verified:
            gaps.append("Battery hold behavior is not verified on this inverter.")
        capability = self.store.get("capability", {})
        if self.settings.control_mode == "legacy_daily":
            if (
                capability.get("mode") == "legacy_daily"
                and capability.get("available") is False
            ):
                gaps.append(
                    capability.get("message", "Daily scheduling is unavailable.")
                )
        elif capability.get("periodic_available") is False:
            gaps.append(
                capability.get("message", "Periodic scheduling is unavailable.")
            )
        return gaps

    async def set_enabled(self, enabled):
        # Save intent immediately, even while a long telemetry/forecast read owns
        # the execution lease. Dispatch rechecks this revision before writing.
        control = {
            "enabled": enabled,
            "restore_pending": not enabled,
            "revision": uuid4().hex,
            "updated_at": utc_now().isoformat(),
        }
        self.store.put("control", control)
        self.store.append(
            {"type": "control", "timestamp": utc_now().isoformat(), **control}
        )
        if enabled:
            return control
        owner = uuid4().hex
        try:
            self.store.acquire(owner)
        except BusyError:
            # A running cycle cannot erase the OFF request. It will stop dispatch
            # and the local loop / next Lambda invocation retries restoration.
            return control
        device = None
        try:
            if not enabled:
                if self.live:
                    try:
                        device = self.device_factory()
                        confirmation = await device.native(
                            self.settings.reserve_percent,
                            self.settings.device_control_verified,
                            guard=lambda: (
                                self.store.is_owner(owner, min_remaining=30)
                                and self.control()["revision"] == control["revision"]
                                and not self.control()["enabled"]
                            ),
                        )
                        if not self.store.is_owner(owner):
                            return self.control()
                        if self.control()["revision"] == control["revision"]:
                            control["restore_pending"] = False
                            self.store.put("control", control)
                        self.store.put(
                            "actuation",
                            {
                                **confirmation,
                                "action": "native",
                                "timestamp": utc_now().isoformat(),
                            },
                        )
                    except Exception as exc:
                        if not self.store.is_owner(owner):
                            return self.control()
                        self.store.put(
                            "actuation",
                            {
                                "status": "pending",
                                "action": "native",
                                "message": safe_error(exc),
                                "timestamp": utc_now().isoformat(),
                            },
                        )
                else:
                    self.store.put(
                        "actuation",
                        {
                            "status": "observation",
                            "action": "native",
                            "message": "OFF is saved. Observation mode cannot change the inverter.",
                            "timestamp": utc_now().isoformat(),
                        },
                    )
            return control
        finally:
            if device:
                await device.close()
            self.store.release(owner)

    async def observe(self, owner, device):
        """Read-only contact, keeping API reachability separate from sample age."""
        try:
            telemetry = await device.telemetry()
            if not self.store.is_owner(owner):
                raise BusyError("Controller lease expired while fetching telemetry")
            telemetry.validate(utc_now(), self.settings.telemetry_max_age_minutes)
            self.store.put("telemetry", asdict(telemetry))
            record_measurement(self.store, asdict(telemetry), utc_now())
            self.store.put(
                "device_connection",
                {
                    "status": "connected",
                    "checked_at": utc_now().isoformat(),
                    "last_success_at": utc_now().isoformat(),
                    "sample_at": telemetry.timestamp,
                    "transport": "AlphaESS cloud API",
                },
            )
            return telemetry
        except Exception as exc:
            if self.store.is_owner(owner):
                previous = self.store.get("device_connection") or {}
                self.store.put(
                    "device_connection",
                    {
                        **previous,
                        "status": "error",
                        "checked_at": utc_now().isoformat(),
                        "message": safe_error(exc),
                        "transport": "AlphaESS cloud API",
                    },
                )
            raise

    async def monitor_once(self):
        """Never write settings or replan; share the writer's execution lease."""
        if not self.live:
            return
        now = utc_now()
        # Leave a clear runway for the next scheduled control cycle.
        if (next_boundary(now) - now).total_seconds() <= 120:
            return
        previous = self.store.get("device_connection") or {}
        if (
            previous.get("checked_at")
            and (now - datetime.fromisoformat(previous["checked_at"])).total_seconds()
            < 60
        ):
            return
        owner = uuid4().hex
        self.store.acquire(owner)
        device = None
        try:
            device = self.device_factory()
            await self.observe(owner, device)
            cached = self.store.get("energy_day") or {}
            checked = cached.get("history_checked_at")
            if (
                not checked
                or not cached.get("soc_history_backfilled")
                or (utc_now() - datetime.fromisoformat(checked)).total_seconds() >= 900
            ):
                try:
                    samples = await device.power_history(
                        utc_now().astimezone(PRAGUE).date()
                    )
                    if self.store.is_owner(owner):
                        cache_power_history(self.store, samples, utc_now())
                except DeviceUnavailable:
                    pass  # Keep available measurements if day-history retrieval fails.
        finally:
            try:
                if device:
                    await device.close()
            finally:
                self.store.release(owner)

    async def run_once(self):
        owner = uuid4().hex
        self.store.acquire(owner)
        now = utc_now()
        device = None
        telemetry = None

        def lost_lease():
            return {
                "type": "cycle",
                "timestamp": now.isoformat(),
                "status": "busy",
                "message": "Controller lease expired; no further dispatch attempted.",
            }

        try:
            control = self.control()
            device = self.device_factory()
            if not control["enabled"] and control["restore_pending"] and self.live:
                try:
                    result = await device.native(
                        self.settings.reserve_percent,
                        self.settings.device_control_verified,
                        guard=lambda: (
                            self.store.is_owner(owner, min_remaining=30)
                            and self.control()["revision"] == control["revision"]
                            and not self.control()["enabled"]
                        ),
                    )
                    if not self.store.is_owner(owner):
                        raise BusyError("Controller lease expired during restoration")
                    if self.control()["revision"] == control["revision"]:
                        control["restore_pending"] = False
                        self.store.put("control", control)
                    self.store.put(
                        "actuation",
                        {**result, "action": "native", "timestamp": now.isoformat()},
                    )
                except Exception as restore_error:
                    if not self.store.is_owner(owner):
                        raise BusyError(
                            "Controller lease expired during restoration"
                        ) from None
                    self.store.put(
                        "actuation",
                        {
                            "status": "pending",
                            "action": "native",
                            "message": safe_error(restore_error),
                            "timestamp": now.isoformat(),
                        },
                    )
            telemetry = await self.observe(owner, device)
            self.store.put("device_last_seen", now.isoformat())
            capability = self.store.get("capability")
            if (
                self.settings.control_mode == "legacy_daily"
                or not capability
                or capability.get("mode", "periodic") != self.settings.control_mode
                or (
                    now - datetime.fromisoformat(capability["checked_at"])
                ).total_seconds()
                > 21600
            ):
                try:
                    periodic, native = await device.read_config()
                    if not self.store.is_owner(owner):
                        raise BusyError(
                            "Controller lease expired while reading configuration"
                        )
                    if self.settings.control_mode == "legacy_daily":
                        self.store.put(
                            "device_schedule",
                            {
                                "charge": periodic,
                                "discharge": native,
                                "checked_at": utc_now().isoformat(),
                                "readback_pending": False,
                                "source": "inverter_read",
                            },
                        )
                    self.store.put(
                        "capability",
                        {
                            "checked_at": now.isoformat(),
                            "periodic_available": True,
                            "available": True,
                            "mode": self.settings.control_mode,
                            "native_reserve_percent": native.get("batUseCap"),
                            "discharge_time_control": native.get("ctrDis"),
                        },
                    )
                except DeviceUnavailable as capability_error:
                    if not self.store.is_owner(owner):
                        raise BusyError(
                            "Controller lease expired while reading configuration"
                        ) from None
                    self.store.put(
                        "capability",
                        {
                            "checked_at": now.isoformat(),
                            "periodic_available": False,
                            "available": False,
                            "mode": self.settings.control_mode,
                            "message": safe_error(capability_error),
                        },
                    )
            slots, notes = await self.providers.slots(now, device, telemetry)
            if not self.store.is_owner(owner):
                raise BusyError("Controller lease expired while fetching forecasts")
            planning_telemetry = replace(
                telemetry,
                capacity_kwh=self.settings.effective_capacity_kwh
                or telemetry.capacity_kwh,
            )
            daily = None
            comparison = None
            if self.settings.control_mode == "legacy_daily":
                daily, comparison = await self.standard_plan(
                    slots, planning_telemetry, self.store.get("device_schedule")
                )
                from .domain import Decision

                plan = [
                    Decision(**d)
                    for d in daily["decisions"]
                    if datetime.fromisoformat(d["timestamp"]) >= next_boundary(now)
                ]
                if not plan:
                    raise ValueError(
                        "Daily forecast horizon has expired; awaiting complete prices"
                    )
                notes = notes + daily["notes"]
            else:
                plan = optimize(slots, planning_telemetry, self.settings)
            payload = {
                "created_at": now.isoformat(),
                "source": "forecast",
                "mode": self.settings.control_mode,
                "notes": notes,
                "decisions": [d.dict() for d in plan],
                "assumptions": {
                    "wear_eur_per_kwh": self.settings.wear_eur_per_kwh,
                    "charge_efficiency": self.settings.charge_efficiency,
                    "nominal_capacity_kwh": telemetry.capacity_kwh,
                    "effective_capacity_kwh": planning_telemetry.capacity_kwh,
                    "discharge_efficiency": self.settings.discharge_efficiency,
                    "tariff_complete": all(
                        schedule_verified(
                            datetime.fromisoformat(slot.timestamp), self.settings
                        )
                        for slot in slots
                    ),
                },
            }
            if daily is not None:
                payload["outlook_decisions"] = daily.get(
                    "outlook_decisions", payload["decisions"]
                )
                payload.update(
                    {
                        k: daily[k]
                        for k in (
                            "charge",
                            "valid_from",
                            "valid_until",
                            "forecast_benefit_eur",
                            "discharge",
                            "version",
                            "lookahead_until",
                            "forecast_improvement_vs_legacy_eur",
                            "candidate_count",
                            "selected",
                            "planner",
                        )
                    }
                )
            if not self.store.is_owner(owner):
                return lost_lease()
            if comparison is not None:
                self.store.put("planner_comparison", comparison)
            self.store.put("plan", payload)
            status = "observing"
            if (
                self.control()["enabled"]
                and self.control()["revision"] == control["revision"]
            ):
                gaps = self.readiness()
                if (
                    self.settings.control_mode == "legacy_daily"
                    and not payload["assumptions"]["tariff_complete"]
                ):
                    gaps.append(
                        "The complete daily horizon needs verified tariff times."
                    )
                if gaps:
                    status = "blocked"
                    self.store.put(
                        "actuation",
                        {
                            "status": "blocked",
                            "message": " ".join(gaps),
                            "timestamp": now.isoformat(),
                        },
                    )
                else:
                    telemetry.validate(
                        utc_now(), self.settings.telemetry_max_age_minutes
                    )
                    method = (
                        device.apply_daily
                        if self.settings.control_mode == "legacy_daily"
                        else device.apply
                    )
                    budgets = (
                        [
                            self.store.get("legacy_" + kind + "_write", {})
                            for kind in ("charge", "discharge")
                        ]
                        if self.settings.control_mode == "legacy_daily"
                        else []
                    )
                    next_write = max(
                        (
                            datetime.fromisoformat(b["attempted_at"])
                            + timedelta(
                                seconds=self.settings.legacy_min_write_interval_seconds
                            )
                            for b in budgets
                            if b.get("attempted_at")
                        ),
                        default=None,
                    )
                    if next_write and utc_now() < next_write:
                        result = {
                            "status": "waiting",
                            "next_write_at": next_write.isoformat(),
                            "message": "Monitoring continues. The stored inverter schedule remains in force; the next daily API update is available at "
                            + next_write.isoformat()
                            + ".",
                        }
                    else:
                        result = await method(
                            payload
                            if self.settings.control_mode == "legacy_daily"
                            else plan[0],
                            self.settings,
                            utc_now(),
                            guard=lambda: (
                                self.store.is_owner(owner, min_remaining=30)
                                and self.control()["enabled"]
                                and self.control()["revision"] == control["revision"]
                            ),
                        )
                    if not self.store.is_owner(owner):
                        raise BusyError("Controller lease expired during dispatch")
                    if (
                        self.settings.control_mode == "legacy_daily"
                        and result.get("status") == "accepted"
                        and result.get("schedule_verified")
                    ):
                        self.store.put(
                            "applied_schedule",
                            {
                                "charge": payload["charge"],
                                "discharge": payload["discharge"],
                                "confirmed_at": utc_now().isoformat(),
                            },
                        )
                    self.store.put(
                        "actuation",
                        {
                            **result,
                            "action": "daily_schedule"
                            if self.settings.control_mode == "legacy_daily"
                            else plan[0].action,
                            "timestamp": utc_now().isoformat(),
                        },
                    )
                    status = (
                        "waiting" if result.get("status") == "waiting" else "active"
                    )
            if not self.store.is_owner(owner):
                return lost_lease()
            if status == "active" and not (daily and daily.get("first_action_projection")):
                capture_forecast(self.store, payload, utc_now())
            self.store.put("last_success", now.isoformat())
            record = {
                "type": "cycle",
                "timestamp": now.isoformat(),
                "status": status,
                "telemetry": asdict(telemetry),
                "decision": plan[0].dict(),
                "notes": notes,
                **(
                    {"planner_comparison": comparison} if comparison is not None else {}
                ),
            }
            self.store.put("health", record)
            self.store.append(record)
            return record
        except Exception as exc:
            if not self.store.is_owner(owner):
                # Another operation may now own the inverter. Do not overwrite
                # its state, reserve API budgets, or attempt native restoration.
                return lost_lease()
            record = {
                "type": "cycle",
                "timestamp": now.isoformat(),
                "status": "degraded",
                "message": safe_error(exc),
            }
            if telemetry:
                record["telemetry"] = asdict(telemetry)
            # No new dispatch from stale telemetry. Previously programmed single-slot
            # commands expire into native demand. Restore explicitly after grace.
            if self.live and self.control()["enabled"]:
                last = self.store.get("last_success")
                age = (
                    (now - datetime.fromisoformat(last)).total_seconds()
                    if last
                    else float("inf")
                )
                if age > self.settings.plan_grace_minutes * 60 and device:
                    try:
                        result = await device.native(
                            self.settings.reserve_percent,
                            self.settings.device_control_verified,
                            guard=lambda: (
                                self.store.is_owner(owner, min_remaining=30)
                                and self.control()["enabled"]
                                and self.control()["revision"] == control["revision"]
                            ),
                        )
                        if not self.store.is_owner(owner):
                            return lost_lease()
                        self.store.put(
                            "actuation",
                            {
                                **result,
                                "action": "fallback_native",
                                "timestamp": utc_now().isoformat(),
                            },
                        )
                    except Exception as fallback_error:
                        if not self.store.is_owner(owner):
                            return lost_lease()
                        self.store.put(
                            "actuation",
                            {
                                "status": "pending",
                                "action": "fallback_native",
                                "message": safe_error(fallback_error),
                                "timestamp": utc_now().isoformat(),
                            },
                        )
            if not self.store.is_owner(owner):
                return lost_lease()
            self.store.put("health", record)
            self.store.append(record)
            return record
        finally:
            if device:
                await device.close()
            self.store.release(owner)


def safe_error(exc):
    if isinstance(exc, (DeviceUnavailable, ValueError)):
        return str(exc)
    return f"{type(exc).__name__}: external service or local operation failed"
