"""Create a standalone Czech-market household solar/battery assessment."""

from __future__ import annotations

from pathlib import Path

from reportlab.graphics.shapes import Drawing, Line, PolyLine, Rect, String
from reportlab.lib import colors
from reportlab.lib.colors import HexColor
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.platypus import (
    HRFlowable,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from analysis.create_shareable_pdf import (
    BLUE,
    GOLD,
    GREEN,
    GRID,
    INK,
    MUTED,
    NAVY,
    PALE_BLUE,
    PALE_GOLD,
    PALE_GREEN,
    S,
    TEAL,
    P,
    bullet_list,
    metric_card,
    standard_table,
)


ROOT = Path(__file__).resolve().parents[1]
OUTPUT_PATH = ROOT / "output/pdf/household_solar_battery_assessment_25242.pdf"

MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
NEGATIVE_HOURS = [0, 0, 10, 72, 81, 69, 6, 54, 23, 7, 0, 0]
WINTER_PROFILE = [97.2, 95.3, 92.8, 90.7, 92.3, 98.1, 116.3, 136.6, 145.4, 133.3, 121.9, 113.8, 111.0, 113.3, 121.6, 135.4, 148.6, 156.9, 151.7, 142.4, 130.0, 116.1, 111.6, 99.8]
SOLAR_PROFILE = [93.2, 87.1, 83.4, 82.5, 83.8, 90.9, 106.2, 109.8, 94.5, 69.0, 46.5, 35.0, 24.9, 18.3, 20.1, 33.8, 52.7, 82.7, 112.1, 147.4, 154.8, 128.5, 110.6, 97.4]
BATTERY_COST = [(0, 1734), (10, 1561), (15, 1537), (20, 1525), (25, 1517), (30, 1510)]
DEMAND_CASES = [("Before EV", 11.25), ("One EV (2025)", 16.07), ("Future two EVs", 19.07)]


def price_profile_chart() -> Drawing:
    width, height = 500, 220
    d = Drawing(width, height)
    left, right, bottom, top = 44, 12, 40, 18
    pw, ph = width - left - right, height - bottom - top
    y_max = 180
    for tick in range(0, 181, 30):
        y = bottom + tick / y_max * ph
        d.add(Line(left, y, width - right, y, strokeColor=GRID, strokeWidth=0.55))
        d.add(String(left - 6, y - 3, str(tick), textAnchor="end", fontName="Helvetica", fontSize=7, fillColor=MUTED))
    d.add(String(6, bottom + ph / 2, "EUR/MWh", fontName="Helvetica", fontSize=7, fillColor=MUTED))
    step = pw / 23
    for hour in range(0, 24, 2):
        x = left + hour * step
        d.add(String(x, 24, f"{hour:02d}", textAnchor="middle", fontName="Helvetica", fontSize=7, fillColor=INK))
    d.add(String(left + pw / 2, 8, "Local hour", textAnchor="middle", fontName="Helvetica", fontSize=7, fillColor=MUTED))

    def add_series(values, color, stroke_width):
        pts = []
        for hour, value in enumerate(values):
            pts.extend((left + hour * step, bottom + value / y_max * ph))
        d.add(PolyLine(pts, strokeColor=color, strokeWidth=stroke_width, fillColor=None))

    add_series(WINTER_PROFILE, BLUE, 2.1)
    add_series(SOLAR_PROFILE, GOLD, 2.4)
    d.add(Line(290, 201, 308, 201, strokeColor=BLUE, strokeWidth=2.1))
    d.add(String(314, 198, "Nov-Feb", fontName="Helvetica", fontSize=7, fillColor=INK))
    d.add(Line(380, 201, 398, 201, strokeColor=GOLD, strokeWidth=2.4))
    d.add(String(404, 198, "Apr-Sep", fontName="Helvetica", fontSize=7, fillColor=INK))
    return d


def negative_price_chart() -> Drawing:
    width, height = 500, 145
    d = Drawing(width, height)
    left, right, bottom, top = 40, 12, 30, 12
    pw, ph = width - left - right, height - bottom - top
    y_max = 90
    for tick in (0, 30, 60, 90):
        y = bottom + tick / y_max * ph
        d.add(Line(left, y, width - right, y, strokeColor=GRID, strokeWidth=0.55))
        d.add(String(left - 6, y - 3, str(tick), textAnchor="end", fontName="Helvetica", fontSize=7, fillColor=MUTED))
    slot = pw / 12
    bar_w = 22
    for i, value in enumerate(NEGATIVE_HOURS):
        x = left + i * slot + (slot - bar_w) / 2
        bar_h = value / y_max * ph
        d.add(Rect(x, bottom, bar_w, bar_h, fillColor=TEAL, strokeColor=None))
        if value:
            d.add(String(x + bar_w / 2, bottom + bar_h + 4, str(value), textAnchor="middle", fontName="Helvetica-Bold", fontSize=6.5, fillColor=INK))
        d.add(String(x + bar_w / 2, 15, MONTHS[i], textAnchor="middle", fontName="Helvetica", fontSize=6.5, fillColor=INK))
    return d


def battery_cost_chart() -> Drawing:
    width, height = 500, 190
    d = Drawing(width, height)
    left, right, bottom, top = 48, 16, 40, 18
    pw, ph = width - left - right, height - bottom - top
    min_y, max_y = 1480, 1760
    for tick in (1500, 1550, 1600, 1650, 1700, 1750):
        y = bottom + (tick - min_y) / (max_y - min_y) * ph
        d.add(Line(left, y, width - right, y, strokeColor=GRID, strokeWidth=0.55))
        d.add(String(left - 6, y - 3, str(tick), textAnchor="end", fontName="Helvetica", fontSize=7, fillColor=MUTED))
    d.add(String(6, bottom + ph / 2, "EUR/year", fontName="Helvetica", fontSize=7, fillColor=MUTED))
    pts = []
    for cap, cost in BATTERY_COST:
        x = left + cap / 30 * pw
        y = bottom + (cost - min_y) / (max_y - min_y) * ph
        pts.extend((x, y))
        d.add(Rect(x - 2.5, y - 2.5, 5, 5, fillColor=BLUE, strokeColor=None))
        d.add(String(x, y + 8, str(cost), textAnchor="middle", fontName="Helvetica-Bold", fontSize=6.8, fillColor=INK))
        d.add(String(x, 23, str(cap), textAnchor="middle", fontName="Helvetica", fontSize=7, fillColor=INK))
    d.add(PolyLine(pts, strokeColor=BLUE, strokeWidth=2.2, fillColor=None))
    d.add(String(left + pw / 2, 8, "Usable stationary battery (kWh)", textAnchor="middle", fontName="Helvetica", fontSize=7, fillColor=MUTED))
    return d


def demand_evolution_chart() -> Drawing:
    width, height = 500, 190
    d = Drawing(width, height)
    left, right, bottom, top = 48, 16, 48, 18
    pw, ph = width - left - right, height - bottom - top
    y_max = 20
    for tick in (0, 5, 10, 15, 20):
        y = bottom + tick / y_max * ph
        d.add(Line(left, y, width - right, y, strokeColor=GRID, strokeWidth=0.55))
        d.add(String(left - 6, y - 3, str(tick), textAnchor="end", fontName="Helvetica", fontSize=7, fillColor=MUTED))
    d.add(String(7, bottom + ph / 2, "MWh/year", fontName="Helvetica", fontSize=7, fillColor=MUTED))
    colors_by_case = [BLUE, TEAL, GOLD]
    slot = pw / len(DEMAND_CASES)
    bar_w = 62
    for i, ((label, value), color) in enumerate(zip(DEMAND_CASES, colors_by_case)):
        x = left + i * slot + (slot - bar_w) / 2
        bar_h = value / y_max * ph
        d.add(Rect(x, bottom, bar_w, bar_h, fillColor=color, strokeColor=None))
        d.add(String(x + bar_w / 2, bottom + bar_h + 7, f"{value:.2f}", textAnchor="middle", fontName="Helvetica-Bold", fontSize=8, fillColor=INK))
        d.add(String(x + bar_w / 2, 27, label, textAnchor="middle", fontName="Helvetica", fontSize=7.5, fillColor=INK))
    return d


def on_page(canvas, doc):
    canvas.saveState()
    page = canvas.getPageNumber()
    if page > 1:
        canvas.setStrokeColor(GRID)
        canvas.setLineWidth(0.5)
        canvas.line(18 * mm, A4[1] - 13 * mm, A4[0] - 18 * mm, A4[1] - 13 * mm)
        canvas.setFont("Helvetica", 7)
        canvas.setFillColor(MUTED)
        canvas.drawString(18 * mm, A4[1] - 10 * mm, "Household solar and battery assessment | 25242")
    canvas.setFont("Helvetica", 7)
    canvas.setFillColor(MUTED)
    canvas.drawString(18 * mm, 10 * mm, "Planning estimate - final economics require the actual supplier import and export formulas")
    canvas.drawRightString(A4[0] - 18 * mm, 10 * mm, f"Page {page}")
    canvas.restoreState()


def build_story():
    story = [
        Spacer(1, 13 * mm),
        P("Household Solar and Battery Assessment", "title"),
        P("Czech OTE price-aware recommendation for a future two-EV household near postal code 25242", "subtitle"),
        HRFlowable(width="100%", thickness=2, color=TEAL, spaceBefore=1, spaceAfter=14),
        P("Why this report exists", "h1"),
        P(
            "The household has <b>one EV today</b>. The other vehicle is not currently electric, but the planning "
            "assumption is that it will also become an EV within roughly three years and travel about "
            "<b>15,000 km/year</b>. The system is therefore being sized for a future operating state rather than "
            "only for present electricity use.",
        ),
        P(
            "Measured annual household demand increased from about <b>11.25 MWh before the first EV</b> to "
            "<b>16.07 MWh with one EV in 2025</b>. The second EV is estimated to add approximately 3.0 MWh/year "
            "of delivered charging, producing a planning demand of about <b>19.1 MWh/year</b>. Because both cars "
            "can charge flexibly, much of this new demand can be moved into sunny, cheap or negative-price periods.",
        ),
        P("Recommendation", "h1"),
    ]
    cards = Table(
        [[
            metric_card("10-12 kWp", "Solar array", "Similar scale to the former roof system", PALE_BLUE),
            metric_card("10-15 kWh", "Usable storage", "High power matters more than extra capacity", PALE_GREEN),
            metric_card("23 kW", "Existing inverter", "Reported rating; verify the nameplate and model", PALE_GOLD),
        ]],
        colWidths=[56 * mm, 56 * mm, 56 * mm],
        hAlign="CENTER",
    )
    cards.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 2), ("RIGHTPADDING", (0, 0), (-1, -1), 2)]))
    story += [
        cards,
        Spacer(1, 7 * mm),
        P(
            "<b>Direct answer:</b> add solar before increasing stationary storage beyond 10-15 kWh. "
            "The two cars already provide approximately 7.8 MWh/year of flexible charging demand, so a larger "
            "stationary battery duplicates flexibility that is already available.",
        ),
        P(
            "Use <b>12 kWp PV plus 15 kWh usable storage</b> as the central tender package, with separate prices "
            "for 10 kWp and 10 kWh. If the south-facing canopy is used, install only about 2-4 kWp there initially "
            "rather than filling it.",
        ),
        P(
            "<b>Inverter approach:</b> retain the reported 23 kW inverter if it passes the control and compatibility "
            "tests in this report. Add a metered EMS and AC-coupled battery interface if required. Replace it with a "
            "controllable hybrid inverter only if it cannot enforce an export ceiling or accept external commands.",
        ),
        PageBreak(),
        P("1. Sizing basis", "h1"),
        P("Annual electricity demand used for planning", "h2"),
        demand_evolution_chart(),
        P(
            "The future case adds the second EV's estimated 3.0 MWh/year to the measured one-EV household. "
            "The recommendation is intentionally based on this future two-EV demand.",
            "small",
        ),
        P("What kWp means", "h2"),
        P(
            "<b>kWp is the DC nameplate rating of the solar modules under standard test conditions.</b> "
            "It is not continuous output, inverter capacity, or the amount the property can draw from the grid. "
            "The reported 23 kW inverter rating and confirmed 20 kW site import limit are separate constraints.",
        ),
        Table(
            [[P(
                "<b>Sizing rationale:</b> the array is intentionally not sized to match total annual consumption. "
                "A larger installation would create substantially more summer export precisely when Czech wholesale "
                "prices are often low or negative. The 10-12 kWp range keeps most production behind the meter while "
                "preserving a practical expansion option.",
            )]],
            colWidths=[170 * mm],
            style=TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, -1), PALE_GOLD),
                    ("BOX", (0, 0), (-1, -1), 0.7, GOLD),
                    ("LEFTPADDING", (0, 0), (-1, -1), 10),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 10),
                    ("TOPPADDING", (0, 0), (-1, -1), 8),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
                ]
            ),
        ),
        Spacer(1, 6 * mm),
        P(
            "The former roof-only system produced 8.0-8.6 MWh/year and reached 8.8 kW in the API. "
            "It was therefore probably closer to 9-10 kWp DC than the remembered 6 kWp. Rebuilding at approximately "
            "that scale is the strongest evidence-based starting point.",
        ),
        PageBreak(),
        P("2. Czech OTE market behavior", "h1"),
        P(
            "The complete 2025 OTE day-ahead price year confirms the described seasonal pattern: spring and summer "
            "have a deep solar-driven midday trough, while winter retains overnight lows and morning/evening peaks.",
        ),
        price_profile_chart(),
        P(
            "In April-September, the average price fell to <b>EUR 18-25/MWh around 13:00-14:00</b> and rose to "
            "about <b>EUR 155/MWh around 20:00</b>. In November-February there were no negative hours; the average "
            "was lowest around 02:00-04:00 and peaked near 17:00.",
        ),
        P("Negative-price hours by month in 2025", "h2"),
        negative_price_chart(),
        P(
            "There were <b>322 negative-price hours</b> and <b>810 hours at or below EUR 20/MWh</b>. "
            "Negative prices were concentrated from March through October. April, May, and June alone accounted "
            "for 222 negative hours.",
        ),
        P(
            "OTE publishes wholesale energy prices. Whether a household is actually paid to consume depends on the "
            "supplier formula after margin, distribution, taxes, and regulated charges. The final model must use "
            "the exact contract, not OTE alone.",
            "small",
        ),
        PageBreak(),
        P("3. More solar versus more battery", "h1"),
        P(
            "The replay uses the measured household shape, 7.83 MWh/year of EV charging movable within each week, "
            "PV curtailment at unattractive export prices, grid charging, a 20 kW import limit, and battery "
            "degradation cost. The illustrative retail case is OTE + EUR 80/MWh on import and OTE - EUR 10/MWh on export.",
        ),
        P("Stationary battery value with 12 kWp PV", "h2"),
        battery_cost_chart(),
    ]
    battery_table = [
        ["Usable battery", "Variable cost", "Benefit vs next smaller option", "Approx. cycles/year"],
        ["0 kWh", "EUR 1,734", "-", "-"],
        ["10 kWh", "EUR 1,561", "EUR 173/year", "239"],
        ["15 kWh", "EUR 1,537", "EUR 24/year", "187"],
        ["20 kWh", "EUR 1,525", "EUR 12/year", "149"],
        ["25 kWh", "EUR 1,517", "EUR 8/year", "123"],
        ["30 kWh", "EUR 1,510", "EUR 6/year", "104"],
    ]
    story += [
        standard_table(battery_table, [32 * mm, 34 * mm, 58 * mm, 40 * mm], highlight_rows=(2, 3)),
        P(
            "The sharp flattening after 10-15 kWh remains even under a low battery-throughput-cost sensitivity. "
            "A larger battery may still be justified for backup, but not by spot arbitrage alone.",
        ),
        P("PV comparison with 15 kWh storage", "h2"),
    ]
    pv_table = [
        ["PV size", "Production", "Grid import", "Grid export", "PV curtailed", "Variable cost"],
        ["8 kWp roof", "5.86 MWh", "14.04 MWh", "0.49 MWh", "0.06 MWh", "EUR 1,960"],
        ["10 kWp", "7.94 MWh", "12.36 MWh", "0.86 MWh", "0.07 MWh", "EUR 1,741"],
        ["12 kWp", "10.03 MWh", "10.84 MWh", "1.40 MWh", "0.09 MWh", "EUR 1,537"],
        ["15 kWp", "12.85 MWh", "9.42 MWh", "2.72 MWh", "0.16 MWh", "EUR 1,291"],
        ["17.5 kWp", "15.46 MWh", "8.43 MWh", "4.11 MWh", "0.37 MWh", "EUR 1,079"],
    ]
    story += [
        standard_table(pv_table, [24 * mm, 28 * mm, 28 * mm, 28 * mm, 28 * mm, 31 * mm], highlight_rows=(3,)),
        P(
            "More PV still lowers the 2025 replay bill, but exports climb from 1.4 MWh at 12 kWp to 4.1 MWh at "
            "17.5 kWp. The larger system therefore depends much more heavily on future export terms. "
            "<b>10-12 kWp is the lower-risk default; preserve an expansion option rather than building it immediately.</b>",
        ),
        PageBreak(),
        P("4. Existing 23 kW inverter and Czech-grid control", "h1"),
        P(
            "<b>Primary recommendation:</b> do not replace a serviceable 23 kW inverter merely because the planned "
            "array is 10-12 kWp. An inverter may be larger than the rebuilt array. First record its exact make, model, "
            "firmware and nameplate rating, then test whether it supports a revenue-grade three-phase meter, local "
            "export limiting, grid charging and writable external control.",
        ),
        P("Decision hierarchy", "h2"),
        bullet_list(
            [
                "Best outcome: retain the 23 kW inverter and connect a compatible meter, Czech EMS and 10-15 kWh AC-coupled battery system.",
                "Second choice: retain it for PV and add a separate battery inverter, provided both devices can be coordinated at the point of connection.",
                "Replace it only if it cannot maintain the export ceiling after internet loss, cannot be controlled by a supported Czech EMS, or has weak service and warranty coverage.",
                "If replacement is required, a GoodWe ET-family system remains a current candidate because Delta Green and CEZ publish GoodWe integrations; select the model available and supported at procurement.",
            ]
        ),
        Table(
            [[P(
                "<b>Important distinction:</b> the inverter does not balance the Czech grid by itself. "
                "The 23 kW inverter rating is not permission to import or export 23 kW. The meter and controller "
                "must enforce the connection agreement; Delta Green or CEZ acts "
                "as supplier/aggregator and sends market or grid-balancing instructions. Both layers are required.",
            )]],
            colWidths=[170 * mm],
            style=TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, -1), PALE_BLUE),
                    ("BOX", (0, 0), (-1, -1), 0.7, BLUE),
                    ("LEFTPADDING", (0, 0), (-1, -1), 10),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 10),
                    ("TOPPADDING", (0, 0), (-1, -1), 8),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
                ]
            ),
        ),
        Spacer(1, 5 * mm),
        P("Commercial and longevity caveats", "h2"),
        P(
            "No vendor can credibly be guaranteed never to fail. The practical protection is a large supported "
            "manufacturer or installer, a written warranty, documented local export control, and a system that continues "
            "operating without the aggregator cloud. Delta Green's automation requires both purchase and sale "
            "contracts with Delta Green and a compatible single inverter. CEZ FlexiEnergie provides the larger "
            "counterparty alternative, but currently limits eligibility and does not allow its battery-control "
            "service with a spot or dynamic tariff.",
        ),
        P(
            "<b>Control-service choice:</b> choose Delta Green PROTEUS when OTE spot arbitrage is the priority and "
            "the exact 23 kW inverter is supported. "
            "Choose CEZ FlexiEnergie when a large Czech counterparty and fixed-price product are more important. "
            "If neither service supports the existing unit, price an AC-coupled control layer against complete "
            "replacement rather than assuming that a new inverter is necessary.",
        ),
        P("Required acceptance test", "h2"),
        bullet_list(
            [
                "Demonstrate closed-loop zero export at the point of connection with the battery full and normal household loads changing.",
                "Demonstrate a remote command that curtails PV and a separate command that charges the battery from the grid.",
                "Document the fail-safe state after internet loss: local self-consumption continues and the configured export ceiling remains enforced.",
                "Confirm the existing inverter's exact 23 kW rating, make, model, firmware, remaining warranty and access to historical data.",
            ]
        ),
        PageBreak(),
        P("5. Operating strategy and final specification", "h1"),
        P("Spring and summer", "h2"),
        bullet_list(
            [
                "Prioritize both EVs during the 10:00-16:00 OTE and solar trough, using a weekly mobility-energy target.",
                "Charge the stationary battery after satisfying immediate household demand and required EV energy.",
                "Curtail solar rather than exporting into a negative settlement interval.",
                "Reserve battery energy for the evening price ramp, typically around 18:00-21:00.",
            ]
        ),
        P("Autumn and winter", "h2"),
        bullet_list(
            [
                "Grid-charge the battery and vehicles during the cheapest overnight intervals, commonly 00:00-06:00.",
                "Discharge against the morning peak and retain enough energy for the stronger late-afternoon peak.",
                "Treat scarce winter solar as direct self-consumption rather than sizing the battery around it.",
            ]
        ),
        P("Tender specification", "h2"),
    ]
    tender_table = [
        ["Component", "Recommended tender basis"],
        ["PV", "10 kWp base option; 12 kWp preferred option; expansion-ready wiring"],
        ["Battery energy", "10 and 15 kWh usable options; no default requirement above 15 kWh"],
        ["Battery power", "Target 10 kW continuous if available independently of energy capacity"],
        ["Grid", "Respect confirmed 20 kW import limit with dynamic site-load control"],
        ["EV charging", "Price-aware weekly scheduler, combined charging cap, solar-surplus mode"],
        ["Export", "Automatic curtailment or zero-export during unattractive settlement intervals"],
        ["Inverter/EMS", "Retain reported 23 kW unit if controllable; add EMS/AC-coupled battery; replace only if needed"],
        ["Backup", "Price separately; backup needs can justify capacity that arbitrage cannot"],
    ]
    story += [
        standard_table(tender_table, [41 * mm, 129 * mm], highlight_rows=(2,)),
        Spacer(1, 5 * mm),
        P("Decision rule at procurement", "h2"),
        P(
            "Select 12 kWp rather than 10 kWp if the extra 2 kWp is inexpensive and can use a good south-facing "
            "surface. Select 15 kWh rather than 10 kWh only if the incremental installed battery price is low, "
            "backup matters, or the final supplier tariff produces materially larger spreads than the illustrative case.",
        ),
        P("Sources and caveats", "h2"),
        P(
            '<link href="https://www.ote-cr.cz/en/about-ote/ote-news/increase-in-negative-pricing-hours-on-the-day-ahead-electricity-market-in-2024" color="#2C6E9B">'
            "OTE - increase in negative-price hours</link>; "
            '<link href="https://www.ote-cr.cz/en/documentation/electricity-documentation/information-to-switch-to-15-min-interval" color="#2C6E9B">'
            "OTE - transition to 15-minute day-ahead trading</link>; "
            '<link href="https://re.jrc.ec.europa.eu/pvg_tools/en/" color="#2C6E9B">European Commission PVGIS</link>; '
            '<link href="https://www.cez.cz/cs/nova-energetika/flexibilita" color="#2C6E9B">CEZ FlexiEnergie</link>; '
            '<link href="https://www.deltagreen.cz/flexibilita" color="#2C6E9B">Delta Green flexibility</link>; '
            '<link href="https://eshop.deltagreen.cz/delta-link-pro-goodwe/" color="#2C6E9B">GoodWe Delta Link</link>; '
            '<link href="https://cz.goodwe.com/et-g2" color="#2C6E9B">GoodWe ET G2</link>; '
            '<link href="https://cz.goodwe.com/support-service" color="#2C6E9B">GoodWe Czech support</link>; '
            '<link href="https://manuals.sma.de/SMA-Premium/en-US/16915921419.html" color="#2C6E9B">SMA energy management</link>; '
            "Alpha ESS Open API through the local optimizer project.",
        ),
        P(
            "The OTE chart endpoint contained one incomplete historical day, 4 July 2025; it was interpolated. "
            "The model's supplier adders are explicit placeholders. Fixed charges and PV/battery capital costs are "
            "not included, so this is a sizing comparison rather than a payback quotation.",
            "small",
        ),
        HRFlowable(width="100%", thickness=1, color=GRID, spaceBefore=7, spaceAfter=7),
        P(
            "<b>Bottom line:</b> rebuild roughly the former PV scale, modestly expand to 10-12 kWp if the marginal "
            "panels are well placed, stop stationary storage at 10-15 kWh unless backup requirements justify more, "
            "and retain the reported 23 kW inverter if it passes a live zero-export test and written compatibility "
            "review. Its rating does not change the confirmed 20 kW grid-import limit.",
        ),
    ]
    return story


def main():
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    doc = SimpleDocTemplate(
        str(OUTPUT_PATH),
        pagesize=A4,
        rightMargin=18 * mm,
        leftMargin=18 * mm,
        topMargin=18 * mm,
        bottomMargin=17 * mm,
        title="Household Solar and Battery Assessment",
        author="Prepared from Alpha ESS and OTE API analysis",
        subject="Price-aware solar and residential battery sizing estimate",
    )
    doc.build(build_story(), onFirstPage=on_page, onLaterPages=on_page)
    print(OUTPUT_PATH)


if __name__ == "__main__":
    main()
