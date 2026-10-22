# Three-year solar and home-battery planning estimate

Date of analysis: 2026-07-30
Location modelled: 49.9681 N, 14.5135 E (postal-code area 25242)
Energy history: Alpha ESS daily records from 2021-01-01 through 2026-07-29

## Recommendation

Use **approximately 17.5 kWp of PV** and **20 kWh of usable battery storage** as
the planning target:

- about **8.5 kWp south-facing on the two-car canopy**;
- about **9 kWp on the main roof**, subject to a site survey confirming its
  actual orientation, usable area, and the size of the previous array;
- a **three-phase 15 kW-class hybrid inverter**;
- at least **10 kW continuous battery charge/discharge power**;
- two load-managed EV charging circuits, with solar-surplus charging and a
  shared site import limit.

Specify battery capacity as *usable*, not nominal. A product with a protected
reserve may need approximately 22–24 kWh nominal capacity to expose 20 kWh for
normal operation.

This is the knee of the storage curve. In the conservative north-roof model,
increasing usable storage from 15 to 20 kWh avoids about 0.80 MWh/year of
additional grid import. Increasing from 20 to 25 kWh avoids only another
0.38 MWh/year, and 25 to 30 kWh only another 0.23 MWh/year.

## What the Alpha ESS history says

Daily household consumption was reconstructed as:

`PV + grid import + battery discharge - grid export - battery charge`

This avoids confusing grid import with total consumption. The historical system
did contain a battery; the future design is nevertheless sized as a new system,
as requested.

| Year | Reconstructed load | PV production | Grid import | Grid export | Notes |
|---:|---:|---:|---:|---:|---|
| 2022 | 11.11 MWh | 7.98 MWh | 7.43 MWh | 4.06 MWh | Pre-EV |
| 2023 | 11.40 MWh | 8.58 MWh | 6.15 MWh | 2.98 MWh | Pre-EV; one missing day imputed |
| 2024 | 14.35 MWh | 8.29 MWh | 8.53 MWh | 2.01 MWh | Model Y from approximately May; missing/anomalous days corrected |
| 2025 | 16.07 MWh | 8.33 MWh | 10.31 MWh | 2.16 MWh | First complete one-EV year |
| 2026 Jan–Jul | 9.20 MWh | 2.28 MWh | 7.37 MWh | 0.20 MWh | Load is 1.8% below the same 2025 period |

The pre-EV baseline is approximately **11.25 MWh/year**. The 2025 increase is
approximately **4.8 MWh/year**, which is consistent with 50,000 km since around
May 2024 after allowing for wall-charging losses. The 2026 partial year confirms
that the one-EV household has stabilized at roughly 16 MWh/year.

## Second vehicle and future load

The second vehicle is modelled at:

- 15,000 km/year total;
- 80% electric, or 12,000 electric km/year;
- 25 kWh/100 km at the wall as a conservative large-PHEV planning value;
- **3.0 MWh/year** of additional electricity.

Expected future household load is therefore **19.1 MWh/year**. A reasonable
sensitivity range is approximately **18.5–20.0 MWh/year**, depending on winter
driving, charging losses, and changes in Model Y mileage.

| Month | Pre-EV average | 2025, Model Y | Two-vehicle forecast | 17.5 kWp PV, conservative |
|---|---:|---:|---:|---:|
| Jan | 1,370 kWh | 2,049 kWh | 2,314 kWh | 312 kWh |
| Feb | 1,138 kWh | 1,574 kWh | 1,803 kWh | 549 kWh |
| Mar | 872 kWh | 1,380 kWh | 1,650 kWh | 1,097 kWh |
| Apr | 908 kWh | 1,114 kWh | 1,371 kWh | 1,506 kWh |
| May | 760 kWh | 1,227 kWh | 1,471 kWh | 2,451 kWh |
| Jun | 719 kWh | 1,084 kWh | 1,334 kWh | 2,396 kWh |
| Jul | 605 kWh | 939 kWh | 1,210 kWh | 2,291 kWh |
| Aug | 773 kWh | 1,113 kWh | 1,364 kWh | 1,793 kWh |
| Sep | 729 kWh | 1,046 kWh | 1,283 kWh | 1,566 kWh |
| Oct | 797 kWh | 901 kWh | 1,160 kWh | 820 kWh |
| Nov | 1,085 kWh | 1,677 kWh | 1,914 kWh | 397 kWh |
| Dec | 1,484 kWh | 1,971 kWh | 2,201 kWh | 284 kWh |
| **Year** | **11.24 MWh** | **16.07 MWh** | **19.07 MWh** | **15.46 MWh** |

## PV geometry and production

The canopy estimate is approximately 7 m × 7 m:

- about 2 m vehicle width for each car;
- about 1 m between cars;
- about 1 m clearance on each outside edge;
- about 5 m vehicle length plus 1 m at each end.

That gives roughly 49 m² gross footprint. Allowing for panel layout, structural
members, drainage, and wind edge zones, **8–10 kWp** is plausible. The model
uses **8.5 kWp**.

European Commission PVGIS estimates at 20° tilt and 14% system losses:

- north-facing: approximately **724 kWh/kWp/year**;
- south-facing: approximately **1,052 kWh/kWp/year**.

The conservative 9 kWp north + 8.5 kWp south design therefore produces about
**15.5 MWh/year**. The south-facing canopy supplies about 8.9 MWh of that.

There is an important site-data discrepancy to resolve. The Alpha ESS API shows
the previous roof-only system producing 8.0–8.6 MWh/year and records
instantaneous PV power as high as 8.8 kW. That is not consistent with a literal
6 kWp, strictly north-facing system. The remembered 6 kW may have been an
inverter rating, an approximate normal peak, or the array may have been larger
or less northerly. If the new main-roof array reproduces its measured
8.3 MWh/year and the canopy adds about 8.9 MWh/year, combined production would
be approximately **17.2 MWh/year**.

## Storage simulation

The simulation replays the measured 2025 five-minute load shape, reconciles it
to daily API totals, adds the second PHEV as evening charging load, uses hourly
PVGIS production, and assumes 90% battery round-trip efficiency. It dispatches
the battery for solar self-consumption only; dynamic-price arbitrage is not
included.

### Conservative roof-orientation case: 17.5 kWp, 15.46 MWh/year PV

| Usable battery | Grid import | Grid export | Load supplied locally | PV self-consumed | Approx. cycles/year |
|---:|---:|---:|---:|---:|---:|
| 0 kWh | 13.77 MWh | 10.16 MWh | 27.8% | 34.3% | – |
| 10 kWh | 10.96 MWh | 7.05 MWh | 42.5% | 54.4% | 296 |
| 15 kWh | 9.92 MWh | 5.88 MWh | 48.0% | 61.9% | 271 |
| **20 kWh** | **9.12 MWh** | **5.00 MWh** | **52.2%** | **67.7%** | **245** |
| 25 kWh | 8.73 MWh | 4.57 MWh | 54.2% | 70.4% | 212 |
| 30 kWh | 8.50 MWh | 4.32 MWh | 55.4% | 72.1% | 185 |

### API-grounded roof-output case: measured roof yield + 8.5 kWp canopy

With 17.2 MWh/year PV and 20 kWh usable storage, the model gives:

- approximately **8.48 MWh/year grid import**;
- approximately **6.09 MWh/year grid export**;
- approximately **55.5% load supplied locally**;
- approximately **64.6% PV self-consumption**;
- approximately **252 equivalent battery cycles/year**.

The apparently modest independence figure is caused by seasons, not an
undersized battery. January load is roughly eight times the proposed array's
January production, while May–July have large surpluses. A home battery moves
energy by hours or days; it cannot economically move summer energy into winter.

## Sodium-ion decision

Size the system by usable energy and power, not chemistry. Sodium-ion is now
commercially credible at utility scale: CATL announced commissioned production
lines, Chinese deliveries during 2026, international deliveries from June 2027,
and a 60 GWh storage agreement. That does not yet guarantee a competitively
priced, certified, supported residential product in the Czech market.

At procurement time, compare complete installed systems on:

- installed price per usable kWh;
- warranted energy throughput and years;
- round-trip efficiency;
- continuous and peak power;
- cold-weather operating limits;
- fire/safety certification and Czech installer support;
- inverter/BMS compatibility and replacement availability.

Do not assume that a future sodium-ion battery can be attached to an arbitrary
hybrid inverter. Buy a supported inverter-and-battery combination, or obtain an
explicit written upgrade path from the inverter vendor.

## Practical design details

- Use a three-phase system and dynamic site-load limiting.
- Provide two EV circuits, but prevent both chargers and the heat pump from
  creating an avoidable simultaneous site peak.
- Prefer daytime solar-surplus charging whenever a vehicle is home.
- If whole-home backup is required, either specify at least 15 kW three-phase
  backup output or split out critical loads; a 10 kW battery interface will not
  cover every measured peak.
- Have the canopy structure and foundations checked for local wind uplift,
  especially panel edge zones at 20° pitch.
- Confirm grid-connection export limits before final inverter sizing.
- Re-run the model with actual import tariff, export compensation, and battery
  installed price before purchase. Price arbitrage may improve economics but
  will not solve the winter energy deficit.

## Sources

- Local load, PV, grid, and battery flows: Alpha ESS Open API through this
  project.
- Solar production model: European Commission Joint Research Centre,
  [PVGIS](https://re.jrc.ec.europa.eu/pvg_tools/en/).
- Vehicle dimensions and charging capability:
  [BMW Czech Republic X5 technical data](https://www.bmw.cz/cs/all-models/x-series/suv/bmw-x5-technicke-udaje.html).
- Sodium-ion commercialization:
  [CATL field-validated sodium-ion BESS](https://www.catl.com/en/news/6861.html)
  and [CATL/HyperStrong 60 GWh agreement](https://www.catl.com/en/news/6812.html).
