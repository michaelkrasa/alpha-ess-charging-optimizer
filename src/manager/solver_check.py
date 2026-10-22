"""Read-only full-horizon solver replay; no API credentials or device writes."""
import argparse
from dataclasses import replace
import json
from pathlib import Path

from .domain import Slot, Telemetry
from .emhass_mpc import EmhassPlanner
from .settings import load_settings


def verify(payload, settings, soc_values=None, repeats=2):
    slots = [Slot(**row) for row in payload['slots']]
    telemetry = Telemetry(**payload['telemetry'])
    if len(slots) <= 96:
        raise ValueError('The full-horizon regression needs more than 96 intervals')
    reports = []
    for soc in soc_values or [telemetry.soc_percent]:
        planner = EmhassPlanner(settings)
        for attempt in range(repeats):
            decisions = planner.plan(slots, replace(telemetry, soc_percent=soc))
            if len(decisions) != len(slots):
                raise ValueError('Solver truncated the priced horizon')
            report = planner.last_solve
            if report['status'] != 'Optimal' or report['mip_gap'] is None:
                raise ValueError('No certified solver result')
            if report['mip_gap'] > report['relative_gap_limit'] + 1e-8:
                raise ValueError('Solver gap exceeds its declared tolerance')
            # plan() independently replays every decision against daily.step,
            # rejecting any SOC mismatch before it returns these decisions.
            reports.append({**report, 'soc_percent': soc, 'cached': bool(attempt),
                            'physical_replay': 'passed'})
    return {'status': 'passed', 'intervals': len(slots), 'runs': reports}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--config', default='manager.yaml')
    parser.add_argument('--soc', type=float, nargs='+')
    args = parser.parse_args()
    report = verify(json.loads(args.input.read_text()), load_settings(args.config), args.soc)
    print(json.dumps(report, allow_nan=False), flush=True)


if __name__ == '__main__':
    main()
