# Czech-market household solar and battery assessment

Date: 2026-07-30

## Why this report exists

The household has **one EV today**. The other vehicle is not currently
electric, but the planning assumption is that it will also become an EV within
roughly three years and travel about **15,000 km/year**. This assessment sizes
the solar, battery and control system for that future operating state rather
than only for present electricity use.

Measured annual household demand increased from approximately **11.25 MWh
before the first EV** to **16.07 MWh with one EV in 2025**. The second EV is
estimated to add approximately 3.0 MWh/year of delivered charging, producing a
planning demand of about **19.1 MWh/year**. Both vehicles can charge flexibly,
so much of this additional load can be moved into sunny, cheap or
negative-price periods.

## Recommendation

Plan around:

- **10-12 kWp of PV**, rather than 17.5 kWp;
- **10-15 kWh of usable stationary storage**;
- **10 kW battery charge/discharge power** if the selected product supports it;
- the confirmed **20 kW site import limit**;
- the reported existing **23 kW inverter**, subject to nameplate and model verification;
- automatic OTE price optimization, PV curtailment, and coordinated EV charging.

Do not replace the reported 23 kW inverter merely because the rebuilt array is
10-12 kWp. Retain it if it supports a three-phase meter, local export limiting
and writable external control. Add an EMS and AC-coupled battery interface
where required. Replace it only if these functions cannot be delivered
reliably.

For tendering, use **12 kWp PV plus 15 kWh usable storage** as the central
comparison package, but require separate prices for 10 kWp and 10 kWh so the
economics remain visible.

If the south-facing canopy is used, place only about 2-4 kWp there initially.
The main roof should remain close to the former installation's scale. The API
measured up to 8.8 kW of PV power and 8.0-8.6 MWh/year, so the former array was
probably closer to 9-10 kWp DC than the remembered 6 kWp.

`kWp` means the DC nameplate rating of the solar modules under standard test
conditions. It is not the amount continuously produced, the inverter rating, or
the 20 kW grid-import capacity. The reported 23 kW inverter rating and 20 kW
grid-import limit are separate constraints.

## Direct answer: more solar or more battery?

Between the two, add **solar before adding stationary battery beyond 10-15
kWh**.

In the 12 kWp OTE replay:

| Usable battery | Modelled annual variable cost | Incremental benefit |
|---:|---:|---:|
| 0 kWh | EUR 1,734 | - |
| 10 kWh | EUR 1,561 | EUR 173/year |
| 15 kWh | EUR 1,537 | Another EUR 24/year |
| 20 kWh | EUR 1,525 | Another EUR 12/year |
| 25 kWh | EUR 1,517 | Another EUR 8/year |
| 30 kWh | EUR 1,510 | Another EUR 6/year |

The stationary battery has sharply diminishing value because:

- both vehicles provide about 7.8 MWh/year of flexible charging demand;
- vehicle charging can move across several days;
- the stationary battery only needs to bridge the remaining household peaks;
- the 20 kW grid connection permits fast charging during cheap intervals.

This does not mean arbitrage is unimportant. It means a relatively small,
high-power battery plus flexible EV charging captures most of it.

## Existing 23 kW inverter and Czech-grid control

First record the existing inverter's exact make, model, firmware and nameplate
rating. Test whether it supports a revenue-grade three-phase meter, local
export limiting, grid charging and writable external control.

Use this decision hierarchy:

- retain the 23 kW inverter and connect a compatible meter, Czech EMS and
  10-15 kWh AC-coupled battery system;
- if necessary, retain it for PV and add a separate battery inverter, provided
  both devices can be coordinated at the point of connection;
- replace it only if it cannot maintain the export ceiling after internet
  loss, cannot be controlled by a supported Czech EMS, or has weak service and
  warranty coverage;
- if replacement is required, a GoodWe ET-family system remains a current
  candidate because both Delta Green and ČEZ publish GoodWe integrations.

The 23 kW inverter rating is not permission to import or export 23 kW. The
meter and controller must enforce the connection agreement; Delta Green or ČEZ
is the supplier/aggregator that sends market or grid-balancing commands.

No company can be guaranteed never to fail. The design therefore requires a
written ten-year warranty, a local fail-safe mode, a live zero-export test, and
clear replacement-unit terms. Delta Green automation requires both purchase
and sale contracts with Delta Green and a compatible single inverter. ČEZ
FlexiEnergie is the larger-counterparty alternative, but its battery-control
service cannot currently be combined with a spot or dynamic tariff.

Choose Delta Green PROTEUS when OTE spot arbitrage is the priority and the
exact 23 kW inverter is supported. Choose ČEZ FlexiEnergie when a large Czech
counterparty and fixed-price product matter more. If neither supports the
existing inverter, compare an AC-coupled control layer against complete
replacement.

Acceptance testing must demonstrate:

- closed-loop zero export with the battery full and household loads changing;
- remote PV curtailment and grid charging as separate commands;
- retention of the export ceiling after internet loss;
- the existing inverter's exact rating, make, model, firmware, remaining
  warranty and historical-data access.

## What the 2025 OTE data shows

The project fetched the full 2025 OTE day-ahead price year. One incomplete OTE
chart day, 2025-07-04, was interpolated. Hourly prices were used through
September and the 15-minute market data beginning 1 October was aggregated to
hours for comparison.

- Annual mean: **EUR 97/MWh**
- Negative-price hours: **322**
- Hours at or below EUR 20/MWh: **810**
- Negative prices occurred from March through October and were concentrated
  around 10:00-16:00.
- There were no negative hours in January, February, November, or December.

| Month | Mean price | Minimum | Negative hours | Hours at or below EUR 20 | Mean daily spread |
|---|---:|---:|---:|---:|---:|
| Jan | 128.5 | 13.0 | 0 | 6 | 103.0 |
| Feb | 132.6 | 54.6 | 0 | 0 | 93.0 |
| Mar | 99.9 | -24.9 | 10 | 68 | 135.7 |
| Apr | 79.7 | -123.0 | 72 | 117 | 153.1 |
| May | 73.1 | -224.5 | 81 | 169 | 164.4 |
| Jun | 77.1 | -91.9 | 69 | 158 | 175.7 |
| Jul | 92.3 | -2.5 | 6 | 46 | 111.4 |
| Aug | 77.0 | -61.2 | 54 | 129 | 143.3 |
| Sep | 92.2 | -53.4 | 23 | 79 | 190.3 |
| Oct | 95.6 | -1.7 | 7 | 36 | 122.9 |
| Nov | 111.5 | 15.8 | 0 | 2 | 87.4 |
| Dec | 107.0 | 49.9 | 0 | 0 | 61.9 |

During April-September, the average OTE price falls from roughly EUR 106/MWh at
06:00 to EUR 18-25/MWh around 13:00-14:00, then rises to roughly EUR 155/MWh
around 20:00. In November-February, the average is lowest around 02:00-04:00 at
roughly EUR 91-93/MWh and peaks around 17:00 at roughly EUR 157/MWh.

That is exactly the operating pattern the control system should exploit:

- spring/summer: charge cars and battery in the solar/OTE midday trough;
- autumn/winter: grid-charge overnight, serve the morning and evening peaks;
- curtail PV rather than export during negative-price settlement intervals;
- retain enough EV energy for mobility before using the stationary battery for
  arbitrage.

## PV-size comparison with 15 kWh storage

The layouts below assume the first 8-9 kWp is on the north-facing roof and
incremental PV is placed south where available.

| PV option | PV production | Grid import | Grid export | PV curtailed | Variable cost |
|---|---:|---:|---:|---:|---:|
| 8 kWp roof | 5.86 MWh | 14.04 MWh | 0.49 MWh | 0.06 MWh | EUR 1,960 |
| 10 kWp | 7.94 MWh | 12.36 MWh | 0.86 MWh | 0.07 MWh | EUR 1,741 |
| **12 kWp** | **10.03 MWh** | **10.84 MWh** | **1.40 MWh** | **0.09 MWh** | **EUR 1,537** |
| 15 kWp | 12.85 MWh | 9.42 MWh | 2.72 MWh | 0.16 MWh | EUR 1,291 |
| 17.5 kWp | 15.46 MWh | 8.43 MWh | 4.11 MWh | 0.37 MWh | EUR 1,079 |

More PV continues to reduce the modelled variable bill, so 17.5 kWp was not
physically impossible. It was, however, too aggressive as a default
recommendation: exports rise from 1.4 MWh at 12 kWp to 4.1 MWh at 17.5 kWp,
making much more of the investment dependent on future export terms and summer
spot prices.

The 10-12 kWp range keeps most production behind the meter while preserving an
option to expand later.

## Price-model assumptions

The comparison uses:

- reconstructed non-EV household demand of approximately 11.24 MWh/year;
- total flexible EV charging energy of approximately 7.83 MWh/year;
- EV charging movable within each week at up to 11 kW combined;
- OTE spot price plus an illustrative EUR 80/MWh import adder;
- OTE spot price minus EUR 10/MWh for exported energy;
- automatic PV curtailment where export is unattractive;
- 45 EUR/MWh battery-throughput cost to represent degradation;
- 95% one-way battery charge and discharge efficiency;
- battery discharge into site demand, not speculative overnight grid export.

The EUR 80/MWh and EUR 10/MWh adjustments are placeholders, not the user's
actual supplier contract. Negative OTE prices do not automatically mean the
final household import price is negative after supplier, distribution, tax, and
other regulated charges. The final tender decision should rerun the model with
the exact import and export formulas.

## Sources

- Alpha ESS Open API: household load and former PV production.
- OTE day-ahead chart API: complete 2025 price history.
- OTE: increase in negative-price hours in 2024 and 15-minute day-ahead
  transition from 1 October 2025.
- European Commission PVGIS: hourly north- and south-facing PV profiles.
- ČEZ FlexiEnergie: supported technology, grid-aware control, and eligibility.
- Delta Green: GoodWe ET integration, spot planning, and household flexibility.
- GoodWe: ET G2 technical data, Czech support, European service, and warranty.
- SMA: energy-management functions and Czech service presence.
