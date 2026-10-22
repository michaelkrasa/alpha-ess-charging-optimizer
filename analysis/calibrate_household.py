"""Approximate SOC/energy fit from September–October development observations.

Power is inferred from household energy balance. SOC quantization, timestamp
alignment and loss location make this an uncertain diagnostic, not a factory
capacity measurement. July is deliberately excluded from fitting.
"""

import json, numpy as np
from pathlib import Path
from datetime import datetime

samples = []
for p in sorted(Path("data/backtests/household").glob("2026-09-*.json")) + sorted(
    Path("data/backtests/household").glob("2026-10-*.json")
):
    rows = sorted(json.load(open(p))["samples"], key=lambda r: r["uploadTime"])
    pts = [
        (
            datetime.fromisoformat(r["uploadTime"]),
            float(r["cbat"]),
            (
                float(r["ppv"])
                + float(r["gridCharge"])
                - float(r["load"])
                - float(r["feedIn"])
            )
            / 1000,
            float(r["ppv"]) / 1000,
        )
        for r in rows
    ]
    for a, b in zip(pts, pts[1:]):
        dt = (b[0] - a[0]).total_seconds()
        ds = b[1] - a[1]
        power = (a[2] + b[2]) / 2
        if (
            240 <= dt <= 360
            and min(a[1], b[1]) > 15
            and max(a[1], b[1]) < 90
            and 0.2 < abs(ds) < 8
            and abs(power) > 0.4
        ):
            samples.append((power * dt / 3600, ds, max(a[3], b[3])))
for kind in ["night-charge", "all-charge", "discharge"]:
    rows = (
        [
            (e, ds)
            for e, ds, pv in samples
            if (e > 0.1 and ds > 0 and (pv < 0.05 if kind == "night-charge" else True))
        ]
        if kind != "discharge"
        else [(e, ds) for e, ds, pv in samples if e < -0.1 and ds < 0]
    )
    a = np.array([x[0] for x in rows])
    b = np.array([x[1] for x in rows])
    slope = float(a @ b / (a @ a))
    ratios = b / a
    print(
        kind,
        "n",
        len(rows),
        "slope SOC/kWh",
        slope,
        "median",
        np.median(ratios),
        "p10-p90",
        np.percentile(ratios, [10, 90]),
        "effective_capacity_at95eff",
        95 / slope if kind != "discharge" else 100 / (0.95 * slope),
    )
episodes = []
for p in sorted(Path("data/backtests/household").glob("2026-09-*.json")) + sorted(
    Path("data/backtests/household").glob("2026-10-*.json")
):
    rows = sorted(json.load(open(p))["samples"], key=lambda r: r["uploadTime"])
    pts = [
        (
            datetime.fromisoformat(r["uploadTime"]),
            float(r["cbat"]),
            (
                float(r["ppv"])
                + float(r["gridCharge"])
                - float(r["load"])
                - float(r["feedIn"])
            )
            / 1000,
            float(r["ppv"]) / 1000,
        )
        for r in rows
    ]
    group = []

    def save(g):
        if len(g) < 5:
            return
        e = sum(
            (a[2] + b[2]) / 2 * (b[0] - a[0]).total_seconds() / 3600
            for a, b in zip(g, g[1:])
        )
        ds = g[-1][1] - g[0][1]
        if abs(e) > 0.5 and abs(ds) > 3 and e * ds > 0:
            episodes.append((e, ds, max(x[3] for x in g), len(g)))

    for a in pts:
        if (
            not 15 < a[1] < 90
            or abs(a[2]) < 0.4
            or group
            and (
                (a[0] - group[-1][0]).total_seconds() > 360 or a[2] * group[-1][2] <= 0
            )
        ):
            save(group)
            group = []
        if 15 < a[1] < 90 and abs(a[2]) > 0.4:
            group.append(a)
    save(group)
slopes = []
for kind in ["charge", "discharge"]:
    rows = [r for r in episodes if r[0] * (1 if kind == "charge" else -1) > 0]
    a = np.array([r[0] for r in rows])
    b = np.array([r[1] for r in rows])
    s = float(a @ b / (a @ a))
    slopes.append(s)
    print(
        "episodes",
        kind,
        "n",
        len(rows),
        "slope",
        s,
        "median",
        np.median(b / a),
        "range",
        np.percentile(b / a, [10, 90]),
    )
a, b = slopes
print(
    "episode symmetric fit capacity", 100 / np.sqrt(a * b), "oneway_eta", np.sqrt(a / b)
)
