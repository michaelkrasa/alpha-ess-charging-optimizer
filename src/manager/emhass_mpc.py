"""EMHASS rolling MPC with AlphaESS's executable battery semantics.

EMHASS owns the energy balance, SOC model, economic objective and rolling
solver. This adapter adds fixed-power grid charging, automatic PV capture and
permission-only household discharge. It never sends device commands.
"""

import csv
import json
import logging
from copy import deepcopy
from datetime import datetime
from pathlib import Path

import cvxpy as cp
import emhass
import numpy as np
import pandas as pd
from emhass.optimization import Optimization

from .daily import step, validate_standard_horizon
from .domain import Decision


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
        super().__init__(*args, **kwargs)

    def _build_objective_function(self, *args, **kwargs):
        base = super()._build_objective_function(*args, **kwargs)
        # Replace the stock terminal target's over-SOC penalty with an explicit
        # residual energy value. The reserve target is also the physical floor.
        correction = (self.terminal_value - self.param_soc_final_reward) * sum(
            self.vars["soc_final_over"]
        )
        return cp.Maximize(base.args[0] + correction)

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
            s.charge_kw * 1000 - self.native_charge, mode_c
        )
        target_d = cp.multiply(self.native_discharge, mode_d)
        mc = cap / (s.charge_efficiency * dt) + s.charge_kw * 1000
        md = cap * s.discharge_efficiency / dt + s.discharge_kw * 1000
        constraints.extend(
            [
                mode_c + mode_d <= 1,
                mode_c <= self.charge_mode_allowed,
                charge <= target_c,
                charge <= charge_space,
                charge >= target_c - mc * full,
                charge >= charge_space - mc * (1 - full),
                discharge <= target_d,
                discharge <= discharge_space,
                discharge >= target_d - md * empty,
                discharge >= discharge_space - md * (1 - empty),
            ]
        )
        if s.import_limit_kw is not None:
            # Explicit user configuration only. No cap is inferred by default.
            bound = self.plant_conf["maximum_power_from_grid"]
            net_load = self.param_load_forecast - self.param_pv_forecast
            constraints.append(
                net_load + charge - discharge
                <= s.import_limit_kw * 1000 + bound * (1 - mode_c)
            )


class EmhassPlanner:
    """Reuse EMHASS's parameterized problems across quarter-hour replans."""

    def __init__(self, settings, exact=True, terminal="matched"):
        self.settings = settings
        self.exact = exact
        self.terminal = terminal
        self.cache = {}
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
            lp_solver_mip_rel_gap=0.0001,
            lp_solver_timeout=15,
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

    def plan(self, slots, telemetry):
        if len(slots) < 5:
            raise ValueError("EMHASS MPC needs at least five known-price intervals")
        validate_standard_horizon(slots)
        s = self.settings
        capacity = s.effective_capacity_kwh or telemetry.capacity_kwh
        obj = self._optimizer(slots, capacity)
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
        result = obj.perform_naive_mpc_optim(
            data,
            pv,
            load,
            prediction_horizon=len(slots),
            soc_init=telemetry.soc_percent / 100,
            soc_final=s.reserve_percent / 100,
        )
        if obj.optim_status != "Optimal" or result is None or result.empty:
            raise ValueError(
                "EMHASS did not return an optimal feasible plan: "
                + str(obj.optim_status)
            )
        decisions = []
        energy = capacity * telemetry.soc_percent / 100
        modes = obj.appliance_modes if self.exact else None
        for i, (slot, (_, r)) in enumerate(zip(slots, result.iterrows())):
            if modes:
                action = (
                    "charge"
                    if modes[0].value[i] > 0.5
                    else "self_consume"
                    if modes[1].value[i] > 0.5
                    else "hold"
                )
                flow = step(
                    energy,
                    slot.load_kw,
                    slot.solar_kw,
                    action == "charge",
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
                )
            )
        return decisions
