"""Conservative AlphaESS execution adapter; never dispatches battery export.

The periodic API's flags aren't reliably echoed by getTimeChargeBySn. Readback
of periods confirms persistence only, not physical execution. Native reserve
and working mode must already be configured on the appliance and verified.
"""

import asyncio
from datetime import datetime, timedelta
import time

import aiohttp
from alphaess.alphaess import alphaess

from .domain import PRAGUE, UTC, Telemetry
from .settings import credentials


class DeviceUnavailable(RuntimeError):
    pass


class AlphaDevice:
    def __init__(self):
        app, secret, self.serial = credentials()
        if not all((app, secret, self.serial)):
            raise DeviceUnavailable("AlphaESS credentials are missing")
        self.session = aiohttp.ClientSession(
            trust_env=True, timeout=aiohttp.ClientTimeout(total=20)
        )
        self.client = alphaess(
            app, secret, session=self.session, raise_on_error=True, timeout=20
        )
        self.last_call = 0

    async def call(self, name, *args, **kwargs):
        write_guard = kwargs.pop("_write_guard", None)
        for attempt in range(2):
            delay = 10 - (time.monotonic() - self.last_call)
            # Monitoring and control create separate clients. Under the shared
            # execution lease, retain their spacing across clients/processes.
            store = getattr(self, "store", None)
            if store is not None:
                previous = store.get("alphaess_request_at")
                if previous is not None:
                    delay = max(delay, 10 - (time.time() - previous))
            if delay > 0:
                await asyncio.sleep(delay)
            if write_guard is not None and not write_guard():
                raise DeviceUnavailable(
                    "Control changed; device write cancelled before transmission"
                )
            self.last_call = time.monotonic()
            if store is not None:
                store.put("alphaess_request_at", time.time())
            try:
                return await getattr(self.client, name)(*args, **kwargs)
            except Exception as exc:
                code = getattr(exc, "code", None)
                # SDK get* methods are reads. Re-entering the SDK creates a fresh
                # signed timestamp after spacing; ambiguous writes never retry.
                if attempt == 0 and name.startswith("get") and code in (6006, "6006"):
                    continue
                # Upstream exceptions can contain identifiers or request URLs.
                raise DeviceUnavailable(
                    f"AlphaESS {name} failed ({'API ' + str(code) if code else type(exc).__name__})"
                ) from None

    async def telemetry(self):
        systems = await self.call("getESSList")
        unit = next((x for x in systems or [] if x.get("sysSn") == self.serial), None)
        if not unit:
            raise DeviceUnavailable("Configured inverter was not returned by AlphaESS")
        row = await self.call("getLastPowerData", self.serial)
        if not isinstance(row, dict):
            raise DeviceUnavailable("Missing power telemetry")
        observed = row.get("uploadTime") or row.get("time") or row.get("createTime")
        if observed is None:
            # getLastPowerData has no timestamp on several real systems. Use the
            # newest timestamped historical sample, never local receipt time.
            from .domain import utc_now

            history = await self.power_history(utc_now().astimezone(PRAGUE).date())
            candidates = []
            for sample in history or []:
                try:
                    sampled = datetime.fromisoformat(
                        str(sample["uploadTime"]).replace("Z", "+00:00")
                    )
                    if sampled.tzinfo is None:
                        sampled = sampled.replace(tzinfo=PRAGUE)
                    candidates.append((sampled, sample))
                except (KeyError, ValueError, TypeError):
                    continue
            if not candidates:
                raise DeviceUnavailable("No timestamped battery samples are available")
            stamp, sample = max(candidates, key=lambda item: item[0].timestamp())
            grid = (float(sample["gridCharge"]) - float(sample["feedIn"])) / 1000
            load = max(0, float(sample["load"]) / 1000)
            pv = max(0, float(sample["ppv"]) / 1000)
            return Telemetry(
                stamp.astimezone(UTC).isoformat(),
                float(sample["cbat"]),
                float(unit["cobat"]),
                load,
                pv,
                grid,
                load - pv - grid,
                str(unit.get("minv", "unknown")),
            )
        if isinstance(observed, (int, float)):
            stamp = datetime.fromtimestamp(
                observed / 1000 if observed > 1e11 else observed, UTC
            )
        else:
            stamp = datetime.fromisoformat(str(observed).replace("Z", "+00:00"))
            if stamp.tzinfo is None:
                stamp = stamp.replace(tzinfo=PRAGUE)
        # Public API powers are W, nominal cobat is kWh. Do not subtract reserve twice.
        return Telemetry(
            stamp.astimezone(UTC).isoformat(),
            float(row["soc"]),
            float(unit["cobat"]),
            max(0, float(row["pload"]) / 1000),
            max(0, float(row["ppv"]) / 1000),
            float(row["pgrid"]) / 1000,
            float(row["pbat"]) / 1000,
            str(unit.get("minv", "unknown")),
        )

    async def power_history(self, day):
        return await self.call("getOneDayPowerBySn", self.serial, day.isoformat())

    async def daily_energy(self, day):
        return await self.call("getOneDateEnergyBySn", self.serial, day.isoformat())

    async def read_config(self):
        periodic = await self.call("getTimeChargeBySn", self.serial)
        native = await self.call("getDisChargeConfigInfo", self.serial)
        charge = await self.call("getChargeConfigInfo", self.serial)
        if not isinstance(periodic, dict) or not isinstance(native, dict):
            raise DeviceUnavailable(
                "Could not read periodic schedules and native reserve"
            )
        if not isinstance(charge, dict):
            raise DeviceUnavailable("Could not read legacy charging controls")
        native = {**native, "legacy_grid_charge": charge.get("gridCharge")}
        return periodic, native

    @staticmethod
    def require_native_baseline(native, reserve):
        if float(native.get("batUseCap", -1)) != reserve:
            raise DeviceUnavailable(
                "Appliance native reserve must already be set to 10%"
            )
        if native.get("ctrDis") != 0 or native.get("legacy_grid_charge") != 0:
            raise DeviceUnavailable(
                "Legacy schedules are still active; disable them once before commissioning the periodic controller"
            )

    @staticmethod
    def valid_periods(items):
        if not isinstance(items, list) or not items:
            return False
        for p in items:
            if not isinstance(p, dict):
                return False
            try:
                begin = datetime.strptime(p["beginTime"], "%H:%M")
                end = datetime.strptime(p["endTime"], "%H:%M")
                if begin >= end or begin.minute % 15 or end.minute % 15:
                    return False
                if not 10 <= float(p["chargeLimit"]) <= 100:
                    return False
                watts = p["chargePower"]
                if (
                    not isinstance(watts, (int, float))
                    or watts <= 0
                    or int(watts) != watts
                ):
                    return False
            except (KeyError, ValueError, TypeError):
                return False
        return True

    async def write(self, payload, guard=lambda: True):
        # Both sides are mandatory even when disabled; no empty-list writes.
        if not all(
            self.valid_periods(payload.get(key))
            for key in ("chargeTimeList", "dischargeTimeList")
        ):
            raise DeviceUnavailable(
                "Device periodic store lacks valid nonempty power-limited periods"
            )
        await self.call("setTimeChargeBySn", self.serial, _write_guard=guard, **payload)
        readback = await self.call("getTimeChargeBySn", self.serial)
        if not isinstance(readback, dict):
            raise DeviceUnavailable("Schedule accepted but readback unavailable")
        for key in ("chargeTimeList", "dischargeTimeList"):
            expected = [
                {
                    k: v
                    for k, v in p.items()
                    if k
                    in ("beginTime", "endTime", "chargeLimit", "chargePower", "weeks")
                }
                for p in payload[key]
            ]
            actual = [
                {
                    k: v
                    for k, v in p.items()
                    if k
                    in ("beginTime", "endTime", "chargeLimit", "chargePower", "weeks")
                }
                for p in readback.get(key, [])
            ]
            if expected != actual:
                raise DeviceUnavailable(
                    "Schedule readback differs; physical execution unconfirmed"
                )
        return {
            "status": "accepted",
            "periods_verified": True,
            "physical_execution_verified": False,
            "flags_verified": False,
        }

    @staticmethod
    def preserved(periodic):
        return {
            "executeCycleType": periodic.get("executeCycleType", 0),
            "chargeTimeList": periodic.get("chargeTimeList"),
            "dischargeTimeList": periodic.get("dischargeTimeList"),
            "gridChargeCycle": 0,
            "ctrDisCycle": 0,
        }

    async def native(self, reserve, verified, guard=lambda: True):
        if not verified:
            raise DeviceUnavailable(
                "Native self-consumption controls have not been verified on this inverter"
            )
        periodic, native = await self.read_config()
        self.require_native_baseline(native, reserve)
        if not guard():
            raise DeviceUnavailable("Control changed; native restore cancelled")
        return await self.write(self.preserved(periodic), guard=guard)

    async def apply(self, decision, settings, now, guard=lambda: True):
        if not guard():
            raise DeviceUnavailable("Control changed; schedule write cancelled")
        if not settings.device_control_verified:
            raise DeviceUnavailable(
                "Device control semantics have not been verified on this inverter"
            )
        if decision.action in ("solar", "self_consume"):
            return await self.native(settings.reserve_percent, True, guard=guard)
        if decision.action == "hold" and not settings.hold_verified:
            raise DeviceUnavailable(
                "Battery hold behavior has not been verified on this inverter"
            )
        periodic, native = await self.read_config()
        self.require_native_baseline(native, settings.reserve_percent)
        start = datetime.fromisoformat(decision.timestamp).astimezone(PRAGUE)
        end = (start.astimezone(UTC) + timedelta(minutes=15)).astimezone(PRAGUE)
        if (
            end.astimezone(UTC) <= now
            or start.date() != end.date()
            or end.hour == 23
            and end.minute > 45
            or start.utcoffset() != end.utcoffset()
        ):
            # Avoid undocumented midnight and repeated-hour dispatch. Native is safe.
            return await self.native(settings.reserve_percent, True, guard=guard)
        if len([t for t in (start, end) if t.fold]) > 0:
            return await self.native(settings.reserve_percent, True, guard=guard)
        payload = self.preserved(periodic)
        payload["executeCycleType"] = 0
        payload["gridChargeCycle"] = 1
        target = (
            settings.reserve_percent
            if decision.action == "hold"
            else min(
                settings.maximum_percent,
                max(settings.reserve_percent, decision.soc_percent),
            )
        )
        period = {
            "beginTime": start.strftime("%H:%M"),
            "endTime": end.strftime("%H:%M"),
            "chargeLimit": int(target),
            "chargePower": 100
            if decision.action == "hold"
            else max(
                100, round(min(settings.charge_kw, decision.grid_charge_kw) * 1000)
            ),
        }
        payload["chargeTimeList"] = [period]
        # Preserve disabled discharge store. Reject overlap instead of inventing data.
        for p in payload.get("dischargeTimeList") or []:
            if (
                period["beginTime"] < p["endTime"]
                and p["beginTime"] < period["endTime"]
            ):
                raise DeviceUnavailable(
                    "New charging interval overlaps the existing discharge store"
                )
        if not guard():
            raise DeviceUnavailable("Control changed; schedule write cancelled")
        return await self.write(payload, guard=guard)

    async def close(self):
        await self.session.close()
