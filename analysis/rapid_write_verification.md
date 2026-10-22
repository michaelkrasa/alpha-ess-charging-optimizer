# Repeated API writes verified — 21 October 2026

The previous once-per-24-hour restriction was imposed by this application from documentation. It had not been tested on the household. Live testing disproved that restriction for this system.

With the controller stopped and a durable execution lease held, one inactive charge start and one inactive discharge start were each changed by 15 minutes and immediately restored. Enabled flags, SOC targets and active windows were preserved. Each write was followed by an independent configuration read.

| Endpoint | First changed value read back (Prague) | Original restored/read back | Separation |
|---|---|---|---|
| Charge | 11:40:43 | 11:41:03 | 20 seconds |
| Discharge | 11:41:23 | 11:41:43 | 20 seconds |

Both original configurations were confirmed restored at the end of testing. Private raw evidence: `data/commissioning/rapid_write_verification.json`. This proves repeated API acceptance and configuration readback, not an unlimited request rate or physical execution of an inactive test window. The documentation still says once per 24 hours: [upstream API reference](https://github.com/CharlesGillanders/alphaess-openAPI/blob/main/docs/API.md). Observed household behavior takes precedence over that unverified restriction.

The live controller now reoptimizes each quarter hour, including before 15:00 using today's known prices, with a ten-second configurable API cooldown. It includes installed charging windows as candidates to preserve continuity when reoptimization gives equivalent outcomes. Changed settings are read back; unchanged settings do not cause writes. Safe three-step staging supports swaps where neither two-write order has a safe intermediate pair. A failed staged update leaves charging disabled and confirmation pending.

Regression tests cover different settings and OFF within 40 seconds, consecutive before-publication replans with changed SOC, retention of ongoing charging windows, short priced horizons and safe staged swaps. Existing financial comparisons simulated daily updates and do not establish savings for the new rolling control policy.
