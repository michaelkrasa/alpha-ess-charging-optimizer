"""Create a polished, shareable PDF from the Alpha ESS planning analysis."""

from __future__ import annotations

from pathlib import Path

from reportlab.graphics.shapes import Drawing, Line, PolyLine, Rect, String
from reportlab.lib import colors
from reportlab.lib.colors import HexColor
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    CondPageBreak,
    HRFlowable,
    KeepTogether,
    ListFlowable,
    ListItem,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
OUTPUT_PATH = PROJECT_ROOT / "output/pdf/alphaess_solar_battery_plan.pdf"

NAVY = HexColor("#18324A")
BLUE = HexColor("#2C6E9B")
TEAL = HexColor("#16809A")
GREEN = HexColor("#3C8D6D")
GOLD = HexColor("#D39A2C")
RED = HexColor("#B55245")
INK = HexColor("#24303A")
MUTED = HexColor("#64727E")
GRID = HexColor("#D8E0E5")
PALE_BLUE = HexColor("#EAF2F7")
PALE_GREEN = HexColor("#EAF4F0")
PALE_GOLD = HexColor("#FBF3E2")
WHITE = colors.white


ANNUAL_LOAD = [
    ("2022", 11.11, "Pre-EV"),
    ("2023", 11.40, "Pre-EV"),
    ("2024*", 14.35, "Model Y from May"),
    ("2025", 16.07, "One-EV full year"),
    ("Future", 19.07, "Two-vehicle plan"),
]

MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
PRE_EV = [1370, 1138, 872, 908, 760, 719, 605, 773, 729, 797, 1085, 1484]
ONE_EV = [2049, 1574, 1380, 1114, 1227, 1084, 939, 1113, 1046, 901, 1677, 1971]
FUTURE = [2314, 1803, 1650, 1371, 1471, 1334, 1210, 1364, 1283, 1160, 1914, 2201]
PV_17_5 = [312, 549, 1097, 1506, 2451, 2396, 2291, 1793, 1566, 820, 397, 284]

BATTERY_CONSERVATIVE = [
    (0, 27.8, 13.77, 10.16, 0),
    (10, 42.5, 10.96, 7.05, 296),
    (15, 48.0, 9.92, 5.88, 271),
    (20, 52.2, 9.12, 5.00, 245),
    (25, 54.2, 8.73, 4.57, 212),
    (30, 55.4, 8.50, 4.32, 185),
]

BATTERY_EMPIRICAL = [
    (0, 30.4),
    (10, 45.7),
    (15, 51.4),
    (20, 55.5),
    (25, 57.4),
    (30, 58.5),
]


def styles():
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle(
            "Title",
            parent=base["Title"],
            fontName="Helvetica-Bold",
            fontSize=27,
            leading=31,
            textColor=NAVY,
            alignment=TA_LEFT,
            spaceAfter=10,
        ),
        "subtitle": ParagraphStyle(
            "Subtitle",
            parent=base["Normal"],
            fontName="Helvetica",
            fontSize=12,
            leading=17,
            textColor=MUTED,
            spaceAfter=16,
        ),
        "h1": ParagraphStyle(
            "H1",
            parent=base["Heading1"],
            fontName="Helvetica-Bold",
            fontSize=18,
            leading=22,
            textColor=NAVY,
            spaceBefore=4,
            spaceAfter=10,
        ),
        "h2": ParagraphStyle(
            "H2",
            parent=base["Heading2"],
            fontName="Helvetica-Bold",
            fontSize=13,
            leading=16,
            textColor=NAVY,
            spaceBefore=10,
            spaceAfter=6,
        ),
        "body": ParagraphStyle(
            "Body",
            parent=base["BodyText"],
            fontName="Helvetica",
            fontSize=9.5,
            leading=14,
            textColor=INK,
            spaceAfter=7,
        ),
        "small": ParagraphStyle(
            "Small",
            parent=base["BodyText"],
            fontName="Helvetica",
            fontSize=7.7,
            leading=10.5,
            textColor=MUTED,
            spaceAfter=4,
        ),
        "callout_title": ParagraphStyle(
            "CalloutTitle",
            parent=base["Heading2"],
            fontName="Helvetica-Bold",
            fontSize=15,
            leading=18,
            textColor=NAVY,
            alignment=TA_CENTER,
            spaceAfter=4,
        ),
        "callout_body": ParagraphStyle(
            "CalloutBody",
            parent=base["BodyText"],
            fontName="Helvetica",
            fontSize=9,
            leading=12,
            textColor=INK,
            alignment=TA_CENTER,
        ),
        "table": ParagraphStyle(
            "TableText",
            parent=base["BodyText"],
            fontName="Helvetica",
            fontSize=7.8,
            leading=9.5,
            textColor=INK,
        ),
        "table_head": ParagraphStyle(
            "TableHead",
            parent=base["BodyText"],
            fontName="Helvetica-Bold",
            fontSize=7.4,
            leading=9,
            textColor=WHITE,
            alignment=TA_CENTER,
        ),
    }


S = styles()


def P(text: str, style: str = "body") -> Paragraph:
    return Paragraph(text, S[style])


def bullet_list(items: list[str]) -> ListFlowable:
    return ListFlowable(
        [ListItem(P(item), leftIndent=8) for item in items],
        bulletType="bullet",
        bulletChar="-",
        leftIndent=15,
        bulletFontName="Helvetica",
        bulletFontSize=8,
        spaceAfter=6,
    )


def standard_table(
    data: list[list],
    widths: list[float],
    *,
    header: bool = True,
    font_size: float = 7.5,
    highlight_rows: tuple[int, ...] = (),
) -> Table:
    table = Table(data, colWidths=widths, repeatRows=1 if header else 0, hAlign="LEFT")
    style = [
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("FONTNAME", (0, 0), (-1, -1), "Helvetica"),
        ("FONTSIZE", (0, 0), (-1, -1), font_size),
        ("TEXTCOLOR", (0, 0), (-1, -1), INK),
        ("GRID", (0, 0), (-1, -1), 0.35, GRID),
    ]
    if header:
        style += [
            ("BACKGROUND", (0, 0), (-1, 0), NAVY),
            ("TEXTCOLOR", (0, 0), (-1, 0), WHITE),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("ALIGN", (1, 0), (-1, -1), "RIGHT"),
        ]
    for row in range(1 if header else 0, len(data)):
        if row % 2 == 0:
            style.append(("BACKGROUND", (0, row), (-1, row), HexColor("#F6F8FA")))
    for row in highlight_rows:
        style += [
            ("BACKGROUND", (0, row), (-1, row), PALE_GREEN),
            ("FONTNAME", (0, row), (-1, row), "Helvetica-Bold"),
        ]
    table.setStyle(TableStyle(style))
    return table


def metric_card(value: str, label: str, detail: str, background=PALE_BLUE) -> Table:
    content = [
        [P(value, "callout_title")],
        [P(label, "callout_body")],
        [P(detail, "small")],
    ]
    card = Table(content, colWidths=[52 * mm], rowHeights=None)
    card.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), background),
                ("BOX", (0, 0), (-1, -1), 0.6, GRID),
                ("LEFTPADDING", (0, 0), (-1, -1), 8),
                ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                ("TOPPADDING", (0, 0), (-1, -1), 7),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ]
        )
    )
    return card


def annual_load_chart() -> Drawing:
    width, height = 500, 210
    drawing = Drawing(width, height)
    left, right, bottom, top = 42, 12, 36, 12
    plot_w, plot_h = width - left - right, height - bottom - top
    max_value = 20
    for tick in range(0, 21, 5):
        y = bottom + tick / max_value * plot_h
        drawing.add(Line(left, y, width - right, y, strokeColor=GRID, strokeWidth=0.6))
        drawing.add(String(left - 7, y - 3, str(tick), textAnchor="end", fontName="Helvetica", fontSize=7, fillColor=MUTED))
    drawing.add(String(8, bottom + plot_h / 2, "MWh/year", fontName="Helvetica", fontSize=7, fillColor=MUTED))
    slot = plot_w / len(ANNUAL_LOAD)
    bar_w = 48
    for index, (label, value, _) in enumerate(ANNUAL_LOAD):
        x = left + index * slot + (slot - bar_w) / 2
        bar_h = value / max_value * plot_h
        fill = TEAL if label == "Future" else BLUE
        drawing.add(Rect(x, bottom, bar_w, bar_h, rx=3, ry=3, fillColor=fill, strokeColor=None))
        drawing.add(String(x + bar_w / 2, bottom + bar_h + 6, f"{value:.1f}", textAnchor="middle", fontName="Helvetica-Bold", fontSize=8, fillColor=INK))
        drawing.add(String(x + bar_w / 2, 18, label, textAnchor="middle", fontName="Helvetica", fontSize=8, fillColor=INK))
    return drawing


def monthly_chart() -> Drawing:
    width, height = 500, 240
    drawing = Drawing(width, height)
    left, right, bottom, top = 40, 12, 46, 18
    plot_w, plot_h = width - left - right, height - bottom - top
    max_value = 2600
    for tick in range(0, 2501, 500):
        y = bottom + tick / max_value * plot_h
        drawing.add(Line(left, y, width - right, y, strokeColor=GRID, strokeWidth=0.55))
        label = "0" if tick == 0 else f"{tick // 1000}.{(tick % 1000) // 100}k"
        drawing.add(String(left - 6, y - 3, label, textAnchor="end", fontName="Helvetica", fontSize=6.5, fillColor=MUTED))
    slot = plot_w / 12
    bar_w = 21
    for index, value in enumerate(PV_17_5):
        x_mid = left + index * slot + slot / 2
        bar_h = value / max_value * plot_h
        drawing.add(Rect(x_mid - bar_w / 2, bottom, bar_w, bar_h, fillColor=GOLD, strokeColor=None))
        drawing.add(String(x_mid, 31, MONTHS[index], textAnchor="middle", fontName="Helvetica", fontSize=6.8, fillColor=INK))

    def add_line(values, color, width=1.8):
        points = []
        for index, value in enumerate(values):
            x = left + index * slot + slot / 2
            y = bottom + value / max_value * plot_h
            points.extend((x, y))
        drawing.add(PolyLine(points, strokeColor=color, strokeWidth=width, fillColor=None))

    add_line(PRE_EV, MUTED, 1.2)
    add_line(ONE_EV, BLUE, 1.7)
    add_line(FUTURE, TEAL, 2.4)
    legend_y = 9
    legend = [
        (MUTED, "Pre-EV load"),
        (BLUE, "2025 Model Y"),
        (TEAL, "Two-vehicle forecast"),
        (GOLD, "17.5 kWp PV"),
    ]
    x = 48
    for color, label in legend:
        drawing.add(Line(x, legend_y + 3, x + 14, legend_y + 3, strokeColor=color, strokeWidth=3))
        drawing.add(String(x + 18, legend_y, label, fontName="Helvetica", fontSize=6.8, fillColor=INK))
        x += 112
    return drawing


def battery_chart() -> Drawing:
    width, height = 500, 220
    drawing = Drawing(width, height)
    left, right, bottom, top = 42, 15, 40, 15
    plot_w, plot_h = width - left - right, height - bottom - top
    min_y, max_y = 25, 60
    capacities = [row[0] for row in BATTERY_CONSERVATIVE]
    for tick in range(30, 61, 5):
        y = bottom + (tick - min_y) / (max_y - min_y) * plot_h
        drawing.add(Line(left, y, width - right, y, strokeColor=GRID, strokeWidth=0.55))
        drawing.add(String(left - 6, y - 3, str(tick), textAnchor="end", fontName="Helvetica", fontSize=7, fillColor=MUTED))
    drawing.add(String(7, bottom + plot_h / 2, "Local supply (%)", fontName="Helvetica", fontSize=7, fillColor=MUTED))
    for cap in capacities:
        x = left + cap / 30 * plot_w
        drawing.add(String(x, 24, str(cap), textAnchor="middle", fontName="Helvetica", fontSize=7, fillColor=INK))
    drawing.add(String(left + plot_w / 2, 8, "Usable battery capacity (kWh)", textAnchor="middle", fontName="Helvetica", fontSize=7, fillColor=MUTED))

    def series(rows, color, width):
        points = []
        for cap, value in rows:
            x = left + cap / 30 * plot_w
            y = bottom + (value - min_y) / (max_y - min_y) * plot_h
            points.extend((x, y))
        drawing.add(PolyLine(points, strokeColor=color, strokeWidth=width, fillColor=None))
        for i in range(0, len(points), 2):
            drawing.add(Rect(points[i] - 2, points[i + 1] - 2, 4, 4, fillColor=color, strokeColor=None))

    series([(r[0], r[1]) for r in BATTERY_CONSERVATIVE], BLUE, 2)
    series(BATTERY_EMPIRICAL, GREEN, 2)
    drawing.add(Line(295, 198, 313, 198, strokeColor=BLUE, strokeWidth=2))
    drawing.add(String(318, 195, "Strict north-roof model", fontName="Helvetica", fontSize=7, fillColor=INK))
    drawing.add(Line(405, 198, 423, 198, strokeColor=GREEN, strokeWidth=2))
    drawing.add(String(428, 195, "Measured roof yield", fontName="Helvetica", fontSize=7, fillColor=INK))
    return drawing


def on_page(canvas, doc):
    canvas.saveState()
    page = canvas.getPageNumber()
    if page > 1:
        canvas.setStrokeColor(GRID)
        canvas.setLineWidth(0.5)
        canvas.line(18 * mm, A4[1] - 13 * mm, A4[0] - 18 * mm, A4[1] - 13 * mm)
        canvas.setFont("Helvetica", 7)
        canvas.setFillColor(MUTED)
        canvas.drawString(18 * mm, A4[1] - 10 * mm, "Solar and home-battery planning estimate | 25242")
    canvas.setFont("Helvetica", 7)
    canvas.setFillColor(MUTED)
    canvas.drawString(18 * mm, 10 * mm, "Planning estimate - verify structure, grid connection, and product compatibility before purchase")
    canvas.drawRightString(A4[0] - 18 * mm, 10 * mm, f"Page {page}")
    canvas.restoreState()


def build_story():
    story = []

    # Cover and executive summary.
    story += [
        Spacer(1, 16 * mm),
        P("Solar and Home-Battery Plan", "title"),
        P("Three-year planning estimate for a two-vehicle household near postal code 25242, Czech Republic", "subtitle"),
        HRFlowable(width="100%", thickness=2, color=TEAL, spaceBefore=1, spaceAfter=16),
        P("Recommended planning target", "h1"),
    ]
    cards = Table(
        [[
            metric_card("17.5 kWp", "Solar array", "About 9 kWp roof + 8.5 kWp canopy", PALE_BLUE),
            metric_card("20 kWh", "Usable storage", "The practical knee of the storage curve", PALE_GREEN),
            metric_card("19.1 MWh", "Future annual load", "Existing home + Model Y + second PHEV", PALE_GOLD),
        ]],
        colWidths=[56 * mm, 56 * mm, 56 * mm],
        hAlign="CENTER",
    )
    cards.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 2), ("RIGHTPADDING", (0, 0), (-1, -1), 2)]))
    story += [
        cards,
        Spacer(1, 8 * mm),
        P(
            "The measured household load increased from approximately <b>11.25 MWh/year before the Model Y</b> "
            "to <b>16.07 MWh in 2025</b>. Adding a second vehicle at 15,000 km/year with 80% electric driving "
            "produces an expected future load of <b>19.1 MWh/year</b>.",
        ),
        P(
            "A 17.5 kWp array is expected to generate <b>15.5 MWh/year in the conservative strict north-roof model</b> "
            "or around <b>17.2 MWh/year if the main roof reproduces the previous system's measured yield</b>.",
        ),
        P(
            "With 20 kWh usable storage, the model supplies approximately <b>52-56% of annual consumption locally</b>. "
            "The remaining grid dependence is primarily a Czech winter-season problem, not a daily storage problem.",
        ),
        Spacer(1, 4 * mm),
        P("Specified equipment envelope", "h2"),
        bullet_list(
            [
                "<b>8.5 kWp south-facing canopy array</b>, subject to structural and wind-uplift design.",
                "<b>Approximately 9 kWp main-roof array</b>, after confirming orientation, area, and former module count.",
                "<b>Three-phase 15 kW-class hybrid inverter</b> and at least <b>10 kW continuous battery power</b>.",
                "Two dynamically load-managed EV circuits with solar-surplus charging and a shared site import limit.",
                "Battery capacity specified as usable; 20 kWh usable may require about 22-24 kWh nominal.",
            ]
        ),
        Spacer(1, 6 * mm),
        P("Prepared 30 July 2026 from Alpha ESS API data through 29 July 2026.", "small"),
        PageBreak(),
    ]

    # Historical evidence.
    story += [
        P("1. What the Alpha ESS history shows", "h1"),
        P(
            "The API contains daily solar, grid, and battery flows. Household consumption was reconstructed as "
            "<b>PV + grid import + battery discharge - grid export - battery charge</b>. This prevents grid import "
            "from being mistaken for total consumption.",
        ),
        annual_load_chart(),
        P("* 2024 was corrected for missing and anomalous API days.", "small"),
    ]
    annual_table = [
        ["Year", "Load", "PV", "Grid import", "Grid export", "Interpretation"],
        ["2022", "11.11 MWh", "7.98 MWh", "7.43 MWh", "4.06 MWh", "Pre-EV"],
        ["2023", "11.40 MWh", "8.58 MWh", "6.15 MWh", "2.98 MWh", "Pre-EV; one day imputed"],
        ["2024", "14.35 MWh", "8.29 MWh", "8.53 MWh", "2.01 MWh", "Model Y from about May"],
        ["2025", "16.07 MWh", "8.33 MWh", "10.31 MWh", "2.16 MWh", "Full one-EV year"],
        ["2026 Jan-Jul", "9.20 MWh", "2.28 MWh", "7.37 MWh", "0.20 MWh", "Load 1.8% below same 2025 period"],
    ]
    story += [
        standard_table(annual_table, [20 * mm, 24 * mm, 22 * mm, 25 * mm, 24 * mm, 57 * mm]),
        Spacer(1, 5 * mm),
        P("Model Y attribution", "h2"),
        P(
            "The difference between the 2022-2023 baseline and 2025 is approximately <b>4.8 MWh/year</b>. "
            "That independently matches roughly 50,000 km since May 2024 after typical wall-charging losses. "
            "The first seven months of 2026 are close to the same period in 2025, so 16 MWh/year is a reasonable "
            "stable one-EV baseline.",
        ),
        P(
            "The historical installation did include battery activity. The proposed capacity is nevertheless sized "
            "as a new system, as requested; historical battery flows are removed when reconstructing site load.",
            "small",
        ),
        PageBreak(),
    ]

    # Monthly seasonality.
    story += [
        P("2. Monthly demand and seasonal solar production", "h1"),
        P(
            "The second vehicle is modelled at 15,000 km/year, with 12,000 electric km and a conservative "
            "<b>3.0 MWh/year</b> of wall energy. This takes the expected future household load to 19.07 MWh/year.",
        ),
        monthly_chart(),
        P(
            "The annual totals conceal the main design constraint: January demand is about 2.31 MWh while the "
            "conservative 17.5 kWp array produces only about 0.31 MWh. In May-July the relationship reverses and "
            "large surpluses are available.",
        ),
    ]
    monthly_table = [["Month", "Pre-EV", "2025 Model Y", "Two vehicles", "17.5 kWp PV"]]
    for row in zip(MONTHS, PRE_EV, ONE_EV, FUTURE, PV_17_5):
        monthly_table.append([row[0], f"{row[1]:,}", f"{row[2]:,}", f"{row[3]:,}", f"{row[4]:,}"])
    monthly_table.append(["Year", "11,240", "16,074", "19,074", "15,462"])
    story += [
        standard_table(monthly_table, [24 * mm, 31 * mm, 35 * mm, 35 * mm, 35 * mm], highlight_rows=(13,)),
        P("Values are kWh. PV is the strict 20-degree north-roof plus south-canopy model.", "small"),
        PageBreak(),
    ]

    # Geometry and solar.
    story += [
        P("3. Roof and canopy solar design", "h1"),
        P("Canopy planning envelope", "h2"),
        P(
            "A comfortable two-car canopy with about 1 m between the vehicles and about 1 m clearance around the "
            "outside edges implies a footprint close to <b>7 m x 7 m, or 49 m2 gross</b>. The BMW X5 is almost "
            "exactly 5 m long and 2 m wide, which supports this estimate.",
        ),
        bullet_list(
            [
                "Allowing for structural members, drainage, module spacing, and wind edge zones, <b>8-10 kWp</b> is plausible.",
                "The planning model uses <b>8.5 kWp south-facing at 20 degrees</b>.",
                "PVGIS estimates about <b>1,052 kWh/kWp/year</b>, or approximately <b>8.9 MWh/year</b> from the canopy.",
                "The canopy structure, connections, and foundations require a site-specific wind-uplift calculation.",
            ]
        ),
        P("Main-roof uncertainty", "h2"),
        P(
            "At this location, the European Commission PVGIS model estimates approximately <b>724 kWh/kWp/year</b> "
            "for a strictly north-facing array at 20 degrees, versus <b>1,052 kWh/kWp/year</b> facing south. "
            "Nine kWp on the north roof therefore contributes approximately 6.5 MWh/year.",
        ),
    ]
    yield_table = [
        ["Design case", "North roof", "South canopy", "Expected PV", "Use in planning"],
        ["Conservative geometry", "9.0 kWp", "8.5 kWp", "15.46 MWh/year", "Primary sizing case"],
        ["Measured roof yield", "Reproduce 8.33 MWh", "8.5 kWp", "17.21 MWh/year", "Evidence-based upside"],
        ["Small north roof", "6.0 kWp", "8.5 kWp", "13.27 MWh/year", "Lower-cost fallback"],
    ]
    story += [
        standard_table(yield_table, [38 * mm, 28 * mm, 29 * mm, 34 * mm, 43 * mm], highlight_rows=(1,)),
        Spacer(1, 5 * mm),
        Table(
            [[P(
                "<b>Important discrepancy:</b> the Alpha ESS API shows 8.0-8.6 MWh/year from the previous roof-only "
                "system and instantaneous PV power up to 8.8 kW. That is not consistent with a literal 6 kWp, "
                "strictly north-facing array. Verify the former module count, DC nameplate, inverter rating, and "
                "actual azimuth before ordering.",
                "body",
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
        Spacer(1, 7 * mm),
        P("Why 17.5 kWp rather than matching annual demand exactly", "h2"),
        P(
            "Adding more north-facing PV raises summer exports much faster than it reduces winter imports. "
            "The 17.5 kWp layout uses the high-value south canopy fully and keeps the main roof near a practical "
            "size. Additional north-roof modules may still make sense if their installed marginal cost is low or "
            "export compensation is attractive.",
        ),
        PageBreak(),
    ]

    # Battery.
    story += [
        P("4. Battery capacity trade-off", "h1"),
        P(
            "The storage model replays the measured 2025 five-minute load profile, adds the second vehicle as "
            "evening charging load, applies hourly PVGIS production, and assumes 90% round-trip battery efficiency. "
            "It dispatches the battery for solar self-consumption only.",
        ),
        battery_chart(),
    ]
    battery_table = [["Usable battery", "Grid import", "Grid export", "Local supply", "PV used on site", "Cycles/year"]]
    pv_self = [34.3, 54.4, 61.9, 67.7, 70.4, 72.1]
    for index, row in enumerate(BATTERY_CONSERVATIVE):
        battery_table.append(
            [
                f"{row[0]} kWh",
                f"{row[2]:.2f} MWh",
                f"{row[3]:.2f} MWh",
                f"{row[1]:.1f}%",
                f"{pv_self[index]:.1f}%",
                "-" if row[0] == 0 else f"{row[4]}",
            ]
        )
    story += [
        standard_table(battery_table, [27 * mm, 27 * mm, 27 * mm, 27 * mm, 31 * mm, 25 * mm], highlight_rows=(4,)),
        Spacer(1, 5 * mm),
        P("Why 20 kWh is the recommended knee", "h2"),
        bullet_list(
            [
                "Moving from 15 to 20 kWh avoids about <b>0.80 MWh/year</b> of additional grid import.",
                "Moving from 20 to 25 kWh avoids only about <b>0.38 MWh/year</b>.",
                "Moving from 25 to 30 kWh avoids only another <b>0.23 MWh/year</b>.",
                "At 20 kWh, the battery completes about <b>245-252 equivalent cycles/year</b>, a healthy utilization level.",
            ]
        ),
        P(
            "In the API-grounded roof-output case, 20 kWh usable storage gives approximately <b>8.48 MWh grid "
            "import, 6.09 MWh export, and 55.5% local supply</b>. More storage cannot economically transfer summer "
            "energy into winter.",
        ),
        PageBreak(),
    ]

    # Sodium and implementation.
    story += [
        P("5. Sodium-ion strategy and procurement", "h1"),
        P(
            "Use <b>20 kWh usable and at least 10 kW continuous power</b> as the requirement, independent of "
            "chemistry. Select the chemistry and product only when the system is purchased.",
        ),
        P(
            "Sodium-ion is commercially credible at utility scale. CATL has announced commissioned production "
            "lines, Chinese deliveries during 2026, international deliveries from June 2027, and a 60 GWh storage "
            "agreement. This does not yet guarantee a competitively priced, certified, supported residential "
            "product in the Czech market.",
        ),
        P("Compare complete installed systems on", "h2"),
        bullet_list(
            [
                "Installed price per usable kWh and total project cost.",
                "Warranted years and energy throughput, not just headline cycle count.",
                "Round-trip efficiency, continuous power, and peak power.",
                "Cold-weather operating and charging limits.",
                "Fire and electrical certification, Czech installer support, and replacement availability.",
                "Explicit inverter/BMS compatibility and a written upgrade path.",
            ]
        ),
        Table(
            [[P(
                "<b>Do not assume a future sodium-ion pack can be attached to an arbitrary hybrid inverter.</b> "
                "The safest procurement route is a supported inverter-and-battery combination purchased together, "
                "or a vendor-backed written compatibility commitment.",
                "body",
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
        Spacer(1, 7 * mm),
        P("Electrical and operational specification", "h2"),
        bullet_list(
            [
                "Three-phase 15 kW-class hybrid inverter, subject to the distribution connection limit.",
                "At least 10 kW continuous battery power; measured household demand reaches about 10 kW at the 95th percentile.",
                "Dynamic site-load control across heat pump, battery, and both EV chargers.",
                "Solar-surplus EV charging whenever either car is home during daylight.",
                "If whole-home backup is required, specify at least 15 kW three-phase backup or separate critical loads.",
                "Confirm export limits and compensation before final inverter and array sizing.",
            ]
        ),
        P("Implementation sequence", "h2"),
    ]
    implementation = [
        ["1", "Site survey", "Measure roof/canopy geometry, azimuth, pitch, shading, wind exposure, and former system nameplates."],
        ["2", "Grid check", "Confirm three-phase connection, main breaker, export limit, and charger load-management requirements."],
        ["3", "Tender basis", "Request prices for 17.5 kWp PV, 20 kWh usable storage, 15 kW hybrid inverter, and two EV circuits."],
        ["4", "Technology choice", "Compare sodium-ion and LFP complete systems using warranty throughput and installed price."],
        ["5", "Final replay", "Re-run the model with actual tariff, export value, equipment efficiency, and quoted system limits."],
    ]
    story += [
        standard_table([["Step", "Decision", "Required output"]] + implementation, [14 * mm, 32 * mm, 124 * mm]),
        PageBreak(),
    ]

    # Method, limitations, sources.
    story += [
        P("6. Method, assumptions, and sources", "h1"),
        P("Core assumptions", "h2"),
        bullet_list(
            [
                "Location represented by 49.9681 N, 14.5135 E in the 25242 postal-code area.",
                "Model Y received around May 2024; 50,000 km accumulated by July 2026.",
                "Second vehicle: 15,000 km/year, 80% electric, 3.0 MWh/year wall energy.",
                "Main roof: 20-degree strict north case for conservative sizing.",
                "Canopy: approximately 7 m x 7 m gross, 8.5 kWp usable south-facing array.",
                "PVGIS fixed-array model with 14% system losses.",
                "Battery: 90% round-trip efficiency, solar-self-consumption dispatch, no price arbitrage.",
                "Battery capacity values are usable energy; backup reserve would reduce energy available for self-consumption.",
            ]
        ),
        P("Limitations", "h2"),
        P(
            "This is a planning model, not a structural design, electrical single-line diagram, grid-connection "
            "approval, or financial quotation. Roof dimensions, shading, snow and wind loads, cable routes, fire "
            "separation, inverter clipping, actual tariff, export compensation, and vehicle-at-home behavior can "
            "change the result. Dynamic-price arbitrage may improve economics but will not remove the winter energy deficit.",
        ),
        P("Primary sources", "h2"),
        P(
            "Alpha ESS Open API through the local optimizer project: daily energy totals and five-minute power data "
            "from 2021-01-01 through 2026-07-29.",
        ),
        P(
            '<link href="https://re.jrc.ec.europa.eu/pvg_tools/en/" color="#2C6E9B">'
            "European Commission Joint Research Centre - PVGIS</link>: fixed-array annual, monthly, and hourly PV estimates.",
        ),
        P(
            '<link href="https://www.bmw.cz/cs/all-models/x-series/suv/bmw-x5-technicke-udaje.html" color="#2C6E9B">'
            "BMW Czech Republic - X5 technical data</link>: vehicle dimensions and charging capability.",
        ),
        P(
            '<link href="https://www.catl.com/en/news/6861.html" color="#2C6E9B">'
            "CATL - field-validated sodium-ion BESS</link>: manufacturing and international-delivery statements.",
        ),
        P(
            '<link href="https://www.catl.com/en/news/6812.html" color="#2C6E9B">'
            "CATL and HyperStrong - 60 GWh sodium-ion storage agreement</link>.",
        ),
        Spacer(1, 8 * mm),
        HRFlowable(width="100%", thickness=1, color=GRID, spaceBefore=2, spaceAfter=8),
        P(
            "<b>Bottom line:</b> plan around 17.5 kWp PV and 20 kWh usable storage. Maximize the south-facing canopy, "
            "verify why the former roof system outperformed the remembered 6 kW figure, and choose sodium-ion or LFP "
            "only when certified residential products and firm installed prices are available.",
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
        title="Solar and Home-Battery Plan",
        author="Prepared from Alpha ESS API analysis",
        subject="Three-year solar and residential battery sizing estimate",
    )
    doc.build(build_story(), onFirstPage=on_page, onLaterPages=on_page)
    print(OUTPUT_PATH)


if __name__ == "__main__":
    main()
