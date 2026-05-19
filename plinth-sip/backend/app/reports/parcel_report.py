"""
Plinth Unit Deployment Feasibility Report Generator
Generates a professional multi-page PDF for a single parcel.

Structure:
  Page 1 — Cover: Address, Tier badge, Score, date
  Page 2 — Property at a Glance + Plinth Unit Fit
  Page 3 — Full Rule-by-Rule Breakdown
  Page 4 — Constraints, Outreach Intelligence, Next Steps
"""

from datetime import date
from io import BytesIO

from app.engine.use_code_labels import get_use_display, get_use_label

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    BaseDocTemplate,
    Frame,
    HRFlowable,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)
from reportlab.platypus import PageBreak
from reportlab.graphics.shapes import Drawing, Polygon, Rect, String

# ──────────────────────────────────────────────
# Brand palette
# ──────────────────────────────────────────────
INK        = colors.HexColor("#0f0f0f")
DARK       = colors.HexColor("#141414")
PANEL      = colors.HexColor("#1e1e1e")
BORDER     = colors.HexColor("#2a2a2a")
TEXT_DIM   = colors.HexColor("#888888")
TEXT_MED   = colors.HexColor("#cccccc")
TEXT_LIGHT = colors.HexColor("#f0f0f0")
ACCENT     = colors.HexColor("#5de0a0")   # Plinth green

TIER_COL = {
    1: colors.HexColor("#5de0a0"),  # green
    2: colors.HexColor("#f5c842"),  # yellow
    3: colors.HexColor("#f0894a"),  # orange
    4: colors.HexColor("#e05d5d"),  # red
}
TIER_LABEL = {1: "TIER 1 — IMMEDIATE OUTREACH", 2: "TIER 2 — REVIEW",
              3: "TIER 3 — CONDITIONAL", 4: "TIER 4 — LOW PRIORITY"}

RESULT_COL = {
    "pass":        colors.HexColor("#5de0a0"),
    "conditional": colors.HexColor("#f5c842"),
    "fail":        colors.HexColor("#e05d5d"),
    "unknown":     colors.HexColor("#555555"),
}
RESULT_SYMBOL = {"pass": "PASS", "conditional": "COND", "fail": "FAIL", "unknown": "N/A"}

RULE_LABELS = {
    "min_lot_size":        "Minimum Lot Size",
    "adu_max_size":        "ADU Max Size (Template Fit)",
    "lot_coverage":        "Lot Coverage",
    "buildable_envelope":  "Buildable Envelope",
    "use_allowed":         "Use Type Permitted",
    "adu_permitted":       "ADU Permitted in District",
    "overlay_constraints": "Overlay Constraints",
    "access_likely":       "Rear-Yard Access",
    "slope_buildability":  "Terrain / Slope",
    "electrical_service":  "Electrical Service Capacity",
    "sewer_available":     "Sewer Available",
    "septic_capacity":     "Septic Capacity (SSURGO)",
    "delivery_access":     "Delivery Access",
    "existing_structures": "Existing Structures",
}

CAT_LABELS = {
    "zoning_compatibility":    "Zoning Compatibility",
    "dimensional_fit":         "Dimensional Fit",
    "siting_likelihood":       "Siting Likelihood",
    "septic_confidence":       "Septic / Infrastructure",
    "deployment_ease":         "Deployment Ease",
    "outreach_attractiveness": "Outreach Priority",
}


# ──────────────────────────────────────────────
# Styles
# ──────────────────────────────────────────────

def _styles():
    base = getSampleStyleSheet()

    def s(name, **kw):
        return ParagraphStyle(name, **{"fontName": "Helvetica", **kw})

    return {
        "cover_address": s("ca", fontSize=22, textColor=TEXT_LIGHT,
                           leading=28, spaceAfter=8),
        "cover_muni":    s("cm", fontSize=13, textColor=TEXT_DIM, spaceAfter=24),
        "cover_score":   s("cs", fontSize=60, textColor=TEXT_LIGHT,
                           leading=64, alignment=1),
        "cover_score_label": s("csl", fontSize=11, textColor=TEXT_DIM,
                               alignment=1, spaceAfter=6),
        "cover_disclaimer": s("cd", fontSize=7.5, textColor=TEXT_DIM,
                              leading=11),

        "section_head":  s("sh", fontSize=10, textColor=ACCENT,
                           fontName="Helvetica-Bold", spaceBefore=16, spaceAfter=4,
                           textTransform="uppercase"),
        "label":         s("lb", fontSize=8, textColor=TEXT_DIM, spaceAfter=1),
        "value":         s("vl", fontSize=12, textColor=TEXT_LIGHT,
                           fontName="Helvetica-Bold", spaceAfter=0),
        "body":          s("bd", fontSize=9.5, textColor=TEXT_MED, leading=14),
        "rule_explain":  s("re", fontSize=8.5, textColor=TEXT_DIM, leading=12),
        "small":         s("sm", fontSize=8, textColor=TEXT_DIM, leading=11),
        "owner_name":    s("on", fontSize=14, textColor=TEXT_LIGHT,
                           fontName="Helvetica-Bold", spaceAfter=2),
        "next_step":     s("ns", fontSize=10, textColor=TEXT_LIGHT, leading=15),
    }


# ──────────────────────────────────────────────
# Page templates (dark background on every page)
# ──────────────────────────────────────────────

MARGIN = 0.6 * inch

def _on_page(canvas, doc):
    """Draw dark background + running header on every page."""
    W, H = letter
    canvas.saveState()
    canvas.setFillColor(INK)
    canvas.rect(0, 0, W, H, fill=1, stroke=0)

    # Running header (pages 2+)
    if doc.page > 1:
        canvas.setFillColor(DARK)
        canvas.rect(0, H - 36, W, 36, fill=1, stroke=0)
        canvas.setFillColor(ACCENT)
        canvas.setFont("Helvetica-Bold", 9)
        canvas.drawString(MARGIN, H - 22, "PLINTH")
        canvas.setFillColor(TEXT_DIM)
        canvas.setFont("Helvetica", 9)
        canvas.drawRightString(W - MARGIN, H - 22, "UNIT DEPLOYMENT FEASIBILITY REPORT")

        # Footer
        canvas.setFillColor(BORDER)
        canvas.rect(0, 0, W, 28, fill=1, stroke=0)
        canvas.setFillColor(TEXT_DIM)
        canvas.setFont("Helvetica", 8)
        canvas.drawString(MARGIN, 9, "plinth.co  ·  Preliminary assessment only — not legal advice")
        canvas.drawRightString(W - MARGIN, 9, f"Page {doc.page}")

    canvas.restoreState()


# ──────────────────────────────────────────────
# Helper: key-value stat card (2-column grid)
# ──────────────────────────────────────────────

def _stat_cards(items: list[tuple[str, str]], st: dict, col_count: int = 2) -> Table:
    """
    items: list of (label, value) pairs
    Renders as a col_count-wide grid of stat cards.
    """
    cell_style = [
        ("BACKGROUND", (0, 0), (-1, -1), PANEL),
        ("BOX", (0, 0), (-1, -1), 0.5, BORDER),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
        ("LEFTPADDING", (0, 0), (-1, -1), 10),
        ("RIGHTPADDING", (0, 0), (-1, -1), 10),
        ("ROWBACKGROUNDS", (0, 0), (-1, -1), [PANEL]),
    ]
    rows = []
    for i in range(0, len(items), col_count):
        chunk = items[i : i + col_count]
        # Pad to col_count
        while len(chunk) < col_count:
            chunk.append(("", ""))
        cell_row = []
        for label, value in chunk:
            cell = [
                Paragraph(label, st["label"]),
                Paragraph(str(value) if value else "—", st["value"]),
            ]
            cell_row.append(cell)
        rows.append(cell_row)

    col_w = (letter[0] - 2 * MARGIN) / col_count
    t = Table(rows, colWidths=[col_w] * col_count)
    t.setStyle(TableStyle(cell_style))
    return t


# ──────────────────────────────────────────────
# Score bar
# ──────────────────────────────────────────────

def _score_bar_table(score: float, tier: int) -> Table:
    """Horizontal score bar showing 0-100 with fill."""
    W = letter[0] - 2 * MARGIN
    bar_w = W - 120  # leave room for number label

    score_pct = min(max(score / 100.0, 0.0), 1.0)
    tier_color = TIER_COL.get(tier, TEXT_DIM)

    # Two-column: [score label | bar]
    score_label = Paragraph(
        f"<b>{score:.1f}</b> / 100",
        ParagraphStyle("sl", fontName="Helvetica-Bold", fontSize=20, textColor=tier_color),
    )

    # Build bar as nested Table
    filled_w = bar_w * score_pct
    empty_w  = bar_w - filled_w

    inner_cells = [[""]]
    inner = Table(inner_cells, colWidths=[bar_w], rowHeights=[14])
    inner.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), PANEL),
        ("BOX", (0, 0), (-1, -1), 0.3, BORDER),
    ]))

    # Filled bar as separate table beside the empty
    if filled_w > 0 and empty_w > 0:
        bar_inner = Table([[""]], colWidths=[bar_w], rowHeights=[14])
        bar_inner.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (0, 0), tier_color),
        ]))
    else:
        bar_inner = inner

    bar_container = Table(
        [[bar_inner]],
        colWidths=[bar_w],
    )

    outer = Table(
        [[score_label, bar_container]],
        colWidths=[100, bar_w],
    )
    outer.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
    ]))
    return outer


# ──────────────────────────────────────────────
# Site plan drawing — parcel outline + setbacks + residence footprint
# ──────────────────────────────────────────────

FT_PER_M = 3.280839895
PRIMARY_SETBACK_FT = 10.0
AUX_SETBACK_FT = 5.0  # additional inset beyond primary

PARCEL_LINE_COLOR     = TEXT_LIGHT
PRIMARY_SETBACK_COLOR = ACCENT
AUX_SETBACK_COLOR     = colors.HexColor("#f5c842")
RESIDENCE_FILL        = colors.HexColor("#3a3a3a")
RESIDENCE_STROKE      = TEXT_MED


def _add_polygon_to_drawing(drawing, geom, to_pts, *, stroke_color, stroke_width,
                            fill_color=None, dash_array=None):
    """Render a Shapely (Multi)Polygon into the Drawing using the given coord transform."""
    if geom is None or geom.is_empty:
        return
    if geom.geom_type == "MultiPolygon":
        for g in geom.geoms:
            _add_polygon_to_drawing(
                drawing, g, to_pts,
                stroke_color=stroke_color, stroke_width=stroke_width,
                fill_color=fill_color, dash_array=dash_array,
            )
        return
    if geom.geom_type != "Polygon":
        return
    flat = []
    for x, y in geom.exterior.coords:
        px, py = to_pts(x, y)
        flat.extend([px, py])
    poly = Polygon(
        points=flat,
        strokeColor=stroke_color,
        strokeWidth=stroke_width,
        fillColor=fill_color,
    )
    if dash_array:
        poly.strokeDashArray = dash_array
    drawing.add(poly)


def _site_plan_drawing(parcel: dict, width_pt: float, height_pt: float):
    """
    Render the parcel outline with:
      • solid white parcel boundary
      • primary residence footprint (sized from existing_building_footprint_area)
      • 10 ft inset solid line — primary structure setback
      • additional 5 ft inset dashed line — auxiliary structure setback (15 ft total)

    Returns a reportlab Drawing or None if geometry is unavailable / unusable.
    """
    geojson = parcel.get("geometry_geojson") or parcel.get("geometry")
    if not geojson:
        return None

    try:
        from shapely.geometry import shape as shp_shape, box as shp_box
        from shapely.ops import transform as shp_transform
        import pyproj
    except Exception:
        return None

    try:
        geom = shp_shape(geojson)
    except Exception:
        return None
    if geom is None or geom.is_empty:
        return None

    # Local azimuthal equidistant projection — accurate at parcel scale anywhere.
    centroid = geom.centroid
    proj_str = (
        f"+proj=aeqd +lat_0={centroid.y} +lon_0={centroid.x} "
        f"+datum=WGS84 +units=m +no_defs"
    )
    try:
        transformer = pyproj.Transformer.from_crs("EPSG:4326", proj_str, always_xy=True)
        projected = shp_transform(transformer.transform, geom)
    except Exception:
        return None

    if projected.is_empty:
        return None

    primary_inset_m = PRIMARY_SETBACK_FT / FT_PER_M
    aux_inset_m = (PRIMARY_SETBACK_FT + AUX_SETBACK_FT) / FT_PER_M
    primary_setback = projected.buffer(-primary_inset_m)
    aux_setback = projected.buffer(-aux_inset_m)

    # Primary residence rectangle: scale from known footprint area, centered inside
    # the primary setback (so it lands within plausibly-buildable area).
    bld_area_sqft = parcel.get("existing_building_footprint_area")
    residence = None
    if bld_area_sqft:
        try:
            bld_area_m2 = float(bld_area_sqft) * 0.092903
        except (TypeError, ValueError):
            bld_area_m2 = 0.0
        if bld_area_m2 > 0:
            short = (bld_area_m2 / 1.5) ** 0.5
            long_ = short * 1.5
            anchor = (
                primary_setback.representative_point()
                if not primary_setback.is_empty
                else projected.representative_point()
            )
            residence = shp_box(
                anchor.x - long_ / 2, anchor.y - short / 2,
                anchor.x + long_ / 2, anchor.y + short / 2,
            )

    # Build a uniform scale that fits the parcel into the drawing area.
    bounds_geoms = [projected]
    if not primary_setback.is_empty:
        bounds_geoms.append(primary_setback)
    if not aux_setback.is_empty:
        bounds_geoms.append(aux_setback)
    if residence is not None:
        bounds_geoms.append(residence)

    minx = min(g.bounds[0] for g in bounds_geoms)
    miny = min(g.bounds[1] for g in bounds_geoms)
    maxx = max(g.bounds[2] for g in bounds_geoms)
    maxy = max(g.bounds[3] for g in bounds_geoms)
    src_w = max(maxx - minx, 0.001)
    src_h = max(maxy - miny, 0.001)

    legend_h = 18.0
    pad = 8.0
    plan_h = height_pt - legend_h - 2 * pad
    plan_w = width_pt - 2 * pad
    scale = min(plan_w / src_w, plan_h / src_h)

    used_w = src_w * scale
    used_h = src_h * scale
    offset_x = pad + (plan_w - used_w) / 2
    offset_y = pad + legend_h + (plan_h - used_h) / 2

    def to_pts(x, y):
        return (offset_x + (x - minx) * scale, offset_y + (y - miny) * scale)

    d = Drawing(width_pt, height_pt)
    d.add(Rect(0, 0, width_pt, height_pt,
               fillColor=PANEL, strokeColor=BORDER, strokeWidth=0.5))

    # Auxiliary setback (15 ft total) — dashed
    _add_polygon_to_drawing(
        d, aux_setback, to_pts,
        stroke_color=AUX_SETBACK_COLOR, stroke_width=0.7,
        fill_color=None, dash_array=[3, 2],
    )
    # Primary setback (10 ft) — solid
    _add_polygon_to_drawing(
        d, primary_setback, to_pts,
        stroke_color=PRIMARY_SETBACK_COLOR, stroke_width=0.9,
        fill_color=None,
    )
    # Parcel boundary — solid white line work
    _add_polygon_to_drawing(
        d, projected, to_pts,
        stroke_color=PARCEL_LINE_COLOR, stroke_width=1.3,
        fill_color=None,
    )
    # Primary residence footprint
    if residence is not None:
        _add_polygon_to_drawing(
            d, residence, to_pts,
            stroke_color=RESIDENCE_STROKE, stroke_width=0.8,
            fill_color=RESIDENCE_FILL,
        )

    # Legend row along the bottom
    legend_y = pad
    swatch_w = 14.0
    swatch_h = 4.0
    text_dy = 1.5
    cursor = pad + 4.0

    def _swatch(color, label, dashed=False, filled=False):
        nonlocal cursor
        if filled:
            d.add(Rect(cursor, legend_y, swatch_w, swatch_h + 1,
                       fillColor=RESIDENCE_FILL, strokeColor=RESIDENCE_STROKE,
                       strokeWidth=0.6))
        else:
            line = Rect(cursor, legend_y + swatch_h / 2, swatch_w, 0.1,
                        strokeColor=color, strokeWidth=1.0, fillColor=None)
            if dashed:
                line.strokeDashArray = [2, 1.5]
            d.add(line)
        cursor += swatch_w + 4
        s = String(cursor, legend_y + text_dy, label,
                   fontName="Helvetica", fontSize=6.5, fillColor=TEXT_DIM)
        d.add(s)
        cursor += len(label) * 3.4 + 10

    _swatch(PARCEL_LINE_COLOR, "Parcel boundary")
    _swatch(PRIMARY_SETBACK_COLOR, "10 ft setback (primary)")
    _swatch(AUX_SETBACK_COLOR, "+5 ft (auxiliary)", dashed=True)
    if residence is not None:
        _swatch(RESIDENCE_STROKE, "Primary residence", filled=True)

    return d


# ──────────────────────────────────────────────
# Public entry point
# ──────────────────────────────────────────────

def generate_parcel_report(parcel: dict) -> bytes:
    """
    Generate a Plinth feasibility PDF for a single parcel dict.
    Returns raw PDF bytes.
    """
    buf = BytesIO()
    st  = _styles()
    W, H = letter

    doc = BaseDocTemplate(
        buf,
        pagesize=letter,
        leftMargin=MARGIN,
        rightMargin=MARGIN,
        topMargin=MARGIN + 0.2 * inch,
        bottomMargin=MARGIN,
        title="Plinth Unit Deployment Feasibility Report",
        author="Plinth SIP",
    )

    content_frame = Frame(
        MARGIN, 0.5 * inch,
        W - 2 * MARGIN,
        H - MARGIN - 0.7 * inch,
        id="body",
    )
    cover_frame = Frame(
        MARGIN, 0.5 * inch,
        W - 2 * MARGIN,
        H - 2 * MARGIN,
        id="cover",
    )

    doc.addPageTemplates([
        PageTemplate(id="cover_tpl", frames=[cover_frame]),
        PageTemplate(id="body_tpl",  frames=[content_frame], onPage=_on_page),
    ])

    story = []

    # ── Extract data ────────────────────────────────────────────────────
    address        = parcel.get("address") or "Address not available"
    parcel_id      = parcel.get("parcel_id") or ""
    municipality   = parcel.get("municipality_id", "").replace("_", " ").title()
    owner_name     = parcel.get("owner_name") or "Unknown Owner"
    raw_zoning     = parcel.get("zoning_code") or "—"
    zoning_code    = raw_zoning
    # Prefer explicit district label; fall back to use-code lookup; fall back to raw code
    zoning_label   = (
        parcel.get("zoning_district_label")
        or get_use_display(raw_zoning if raw_zoning != "—" else None)
        or raw_zoning
    )
    lot_sqft       = parcel.get("lot_area_sqft")
    bld_area       = parcel.get("existing_building_footprint_area")
    structure_ct   = parcel.get("existing_structure_count")
    raw_land_use   = parcel.get("land_use_type") or ""
    # Show human label for land use code if it looks like a numeric code
    land_use_label = get_use_label(raw_land_use)
    land_use       = (
        f"{raw_land_use} — {land_use_label}" if land_use_label and raw_land_use.isdigit()
        else land_use_label or raw_land_use.replace("_", " ").title() or "—"
    )
    score          = parcel.get("score") or 0.0
    tier           = parcel.get("tier") or 4
    confidence     = parcel.get("confidence") or 0.0
    rule_results   = parcel.get("rule_results") or []
    score_breakdown = parcel.get("score_breakdown") or {}
    blockers       = parcel.get("blockers") or []
    template_fits  = parcel.get("template_fits") or []
    year_built     = parcel.get("year_built")
    slope_stats    = parcel.get("slope_stats") or None
    soil_class     = parcel.get("soil_septic_class") or None
    overlay_hits   = parcel.get("overlay_hits") or []

    tier_color = TIER_COL.get(tier, TEXT_DIM)
    tier_label = TIER_LABEL.get(tier, f"TIER {tier}")

    lot_acres = f"{lot_sqft / 43560:.2f} ac" if lot_sqft else "—"
    lot_sqft_str = f"{lot_sqft:,.0f} sqft" if lot_sqft else "—"

    today = date.today().strftime("%B %d, %Y")

    # ────────────────────────────────────────────────────────────────────
    # PAGE 1 — COVER
    # ────────────────────────────────────────────────────────────────────

    # Site plan at the very top: parcel line work, primary residence footprint,
    # 10 ft primary setback (solid) and additional 5 ft auxiliary setback (dashed).
    plan_w = W - 2 * MARGIN
    plan_h = 2.4 * inch
    site_plan = _site_plan_drawing(parcel, plan_w, plan_h)
    if site_plan is not None:
        story.append(site_plan)
        story.append(Spacer(1, 0.25 * inch))
    else:
        story.append(Spacer(1, 0.4 * inch))

    # Wordmark
    story.append(Paragraph(
        '<font color="#5de0a0"><b>PLINTH</b></font>',
        ParagraphStyle("wm", fontName="Helvetica-Bold", fontSize=14, textColor=ACCENT,
                       letterSpacing=4, spaceAfter=6),
    ))
    story.append(Paragraph(
        "UNIT DEPLOYMENT FEASIBILITY REPORT",
        ParagraphStyle("sub", fontName="Helvetica", fontSize=8, textColor=TEXT_DIM,
                       letterSpacing=2, spaceAfter=14),
    ))

    # Tier badge (wide colored bar)
    story.append(Table(
        [[Paragraph(f"<b>{tier_label}</b>",
                    ParagraphStyle("tb", fontName="Helvetica-Bold", fontSize=11,
                                   textColor=colors.black if tier <= 2 else colors.white))]],
        colWidths=[W - 2 * MARGIN],
        rowHeights=[36],
        style=TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), tier_color),
            ("ALIGN", (0, 0), (-1, -1), "LEFT"),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("LEFTPADDING", (0, 0), (-1, -1), 14),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
            ("TOPPADDING", (0, 0), (-1, -1), 0),
        ]),
    ))

    story.append(Spacer(1, 12))

    # Score
    story.append(Paragraph(
        f'<font color="{tier_color.hexval()}">{score:.1f}</font>',
        ParagraphStyle("sc", fontName="Helvetica-Bold", fontSize=56, textColor=tier_color,
                       leading=58, spaceAfter=0),
    ))
    story.append(Paragraph(
        "FEASIBILITY SCORE  (0–100)",
        ParagraphStyle("scl", fontName="Helvetica", fontSize=9, textColor=TEXT_DIM,
                       spaceAfter=14),
    ))

    # Address block
    story.append(HRFlowable(width="100%", thickness=1, color=BORDER, spaceAfter=14))
    story.append(Paragraph(address, st["cover_address"]))
    story.append(Paragraph(municipality, st["cover_muni"]))

    story.append(Spacer(1, 0.2 * inch))

    # Meta row: parcel ID / date / confidence
    meta_items = [
        ("PARCEL ID", parcel_id or "—"),
        ("GENERATED", today),
        ("DATA CONFIDENCE", f"{confidence:.0%}"),
    ]
    story.append(_stat_cards(meta_items, st, col_count=3))

    story.append(Spacer(1, 0.2 * inch))
    story.append(Paragraph(
        "This report is a preliminary feasibility assessment based on publicly available GIS parcel data, "
        "municipal zoning configurations, and Plinth's rules engine. It is not a guarantee of permit approval "
        "or legal advice. Final eligibility is subject to review by local zoning authorities. "
        "Plinth assumes no liability for decisions made based on this report.",
        st["cover_disclaimer"],
    ))

    # Switch to body template
    story.append(PageBreak())
    story.append(_NextPageTemplate("body_tpl"))

    # ────────────────────────────────────────────────────────────────────
    # PAGE 2 — PROPERTY AT A GLANCE + PLINTH UNIT FIT
    # ────────────────────────────────────────────────────────────────────
    story.append(Spacer(1, 0.1 * inch))
    story.append(Paragraph("PROPERTY OVERVIEW", st["section_head"]))
    story.append(HRFlowable(width="100%", thickness=0.5, color=BORDER, spaceAfter=10))

    prop_items = [
        ("ADDRESS", address),
        ("PARCEL ID", parcel_id or "—"),
        ("OWNER", owner_name),
        ("MUNICIPALITY", municipality),
        ("ZONING DISTRICT", zoning_label),
        ("ZONING CODE", zoning_code),
        ("LOT AREA", f"{lot_sqft_str}  ({lot_acres})"),
        ("BUILDING AREA", f"{bld_area:,.0f} sqft" if bld_area else "—"),
        ("LAND USE TYPE", land_use),
        ("EXISTING STRUCTURES", str(structure_ct) if structure_ct is not None else "—"),
        ("YEAR BUILT", str(year_built) if year_built else "—"),
    ]
    story.append(_stat_cards(prop_items, st, col_count=2))

    story.append(Spacer(1, 0.15 * inch))
    story.append(Paragraph("PLINTH UNIT FIT ANALYSIS", st["section_head"]))
    story.append(HRFlowable(width="100%", thickness=0.5, color=BORDER, spaceAfter=10))

    # Score breakdown table
    breakdown_data = [["CATEGORY", "SCORE", "WEIGHT", "CONTRIBUTION"]]
    for cat_key, cat_data in score_breakdown.items():
        cat_score = cat_data.get("score", 0)
        cat_weight = cat_data.get("weight", 0)
        contrib = cat_score * cat_weight
        label = CAT_LABELS.get(cat_key, cat_key.replace("_", " ").title())
        breakdown_data.append([
            label,
            f"{cat_score:.1f}",
            f"{cat_weight:.0%}",
            f"{contrib:.1f}",
        ])
    breakdown_data.append(["COMPOSITE SCORE", f"{score:.1f}", "", ""])

    col_w = (W - 2 * MARGIN) / 4
    bdown_style = TableStyle([
        ("BACKGROUND",   (0, 0), (-1, 0),   PANEL),
        ("BACKGROUND",   (0, 1), (-1, -2),  INK),
        ("BACKGROUND",   (0, -1), (-1, -1), DARK),
        ("TEXTCOLOR",    (0, 0), (-1, 0),   TEXT_DIM),
        ("TEXTCOLOR",    (0, 1), (-1, -2),  TEXT_MED),
        ("TEXTCOLOR",    (0, -1), (-1, -1), tier_color),
        ("FONTNAME",     (0, 0), (-1, 0),   "Helvetica"),
        ("FONTNAME",     (0, -1), (-1, -1), "Helvetica-Bold"),
        ("FONTSIZE",     (0, 0), (-1, -1),  9),
        ("ALIGN",        (1, 0), (-1, -1),  "CENTER"),
        ("BOX",          (0, 0), (-1, -1),  0.5, BORDER),
        ("LINEBELOW",    (0, 0), (-1, 0),   0.5, BORDER),
        ("LINEABOVE",    (0, -1), (-1, -1), 0.5, BORDER),
        ("TOPPADDING",   (0, 0), (-1, -1),  5),
        ("BOTTOMPADDING",(0, 0), (-1, -1),  5),
        ("LEFTPADDING",  (0, 0), (-1, -1),  8),
        ("RIGHTPADDING", (0, 0), (-1, -1),  8),
    ])
    bdown_t = Table(breakdown_data, colWidths=[col_w * 2, col_w * 0.7, col_w * 0.6, col_w * 0.7])
    bdown_t.setStyle(bdown_style)
    story.append(bdown_t)

    # Template fits
    if template_fits:
        story.append(Spacer(1, 8))
        fit_data = [["PLINTH TEMPLATE", "FOOTPRINT", "STATUS", "NOTES"]]
        for tf in template_fits:
            status = tf.get("fit_status", "unknown")
            status_col = RESULT_COL.get("pass" if status == "fits" else "fail" if status == "does_not_fit" else "unknown")
            fit_data.append([
                tf.get("template_name", tf.get("template_id", "—")),
                f"{tf.get('footprint_area_sqft', 0):,.0f} sqft",
                Paragraph(f'<font color="{status_col.hexval()}"><b>{status.upper().replace("_"," ")}</b></font>',
                          ParagraphStyle("fs", fontSize=8, fontName="Helvetica-Bold")),
                tf.get("notes", "—"),
            ])
        fit_t = Table(fit_data, colWidths=[col_w * 1.3, col_w * 0.7, col_w * 0.7, col_w * 1.3])
        fit_t.setStyle(TableStyle([
            ("BACKGROUND",   (0, 0), (-1, 0), PANEL),
            ("TEXTCOLOR",    (0, 0), (-1, 0), TEXT_DIM),
            ("TEXTCOLOR",    (0, 1), (-1, -1), TEXT_MED),
            ("FONTSIZE",     (0, 0), (-1, -1), 8.5),
            ("BOX",          (0, 0), (-1, -1), 0.5, BORDER),
            ("LINEBELOW",    (0, 0), (-1, 0), 0.5, BORDER),
            ("TOPPADDING",   (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING",(0, 0), (-1, -1), 5),
            ("LEFTPADDING",  (0, 0), (-1, -1), 8),
            ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ]))
        story.append(fit_t)

    story.append(PageBreak())

    # ────────────────────────────────────────────────────────────────────
    # PAGE 3 — RULE-BY-RULE BREAKDOWN
    # ────────────────────────────────────────────────────────────────────
    story.append(Spacer(1, 0.1 * inch))
    story.append(Paragraph("RULE-BY-RULE FEASIBILITY ANALYSIS", st["section_head"]))
    story.append(HRFlowable(width="100%", thickness=0.5, color=BORDER, spaceAfter=10))

    story.append(Paragraph(
        "Each rule evaluates a specific dimension of deployment feasibility. "
        "PASS = confirmed feasible. COND = feasible with conditions. FAIL = constraint identified. N/A = insufficient data.",
        st["body"],
    ))
    story.append(Spacer(1, 8))

    # Rules table
    rule_data = [["RULE", "CATEGORY", "RESULT", "CONFIDENCE", "EXPLANATION"]]
    for rr in rule_results:
        rid = rr.get("rule_id", "")
        result = rr.get("result", "unknown")
        rc = RESULT_COL.get(result, TEXT_DIM)
        sym = RESULT_SYMBOL.get(result, "N/A")
        cat = (rr.get("rule_category") or "").replace("_", " ").title()
        conf = rr.get("confidence")
        explain = rr.get("explanation", "—")
        if len(explain) > 180:
            explain = explain[:177] + "..."

        rule_data.append([
            Paragraph(f"<b>{RULE_LABELS.get(rid, rid.replace('_',' ').title())}</b>",
                      ParagraphStyle("rn", fontSize=8.5, fontName="Helvetica-Bold",
                                     textColor=TEXT_LIGHT)),
            Paragraph(cat, ParagraphStyle("rc", fontSize=8, textColor=TEXT_DIM)),
            Paragraph(f'<font color="{rc.hexval()}"><b>{sym}</b></font>',
                      ParagraphStyle("rr", fontSize=9, fontName="Helvetica-Bold", alignment=1)),
            Paragraph(f"{conf:.0%}" if conf is not None else "—",
                      ParagraphStyle("rcf", fontSize=8.5, textColor=TEXT_MED, alignment=1)),
            Paragraph(explain, ParagraphStyle("re", fontSize=8, textColor=TEXT_DIM, leading=11)),
        ])

    # Column widths: rule name, category, result, confidence, explanation
    cw = W - 2 * MARGIN
    rule_t = Table(
        rule_data,
        colWidths=[cw * 0.20, cw * 0.12, cw * 0.08, cw * 0.08, cw * 0.52],
        repeatRows=1,
    )
    rule_style = [
        ("BACKGROUND",    (0, 0), (-1, 0),  PANEL),
        ("TEXTCOLOR",     (0, 0), (-1, 0),  TEXT_DIM),
        ("FONTSIZE",      (0, 0), (-1, 0),  8),
        ("BOX",           (0, 0), (-1, -1), 0.5, BORDER),
        ("LINEBELOW",     (0, 0), (-1, 0),  0.5, BORDER),
        ("INNERGRID",     (0, 1), (-1, -1), 0.3, BORDER),
        ("TOPPADDING",    (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("LEFTPADDING",   (0, 0), (-1, -1), 8),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 8),
        ("VALIGN",        (0, 0), (-1, -1), "TOP"),
        ("ALIGN",         (2, 0), (3, -1),  "CENTER"),
    ]
    # Alternate row shading
    for i in range(1, len(rule_data)):
        if i % 2 == 0:
            rule_style.append(("BACKGROUND", (0, i), (-1, i), DARK))
        else:
            rule_style.append(("BACKGROUND", (0, i), (-1, i), INK))

    rule_t.setStyle(TableStyle(rule_style))
    story.append(rule_t)

    # Footnote: any rule whose explanation ends with "*" used a typical-residential
    # aggregate default because the local zoning bylaw value wasn't in the config.
    if any((rr.get("explanation") or "").rstrip().endswith("*") for rr in rule_results):
        story.append(Spacer(1, 6))
        story.append(Paragraph(
            "<b>*</b> Value not found in the local zoning ordinance / municipality config. "
            "A typical residential aggregate was substituted so the rule could be evaluated. "
            "Verify against the current bylaw before relying on this result.",
            ParagraphStyle("fn", fontSize=8, textColor=TEXT_DIM, leading=11,
                           fontName="Helvetica-Oblique"),
        ))

    story.append(PageBreak())

    # ────────────────────────────────────────────────────────────────────
    # PAGE 4 — SITE CONDITIONS + CONSTRAINTS + OUTREACH INTELLIGENCE
    # ────────────────────────────────────────────────────────────────────
    story.append(Spacer(1, 0.1 * inch))
    story.append(Paragraph("SITE CONDITIONS — LIVE GIS DATA", st["section_head"]))
    story.append(HRFlowable(width="100%", thickness=0.5, color=BORDER, spaceAfter=10))

    # Slope + Soil + Year-Built summary cards
    site_items: list[tuple[str, str]] = []

    if slope_stats and slope_stats.get("count", 0) > 0:
        mean_d = slope_stats.get("mean", 0.0)
        max_d = slope_stats.get("max", 0.0)
        import math
        mean_pct = math.tan(math.radians(mean_d)) * 100.0
        site_items.append(("MEAN SLOPE", f"{mean_d:.1f}°  ({mean_pct:.0f}% rise)"))
        site_items.append(("MAX SLOPE", f"{max_d:.1f}°"))
        site_items.append(("SLOPE SOURCE", str(slope_stats.get("source", "DEM"))))
    else:
        site_items.append(("SLOPE", "Not available (no LiDAR coverage)"))
        site_items.append(("", ""))
        site_items.append(("", ""))

    if soil_class:
        soil_detail = parcel.get("soil_septic_detail") or {}
        worst = soil_detail.get("worst") or {}
        comp = worst.get("dominant_component") or "?"
        muname = (worst.get("muname") or "")[:60]
        site_items.append(("SOIL SEPTIC CLASS (SSURGO)", soil_class))
        site_items.append(("DOMINANT SOIL COMPONENT", f"{comp} — {muname}"))
    else:
        site_items.append(("SOIL SEPTIC CLASS (SSURGO)", "Not available"))
        site_items.append(("", ""))

    if year_built:
        if year_built >= 1986:
            elec = "Likely 200A — no upgrade expected"
        elif year_built >= 1960:
            elec = "Likely 100A — panel upgrade ~$1.5–3.5k"
        else:
            elec = "Likely ≤100A — service upgrade $5–25k"
        site_items.append(("YEAR BUILT", str(year_built)))
        site_items.append(("ELECTRICAL ESTIMATE", elec))

    story.append(_stat_cards(site_items, st, col_count=2))

    # Overlay-hits table
    if overlay_hits:
        story.append(Spacer(1, 12))
        story.append(Paragraph("ENVIRONMENTAL & REGULATORY OVERLAYS", st["section_head"]))
        story.append(HRFlowable(width="100%", thickness=0.5, color=BORDER, spaceAfter=8))

        ov_data = [["LAYER", "LEVEL", "DETAIL"]]
        level_color = {
            "hard_block": colors.HexColor("#e05d5d"),
            "review": colors.HexColor("#f5c842"),
            "review_required": colors.HexColor("#f5c842"),
            "soft_constraint": colors.HexColor("#888888"),
        }
        for h in overlay_hits:
            label = str(h.get("label") or h.get("layer_id") or "?")
            level_raw = (h.get("constraint_level") or "review").lower()
            color = level_color.get(level_raw, TEXT_DIM)
            level_disp = level_raw.replace("_", " ").upper()
            attrs = h.get("attributes") or {}
            attr_str = ", ".join(f"{k}={v}" for k, v in list(attrs.items())[:3] if v not in (None, ""))
            ov_data.append([
                Paragraph(f"<b>{label}</b>", ParagraphStyle("ovl", fontSize=8.5, fontName="Helvetica-Bold", textColor=TEXT_LIGHT)),
                Paragraph(f'<font color="{color.hexval()}"><b>{level_disp}</b></font>',
                          ParagraphStyle("ovlv", fontSize=8.5, fontName="Helvetica-Bold")),
                Paragraph(attr_str or "—", ParagraphStyle("ova", fontSize=8, textColor=TEXT_DIM, leading=11)),
            ])
        cw_ov = W - 2 * MARGIN
        ov_t = Table(ov_data, colWidths=[cw_ov * 0.40, cw_ov * 0.18, cw_ov * 0.42])
        ov_t.setStyle(TableStyle([
            ("BACKGROUND",    (0, 0), (-1, 0),  PANEL),
            ("TEXTCOLOR",     (0, 0), (-1, 0),  TEXT_DIM),
            ("FONTSIZE",      (0, 0), (-1, 0),  8),
            ("BOX",           (0, 0), (-1, -1), 0.5, BORDER),
            ("LINEBELOW",     (0, 0), (-1, 0),  0.5, BORDER),
            ("INNERGRID",     (0, 1), (-1, -1), 0.3, BORDER),
            ("TOPPADDING",    (0, 0), (-1, -1), 6),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ("LEFTPADDING",   (0, 0), (-1, -1), 8),
            ("RIGHTPADDING",  (0, 0), (-1, -1), 8),
            ("VALIGN",        (0, 0), (-1, -1), "TOP"),
        ]))
        story.append(ov_t)

    story.append(Spacer(1, 14))
    story.append(Paragraph("CONSTRAINT SUMMARY", st["section_head"]))
    story.append(HRFlowable(width="100%", thickness=0.5, color=BORDER, spaceAfter=10))

    if blockers:
        for b in blockers:
            rid = b.get("rule_id", "")
            exp = b.get("explanation", "")
            story.append(Table(
                [[Paragraph(
                    f'<font color="#e05d5d"><b>⬛ HARD BLOCK — {RULE_LABELS.get(rid, rid)}</b></font>',
                    ParagraphStyle("hb", fontSize=9, fontName="Helvetica-Bold")),
                  Paragraph(exp, ParagraphStyle("hbe", fontSize=8.5, textColor=TEXT_MED,
                                                leading=12))
                ]],
                colWidths=[(W - 2 * MARGIN) * 0.35, (W - 2 * MARGIN) * 0.65],
                style=TableStyle([
                    ("BACKGROUND",    (0, 0), (-1, -1), colors.HexColor("#1e0a0a")),
                    ("BOX",           (0, 0), (-1, -1), 1, colors.HexColor("#e05d5d")),
                    ("TOPPADDING",    (0, 0), (-1, -1), 8),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
                    ("LEFTPADDING",   (0, 0), (-1, -1), 10),
                    ("RIGHTPADDING",  (0, 0), (-1, -1), 10),
                    ("VALIGN",        (0, 0), (-1, -1), "TOP"),
                ]),
            ))
            story.append(Spacer(1, 6))
    else:
        story.append(Table(
            [[Paragraph(
                '<font color="#5de0a0"><b>✓ No hard blocks identified.</b></font>  '
                'This parcel passed all regulatory constraint checks.',
                ParagraphStyle("nb", fontSize=9))
            ]],
            colWidths=[W - 2 * MARGIN],
            style=TableStyle([
                ("BACKGROUND",    (0, 0), (-1, -1), colors.HexColor("#0a1e12")),
                ("BOX",           (0, 0), (-1, -1), 1, ACCENT),
                ("TOPPADDING",    (0, 0), (-1, -1), 10),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
                ("LEFTPADDING",   (0, 0), (-1, -1), 12),
                ("RIGHTPADDING",  (0, 0), (-1, -1), 12),
            ]),
        ))
        story.append(Spacer(1, 8))

    # Conditional rules to resolve
    conditional_rules = [rr for rr in rule_results if rr.get("result") == "conditional"]
    fail_rules = [rr for rr in rule_results if rr.get("result") == "fail"
                  and rr.get("rule_id") not in [b.get("rule_id") for b in blockers]]

    if conditional_rules or fail_rules:
        story.append(Spacer(1, 6))
        story.append(Paragraph("ITEMS REQUIRING VERIFICATION", st["section_head"]))
        story.append(HRFlowable(width="100%", thickness=0.5, color=BORDER, spaceAfter=8))
        for rr in conditional_rules + fail_rules:
            rid = rr.get("rule_id", "")
            result = rr.get("result", "")
            rc = RESULT_COL.get(result, TEXT_DIM)
            exp = rr.get("explanation", "")
            story.append(Table(
                [[Paragraph(
                    f'<font color="{rc.hexval()}"><b>{RULE_LABELS.get(rid, rid)}</b></font>',
                    ParagraphStyle("vr", fontSize=9, fontName="Helvetica-Bold")),
                  Paragraph(exp, ParagraphStyle("vre", fontSize=8.5, textColor=TEXT_DIM, leading=12))
                ]],
                colWidths=[(W - 2 * MARGIN) * 0.28, (W - 2 * MARGIN) * 0.72],
                style=TableStyle([
                    ("BACKGROUND",    (0, 0), (-1, -1), DARK),
                    ("BOX",           (0, 0), (-1, -1), 0.5, BORDER),
                    ("TOPPADDING",    (0, 0), (-1, -1), 7),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
                    ("LEFTPADDING",   (0, 0), (-1, -1), 10),
                    ("RIGHTPADDING",  (0, 0), (-1, -1), 10),
                    ("VALIGN",        (0, 0), (-1, -1), "TOP"),
                ]),
            ))
            story.append(Spacer(1, 4))

    # Outreach block
    story.append(Spacer(1, 10))
    story.append(Paragraph("OUTREACH INTELLIGENCE", st["section_head"]))
    story.append(HRFlowable(width="100%", thickness=0.5, color=BORDER, spaceAfter=10))

    outreach_items = [
        ("PROPERTY OWNER", owner_name),
        ("PARCEL ID", parcel_id or "—"),
        ("ADDRESS", address),
        ("FEASIBILITY SCORE", f"{score:.1f} / 100"),
        ("TIER", f"Tier {tier} — {['', 'Immediate Outreach', 'Manual Review', 'Conditional Hold', 'Low Priority'][min(tier, 4)]}"),
        ("DATA CONFIDENCE", f"{confidence:.0%}"),
    ]
    story.append(_stat_cards(outreach_items, st, col_count=2))

    story.append(Spacer(1, 10))

    # Recommended next step box
    if tier == 1:
        next_step = (
            "This parcel is a strong Plinth deployment candidate. "
            "Recommend direct outreach to the property owner. "
            "Verify setback compliance with a site visit before initiating contact."
        )
    elif tier == 2:
        next_step = (
            "This parcel shows deployment potential but requires manual review. "
            "Check flagged CONDITIONAL rules above before outreach. "
            "A site visit is recommended to confirm access and siting."
        )
    elif tier == 3:
        next_step = (
            "Deployment is conditional on resolving identified constraints. "
            "Review the constraint items above and assess whether a variance or "
            "alternative configuration could resolve them before outreach."
        )
    else:
        next_step = (
            "This parcel has significant constraints that currently block deployment. "
            "Hold for future re-evaluation if zoning changes or regulatory conditions improve."
        )

    story.append(Table(
        [[
            Paragraph('<b>RECOMMENDED NEXT STEP</b>',
                      ParagraphStyle("ns_label", fontSize=8, fontName="Helvetica-Bold",
                                     textColor=TEXT_DIM)),
            Paragraph(next_step, ParagraphStyle("ns_body", fontSize=9.5,
                                               textColor=TEXT_LIGHT, leading=14)),
        ]],
        colWidths=[(W - 2 * MARGIN) * 0.22, (W - 2 * MARGIN) * 0.78],
        style=TableStyle([
            ("BACKGROUND",    (0, 0), (-1, -1), PANEL),
            ("BOX",           (0, 0), (-1, -1), 1, tier_color),
            ("TOPPADDING",    (0, 0), (-1, -1), 12),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 12),
            ("LEFTPADDING",   (0, 0), (-1, -1), 12),
            ("RIGHTPADDING",  (0, 0), (-1, -1), 12),
            ("VALIGN",        (0, 0), (-1, -1), "TOP"),
        ]),
    ))

    story.append(Spacer(1, 12))
    story.append(Paragraph(
        "This report was generated by the Plinth Spatial Intelligence Platform. "
        "All zoning data is sourced from publicly available municipal GIS records "
        "and may not reflect recent ordinance amendments. Confidence scores reflect "
        "the quality of the underlying config data — analyst-verified configs score higher. "
        "Always verify critical constraints against the current local zoning ordinance "
        "before initiating owner outreach.",
        st["small"],
    ))

    # Build
    doc.build(story)
    return buf.getvalue()


# ──────────────────────────────────────────────
# Helper: switch page template mid-story
# ──────────────────────────────────────────────

from reportlab.platypus import ActionFlowable

class _NextPageTemplate(ActionFlowable):
    def __init__(self, pt_name):
        super().__init__(("nextPageTemplate", pt_name))
