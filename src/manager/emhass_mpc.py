"""EMHASS rolling MPC with AlphaESS's executable battery semantics.

EMHASS owns the energy balance, SOC model, economic objective and rolling
solver. This adapter adds fixed-power grid charging, automatic PV capture and
permission-only household discharge. It never sends device commands.
"""

import csv
import json
import logging
import time
from copy import deepcopy
from datetime import datetime
from pathlib import Path

import cvxpy as cp
import emhass
import numpy as np
import pandas as pd
from emhass.optimization import Optimization

from .daily import validate_standard_horizon
from .domain import Decision
from .charge_policy import buffer_request, maintenance_target


def _defaults():
    root = Path(emhass.__file__).parent / "data"
    flat = json.loads((root / "config_defaults.json").read_text())
    grouped = {
        k: {}
        for k in ("retrieve_hass_conf", "optim_conf", "plant_conf", "params_secrets")
    }
    with (root / "associations.csv").open() as f:
        for group, _, key, *_ in list(csv.reader(f))[1:]:
            if key in flat:
                grouped[group][key] = flat[key]
    return grouped


class ApplianceOptimization(Optimization):
    """Small, version-pinned constraint extension to EMHASS 0.18.6."""

    def __init__(self, *args, appliance_settings, exact=True, **kwargs):
        self.appliance_settings = appliance_settings
        self.exact = exact
        n = kwargs["num_timesteps"]
        self.native_charge = cp.Parameter(
            n, nonneg=True, name="native_pv_capture_limit"
        )
        self.native_discharge = cp.Parameter(
            n, nonneg=True, name="native_house_supply_limit"
        )
        self.charge_mode_allowed = cp.Parameter(
            n, nonneg=True, name="charge_headroom_allowed"
        )
        self.terminal_value = cp.Parameter(
            nonneg=True, name="stored_energy_value_eur_wh"
        )
        self.hedge_anchor = cp.Parameter(n, nonneg=True)
        self.hedge_floor = cp.Parameter()
        self.hedge_limit = cp.Parameter(nonneg=True)
        self.hedge_reward = cp.Parameter(nonneg=True)
        self.full_anchor = cp.Parameter(n, nonneg=True)
        self.full_required = cp.Parameter(nonneg=True)
        self.full_finish = cp.Parameter(n, nonneg=True)
        self.hedged_energy = cp.Variable(nonneg=True)
        super().__init__(*args, **kwargs)

    def _build_objective_function(self, *args, **kwargs):
        base = super()._build_objective_function(*args, **kwargs)
        # Replace the stock terminal target's over-SOC penalty with an explicit
        # residual energy value. The reserve target is also the physical floor.
        correction = (self.terminal_value - self.param_soc_final_reward) * sum(
            self.vars["soc_final_over"]
        )
        return cp.Maximize(
            base.args[0] + correction + self.hedge_reward * self.hedged_energy
        )

    def _add_battery_constraints(self, constraints, batt_stress_conf):
        super()._add_battery_constraints(constraints, batt_stress_conf)
        if not self.exact:
            return
        s = self.appliance_settings
        n = self.num_timesteps
        dt = self.time_step
        cap = self.plant_conf["battery_nominal_energy_capacity"]
        charge = -self.vars["p_sto_neg"][0]
        discharge = self.vars["p_sto_pos"][0]
        mode_c = cp.Variable(n, boolean=True, name="appliance_grid_charge")
        mode_d = cp.Variable(n, boolean=True, name="appliance_house_supply")
        full = cp.Variable(n, boolean=True, name="appliance_charge_saturated")
        empty = cp.Variable(n, boolean=True, name="appliance_discharge_saturated")
        self.appliance_modes = (mode_c, mode_d)
        if s.charge_step_minutes < 15:
            units = cp.Variable(n, integer=True, name="charge_five_minute_units")
            constraints.extend([units >= mode_c, units <= 3 * mode_c])
            self.charge_fraction = units / 3
        else:
            self.charge_fraction = mode_c
        delta = (charge * s.charge_efficiency - discharge / s.discharge_efficiency) * dt
        previous = cp.hstack(
            [
                self.param_soc_init[0] * cap,
                self.param_soc_init[0] * cap + cp.cumsum(delta)[:-1],
            ]
        )
        charge_space = (cap * s.maximum_percent / 100 - previous) / (
            s.charge_efficiency * dt
        )
        discharge_space = (
            (previous - cap * s.reserve_percent / 100) * s.discharge_efficiency / dt
        )
        target_c = self.native_charge + cp.multiply(
            s.charge_kw * 1000 - self.native_charge, self.charge_fraction
        )
        target_d = cp.multiply(self.native_discharge, mode_d)
        mc = cap / (s.charge_efficiency * dt) + s.charge_kw * 1000
        md = cap * s.discharge_efficiency / dt + s.discharge_kw * 1000
        constraints.extend(
            [
                mode_c + mode_d <= 1,
                mode_c <= self.charge_mode_allowed,
                self.charge_fraction >= self.full_finish,
                charge <= target_c,
                charge <= charge_space,
                charge >= target_c - mc * full,
                charge >= charge_space - mc * (1 - full),
                discharge <= target_d,
                # Below-reserve telemetry is valid during native balancing.
                # An inactive house-supply mode must allow zero discharge even
                # when raw available energy is negative. When enabled, retain
                # the exact reserve bound; this cannot authorize discharge
                # below the reserve or alter the measured starting SOC.
                discharge <= discharge_space + md * (1 - mode_d),
                discharge >= target_d - md * empty,
                discharge >= discharge_space - md * (1 - empty),
            ]
        )
        cumulative = cp.cumsum(delta)
        constraints.extend(
            [
                self.hedged_energy <= self.hedge_limit,
                self.hedged_energy
                <= cp.sum(cp.multiply(self.hedge_anchor, cumulative))
                - self.hedge_floor,
                cp.sum(cp.multiply(self.full_anchor, cumulative)) >= self.full_required,
            ]
        )
        if s.import_limit_kw is not None:
            # Explicit user configuration only. No cap is inferred by default.
            bound = self.plant_conf["maximum_power_from_grid"]
            net_load = self.param_load_forecast - self.param_pv_forecast
            constraints.append(
                net_load + s.charge_kw * 1000
                <= s.import_limit_kw * 1000 + bound * (1 - mode_c)
            )


class EmhassPlanner:
    """Reuse EMHASS's parameterized problems across quarter-hour replans."""

    def __init__(self, settings, exact=True, terminal="matched"):
        self.settings = settings
        self.exact = exact
        self.terminal = terminal
        self.cache = {}
        self.last_solve = None
        self.logger = logging.getLogger("household.emhass")
        self.logger.setLevel(logging.ERROR)

    def _optimizer(self, slots, capacity):
        s = self.settings
        n = len(slots)
        needed = max(r.load_kw for r in slots) * 1000 + s.charge_kw * 1000 + 1
        pv_bound = max(r.solar_kw for r in slots) * 1000 + 1
        key = (n, capacity, self.exact)
        cached = self.cache.get(key)
        if (
            cached
            and cached.plant_conf["maximum_power_from_grid"] >= needed
            and cached.plant_conf["maximum_power_to_grid"] >= pv_bound
        ):
            return cached
        grouped = _defaults()
        retrieve, optim, plant = (
            deepcopy(grouped[k])
            for k in ("retrieve_hass_conf", "optim_conf", "plant_conf")
        )
        retrieve.update(
            optimization_time_step=pd.Timedelta(minutes=15), time_zone="Europe/Prague"
        )
        optim.update(
            set_use_pv=True,
            set_use_battery=True,
            number_of_deferrable_loads=0,
            set_nocharge_from_grid=False,
            set_nodischarge_to_grid=True,
            set_battery_first_priority=False,
            set_battery_dynamic=False,
            weight_battery_discharge=s.wear_eur_per_kwh,
            weight_battery_charge=0.0,
            set_total_pv_sell=False,
            num_threads=1,
            # A 0.1% objective gap is stricter than EMHASS's 1% default, while
            # avoiding excessive branch-and-bound work on low-power ARM hosts.
            lp_solver_mip_rel_gap=0.001,
            lp_solver_timeout=60,
        )
        # These are nonbinding numerical bounds derived from physical capacity
        # and the supplied forecasts, never a household grid-import policy.
        numerical_margin = capacity * 1000 / 0.25
        plant.update(
            inverter_is_hybrid=False,
            compute_curtailment=False,
            battery_nominal_energy_capacity=capacity * 1000,
            battery_minimum_state_of_charge=s.reserve_percent / 100,
            battery_maximum_state_of_charge=s.maximum_percent / 100,
            battery_target_state_of_charge=s.reserve_percent / 100,
            battery_charge_efficiency=s.charge_efficiency,
            battery_discharge_efficiency=s.discharge_efficiency,
            battery_charge_power_max=s.charge_kw * 1000,
            battery_discharge_power_max=s.discharge_kw * 1000 / s.discharge_efficiency,
            maximum_power_from_grid=needed + numerical_margin,
            maximum_power_to_grid=pv_bound + numerical_margin,
        )
        obj = ApplianceOptimization(
            retrieve,
            optim,
            plant,
            "import_eur_kwh",
            "export_eur_kwh",
            "profit",
            {"data_path": Path("data/emhass")},
            self.logger,
            num_timesteps=n,
            appliance_settings=s,
            exact=self.exact,
        )
        self.cache[key] = obj
        return obj

    def plan(
        self,
        slots,
        telemetry,
        *,
        errors=(),
        full_charge_due=False,
        maintenance_deadline=None,
        premium_budget_eur=None,
    ):
        if len(slots) < 5:
            raise ValueError("EMHASS MPC needs at least five known-price intervals")
        validate_standard_horizon(slots)
        s = self.settings
        capacity = s.effective_capacity_kwh or telemetry.capacity_kwh
        obj = self._optimizer(slots, capacity)
        self.last_policy = {"buffer": None, "maintenance": "not_due"}
        obj.hedge_anchor.value = np.zeros(len(slots))
        obj.hedge_floor.value = obj.hedge_limit.value = obj.hedge_reward.value = 0.0
        obj.full_anchor.value = np.zeros(len(slots))
        obj.full_required.value = 0.0
        obj.full_finish.value = np.zeros(len(slots))
        index = pd.DatetimeIndex([datetime.fromisoformat(r.timestamp) for r in slots])
        load = pd.Series([r.load_kw * 1000 for r in slots], index=index)
        pv = pd.Series([r.solar_kw * 1000 for r in slots], index=index)
        data = pd.DataFrame(
            {
                "import_eur_kwh": [r.import_eur_kwh for r in slots],
                "export_eur_kwh": [r.export_eur_kwh for r in slots],
            },
            index=index,
        )
        obj.native_charge.value = np.minimum(
            np.maximum(pv.values - load.values, 0), s.charge_kw * 1000
        )
        obj.native_discharge.value = np.minimum(
            np.maximum(load.values - pv.values, 0), s.discharge_kw * 1000
        )
        obj.charge_mode_allowed.value = np.array(
            [
                0.0
                if s.import_limit_kw is not None
                and r.charging_load_ceiling_kw is not None
                and r.charging_load_ceiling_kw + s.charge_kw > s.import_limit_kw + 1e-7
                else 1.0
                for r in slots
            ]
        )
        terminal = (
            max(
                0,
                min(r.import_eur_kwh for r in slots) * s.discharge_efficiency
                - s.wear_eur_per_kwh,
            )
            if self.terminal == "matched"
            else float(self.terminal)
        )
        obj.terminal_value.value = terminal / 1000
        if full_charge_due:
            # Find the cheapest feasible prefix fill; the solver chooses its
            # actual charging inside that prefix. No unknown prices are used.
            needed = max(0.0, capacity * (1 - telemetry.soc_percent / 100))
            allowed = [
                bool(obj.charge_mode_allowed.value[i])
                and (
                    s.import_limit_kw is None
                    or slot.load_kw - slot.solar_kw + s.charge_kw
                    <= s.import_limit_kw + 1e-7
                )
                for i, slot in enumerate(slots)
            ]
            anchor = maintenance_target(slots, allowed, needed, s, maintenance_deadline)
            if anchor is not None:
                obj.full_anchor.value[anchor] = 1
                obj.full_required.value = needed * 1000
                # Leave charging enabled through a further quarter for native
                # taper/balancing observation, rather than cutting it at 100%.
                obj.full_finish.value[anchor : anchor + 2] = 1
                self.last_policy["maintenance"] = {
                    "target_at": slots[anchor].timestamp,
                    "target_soc": 100,
                }
            else:
                self.last_policy["maintenance"] = (
                    "pending: insufficient executable priced horizon"
                )
        started = time.monotonic()
        result = obj.perform_naive_mpc_optim(
            data,
            pv,
            load,
            prediction_horizon=len(slots),
            soc_init=telemetry.soc_percent / 100,
            soc_final=s.reserve_percent / 100,
        )
        baseline_profit = (
            float(obj.prob.value) if obj.optim_status == "Optimal" else None
        )
        baseline_first_minutes = (
            int(round(float(obj.charge_fraction.value[0]) * 15))
            if self.exact and baseline_profit is not None
            else 0
        )
        baseline_first_supply = (
            bool(obj.appliance_modes[1].value[0] > 0.5)
            if self.exact and baseline_profit is not None
            else False
        )
        fallback = None
        if (
            self.exact
            and baseline_profit is not None
            and result is not None
            and not result.empty
        ):
            fallback = (
                result.copy(deep=True),
                tuple(np.array(mode.value, copy=True) for mode in obj.appliance_modes),
                np.array(obj.charge_fraction.value, copy=True),
                obj.prob.solver_stats.extra_stats if obj.prob.solver_stats else None,
            )
        risk_failed = False
        request = buffer_request(slots, s, errors) if self.exact else None
        if request and premium_budget_eur is not None:
            request["premium_eur"] = min(
                request["premium_eur"], max(0.0, premium_budget_eur)
            )
        if (
            request
            and request["buffer_kwh"] > 1e-7
            and obj.optim_status == "Optimal"
            and result is not None
            and not result.empty
        ):
            anchor = request["anchor"]
            baseline = float(result.iloc[anchor]["SOC_opt"]) * capacity * 1000
            extra = min(
                request["buffer_kwh"] * 1000,
                max(0.0, capacity * s.maximum_percent * 10 - baseline),
            )
            if extra > 1e-5 and request["premium_eur"] > 0:
                obj.hedge_anchor.value[anchor] = 1
                obj.hedge_floor.value = baseline - capacity * telemetry.soc_percent * 10
                obj.hedge_limit.value = extra
                obj.hedge_reward.value = request["premium_eur"] / extra
                try:
                    result = obj.perform_naive_mpc_optim(
                        data,
                        pv,
                        load,
                        prediction_horizon=len(slots),
                        soc_init=telemetry.soc_percent / 100,
                        soc_final=s.reserve_percent / 100,
                    )
                    risk_failed = (
                        obj.optim_status != "Optimal" or result is None or result.empty
                    )
                except Exception:
                    risk_failed = True
            self.last_policy["buffer"] = {
                **request,
                "effective_buffer_kwh": extra / 1000,
            }
            if (
                not risk_failed
                and obj.optim_status == "Optimal"
                and result is not None
                and not result.empty
            ):
                reward = float(obj.hedge_reward.value) * float(obj.hedged_energy.value)
                self.last_policy["buffer"].update(
                    {
                        "forecast_premium_eur": max(
                            0.0, baseline_profit - (float(obj.prob.value) - reward)
                        ),
                        "first_action_changed": (
                            int(round(float(obj.charge_fraction.value[0]) * 15))
                            != baseline_first_minutes
                            or bool(obj.appliance_modes[1].value[0] > 0.5)
                            != baseline_first_supply
                        ),
                        "first_extra_minutes": max(
                            0,
                            int(round(float(obj.charge_fraction.value[0]) * 15))
                            - baseline_first_minutes,
                        ),
                        "planned_extra_kwh": max(
                            0.0,
                            float(result.iloc[anchor]["SOC_opt"]) * capacity
                            - baseline / 1000,
                        ),
                    }
                )
        info = obj.prob.solver_stats.extra_stats if obj.prob.solver_stats else None
        modes = (
            tuple(np.array(mode.value, copy=True) for mode in obj.appliance_modes)
            if self.exact and not risk_failed
            else None
        )
        fractions = (
            np.array(obj.charge_fraction.value, copy=True)
            if self.exact and not risk_failed
            else None
        )
        status = str(obj.optim_status)
        if risk_failed and fallback:
            result, modes, fractions, info = fallback
            self.last_policy["buffer"]["status"] = (
                "risk solve unavailable; certified nominal plan retained"
            )
            status = "Optimal"
        self.last_solve = {
            "intervals": len(slots),
            "seconds": round(time.monotonic() - started, 3),
            "status": status,
            "risk_fallback": risk_failed,
            "mip_gap": getattr(info, "mip_gap", None),
            "relative_gap_limit": 0.001,
            "time_limit_seconds": 60,
        }
        if status != "Optimal" or result is None or result.empty:
            raise ValueError(
                "EMHASS did not return an optimal feasible plan: "
                + str(obj.optim_status)
            )
        decisions = []
        energy = capacity * telemetry.soc_percent / 100
        for i, (slot, (_, r)) in enumerate(zip(slots, result.iterrows())):
            if modes:
                action = (
                    "charge"
                    if modes[0][i] > 0.5
                    else "self_consume"
                    if modes[1][i] > 0.5
                    else "hold"
                )
                minutes = (
                    int(round(float(fractions[i]) * 15)) if action == "charge" else 0
                )
                from .partial_charge import partial_step

                flow = partial_step(
                    energy,
                    slot.load_kw,
                    slot.solar_kw,
                    minutes,
                    s.maximum_percent,
                    capacity,
                    s,
                    action == "self_consume",
                )
                energy = flow[0]
                if abs(energy / capacity - float(r["SOC_opt"])) > 2e-5:
                    raise ValueError("EMHASS/appliance SOC mismatch")
                _imported, _exported, discharged, grid, pvcharge = flow[1:]
            else:
                action = (
                    "charge"
                    if float(r["P_batt"]) < -0.1
                    else "self_consume"
                    if float(r["P_batt"]) > 0.1
                    else "hold"
                )
                energy = float(r["SOC_opt"]) * capacity
                discharged = grid = pvcharge = 0
            decisions.append(
                Decision(
                    slot.timestamp,
                    action,
                    grid * 4,
                    discharged * 4,
                    pvcharge * 4,
                    energy / capacity * 100,
                    slot.import_eur_kwh,
                    slot.export_eur_kwh,
                    slot.load_kw,
                    slot.solar_kw,
                    "EMHASS rolling MPC with fixed-power appliance constraints."
                    if self.exact
                    else "EMHASS continuous-power reference; not an executable AlphaESS command.",
                    minutes if self.exact and action == "charge" else None,
                )
            )
        return decisions
