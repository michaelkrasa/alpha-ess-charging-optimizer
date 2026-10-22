"""Run with .venv-emhass/bin/python analysis/emhass_minimal_example.py.

Offline API example only: no Home Assistant, network, or inverter commands.
The optional first_action AC wattage pins exactly one executable next step;
the remaining horizon retains EMHASS continuous-control semantics.
"""

import csv
import json
import logging
from pathlib import Path

import emhass
import pandas as pd
from emhass.optimization import Optimization


class FirstActionOptimization(Optimization):
    """Pin one action computed by the accurate physical plant outside EMHASS."""

    def __init__(self, *args, first_action=None, **kwargs):
        self.first_action = first_action  # (AC charge W, AC discharge W), or None
        super().__init__(*args, **kwargs)

    def _add_battery_constraints(self, constraints, batt_stress_conf):
        super()._add_battery_constraints(constraints, batt_stress_conf)
        if self.first_action is not None:
            charge_w, discharge_w = self.first_action
            constraints.extend(
                [
                    self.vars['p_sto_neg'][0][0] == -charge_w,
                    self.vars['p_sto_pos'][0][0] == discharge_w,
                ]
            )


def make_optimizer(index, load_w, pv_w, first_action=None):
    """Defaults are shipped inside the installed emhass Python package."""
    data_dir = Path(emhass.__file__).parent / 'data'
    flat = json.loads((data_dir / 'config_defaults.json').read_text())
    grouped = {'retrieve_hass_conf': {}, 'optim_conf': {}, 'plant_conf': {}, 'params_secrets': {}}
    with (data_dir / 'associations.csv').open() as stream:
        for row in list(csv.reader(stream))[1:]:
            group, _, key, *_ = row
            if key in flat:
                grouped[group][key] = flat[key]
    retrieve, optim, plant = (grouped[k] for k in ('retrieve_hass_conf', 'optim_conf', 'plant_conf'))
    retrieve.update(optimization_time_step=pd.Timedelta(minutes=15), time_zone='Europe/Prague')
    optim.update(
        set_use_pv=True, set_use_battery=True, number_of_deferrable_loads=0,
        set_nocharge_from_grid=False, set_nodischarge_to_grid=True,
        set_battery_first_priority=False, set_battery_dynamic=False,
        weight_battery_discharge=.045, weight_battery_charge=0.,
        set_total_pv_sell=False, num_threads=1, lp_solver_mip_rel_gap=0.,
    )
    # Nonbinding numerical bounds, not household import/export limits.
    import_bound = max(load_w) + 8600 + max(pv_w) + 1
    export_bound = max(pv_w) + 8600 + 1
    plant.update(
        inverter_is_hybrid=False, compute_curtailment=False,
        battery_nominal_energy_capacity=14200,
        battery_minimum_state_of_charge=.10, battery_maximum_state_of_charge=1.,
        battery_target_state_of_charge=.10,
        battery_charge_efficiency=.95, battery_discharge_efficiency=.95,
        battery_charge_power_max=8600, battery_discharge_power_max=8600/.95,
        maximum_power_from_grid=import_bound, maximum_power_to_grid=export_bound,
    )
    return FirstActionOptimization(
        retrieve, optim, plant, 'import_eur_kwh', 'export_eur_kwh', 'profit',
        {'data_path': Path('/tmp')}, logging.getLogger('emhass-example'),
        num_timesteps=len(index), first_action=first_action,
    )


if __name__ == '__main__':
    index = pd.date_range('2026-10-21', periods=96, freq='15min', tz='Europe/Prague')
    load = pd.Series(1200., index=index)
    pv = pd.Series(0., index=index)
    tariffs = pd.DataFrame(
        {'import_eur_kwh': [.15]*24 + [.40]*24 + [.20]*24 + [.35]*24,
         'export_eur_kwh': [.05]*96}, index=index,
    )
    for action in (None, (8600., 0.), (0., 0.), (0., 1200.)):
        optimizer = make_optimizer(index, load, pv, first_action=action)
        result = optimizer.perform_naive_mpc_optim(
            tariffs, pv, load, prediction_horizon=len(index), soc_init=.5, soc_final=.5,
        )
        print(action, optimizer.optim_status)
        print(result[['P_batt', 'SOC_opt', 'P_grid']].head(1).to_string())
