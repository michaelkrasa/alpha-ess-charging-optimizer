# Dashboard energy colors

Checked 22 October 2026. Recommendation for the dashboard, not a formal electrical color standard.

## What mature products do

| Primary source | Solar | Grid | Battery / house |
| --- | --- | --- | --- |
| [Tesla Powerwall energy data](https://www.tesla.com/support/energy/powerwall/mobile-app/energy-data) | Yellow | Grey | Battery green; home blue; charge/discharge distinguished by sign |
| [Home Assistant frontend defaults](https://github.com/home-assistant/frontend/blob/dev/src/resources/theme/color/color.globals.ts) | Orange `#ff9800` | Import blue `#488fc2`; return purple `#8353d1` | Battery in pink `#f06292`; out teal `#4db6ac` |
| [Enphase app quick-start guide](https://enphase.com/en-gb/download/enphase-app-quick-start-guide) | Production blue | Import/export black | Consumption orange; direction also distinguished by graph sign |

These first-party conventions differ substantially. They do **not establish a universal energy-dashboard palette**; do not describe any of them as an industry-mandated standard. Their shared useful practice is consistent component colors, an explicit legend and additional direction cues. The electrical wiring color rules are a different subject and should not dictate these UI states.

## Recommendation for this dashboard

Reserve sunny amber for solar and use blue for **grid charging**. Keep the existing forest green for **battery supplying the house**, and muted grey for **preserve battery**. This separates solar from grid charging and follows Home Assistant's grid-import convention while retaining the cream/green identity. These are project design choices informed by existing products.

| Meaning | Suggested stroke/accent | Extra cue |
| --- | --- | --- |
| Solar generation | `#b57613` | Sun icon; “Solar” |
| Grid charging | `#39769d` | “Grid → battery” |
| Battery supplying house | `#42694d` | “Battery → home” |
| Preserve battery | `#65705b` | “Hold” |

Use pale tinted fills with dark labels for the schedule cells; keep the same semantics in the legend, detail badges, chart bands and readback map. Installed versus proposed schedules should differ by a solid versus dashed outline (plus their labels), rather than by opacity alone. For SOC, this implementation uses dashed green for historical expectations and solid blue for measured charge, with explicit labels. Future SOC remains green; the dash distinction is retained when color perception is limited. Grid charging is permission/settings, whereas actual battery power is measured: labels must preserve that distinction.

[WCAG 1.4.1](https://www.w3.org/WAI/WCAG22/Understanding/use-of-color.html) requires meaning beyond color. [WCAG 1.4.11](https://www.w3.org/WAI/WCAG22/Understanding/non-text-contrast.html) calls for at least 3:1 contrast for meaningful graphics against adjacent backgrounds. Calculated on cream `#fbfaf6`, the proposed blue, green, amber and grey strokes have contrast ratios 4.73, 5.99, 3.61 and 5.00 respectively. Amber is a graph/icon accent, not small text: use dark text for captions. These ratios are checks of the proposed opaque colors only, not an assertion that the entire rendered dashboard meets WCAG; verify actual fills, alpha and focus states in the preview.

## Update: light Carbon palette (November 2026)

The React dashboard (`dashboard/`) now uses a white, minimal theme on [IBM Carbon](https://carbondesignsystem.com/elements/color/tokens/) White-theme tokens: neutral surfaces (`#ffffff`, `#f4f4f4`, borders `#e0e0e0`, text `#161616`) with color reserved for meaning. The semantics above are kept, with Carbon values:

| Meaning | Color | Extra cue |
| --- | --- | --- |
| Grid charging | blue 60 `#0f62fe` | Schedule band label “Grid charge” |
| Battery supplying house | green 60 `#198038` | “House supply” |
| Preserve battery | gray 30 `#c6c6c6` | “Preserve” |
| Solar | yellow 30 fill `#f1c21b`, line `#b28600` | Sun icon; own kW scale in the lower half of the chart |
| Price | purple 60 `#8a3ffc` | Left axis €/MWh |
| Battery SOC | ink `#161616` measured (solid), `#525252` forecast (dashed), `#a8a8a8` past expectation (dotted) | Right axis %; ink stays legible over charge/supply tints |

Status uses Carbon support colors: success `#24a148`, warning `#f1c21b`, error `#da1e28`, off `#8d8d8d`. The outlook shows one schedule band directly under the chart: the plan where it exists, the inverter's charge/discharge windows elsewhere. The chart itself is not shaded by action; the band carries that meaning. Meaning is still carried by labels, line style and position, not color alone.
