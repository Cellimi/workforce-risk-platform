"""The report a consultant hands a client: a `ReportContent` laid out as a PDF.

This module decides how the report looks and nothing about what it says. Every number and
every sentence comes from the `ReportContent`; if you want to change a figure, change it
there, and the workbook changes with it.

Text supplied by a person -- a client's name, a department, the reason given for an override
-- is escaped before it is laid out, because the layout engine reads `<b>` and `&` as markup.
"""

from __future__ import annotations

import io
from decimal import Decimal
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    Image,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from wri_engine.reporting.content import PRODUCT_NAME, ReportContent

INK = colors.HexColor("#1f2933")
MUTED = colors.HexColor("#52606d")
RULE = colors.HexColor("#cbd2d9")
BAND = colors.HexColor("#f0f4f8")
ACCENT = colors.HexColor("#243b53")
SAVING = "#9b1c1c"

#: The matrix can run to hundreds of cells. The report shows the dearest and says how many
#: more there are; the workbook carries every one.
MAX_MATRIX_ROWS = 30
LOGO_MAX_WIDTH = 2.2 * inch
LOGO_MAX_HEIGHT = 1.0 * inch


def _styles() -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()
    body = ParagraphStyle("body", parent=base["BodyText"], fontSize=9.5, leading=13, textColor=INK)
    return {
        "body": body,
        "small": ParagraphStyle("small", parent=body, fontSize=8, leading=10.5, textColor=MUTED),
        "cell": ParagraphStyle("cell", parent=body, fontSize=8, leading=10),
        "cell_right": ParagraphStyle(
            "cell_right", parent=body, fontSize=8, leading=10, alignment=TA_RIGHT
        ),
        "head": ParagraphStyle(
            "head",
            parent=body,
            fontSize=8,
            leading=10,
            textColor=colors.white,
            fontName="Helvetica-Bold",
        ),
        "head_right": ParagraphStyle(
            "head_right",
            parent=body,
            fontSize=8,
            leading=10,
            textColor=colors.white,
            fontName="Helvetica-Bold",
            alignment=TA_RIGHT,
        ),
        "h1": ParagraphStyle(
            "h1",
            parent=base["Heading1"],
            fontSize=15,
            leading=19,
            textColor=ACCENT,
            spaceBefore=6,
            spaceAfter=6,
        ),
        "h2": ParagraphStyle(
            "h2",
            parent=base["Heading2"],
            fontSize=11.5,
            leading=15,
            textColor=ACCENT,
            spaceBefore=10,
            spaceAfter=4,
            keepWithNext=1,
        ),
        "cover_firm": ParagraphStyle(
            "cover_firm", parent=body, fontSize=13, leading=17, textColor=MUTED
        ),
        "cover_title": ParagraphStyle(
            "cover_title",
            parent=base["Title"],
            fontSize=28,
            leading=34,
            textColor=ACCENT,
            alignment=0,
            spaceAfter=4,
        ),
        "cover_client": ParagraphStyle(
            "cover_client", parent=body, fontSize=16, leading=21, textColor=INK
        ),
        "headline": ParagraphStyle(
            "headline",
            parent=body,
            fontSize=19,
            leading=23,
            textColor=ACCENT,
            fontName="Helvetica-Bold",
        ),
        "headline_label": ParagraphStyle(
            "headline_label", parent=body, fontSize=8.5, leading=11, textColor=MUTED
        ),
    }


def money(value: Decimal | None) -> str:
    """Whole dollars, with a real minus sign in front. Rounding happens here and only here."""
    if value is None:
        return "n/a"
    rounded = value.quantize(Decimal("1"))
    return f"-${-rounded:,}" if rounded < 0 else f"${rounded:,}"


def _text(value) -> str:
    return escape(str(value))


def _table(header: list[str], rows: list[list], widths: list[float], styles, right_from: int):
    """A banded table whose header repeats on every page. Columns from `right_from` on are
    numbers and are right-aligned."""

    def cell(value, column: int):
        style = styles["cell_right"] if column >= right_from else styles["cell"]
        return value if isinstance(value, Paragraph) else Paragraph(_text(value), style)

    data = [
        [
            Paragraph(_text(h), styles["head_right" if i >= right_from else "head"])
            for i, h in enumerate(header)
        ]
    ]
    data += [[cell(v, i) for i, v in enumerate(row)] for row in rows]
    table = Table(data, colWidths=widths, repeatRows=1, hAlign="LEFT")
    commands = [
        ("BACKGROUND", (0, 0), (-1, 0), ACCENT),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LINEBELOW", (0, 0), (-1, -1), 0.25, RULE),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
    ]
    for index in range(2, len(data), 2):
        commands.append(("BACKGROUND", (0, index), (-1, index), BAND))
    table.setStyle(TableStyle(commands))
    return table


def _logo(content: ReportContent):
    """The firm's logo, scaled to fit. A logo that cannot be read is left out rather than
    stopping the report: the name still says whose it is."""
    if not content.branding.logo:
        return None
    try:
        image = Image(io.BytesIO(content.branding.logo))
        scale = min(LOGO_MAX_WIDTH / image.imageWidth, LOGO_MAX_HEIGHT / image.imageHeight, 1)
        image.drawWidth = image.imageWidth * scale
        image.drawHeight = image.imageHeight * scale
        image.hAlign = "LEFT"
        return image
    except Exception:  # noqa: BLE001 - a bad image must not cost the client their report
        return None


def _cover(content: ReportContent, styles) -> list:
    story: list = [Spacer(1, 0.9 * inch)]
    logo = _logo(content)
    if logo is not None:
        story += [logo, Spacer(1, 0.25 * inch)]
    story += [
        Paragraph(_text(content.branding.prepared_by), styles["cover_firm"]),
        Spacer(1, 0.5 * inch),
        Paragraph(_text(content.title), styles["cover_title"]),
        Paragraph(_text(content.client_name), styles["cover_client"]),
        Spacer(1, 0.45 * inch),
    ]
    facts = [("Prepared for", content.client_name), ("Prepared by", content.branding.prepared_by)]
    if content.engagement_name:
        facts.append(("Engagement", content.engagement_name))
    facts.append(("Cost model", content.cost_model))
    if content.occupation_group:
        facts.append(("Occupation group", content.occupation_group))
    facts += [
        ("Data as of", content.as_of.isoformat() if content.as_of else "not stated"),
        ("Period covered", f"{content.window_years} years of decided actions"),
        ("Assumptions at", f"{content.mode} values"),
        ("Report prepared", content.generated_on.isoformat()),
    ]
    table = Table(
        [
            [Paragraph(_text(k), styles["small"]), Paragraph(_text(v), styles["body"])]
            for k, v in facts
        ],
        colWidths=[1.5 * inch, 4.6 * inch],
    )
    table.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LINEBELOW", (0, 0), (-1, -1), 0.25, RULE),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
            ]
        )
    )
    story += [table, Spacer(1, 0.4 * inch), Paragraph(_text(content.caption), styles["small"])]
    story.append(PageBreak())
    return story


def _headline(content: ReportContent, styles) -> list:
    tiles = [
        (money(content.annual_net), "net cost per year"),
        (money(content.net_per_employee_per_year), "per employee per year"),
        (money(content.net), f"net over {content.window_years} years"),
    ]
    table = Table(
        [
            [Paragraph(_text(value), styles["headline"]) for value, _ in tiles],
            [Paragraph(_text(label), styles["headline_label"]) for _, label in tiles],
        ],
        colWidths=[2.3 * inch] * 3,
    )
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), BAND),
                ("TOPPADDING", (0, 0), (-1, 0), 10),
                ("BOTTOMPADDING", (0, 1), (-1, 1), 10),
                ("LEFTPADDING", (0, 0), (-1, -1), 10),
            ]
        )
    )
    counted = (
        f"{content.actions_costed:,} disciplinary actions were costed across "
        f"{content.active_employees:,} active employees. "
        f"{content.actions_excluded:,} were left out because their records could not be used, "
        f"and {content.actions_incomplete:,} are still accruing cost, so their figures are a "
        f"floor. Gross cost was {money(content.gross)}; {money(-content.offset)} of savings "
        f"(wages not paid during an unpaid suspension or a vacancy, and amounts an insurer "
        f"paid) are shown separately rather than netted away, leaving {money(content.net)}."
    )
    return [
        Paragraph("Summary", styles["h1"]),
        table,
        Spacer(1, 8),
        Paragraph(_text(counted), styles["body"]),
    ]


def _components(content: ReportContent, styles) -> list:
    rows = []
    for row in content.components:
        share = "" if row.share_of_gross is None else f"{row.share_of_gross * 100:.0f}%"
        amount = Paragraph(
            f'<font color="{SAVING}">{_text(money(row.amount))}</font>'
            if row.is_offset
            else _text(money(row.amount)),
            styles["cell_right"],
        )
        rows.append([row.code, row.label, amount, share])
    rows.append(["", "Gross", money(content.gross), "100%"])
    rows.append(["", "Savings shown separately", money(content.offset), ""])
    rows.append(["", "Net", money(content.net), ""])
    return [
        Paragraph("Where the money goes", styles["h2"]),
        _table(
            ["", "Component", "Amount", "Share of gross"],
            rows,
            [0.9 * inch, 3.6 * inch, 1.3 * inch, 1.1 * inch],
            styles,
            right_from=2,
        ),
    ]


def _range(content: ReportContent, styles) -> list:
    if len(content.modes) < 2:
        return []
    rows = [
        [m.mode, money(m.gross), money(m.offset), money(m.net), money(m.annual_net)]
        for m in content.modes
    ]
    return [
        Paragraph("How far the figure moves with the assumptions", styles["h2"]),
        Paragraph(
            "The same records costed three times: with every assumption at its low bound, at "
            "its working value, and at its high bound. The truth is not expected to sit at "
            "either end; the spread shows how much of the total rests on judgement.",
            styles["body"],
        ),
        Spacer(1, 4),
        _table(
            ["Assumptions at", "Gross", "Savings", "Net", "Net per year"],
            rows,
            [1.4 * inch, 1.4 * inch, 1.3 * inch, 1.4 * inch, 1.4 * inch],
            styles,
            right_from=1,
        ),
    ]


def _matrix(content: ReportContent, styles) -> list:
    visible = content.visible_cells
    shown = visible[:MAX_MATRIX_ROWS]
    rows = [
        [c.row_label, c.col_label, f"{c.action_count:,}", money(c.net), money(c.cost_per_action)]
        for c in shown
    ]
    note = (
        f"Cells are {content.row_dimension.lower()} against "
        f"{content.col_dimension.lower()}. A cell is shown only where at least "
        f"{content.min_cell_size} different employees stand behind it, so that no figure can "
        f"be traced to a person. {content.suppressed_cell_count} cell(s) were withheld for "
        f"that reason; together they come to {money(content.suppressed_net)} net, which is "
        f"included in every total in this report."
    )
    if len(visible) > len(shown):
        note += (
            f" The {len(shown)} most expensive of {len(visible)} cells are listed here; the "
            f"workbook that accompanies this report lists them all."
        )
    return [
        Paragraph("Where the cost concentrates", styles["h2"]),
        Paragraph(_text(note), styles["body"]),
        Spacer(1, 4),
        _table(
            [content.row_dimension, content.col_dimension, "Actions", "Net cost", "Per action"],
            rows,
            [2.0 * inch, 2.3 * inch, 0.7 * inch, 1.0 * inch, 0.9 * inch],
            styles,
            right_from=2,
        ),
    ]


def _or_withheld(row, shown: str) -> str:
    """A comparison figure, or the word "withheld" where this copy may not carry it."""
    return "withheld" if row.comparison_withheld else shown


def _pattern_settings(section) -> str:
    """How a flag is decided, and over what period, in one sentence each."""
    text = (
        f"A flag needs an adjusted chance value of {section.false_discovery_rate:f} or "
        f"less, which allows for every comparison made, a rate at least "
        f"{section.min_rate_ratio:f} times the rate elsewhere, and at least "
        f"{section.min_people} different people disciplined."
    )
    if section.window_start and section.window_end:
        text = (
            f"The period is {section.window_start.isoformat()} to "
            f"{section.window_end.isoformat()}. " + text
        )
    if section.unreviewed:
        text += (
            " The thresholds that decide a flag are the product's starting values and are "
            "still under review."
        )
    return text


def _patterns(content: ReportContent, styles) -> list:
    """Where a type of misconduct clusters, and what it costs there."""
    section = content.patterns
    if section is None:
        return []
    story = [
        Paragraph("Where discipline clusters", styles["h2"]),
        Paragraph(_text(section.note), styles["body"]),
        Spacer(1, 3),
        Paragraph(_text(_pattern_settings(section)), styles["small"]),
    ]
    for view in section.views:
        story += [Spacer(1, 6), Paragraph(f"<b>{_text(view.label)}</b>", styles["body"])]
        if not view.flagged:
            nothing = (
                f"{view.comparisons:,} comparisons were made and nothing stood out."
                if not view.withheld
                else f"{view.comparisons:,} comparisons were made."
            )
            story.append(Paragraph(_text(nothing), styles["body"]))
        else:
            story.append(
                Paragraph(
                    _text(
                        f"{view.comparisons:,} comparisons were made. "
                        f"{len(view.flagged)} stood out."
                    ),
                    styles["body"],
                )
            )
            story.append(Spacer(1, 3))
            story.append(
                _table(
                    [
                        "Where",
                        "Actions",
                        "Expected at the rate elsewhere",
                        "Times the rate elsewhere",
                        "Cost of these actions",
                        "Estimated excess",
                    ],
                    [
                        [
                            row.name,
                            f"{row.actions:,}",
                            _or_withheld(row, f"{row.expected_actions:,}"),
                            _or_withheld(row, "n/a" if row.ratio is None else f"{row.ratio:.2f}"),
                            money(row.net),
                            _or_withheld(row, money(row.estimated_excess_cost)),
                        ]
                        for row in view.flagged
                    ],
                    [2.4 * inch, 0.6 * inch, 0.95 * inch, 0.85 * inch, 1.05 * inch, 1.05 * inch],
                    styles,
                    right_from=1,
                )
            )
            for row in view.flagged:
                story.append(Spacer(1, 3))
                worked = f"<b>{_text(row.name)}.</b> {_text(row.explanation)}"
                if row.formula:
                    worked += f" Estimated excess: {_text(row.formula)}."
                story.append(Paragraph(worked, styles["small"]))
        if view.withheld:
            one = view.withheld == 1
            story.append(
                Paragraph(
                    _text(
                        f"{view.withheld} more stood out and {'is' if one else 'are'} not "
                        f"shown: with the other figures in this report "
                        f"{'it' if one else 'they'} would let a group of fewer than "
                        f"{section.min_people} people be worked out."
                    ),
                    styles["small"],
                )
            )
    if section.flagged:
        story += [Spacer(1, 4), Paragraph(_text(section.cost_note), styles["small"])]
    story += _rising(section, styles)
    return story


def _times(value: Decimal | None) -> str:
    return "n/a" if value is None else f"{value:.2f}"


def _rising(section, styles) -> list:
    """Units whose rate rose over the period, by more than the rest of the organization."""
    rising = section.rising
    if rising is None:
        return []
    story = [
        Spacer(1, 8),
        Paragraph("<b>Rising over the period</b>", styles["body"]),
        Paragraph(_text(rising.note), styles["body"]),
    ]
    if rising.earlier_start:
        story += [
            Spacer(1, 3),
            Paragraph(
                _text(
                    f"Earlier half: {rising.earlier_start.isoformat()} to "
                    f"{rising.earlier_end.isoformat()}. Later half: "
                    f"{rising.later_start.isoformat()} to {rising.later_end.isoformat()}. "
                    f"The same thresholds apply, to the unit's own rise and to its rise "
                    f"against the rest of the organization."
                ),
                styles["small"],
            ),
        ]
    for view in rising.views:
        story += [Spacer(1, 5), Paragraph(f"<i>{_text(view.label)}</i>", styles["body"])]
        if not view.flagged:
            nothing = (
                f"{view.comparisons:,} comparisons were made and nothing rose by more than "
                f"the rest of the organization."
                if not view.withheld
                else f"{view.comparisons:,} comparisons were made."
            )
            story.append(Paragraph(_text(nothing), styles["body"]))
        else:
            story += [
                Paragraph(
                    _text(f"{view.comparisons:,} comparisons were made. {len(view.flagged)} rose."),
                    styles["body"],
                ),
                Spacer(1, 3),
                _table(
                    [
                        "Where",
                        "Actions, earlier then later",
                        "Times its earlier rate",
                        "Times the rise elsewhere",
                        "Cost, earlier then later",
                        "Estimated excess",
                    ],
                    [
                        [
                            row.name,
                            f"{row.earlier_actions:,} then {row.later_actions:,}",
                            _times(row.own_change),
                            _or_withheld(row, _times(row.ratio)),
                            f"{money(row.earlier_net)} then {money(row.later_net)}",
                            _or_withheld(row, money(row.estimated_excess_cost)),
                        ]
                        for row in view.flagged
                    ],
                    [2.1 * inch, 0.85 * inch, 0.75 * inch, 0.8 * inch, 1.3 * inch, 1.0 * inch],
                    styles,
                    right_from=1,
                ),
            ]
            for row in view.flagged:
                worked = f"<b>{_text(row.name)}.</b> {_text(row.explanation)}"
                if row.formula:
                    worked += f" Estimated excess: {_text(row.formula)}."
                story += [Spacer(1, 3), Paragraph(worked, styles["small"])]
        if view.withheld:
            one = view.withheld == 1
            story.append(
                Paragraph(
                    _text(
                        f"{view.withheld} more rose and {'is' if one else 'are'} not shown: "
                        f"{'it' if one else 'they'} would state a figure about a group of "
                        f"fewer than {section.min_people} people, or let one be worked out "
                        f"from the other figures in this report."
                    ),
                    styles["small"],
                )
            )
    if rising.flagged:
        story += [Spacer(1, 4), Paragraph(_text(rising.cost_note), styles["small"])]
    return story


def _share(value: Decimal) -> str:
    return f"{(value * 100).normalize():f}%"


def _break_even(share: Decimal | None) -> str:
    """The share of actions an intervention must prevent to pay for itself, in words where
    a percentage would mislead."""
    if share is None:
        return "nothing to save"
    if share > 1:
        return "cannot"
    return f"{_share(share)} prevented"


def _interventions(content: ReportContent, styles) -> list:
    """What was found on site, what is proposed, and what it would cost and save."""
    section = content.interventions
    if section is None:
        return []
    story = [
        Paragraph("What was found, and what could be done", styles["h2"]),
        Paragraph(_text(section.note), styles["body"]),
    ]
    for row in section.current:
        story += [
            Spacer(1, 7),
            Paragraph(f"<b>{_text(row.name)}: {_text(row.unit_name)}</b>", styles["body"]),
            Paragraph(
                _text(
                    f"Found on site: {row.found}. The finding of {row.entered_by}, entered "
                    f"{row.entered_on}."
                ),
                styles["body"],
            ),
        ]
        if row.annual_net is None:
            story.append(
                Paragraph(
                    _text(
                        f"The intervention would cost {money(row.total_cost)} over "
                        f"{row.years} years. {section.money_withheld}"
                    ),
                    styles["body"],
                )
            )
        else:
            story += [
                Spacer(1, 3),
                _table(
                    [
                        "The discipline costs, a year",
                        f"The intervention costs, {row.years} years",
                        *(
                            f"Net saving if it prevents {_share(s.reduction)} ({s.label.lower()})"
                            for s in row.savings
                        ),
                    ],
                    [
                        [
                            money(row.annual_net),
                            money(row.total_cost),
                            *(money(s.net_saving) for s in row.savings),
                        ]
                    ],
                    [1.25 * inch, 1.35 * inch, 1.4 * inch, 1.45 * inch, 1.4 * inch],
                    styles,
                    right_from=0,
                ),
                Spacer(1, 3),
                Paragraph(_text(row.break_even_note), styles["small"]),
            ]
        story.append(Paragraph(_text(f"Based on: {row.basis}"), styles["small"]))
    for table in section.side_by_side:
        story += [
            Spacer(1, 8),
            Paragraph(f"<b>Side by side: {_text(table.unit_name)}</b>", styles["body"]),
            Paragraph(
                _text(
                    f"The discipline costs {money(table.annual_net)} a year. Each proposal "
                    f"is put per year, so that those looking different distances ahead can "
                    f"be read together."
                ),
                styles["body"],
            ),
            Spacer(1, 3),
            _table(
                [
                    "Proposed",
                    "For",
                    "Costs, a year",
                    "Pays for itself at",
                    "Net saving a year: low",
                    "expected",
                    "high",
                ],
                [
                    [
                        option.name,
                        option.found,
                        money(option.yearly_cost),
                        _break_even(option.break_even),
                        money(option.yearly_net_low),
                        money(option.yearly_net_expected),
                        money(option.yearly_net_high),
                    ]
                    for option in table.options
                ],
                [
                    1.55 * inch,
                    1.3 * inch,
                    0.8 * inch,
                    0.85 * inch,
                    0.8 * inch,
                    0.8 * inch,
                    0.75 * inch,
                ],
                styles,
                right_from=2,
            ),
            Spacer(1, 3),
            Paragraph(_text(" ".join(table.notes)), styles["small"]),
        ]
    if section.not_shown:
        one = section.not_shown == 1
        concerns = "it concerns" if one else "they concern"
        story += [
            Spacer(1, 5),
            Paragraph(
                _text(
                    f"{section.not_shown} more {'was' if one else 'were'} looked into and "
                    f"{'is' if one else 'are'} not shown here: {concerns} fewer than "
                    f"{section.min_people} people."
                ),
                styles["small"],
            ),
        ]
    if any(row.savings for row in section.current):
        story += [
            Spacer(1, 5),
            Paragraph(
                _text("A saving above assumes: " + " ".join(section.assumes)), styles["small"]
            ),
        ]
    return story


def _departments(content: ReportContent, styles) -> list:
    if not content.departments:
        return []
    rows = [
        [d.department, f"{d.active_fte:,}", money(d.net), money(d.net_per_fte_per_year)]
        for d in content.departments
    ]
    return [
        Paragraph("Cost per employee per year, by department", styles["h2"]),
        _table(
            ["Department", "Active employees", "Net cost", "Per employee per year"],
            rows,
            [3.0 * inch, 1.2 * inch, 1.2 * inch, 1.5 * inch],
            styles,
            right_from=1,
        ),
    ]


def _quality(content: ReportContent, styles) -> list:
    story = [Paragraph("What the records could and could not support", styles["h2"])]
    if not content.quality:
        story.append(Paragraph("No problems were found in the records supplied.", styles["body"]))
        return story
    story.append(
        Paragraph(
            _text(
                f"{content.quality_blocking} problem(s) stopped a record being used and "
                f"{content.quality_warnings} were noted without stopping anything. "
                f"{content.actions_excluded} action(s) were left out of every figure as a "
                f"result. Where a detail was missing the lighter assumption was taken, so a "
                f"gap lowers the total rather than raising it."
            ),
            styles["body"],
        )
    )
    story.append(Spacer(1, 4))
    story.append(
        _table(
            ["What was found", "How many"],
            [[q.rule_id.replace("_", " "), f"{q.count:,}"] for q in content.quality],
            [5.7 * inch, 1.2 * inch],
            styles,
            right_from=1,
        )
    )
    return story


def _number(value: Decimal) -> str:
    return f"{value.normalize():f}" if value == value.to_integral() else f"{value:f}"


def _appendix_assumptions(content: ReportContent, styles) -> list:
    rows = []
    for a in content.assumptions_used:
        value = _number(a.value) + (" *" if a.overridden else "")
        rows.append(
            [
                Paragraph(_text(a.id), styles["cell"]),
                value,
                a.unit.replace("_", " "),
                f"{a.status}, {a.confidence}",
                Paragraph(_text(a.source), styles["small"]),
            ]
        )
    return [
        PageBreak(),
        Paragraph("Appendix A. The assumptions behind these figures", styles["h1"]),
        Paragraph(_text(content.assumption_note), styles["body"]),
        Spacer(1, 4),
        Paragraph(
            _text(
                f"These are the {len(content.assumptions_used)} assumptions the figures in "
                f"this report actually used, out of {content.registry_size} in the registry. "
                f"{content.standing} A value marked * is the client's own; see Appendix B."
            ),
            styles["body"],
        ),
        Spacer(1, 6),
        _table(
            ["Assumption", "Value", "Unit", "Standing", "Where it comes from"],
            rows,
            [1.9 * inch, 0.6 * inch, 0.95 * inch, 0.85 * inch, 2.6 * inch],
            styles,
            right_from=99,
        ),
    ]


def _appendix_overrides(content: ReportContent, styles) -> list:
    story = [
        Spacer(1, 10),
        Paragraph("Appendix B. Values specific to this client", styles["h1"]),
    ]
    if not content.overrides:
        story.append(
            Paragraph(
                "None. Every figure in this report uses the default value of each assumption.",
                styles["body"],
            )
        )
        return story
    rows = [
        [
            Paragraph(_text(o.assumption_id), styles["cell"]),
            _number(o.default_value),
            _number(o.value),
            Paragraph(_text(o.reason), styles["cell"]),
            Paragraph(_text(f"{o.set_by}, {o.set_on.isoformat()}"), styles["small"]),
        ]
        for o in content.overrides
    ]
    story.append(
        _table(
            ["Assumption", "Default", "This client", "Reason given", "Set by"],
            rows,
            [1.9 * inch, 0.7 * inch, 0.8 * inch, 2.2 * inch, 1.3 * inch],
            styles,
            right_from=99,
        )
    )
    return story


def render_pdf(content: ReportContent) -> bytes:
    """Lay a report out as a PDF and return the file's bytes."""
    styles = _styles()
    buffer = io.BytesIO()
    document = SimpleDocTemplate(
        buffer,
        pagesize=letter,
        leftMargin=0.8 * inch,
        rightMargin=0.8 * inch,
        topMargin=0.8 * inch,
        bottomMargin=0.9 * inch,
        title=f"{content.title} - {content.client_name}",
        author=content.branding.prepared_by,
        subject=f"{PRODUCT_NAME}: {content.cost_model}",
    )

    def footer(canvas, doc) -> None:
        canvas.saveState()
        canvas.setStrokeColor(RULE)
        canvas.line(0.8 * inch, 0.7 * inch, letter[0] - 0.8 * inch, 0.7 * inch)
        canvas.setFont("Helvetica", 7.5)
        canvas.setFillColor(MUTED)
        left = f"{content.branding.prepared_by}  |  {content.title}: {content.client_name}"
        canvas.drawString(0.8 * inch, 0.52 * inch, left[:110])
        canvas.drawRightString(letter[0] - 0.8 * inch, 0.52 * inch, f"Page {doc.page}")
        canvas.restoreState()

    story: list = []
    story += _cover(content, styles)
    story += _headline(content, styles)
    story += _components(content, styles)
    story += _range(content, styles)
    story += _matrix(content, styles)
    story += _patterns(content, styles)
    story += _interventions(content, styles)
    story += _departments(content, styles)
    story += _quality(content, styles)
    story += [
        Paragraph("How to read these figures", styles["h2"]),
        Paragraph(_text(content.method_note), styles["body"]),
    ]
    story += _appendix_assumptions(content, styles)
    story += _appendix_overrides(content, styles)
    document.build(story, onFirstPage=footer, onLaterPages=footer)
    return buffer.getvalue()
