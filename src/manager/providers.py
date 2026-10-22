"""Forecast and price ingestion. Cache immutable published prices durably."""

from datetime import date, datetime, timedelta
import math
from pathlib import Path
import statistics
import os
import xml.etree.ElementTree as ET

import httpx
from ote_cr_price_fetcher import PriceFetcher
import yaml

from .domain import PRAGUE, UTC, Slot, day_slots, next_boundary
from .tariff import high_tariff, prices, schedule_verified
from .load_profile import expected_profile, LOAD_PROFILE_VERSION, charging_load_ceiling
from .consumption import household_training_history, EV_ESTIMATOR_VERSION


def parse_entsoe(xml, expected):
    root = ET.fromstring(xml)
    tag = lambda e: e.tag.rsplit("}", 1)[-1]

    def child(element, name):
        return next((x for x in element if tag(x) == name), None)

    def value(element, name):
        node = child(element, name)
        return node.text if node is not None else None

    found = {}
    for series in root.iter():
        if tag(series) != "TimeSeries":
            continue
        if (
            value(series, "currency_Unit.name") != "EUR"
            or value(series, "price_Measure_Unit.name") != "MWH"
        ):
            raise ValueError("ENTSO-E prices must be EUR/MWh")
        for period in series:
            if tag(period) != "Period":
                continue
            interval = child(period, "timeInterval")
            start = datetime.fromisoformat(
                value(interval, "start").replace("Z", "+00:00")
            )
            resolution = value(period, "resolution")
            minutes = {"PT15M": 15, "PT60M": 60}.get(resolution)
            if minutes is None:
                raise ValueError("Unsupported ENTSO-E resolution")
            for point in period:
                if tag(point) != "Point":
                    continue
                position = int(value(point, "position"))
                amount = float(value(point, "price.amount"))
                for j in range(minutes // 15):
                    instant = start + timedelta(
                        minutes=(position - 1) * minutes + j * 15
                    )
                    key = instant.astimezone(UTC).isoformat()
                    if key in found and found[key] != amount:
                        raise ValueError("Conflicting ENTSO-E intervals")
                    found[key] = amount
    result = [found.get(t.isoformat()) for t in expected]
    if any(x is None or not math.isfinite(x) for x in result):
        raise ValueError("Incomplete ENTSO-E day-ahead prices")
    return result


class Providers:
    def __init__(self, settings, store):
        self.settings = settings
        self.store = store

    async def market(self, day):
        key = f"prices:{self.settings.price_source}:{day}"
        cached = self.store.get(key)
        expected = day_slots(day)
        if cached is not None and len(cached) == len(expected):
            return cached
        if self.settings.price_source == "entsoe":
            token = os.getenv("ENTSOE_TOKEN")
            if not token:
                raise ValueError(
                    "ENTSOE_TOKEN is required for ENTSO-E; OTE is also supported"
                )
            end = day_slots(day + timedelta(days=1))[0]
            params = {
                "securityToken": token,
                "documentType": "A44",
                "in_Domain": self.settings.entsoe_area,
                "out_Domain": self.settings.entsoe_area,
                "periodStart": expected[0].strftime("%Y%m%d%H%M"),
                "periodEnd": end.strftime("%Y%m%d%H%M"),
            }
            try:
                async with httpx.AsyncClient(timeout=20) as client:
                    response = await client.get(
                        "https://web-api.tp.entsoe.eu/api", params=params
                    )
                    response.raise_for_status()
                    values = parse_entsoe(response.text, expected)
            except httpx.HTTPError:
                raise ValueError("ENTSO-E request failed") from None
        else:
            fetcher = PriceFetcher(max_retry_time=10, timeout=10, connect_timeout=10)
            try:
                values = await fetcher.fetch_prices_for_date(day, hourly=False)
            except Exception:
                raise ValueError(f"OTE prices unavailable for {day}") from None
        if len(values) != len(expected) or any(not math.isfinite(x) for x in values):
            raise ValueError(
                f"Incomplete prices for {day}: expected {len(expected)} intervals"
            )
        self.store.put(key, values)
        return values

    async def fx(self, now):
        cached = self.store.get("eur_conversion")
        if cached and cached.get("fetched_day") == str(now.astimezone(PRAGUE).date()):
            return cached["rate"]
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                response = await client.get(
                    "https://www.cnb.cz/en/financial_markets/foreign_exchange_market/exchange_rate_fixing/daily.txt"
                )
                response.raise_for_status()
            lines = response.text.splitlines()
            published = datetime.strptime(lines[0].split(" #")[0], "%d %b %Y").date()
            if not 0 <= (now.astimezone(PRAGUE).date() - published).days <= 7:
                raise ValueError("CNB conversion rate is stale")
            rate = next(
                float(row.split("|")[4])
                for row in lines[2:]
                if row.split("|")[3] == "EUR"
            )
            if not 10 < rate < 100:
                raise ValueError("Invalid EUR conversion rate")
            self.store.put(
                "eur_conversion",
                {
                    "rate": rate,
                    "published": str(published),
                    "fetched_day": str(now.astimezone(PRAGUE).date()),
                },
            )
            return rate
        except Exception:
            if (
                cached
                and 0
                <= (
                    now.astimezone(PRAGUE).date()
                    - date.fromisoformat(cached["published"])
                ).days
                <= 7
            ):
                return cached["rate"]
            raise ValueError("No recent CNB EUR conversion rate") from None

    async def forecast(self, instants, device, telemetry, now):
        cached = self.store.get("load_profile")
        estimator = LOAD_PROFILE_VERSION + (
            ":" + EV_ESTIMATOR_VERSION
            if self.settings.exclude_inferred_grid_only_ev
            else ""
        )
        if (
            not cached
            or cached.get("day") != str(now.astimezone(PRAGUE).date())
            or cached.get("estimator") != estimator
        ):
            history = []
            for offset in range(1, 8):
                day = now.astimezone(PRAGUE).date() - timedelta(days=offset)
                rows = await device.power_history(day)
                history.append((day, rows))
            training = (
                household_training_history(history, now)
                if self.settings.exclude_inferred_grid_only_ev
                else history
            )
            cached = {
                "day": str(now.astimezone(PRAGUE).date()),
                "values": expected_profile(training, now),
                "estimator": estimator,
                "ev_separation": "inferred grid-only sessions"
                if self.settings.exclude_inferred_grid_only_ev
                else "disabled",
                "inferred_ev_samples": sum(
                    bool(r.get("ev_grid_only")) for _, rows in training for r in rows
                ),
                "ceiling_values": charging_load_ceiling(history, now),
            }
            self.store.put("load_profile", cached)
        values = cached["values"]
        load = [
            values.get(
                str(t.astimezone(PRAGUE).hour * 4 + t.astimezone(PRAGUE).minute // 15),
                telemetry.load_kw,
            )
            for t in instants
        ]
        config = yaml.safe_load(Path(self.settings.solar_config_path).read_text())
        weather_key = "solar_forecast"
        cache = self.store.get(weather_key)
        if (
            not cache
            or (now - datetime.fromisoformat(cache["fetched"])).total_seconds() > 3600
        ):
            params = {
                "latitude": config["latitude"],
                "longitude": config["longitude"],
                "tilt": config.get("tilt", 15),
                "azimuth": config.get("azimuth", -30),
                "timezone": "UTC",
                "hourly": "global_tilted_irradiance,temperature_2m",
                "forecast_days": 3,
                "past_days": 7,
            }
            async with httpx.AsyncClient(timeout=20) as client:
                response = await client.get(
                    "https://api.open-meteo.com/v1/forecast", params=params
                )
                response.raise_for_status()
                data = response.json()["hourly"]
            cache = {"fetched": now.isoformat(), "data": data}
            self.store.put(weather_key, cache)
        data = cache["data"]
        # Calibrate effective PV kW against last week's actual production instead of
        # guessing panel nameplate from a proposed future installation.
        scale = self.store.get("pv_scale")
        if (
            not scale
            or scale.get("irradiance_alignment_version") != 2
            or (now - datetime.fromisoformat(scale["fetched"])).total_seconds() > 86400
        ):
            day = now.astimezone(PRAGUE).date() - timedelta(days=1)
            actual = await device.power_history(day)
            solar_samples = []
            irradiance = {
                datetime.fromisoformat(t).replace(tzinfo=UTC).isoformat(): float(g or 0)
                for t, g in zip(data["time"], data["global_tilted_irradiance"])
            }
            for row in actual or []:
                try:
                    t = datetime.fromisoformat(
                        str(row.get("time") or row["uploadTime"])
                    )
                    if t.tzinfo is None:
                        t = t.replace(tzinfo=PRAGUE)
                    # Open-Meteo irradiance is averaged over the PRECEDING hour.
                    # Samples within 11:00-12:00 therefore use the 12:00 value.
                    t = t.astimezone(UTC).replace(
                        minute=0, second=0, microsecond=0
                    ) + timedelta(hours=1)
                    g = irradiance.get(t.isoformat(), 0)
                    if g >= 200 and float(row["ppv"]) > 0:
                        solar_samples.append(float(row["ppv"]) / g)
                except (ValueError, TypeError, KeyError):
                    continue
            if len(solar_samples) < 6:
                # A dark day is not evidence that panel capacity has changed.
                # Keep a correctly aligned, bounded-age calibration without
                # renewing its original timestamp. Never invent zero PV.
                usable = (
                    scale
                    and scale.get("irradiance_alignment_version") == 2
                    and math.isfinite(scale.get("kw", float("nan")))
                    and scale["kw"] > 0
                    and 0
                    <= (now - datetime.fromisoformat(scale["fetched"])).total_seconds()
                    <= 7 * 86400
                )
                if not usable:
                    raise ValueError(
                        "Insufficient measured solar production to calibrate PV forecast"
                    )
            else:
                scale = {
                    "fetched": now.isoformat(),
                    "kw": statistics.median(solar_samples),
                    "irradiance_alignment_version": 2,
                }
                self.store.put("pv_scale", scale)
        solar = {
            datetime.fromisoformat(t).replace(tzinfo=UTC).isoformat(): max(
                0, float(g or 0)
            )
            / 1000
            * scale["kw"]
            * 0.8
            for t, g in zip(data["time"], data["global_tilted_irradiance"])
        }
        predicted = []
        for t in instants:
            key = (
                t.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
            ).isoformat()
            if key not in solar:
                raise ValueError("Solar forecast does not cover priced horizon")
            predicted.append(solar[key])
        return load, predicted

    async def slots(self, now, device, telemetry):
        today = now.astimezone(PRAGUE).date()
        instant_prices = []
        notes = []
        for offset in (0, 1):
            day = today + timedelta(days=offset)
            if (
                offset
                and now.astimezone(PRAGUE).hour < self.settings.publication_hour
                and not self.store.get(f"prices:{self.settings.price_source}:{day}")
            ):
                notes.append(
                    "Tomorrow's prices are normally published around 15:00 local time."
                )
                continue
            try:
                values = await self.market(day)
            except ValueError:
                if offset:
                    notes.append(
                        "Tomorrow's prices are not available yet; using the published horizon."
                    )
                    continue
                raise
            instant_prices.extend(zip(day_slots(day), values))
        instant_prices = [(t, p) for t, p in instant_prices if t >= next_boundary(now)]
        if len(instant_prices) < 4:
            raise ValueError("Fewer than one hour of published future prices")
        fx = await self.fx(now)
        load, solar = await self.forecast(
            [t for t, _ in instant_prices], device, telemetry, now
        )
        if not all(schedule_verified(t, self.settings) for t, _ in instant_prices):
            notes.append(
                "Some HDO tariff times are unverified; displaying high-tariff costs conservatively for those intervals."
            )
        slots = []
        for (t, p), l, s in zip(instant_prices, load, solar):
            high = (
                True
                if not schedule_verified(t, self.settings)
                else high_tariff(t, self.settings.high_tariff_periods)
            )
            buy, sell = prices(p, fx, high)
            slots.append(
                Slot(
                    t.isoformat(),
                    p,
                    buy,
                    sell,
                    l,
                    s,
                    "VT" if high else "NT",
                    self.store.get("load_profile", {})
                    .get("ceiling_values", {})
                    .get(
                        str(
                            t.astimezone(PRAGUE).hour * 4
                            + t.astimezone(PRAGUE).minute // 15
                        )
                    ),
                )
            )
        return slots, notes
