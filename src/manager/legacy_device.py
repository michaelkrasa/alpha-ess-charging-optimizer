"""Two-window API adapter. Callers own the durable execution lease.

Repeated writes are supported by this household. Persist attempts and apply a
short configurable cooldown; always read back before claiming acceptance.
"""

from datetime import datetime, timedelta

from .device import AlphaDevice, DeviceUnavailable
from .domain import utc_now

CHARGE_FIELDS = (
    "gridCharge",
    "batHighCap",
    "timeChaf1",
    "timeChae1",
    "timeChaf2",
    "timeChae2",
)


DISCHARGE_FIELDS = (
    "ctrDis",
    "batUseCap",
    "timeDisf1",
    "timeDise1",
    "timeDisf2",
    "timeDise2",
)


class LegacyDevice(AlphaDevice):
    minimum_write_interval_seconds = 10

    def __init__(self, store, minimum_write_interval_seconds=10):
        super().__init__()
        self.store = store
        self.minimum_write_interval_seconds = minimum_write_interval_seconds

    async def read_config(self):
        charge = await self.call("getChargeConfigInfo", self.serial)
        native = await self.call("getDisChargeConfigInfo", self.serial)
        if (
            not isinstance(charge, dict)
            or not isinstance(native, dict)
            or not set(CHARGE_FIELDS) <= charge.keys()
            or not set(DISCHARGE_FIELDS) <= native.keys()
        ):
            raise DeviceUnavailable(
                "Standard daily schedule configuration is unavailable"
            )
        return charge, native

    @staticmethod
    def require_daily_baseline(native, reserve):
        if float(native.get("batUseCap", -1)) != reserve:
            raise DeviceUnavailable(
                "Appliance native reserve must already match the configured reserve"
            )

    @staticmethod
    def validate_settings(payload, kind):
        fields = CHARGE_FIELDS if kind == "charge" else DISCHARGE_FIELDS
        flag, cap, prefix = (
            ("gridCharge", "batHighCap", "timeCha")
            if kind == "charge"
            else ("ctrDis", "batUseCap", "timeDis")
        )
        if not set(fields) <= payload.keys():
            raise DeviceUnavailable("Incomplete daily charge schedule")
        if payload[flag] not in (0, 1) or not 10 <= payload[cap] <= 100:
            raise DeviceUnavailable("Invalid daily charge settings")
        periods = []
        for index in (1, 2):
            start, end = payload[f"{prefix}f{index}"], payload[f"{prefix}e{index}"]
            try:
                a, b = (datetime.strptime(v, "%H:%M") for v in (start, end))
                if a.minute % 15 or b.minute % 15 or a > b:
                    raise ValueError
            except (ValueError, TypeError):
                raise DeviceUnavailable("Invalid daily charge period") from None
            if a != b:
                periods.append((start, end))
        if len(periods) == 2 and max(p[0] for p in periods) < min(
            p[1] for p in periods
        ):
            raise DeviceUnavailable("Daily charge periods overlap")
        if payload[flag] and not periods:
            raise DeviceUnavailable(
                "Enabled daily time control needs at least one valid period"
            )
        return periods

    async def write_settings(self, payload, current, guard, kind="charge"):
        fields = CHARGE_FIELDS if kind == "charge" else DISCHARGE_FIELDS
        key = "legacy_" + kind + "_write"
        read_method = (
            "getChargeConfigInfo" if kind == "charge" else "getDisChargeConfigInfo"
        )
        write_method = (
            "updateChargeConfigInfo"
            if kind == "charge"
            else "updateDisChargeConfigInfo"
        )
        self.validate_settings(payload, kind)
        if not guard():
            raise DeviceUnavailable("Control changed; daily schedule write cancelled")
        if all(current.get(k) == payload[k] for k in fields):
            return {
                "status": "accepted",
                "schedule_verified": True,
                "physical_execution_verified": False,
                "message": "Daily charge settings already match.",
            }
        now = utc_now()
        budget = self.store.get(key, {})
        if budget.get("attempted_at"):
            next_at = datetime.fromisoformat(budget["attempted_at"]) + timedelta(
                seconds=self.minimum_write_interval_seconds
            )
            if now < next_at:
                raise DeviceUnavailable(
                    f"API cooldown is in effect until {next_at.isoformat()}; use the AlphaESS app for an urgent manual change"
                )
        # A timeout may have reached the inverter. Persist the attempt; callers
        # read current settings before another replacement, rather than blindly retrying.
        self.store.put(
            key,
            {"attempted_at": now.isoformat(), "requested": payload},
        )

        def transmission_guard():
            if not guard():
                return False
            # AlphaDevice.call may wait for polling rate limits. The next write
            # is measured from transmission, not from entering that wait.
            self.store.put(
                key,
                {"attempted_at": utc_now().isoformat(), "requested": payload},
            )
            return True

        await self.call(
            write_method,
            self.serial,
            _write_guard=transmission_guard,
            **payload,
        )
        readback = await self.call(read_method, self.serial)
        if not isinstance(readback, dict) or any(
            readback.get(k) != payload[k] for k in fields
        ):
            raise DeviceUnavailable(
                "Daily schedule write is unconfirmed; readback differs or is unavailable"
            )
        return {
            "status": "accepted",
            "schedule_verified": True,
            "physical_execution_verified": False,
            "next_write_at": (
                datetime.fromisoformat(self.store.get(key)["attempted_at"])
                + timedelta(seconds=self.minimum_write_interval_seconds)
            ).isoformat(),
            "message": "Daily schedule settings confirmed by API readback; measured battery activity is reported separately.",
        }

    async def write_charge(self, payload, current, guard):
        return await self.write_settings(payload, current, guard, "charge")

    def preflight(self, changes):
        for kind, payload, current in changes:
            fields = CHARGE_FIELDS if kind == "charge" else DISCHARGE_FIELDS
            if all(current.get(k) == payload[k] for k in fields):
                continue
            budget = self.store.get("legacy_" + kind + "_write", {})
            if budget.get("attempted_at"):
                until = datetime.fromisoformat(budget["attempted_at"]) + timedelta(
                    seconds=self.minimum_write_interval_seconds
                )
                if utc_now() < until:
                    raise DeviceUnavailable(
                        f"{kind} API cooldown is in effect until {until.isoformat()}; existing settings remain in force"
                    )

    async def replace_pair(
        self, charge, discharge, current_charge, current_discharge, guard
    ):
        changes = [
            ("discharge", discharge, current_discharge),
            ("charge", charge, current_charge),
        ]
        cp = self.validate_settings(charge, "charge")
        dp = self.validate_settings(discharge, "discharge")
        if (
            charge["gridCharge"]
            and discharge["ctrDis"]
            and any(a < y and x < b for a, b in cp for x, y in dp)
        ):
            raise DeviceUnavailable(
                "Charge and household discharge windows must not overlap"
            )

        def safe_pair(c, d):
            # Disabled controls do not activate their stored periods. In
            # particular, OFF must be able to clear an overlapping old pair.
            if not c["gridCharge"] or not d["ctrDis"]:
                return True
            charge_periods = self.validate_settings(c, "charge")
            discharge_periods = self.validate_settings(d, "discharge")
            return not any(
                a < y and x < b for a, b in charge_periods for x, y in discharge_periods
            )

        # These endpoints are not atomic and a second write may fail or be
        # cancelled. The first confirmed write must leave a valid mixed pair
        # even if a subsequent call fails, not only during the inter-call gap.
        if not safe_pair(current_charge, discharge):
            if safe_pair(charge, current_discharge):
                changes.reverse()
            else:
                # With repeatable writes, a window swap can be staged safely:
                # stop grid charge, replace discharge, then install new charge.
                # A failure leaves charging disabled rather than overlapping.
                stopped = {k: current_charge[k] for k in CHARGE_FIELDS}
                stopped["gridCharge"] = 0
                changes = [
                    ("charge", stopped, current_charge),
                    ("discharge", discharge, current_discharge),
                    ("charge", charge, stopped),
                ]
        self.preflight(changes)
        if not guard():
            raise DeviceUnavailable("Control changed; daily schedule pair cancelled")
        if any(
            any(
                current.get(k) != payload[k]
                for k in (CHARGE_FIELDS if kind == "charge" else DISCHARGE_FIELDS)
            )
            for kind, payload, current in changes
        ):
            # A partial/ambiguous pair must never look like a confirmed current
            # schedule. Clear this only after both readbacks match.
            self.store.put(
                "device_schedule",
                {
                    **self.store.get("device_schedule", {}),
                    "readback_pending": True,
                },
            )
        results = []
        for kind, payload, current in changes:
            try:
                results.append(await self.write_settings(payload, current, guard, kind))
            except DeviceUnavailable as exc:
                raise DeviceUnavailable(
                    "Daily schedule pair is unconfirmed; endpoints are not atomic. "
                    + str(exc)
                ) from None
        if guard():
            self.store.put(
                "device_schedule",
                {
                    "charge": charge,
                    "discharge": discharge,
                    "checked_at": utc_now().isoformat(),
                    "readback_pending": False,
                    "source": "pair_readback",
                },
            )
        return {
            "status": "accepted",
            "schedule_verified": True,
            "physical_execution_verified": False,
            "message": "Charge and house-supply settings confirmed by API readback; measured battery activity is reported separately.",
            "endpoints": results,
        }

    async def native(self, reserve, verified, guard=lambda: True):
        if not verified:
            raise DeviceUnavailable(
                "Daily API native self-consumption and configured charging power are not verified on this inverter"
            )
        charge, native = await self.read_config()
        self.require_daily_baseline(native, reserve)
        payload = {k: charge[k] for k in CHARGE_FIELDS}
        payload["gridCharge"] = 0
        discharge = {k: native.get(k, "00:00") for k in DISCHARGE_FIELDS}
        discharge.update(ctrDis=0, batUseCap=reserve)
        return await self.replace_pair(payload, discharge, charge, native, guard)

    async def apply_daily(self, plan, settings, now, guard=lambda: True):
        if not settings.device_control_verified:
            raise DeviceUnavailable(
                "Daily API physical behavior has not been commissioned"
            )
        if not (
            datetime.fromisoformat(plan["valid_from"]) <= now + timedelta(minutes=15)
            and now < datetime.fromisoformat(plan["valid_until"])
        ):
            raise DeviceUnavailable("Daily forecast plan is outside its valid horizon")
        charge, native = await self.read_config()
        self.require_daily_baseline(native, settings.reserve_percent)
        return await self.replace_pair(
            plan["charge"], plan["discharge"], charge, native, guard
        )
