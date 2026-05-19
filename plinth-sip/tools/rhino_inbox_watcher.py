# -*- coding: utf-8 -*-
"""Plinth -> Rhino inbox watcher.

Run this once inside Rhino (Tools > PythonScript > Run, or _RunPythonScript).
It polls a folder for *.dxf files written by the Plinth web UI's
"Send to Rhino" button and imports each one onto a PARCEL layer in the
active document. Processed files are moved to an `imported/` subfolder.

After each import we open Grasshopper with a "DC Cube Grid" component
(now produces a 3D volume, not voxel cubes): one number slider per parcel
edge drives an inward offset for that edge, the resulting polygon is
extruded by a single height slider, and the resulting Brep is the
buildable volume. Drag any edge slider and watch the volume reshape live.

Stop with: Plinth_StopInboxWatcher (run this script's stop helper, or
just close Rhino).

Tested on Rhino 7 / Rhino 8 (Windows). IronPython 2.7.
"""

import math
import os
import shutil
import time
import traceback

import Rhino
import scriptcontext as sc
import System
from Eto.Forms import UITimer

INBOX = os.path.join(os.path.expanduser("~"), "Plinth", "rhino_inbox")
PROCESSED = os.path.join(INBOX, "imported")
LAYER_NAME = "PARCEL"
POLL_SECONDS = 1.0

# Per-edge offset slider range (feet)
EDGE_OFFSET_MIN = 1.0
EDGE_OFFSET_DEFAULT = 15.0
EDGE_OFFSET_MAX = 30.0

# Height slider range (feet)
HEIGHT_MIN = 10.0
HEIGHT_DEFAULT = 57.0
HEIGHT_MAX = 80.0

# Lot-coverage cap. Default fallback when the DXF has no PLINTH_META block
# with a per-district override. After per-edge offsets, if the resulting
# footprint covers more than this fraction of the parcel area, the polygon is
# uniformly shrunk around its centroid so coverage hits the cap. Bounds the
# buildable mass regardless of parcel size.
MAX_LOT_COVERAGE = 0.60

# Story height assumption for the FAR cap. The volume is treated as
# floor(H / STORY_HEIGHT_FT) stories of identical footprint when computing
# gross floor area. 14 ft is a reasonable data-center default (raised floor
# + plenum + mech); residential is typically 9-10 ft. Tune per use case.
STORY_HEIGHT_FT = 14.0

# DXF parcel polylines often store every quantized vertex along an edge that
# is visually a single straight side (e.g., a 5-sided parcel arrives with 30+
# vertices). A vertex is merged into the line between its neighbors if EITHER:
#   (a) its turn angle is below SIMPLIFY_ANGLE_DEG, OR
#   (b) its perpendicular distance from that line is below SIMPLIFY_DEV_FT.
# (b) is the load-bearing rule for noisy survey-grade data where sides have
# small (1-5 deg) zigzag jogs that are real angles but visually a single side
# for ADU-feasibility purposes.
SIMPLIFY_ANGLE_DEG = 5.0
SIMPLIFY_DEV_FT = 3.0

# Track of in-flight files so we don't re-attempt while a write is finishing.
_seen_sizes = {}
_timer = None
# Re-entrancy guard. _scan can take several seconds (DXF parse + GH document
# build). If the timer fires again mid-scan and Rhino has yielded the UI
# thread for any reason, we'd start a second concurrent import. The guard
# turns the second tick into a no-op.
_scanning = False


def _ensure_dir(p):
    # IronPython 2 (Rhino's classic Python) predates os.makedirs(exist_ok=...).
    if not os.path.isdir(p):
        try:
            os.makedirs(p)
        except OSError:
            if not os.path.isdir(p):
                raise


def _ensure_dirs():
    _ensure_dir(INBOX)
    _ensure_dir(PROCESSED)


def _ensure_layer(name, color, set_current=False):
    # Always reach for Rhino.RhinoDoc.ActiveDoc, not scriptcontext.doc.
    # The watcher's UITimer can fire while Grasshopper is mid-solve, at
    # which point scriptcontext.doc is the GH document and `doc.Layers`
    # raises "This type of object is not supported in Grasshopper".
    doc = Rhino.RhinoDoc.ActiveDoc
    idx = doc.Layers.FindByFullPath(name, True)
    if idx < 0:
        layer = Rhino.DocObjects.Layer()
        layer.Name = name
        layer.Color = color
        idx = doc.Layers.Add(layer)
    if set_current:
        doc.Layers.SetCurrentLayerIndex(idx, True)
    return idx


def _ensure_parcel_layer():
    return _ensure_layer(
        LAYER_NAME,
        System.Drawing.Color.FromArgb(122, 237, 224),  # Plinth teal
        set_current=True,
    )


def _clear_layer_objects(layer_idx):
    """Delete all top-level objects on the given layer.

    Each new parcel import calls this on PARCEL so the doc only ever shows
    the current parcel. Without it, every parcel ever sent accumulates and
    the GhPython tries to compute a volume across all of them.
    """
    if layer_idx < 0:
        return 0
    doc = Rhino.RhinoDoc.ActiveDoc
    to_delete = []
    for obj in doc.Objects:
        if obj.Attributes.LayerIndex == layer_idx:
            to_delete.append(obj.Id)
    n = 0
    for guid in to_delete:
        if doc.Objects.Delete(guid, True):
            n += 1
    return n


def _parse_lwpolylines(path):
    """Yield lists of (x, y) for each LWPOLYLINE in the DXF.

    DXF group-code format is alternating lines: a code, then its value.
    We only need to recognize `0 LWPOLYLINE` (entity start), `10`/`20`
    (vertex X/Y), and `0 <anything>` (entity end). Everything else is
    ignored. This is sufficient for the parcel DXFs the web UI emits.
    """
    f = open(path, "r")
    try:
        raw = f.read()
    finally:
        f.close()
    parts = raw.replace("\r\n", "\n").replace("\r", "\n").split("\n")

    polylines = []
    in_entity = False
    current = []
    pending_x = None
    i = 0
    while i + 1 < len(parts):
        code = parts[i].strip()
        value = parts[i + 1]
        i += 2
        if code == "0":
            if in_entity and len(current) >= 2:
                polylines.append(current)
            current = []
            pending_x = None
            in_entity = (value.strip().upper() == "LWPOLYLINE")
            continue
        if not in_entity:
            continue
        if code == "10":
            try:
                pending_x = float(value)
            except ValueError:
                pending_x = None
        elif code == "20" and pending_x is not None:
            try:
                current.append((pending_x, float(value)))
            except ValueError:
                pass
            pending_x = None

    if in_entity and len(current) >= 2:
        polylines.append(current)
    return polylines


# Field-level coercions for the metadata block. Anything not listed here is
# kept as the raw string from the DXF. Numeric fields use float so the
# downstream GH script doesn't have to re-parse.
_META_FLOAT_KEYS = (
    "front_setback_ft", "side_setback_ft", "rear_setback_ft",
    "max_height_ft", "max_lot_coverage", "max_far",
    "min_lot_area_sqft", "lot_area_sqft",
)
_META_INT_KEYS = ("config_version",)


def _parse_plinth_metadata(path):
    """Extract the PLINTH_META_BEGIN/END key=value block from a DXF.

    The web UI emits this block at the top of every DXF using DXF group code
    999 (the "comment" code), so standard DXF readers ignore it. Returns a
    dict {field_name: value} or {} if no block is present. Numeric fields are
    coerced to float/int per `_META_FLOAT_KEYS` / `_META_INT_KEYS` so the GH
    script gets ready-to-use values; the rest stay as strings.
    """
    meta = {}
    try:
        f = open(path, "r")
    except IOError:
        return meta
    try:
        raw = f.read()
    finally:
        f.close()
    parts = raw.replace("\r\n", "\n").replace("\r", "\n").split("\n")

    in_block = False
    i = 0
    while i + 1 < len(parts):
        code = parts[i].strip()
        value = parts[i + 1]
        i += 2
        if code != "999":
            # Once we leave the run of leading 999 comments without ever
            # seeing PLINTH_META_BEGIN, give up -- the metadata block (if it
            # exists) is always at the file head.
            if not in_block and value.strip():
                # Tolerate non-999 lines interleaved (header etc.) by simply
                # continuing -- we only care about 999 lines.
                pass
            continue
        token = value.strip()
        if token == "PLINTH_META_BEGIN":
            in_block = True
            continue
        if token == "PLINTH_META_END":
            in_block = False
            break
        if not in_block:
            continue
        if "=" not in token:
            continue
        key, _, val = token.partition("=")
        key = key.strip()
        val = val.strip()
        if not key:
            continue
        if key in _META_FLOAT_KEYS:
            try:
                meta[key] = float(val)
            except ValueError:
                pass  # silently drop malformed numerics; consumers handle missing
        elif key in _META_INT_KEYS:
            try:
                meta[key] = int(val)
            except ValueError:
                pass
        else:
            meta[key] = val
    return meta


def _simplify_collinear_ring(ring, angle_deg=SIMPLIFY_ANGLE_DEG,
                             dev_ft=SIMPLIFY_DEV_FT):
    """Drop vertices that are effectively on the line between their neighbors.

    A vertex is dropped if the turn angle there is below `angle_deg` OR if
    its perpendicular distance from the chord prev->next is below `dev_ft`.
    The deviation rule handles noisy real-world parcel boundaries where small
    (1-5 deg) zigzag jogs add up to a "side" that's visually straight but
    isn't strictly collinear.

    Iterates passes until stable -- a chain of N collinear vertices needs
    multiple passes because each pass evaluates each vertex against its
    *current* neighbors. Cap at 50 iterations as a safety net.
    """
    if not ring or len(ring) < 4:
        return ring
    sin_tol = math.sin(math.radians(angle_deg))
    pts = list(ring)
    closed = (pts[0] == pts[-1])
    if closed:
        pts = pts[:-1]
    # Drop consecutive duplicates (zero-length edges from re-stored closing
    # points etc.) so the wrap-around neighbor checks below don't fold a
    # corner into a collinear "drop" because one of its neighbors is itself.
    if pts:
        deduped = [pts[0]]
        for p in pts[1:]:
            if p != deduped[-1]:
                deduped.append(p)
        while len(deduped) >= 2 and deduped[-1] == deduped[0]:
            deduped.pop()
        pts = deduped
    if len(pts) < 3:
        return ring

    for _ in range(50):
        n = len(pts)
        if n < 3:
            break
        keep = []
        for i in range(n):
            prev = pts[i - 1]  # negative index wraps for closed rings
            curr = pts[i]
            nxt = pts[(i + 1) % n]
            v1x = curr[0] - prev[0]
            v1y = curr[1] - prev[1]
            v2x = nxt[0] - curr[0]
            v2y = nxt[1] - curr[1]
            l1 = (v1x * v1x + v1y * v1y) ** 0.5
            l2 = (v2x * v2x + v2y * v2y) ** 0.5
            if l1 < 1e-9 or l2 < 1e-9:
                continue  # zero-length segment, drop the redundant vertex

            # Angle-based check: |sin(turn)| = |cross(v1,v2)| / (l1 * l2)
            cross_v = v1x * v2y - v1y * v2x
            if abs(cross_v) / (l1 * l2) < sin_tol:
                continue

            # Deviation-based check: perpendicular distance from curr to the
            # chord (prev -> next). cross(chord, prev->curr) / |chord|.
            chord_x = nxt[0] - prev[0]
            chord_y = nxt[1] - prev[1]
            chord_len = (chord_x * chord_x + chord_y * chord_y) ** 0.5
            if chord_len > 1e-9:
                dev = abs((curr[0] - prev[0]) * chord_y -
                          (curr[1] - prev[1]) * chord_x) / chord_len
                if dev < dev_ft:
                    continue

            keep.append(curr)
        if len(keep) == n:
            break  # stable
        pts = keep

    if closed and pts:
        pts.append(pts[0])
    return pts


def _ring_area_2d(ring):
    """Shoelace area of a 2D ring (treats first==last as the closing point)."""
    if not ring or len(ring) < 3:
        return 0.0
    n = len(ring)
    if ring[0] == ring[-1]:
        n -= 1
    if n < 3:
        return 0.0
    s = 0.0
    for i in range(n):
        x1, y1 = ring[i]
        x2, y2 = ring[(i + 1) % n]
        s += x1 * y2 - x2 * y1
    return abs(s) * 0.5


def _largest_ring_edge_count(rings):
    """Edge count of the ring with the largest enclosed area."""
    if not rings:
        return 0
    best_idx = 0
    best_a = -1.0
    for i, r in enumerate(rings):
        a = _ring_area_2d(r)
        if a > best_a:
            best_a = a
            best_idx = i
    r = rings[best_idx]
    n = len(r)
    if n >= 2 and r[0] == r[-1]:
        n -= 1
    return n


# --- Grasshopper integration ----------------------------------------------
#
# After each parcel import we open Grasshopper and (re)load a definition:
# N number sliders (one per parcel edge) plus a height slider drive a
# GhPython component that reads the PARCEL curve from the active doc,
# applies a per-edge inward offset, and extrudes the resulting polygon to
# the height value. The Brep is GH preview geometry, so changing any
# slider rebuilds the volume live.

GHPY_VOLUME_SCRIPT_TEMPLATE = '''# Plinth full data-center layout generator.
# Inputs:
#   OFF -- list of inward offsets in feet, one per parcel edge (in edge order).
#   H   -- max height in feet (extrusion height).
#   KW  -- target IT load in kW. Drives counts of EVERY component below.
#   NG/NU/NB/NA/NC/ND/NR -- per-component count overrides. Default 0 means
#       "use the value derived from KW + the proportions formula"; > 0
#       forces exactly that many units. Lets the user pin specific counts
#       (e.g. "exactly 8 generators") without touching formula constants.
# Outputs:
#   a   -- buildable volume (Brep) extruded from the post-offset footprint.
# Side effects:
#   Bakes the data-center component layout onto separate Rhino layers, one
#   per equipment family, each with its own color. Layers wiped at solve
#   start so slider drags do not accumulate stale geometry.
#     RACK            (yellow)  -- server cabinets, in cold/hot-aisle pods
#     CRAC            (mauve)   -- climate-control units in data hall
#     UPS             (orange)  -- APC PX500-class UPS modules in power room
#     BATTERY         (navy)    -- battery cabinets in power room
#     ATS             (lime)    -- ATS lineups in power room
#     GEN             (blue)    -- diesel gensets in yard (grade-level only)
#     CHILLER         (cyan)    -- chillers in yard (grade-level only)
#     DRYCOOLER       (gray)    -- dry coolers in yard (grade-level only)
#     CIRCUIT_POWER   (red)     -- gen->ATS->UPS->racks + UPS->battery
#     CIRCUIT_COOLING (cyan)    -- drycooler->chiller->CRAC->racks
#     READOUT         (white)   -- multi-line capacity card text above building
#     SETBACK_DIM     (amber)   -- per-edge setback dim lines + distance labels
#     LABELS          (gray)    -- equipment cluster TextDots
# Multi-story stacking: n_stories = floor(H / STORY_HEIGHT_FT). Yard
# equipment stays at grade; power-room (ATS/UPS/BATTERY) and data hall
# (racks + CRAC) stack onto every floor. Per-floor counts = total / stories.
# The layout CAPS rack count at racks_per_floor * n_stories so nothing is
# ever baked outside the red volume; the script reports the parcel's max
# IT-load and bakes a capacity-card text above the building.
import math
import traceback
import Rhino
import Rhino.Geometry as rg

doc = Rhino.RhinoDoc.ActiveDoc
tol = doc.ModelAbsoluteTolerance or 0.001

offsets = []
if OFF is not None:
    for v in OFF:
        try:
            offsets.append(float(v))
        except:
            offsets.append(0.0)

height = 35.0
try:
    if H is not None:
        h = float(H)
        if h > 0:
            height = h
except:
    pass

# === WP 144 hot-aisle / cold-aisle layout constants =========================
# These are FIXED dimensions per Schneider WP 144 ("Data Center Projects:
# Establishing a Floor Plan"). They never scale with parcel size -- only
# rack COUNT and rectangle EXTENT scale. RACK_W 2 ft / RACK_D 4 ft = standard
# 600x1200 mm cabinet that snaps to the 2 ft tile grid (1 tile x 2 tiles).
RACK_W = 2.0
RACK_D = 4.0
RACK_H = 7.0
KW_PER_RACK = 15.0
TILE_FT = 2.0                    # 2 ft x 2 ft floor tile -- the modular unit.
COLD_AISLE_FT = 4.0              # 2 tiles. WP 144 minimum.
HOT_AISLE_FT = 4.0               # 2 tiles. WP 144 says 3 ft min; 4 ft preferred.
# Row pair = | RACK | COLD | RACK | (fronts facing across cold aisle).
# Pair inner width (V) = 2*RACK_D + COLD = 12 ft. Adjacent pairs share a
# HOT aisle, so pair-to-pair pitch (centerline-to-centerline) = 16 ft. WP 144
# minimum row pitch is 15 ft 4 in (= 4*2 + 4 + 3 + tolerances); 16 ft uses
# the preferred 4 ft hot aisle and snaps cleanly to 8 tiles.
PAIR_INNER_FT = 2 * RACK_D + COLD_AISLE_FT       # 12 ft
ROW_PAIR_PITCH_FT = PAIR_INNER_FT + HOT_AISLE_FT # 16 ft
MAX_ROW_LEN_FT = 52.0                            # 16 m egress cap (WP 144).
CROSS_AISLE_FT = 4.0                             # inserted when a row exceeds MAX_ROW_LEN_FT.
MIN_RACKS_PER_ROW = 10                           # below this, airflow drops; row gets dropped.
PERIMETER_CLEARANCE_FT = 4.0                     # min clear from any interior wall.
CRAC_RACK_CLEARANCE_FT = 4.0                     # CRAC face -> first rack row (4 ft min, 6 ft preferred).
ELEC_SERVICE_AISLE_FT = 4.0                      # service aisle inside electrical room.

# === Data-center component sizing model =====================================
# Per-unit kW values match common equipment specs (APC PX500, SDMO 1.5 MW,
# etc.); bump them to rebalance the layout. All component counts derive from
# `total_kw` so the whole layout scales uniformly with the IT-load slider.
# Component footprints are snapped to multiples of TILE_FT (2 ft) so they
# land on the floor grid; bumped UP from real product dims when needed.
PUE = 1.4                        # Power Usage Effectiveness
COOLING_FRACTION = 0.40          # cooling kW = COOLING_FRACTION * IT_KW
POWER_ROOM_FRACTION = 0.12       # 12% of building footprint reserved for UPS/ATS/batteries

# Generators (SDMO-class, 1.5 MW each, N+1 redundancy). W is the dimension
# ALONG the yard edge (long side facing the wall), D is perpendicular (depth
# into the yard). 10 ft GEN_GAP per typical genset service-clearance spec.
GEN_KW_PER_UNIT = 1500.0
GEN_W, GEN_D, GEN_H = 30.0, 10.0, 12.0
GEN_GAP = 10.0
# UPS modules (APC Symmetra PX500-class, 500 kW each, N+1). D snapped to 4 ft.
UPS_KW_PER_UNIT = 500.0
UPS_W, UPS_D, UPS_H = 6.0, 4.0, 7.0
UPS_GAP = 2.0
# Battery cabinets (4 per UPS module typical). W snapped to 4 ft.
BATTERY_PER_UPS = 4
BATTERY_W, BATTERY_D, BATTERY_H = 4.0, 2.0, 6.0
BATTERY_GAP = 2.0
# ATS lineups (one per 2 MW IT). Already tile-aligned.
ATS_KW_PER_UNIT = 2000.0
ATS_W, ATS_D, ATS_H = 8.0, 4.0, 7.0
ATS_GAP = 2.0
# Chillers (Emerson-class, 750 kW cooling each, N+1) -- W along wall, D into yard.
CHILLER_KW_PER_UNIT = 750.0
CHILLER_W, CHILLER_D, CHILLER_H = 20.0, 8.0, 8.0
CHILLER_GAP = 4.0
# Dry coolers (LU-VE-class, 1:1 with chillers). W snapped to 16 ft.
DRYCOOLER_W, DRYCOOLER_D, DRYCOOLER_H = 16.0, 6.0, 8.0
DRYCOOLER_GAP = 4.0
# CRAC units (~100 kW per unit, on the IT-hall perimeter band). D snapped to 4 ft.
CRAC_KW_PER_UNIT = 100.0
CRAC_W, CRAC_D, CRAC_H = 4.0, 4.0, 7.0

# === Yard tuning ============================================================
# MECH_YARD_CLEARANCE_FT -- clear gap between the rectangle wall and the
#     first row of yard equipment (gen / chiller / drycooler). Doubles as a
#     vehicle-access aisle. WP 144-style 10 ft service clearance.
MECH_YARD_CLEARANCE_FT = 10.0

target_racks = 0
total_kw = 0.0
try:
    if KW is not None:
        total_kw = float(KW)
        if total_kw > 0:
            target_racks = int(math.ceil(total_kw / KW_PER_RACK))
except:
    pass

# Component counts (the proportions formula in action). N+1 redundancy on
# critical (gen, UPS, chiller); 1:1 on derived (battery, dry cooler);
# per-load on distributed (CRAC, ATS).
def _ceil_or_zero(x):
    return int(math.ceil(x)) if x > 0 else 0

n_gen = _ceil_or_zero(total_kw / GEN_KW_PER_UNIT) + (1 if total_kw > 0 else 0)
n_ups = _ceil_or_zero(total_kw / UPS_KW_PER_UNIT) + (1 if total_kw > 0 else 0)
n_battery = n_ups * BATTERY_PER_UPS
n_ats = _ceil_or_zero(total_kw / ATS_KW_PER_UNIT)
n_chiller = _ceil_or_zero(total_kw * COOLING_FRACTION / CHILLER_KW_PER_UNIT) + (1 if total_kw > 0 else 0)
n_drycooler = n_chiller
n_crac = _ceil_or_zero(total_kw / CRAC_KW_PER_UNIT)

# Apply per-component count overrides. Slider sends 0 when the user wants
# auto, > 0 to force a specific count. Anything non-numeric falls back to
# the auto value.
def _apply_override(default, override_val):
    try:
        if override_val is not None:
            ov = int(round(float(override_val)))
            if ov > 0:
                return ov
    except:
        pass
    return default

try:
    n_gen       = _apply_override(n_gen,       NG)
    n_ups       = _apply_override(n_ups,       NU)
    n_battery   = _apply_override(n_battery,   NB)
    n_ats       = _apply_override(n_ats,       NA)
    n_chiller   = _apply_override(n_chiller,   NC)
    n_drycooler = _apply_override(n_drycooler, ND)
    n_crac      = _apply_override(n_crac,      NR)
except NameError as _ne:
    # If override inputs aren't registered (older builder), keep auto counts.
    print "[GH] override inputs missing, using auto counts:", _ne

a = None
setback = None
report = ""  # multi-line site report; populated in the layout block, surfaced as the second component output

# DC component layers. Each equipment family lives on its own layer for
# selectability + per-layer color in the Rhino viewport. Wiped at solve start
# so slider drags do not accumulate stale geometry. Colors are picked to
# loosely match the brands in the reference layout (APC orange, SDMO blue).
import System.Drawing as _sysdraw
DC_LAYER_SPEC = [
    ("RACK",            _sysdraw.Color.FromArgb(255, 230,   0)),  # yellow
    ("CRAC",            _sysdraw.Color.FromArgb(160, 130, 200)),  # mauve
    ("UPS",             _sysdraw.Color.FromArgb(255, 130,   0)),  # APC orange
    ("BATTERY",         _sysdraw.Color.FromArgb( 40,  80, 130)),  # dark blue
    ("ATS",             _sysdraw.Color.FromArgb(180, 200,  50)),  # yellow-green
    ("GEN",             _sysdraw.Color.FromArgb(  0,  80, 180)),  # SDMO blue
    ("CHILLER",         _sysdraw.Color.FromArgb(100, 200, 220)),  # cyan
    ("DRYCOOLER",       _sysdraw.Color.FromArgb(200, 200, 200)),  # light gray
    # Flow-diagram polylines on top of the equipment. Drawn at z=2 ft so the
    # lines float just above ground and stay visible in plan view.
    ("CIRCUIT_POWER",   _sysdraw.Color.FromArgb(220,  40,  40)),  # red    -- electrical
    ("CIRCUIT_COOLING", _sysdraw.Color.FromArgb( 40, 180, 220)),  # cyan   -- heat-rejection loop
    # Annotation layers (text + dim lines). Color choices read on both
    # dark and light viewports.
    ("READOUT",         _sysdraw.Color.FromArgb(255, 255, 255)),  # white  -- capacity card text above building
    ("SETBACK_DIM",     _sysdraw.Color.FromArgb(255, 200,  60)),  # amber  -- per-edge setback dim lines
    ("LABELS",          _sysdraw.Color.FromArgb(220, 220, 220)),  # gray   -- equipment cluster labels
    # Zone outline polylines: building rectangle, electrical room, IT hall,
    # CRAC perimeter band. Drawn at z=0.5 so they sit just above the parcel
    # plane in plan view but never collide with the equipment Z=0..H bakes.
    ("ZONE_BOUNDARY",   _sysdraw.Color.FromArgb(255, 255, 255)),  # white  -- zone outlines
]
dc_layer_idx = {}
for _ln, _lc in DC_LAYER_SPEC:
    try:
        _i = doc.Layers.FindByFullPath(_ln, True)
        if _i < 0:
            _ly = Rhino.DocObjects.Layer()
            _ly.Name = _ln
            _ly.Color = _lc
            _i = doc.Layers.Add(_ly)
        dc_layer_idx[_ln] = _i
        if _i >= 0:
            _kill = []
            for _obj in doc.Objects:
                if _obj.Attributes.LayerIndex == _i:
                    _kill.append(_obj.Id)
            for _gid in _kill:
                doc.Objects.Delete(_gid, True)
    except Exception as _le:
        print "[GH] layer", _ln, "prep failed:", _le
        dc_layer_idx[_ln] = -1

# Force visibility on for the layers that carry the demo-critical
# annotations -- these are easy to toggle off accidentally in Rhino's
# layer panel, and the bakes silently land on a hidden layer otherwise.
# (Other DC layers like ZONE_BOUNDARY / SETBACK_DIM keep whatever
# visibility the user set so they can hide construction lines for video.)
for _ln in ("LABELS", "RACK", "GEN", "ATS", "UPS", "BATTERY", "CHILLER", "DRYCOOLER", "CRAC"):
    _i = dc_layer_idx.get(_ln, -1)
    if _i >= 0:
        try:
            doc.Layers[_i].IsVisible = True
            doc.Layers[_i].SetPersistentVisibility(True)
        except Exception:
            pass

rack_layer_idx = dc_layer_idx.get("RACK", -1)


def _frame_l2w(lu, lv, frame):
    """Local-frame (u, v) -> world (x, y). Frame = (ox, oy, ux, uy, vx, vy)."""
    ox, oy, ux, uy, vx, vy = frame
    return (ox + lu * ux + lv * vx, oy + lu * uy + lv * vy)


def _build_frame(curve):
    """Build a local frame anchored to the polygon's longest edge.

    Origin = midpoint of the longest edge.
    U-axis = unit vector along the longest edge (start -> end). This is the
        building's PRIMARY axis -- row PAIRS stack along U (long direction)
        while individual rack rows run along V (short direction). The
        layout therefore fills the parcel's short axis first and only
        extends along the long axis once the short side is saturated.
    V-axis = perpendicular to U, pointing INTO the polygon interior.

    With this frame the longest edge sits at V=0 spanning U=[-L/2, +L/2];
    every other vertex lands at V > 0. That anchors the inscribed-rectangle
    search (lv_min fixed at 0; grow lv_max upward) and gives all downstream
    layout code a stable local CS that rotates with the parcel.
    Returns (origin_x, origin_y, ux, uy, vx, vy).
    """
    tg = curve.TryGetPolyline()
    poly = None
    if isinstance(tg, tuple) and len(tg) == 2:
        if tg[0]:
            poly = tg[1]
    elif tg is not None and hasattr(tg, "Count"):
        poly = tg
    if poly is None or poly.Count < 3:
        return (0.0, 0.0, 1.0, 0.0, 0.0, 1.0)
    longest_len = -1.0
    li = 0
    for i in range(poly.Count - 1):
        v0 = poly[i]
        v1 = poly[i + 1]
        elen = ((v1.X - v0.X) ** 2 + (v1.Y - v0.Y) ** 2) ** 0.5
        if elen > longest_len:
            longest_len = elen
            li = i
    if longest_len < 1e-9:
        return (0.0, 0.0, 1.0, 0.0, 0.0, 1.0)
    v0 = poly[li]
    v1 = poly[li + 1]
    ox = (v0.X + v1.X) / 2.0
    oy = (v0.Y + v1.Y) / 2.0
    ux = (v1.X - v0.X) / longest_len
    uy = (v1.Y - v0.Y) / longest_len
    # 90-deg CCW perpendicular. Flip if it points away from the centroid so
    # V always faces into the polygon interior, regardless of ring winding.
    nx, ny = -uy, ux
    mp = rg.AreaMassProperties.Compute(curve)
    if mp is not None:
        cx, cy = mp.Centroid.X, mp.Centroid.Y
    else:
        cx, cy = poly[0].X, poly[0].Y
    if (cx - ox) * nx + (cy - oy) * ny < 0:
        nx, ny = -nx, -ny
    return (ox, oy, ux, uy, nx, ny)


def _local_bounds(curve, frame):
    """Project the curve's polyline vertices into the frame and return
    (lu_min, lv_min, lu_max, lv_max) -- the OBB extents in local coords."""
    tg = curve.TryGetPolyline()
    poly = None
    if isinstance(tg, tuple) and len(tg) == 2:
        if tg[0]:
            poly = tg[1]
    elif tg is not None and hasattr(tg, "Count"):
        poly = tg
    if poly is None or poly.Count < 3:
        return (0.0, 0.0, 0.0, 0.0)
    ox, oy, ux, uy, vx, vy = frame
    lu_min, lv_min = 1.0e18, 1.0e18
    lu_max, lv_max = -1.0e18, -1.0e18
    for i in range(poly.Count):
        p = poly[i]
        dx = p.X - ox
        dy = p.Y - oy
        lu = dx * ux + dy * uy
        lv = dx * vx + dy * vy
        if lu < lu_min: lu_min = lu
        if lu > lu_max: lu_max = lu
        if lv < lv_min: lv_min = lv
        if lv > lv_max: lv_max = lv
    return (lu_min, lv_min, lu_max, lv_max)


def _project_polyline_to_frame(curve, frame):
    """Project a polyline curve's vertices into the local frame.
    Returns a list of (u, v) tuples (closing vertex included), or None.
    """
    tg = curve.TryGetPolyline()
    poly = None
    if isinstance(tg, tuple) and len(tg) == 2:
        if tg[0]:
            poly = tg[1]
    elif tg is not None and hasattr(tg, "Count"):
        poly = tg
    if poly is None or poly.Count < 3:
        return None
    ox, oy, ux, uy, vx, vy = frame
    pts = []
    for i in range(poly.Count):
        p = poly[i]
        dx = p.X - ox
        dy = p.Y - oy
        pts.append((dx * ux + dy * uy, dx * vx + dy * vy))
    return pts


def _inscribe_rectangle(curve, frame, samples=160):
    """Fit the largest axis-aligned (in `frame`) rectangle inscribed in
    `curve`, with its bottom side ANCHORED on V=0 (the chosen edge).

    Why V=0 anchor and not float-V: anchoring guarantees a single
    contiguous yard strip on the V+ ("interior") side of the rectangle.
    A float-V rectangle can land in the polygon's middle and split the
    yard into two strips on opposite sides; that broke user mental model
    of "building south, yard north" so we keep the yard contiguous.

    Algorithm: sweep the rectangle's TOP V from 0 up to v_max; at each
    candidate top, the rectangle's allowable U range is the running
    intersection of polygon-cross-section U ranges over [0, v_top].
    Pick the (u_lo, u_hi, v_top) with maximum area.

    Works for convex AND mildly non-convex polygons. For multi-chord
    crossings at the same V (rare in setback footprints) it takes the
    outermost two crossings, which conservatively narrows the rect.
    Returns (lu_min, lv_min, lu_max, lv_max) or None if no fit.
    """
    local = _project_polyline_to_frame(curve, frame)
    if local is None or len(local) < 4:
        return None
    n = len(local) - 1  # last == first
    v_max = max(v for _, v in local)
    if v_max <= 1e-6:
        return None

    def _crossings_at_v(v_query):
        out = []
        for i in range(n):
            u0, vv0 = local[i]
            u1, vv1 = local[i + 1]
            # Skip edges that don't straddle the V line.
            if vv0 < v_query - 1e-9 and vv1 < v_query - 1e-9:
                continue
            if vv0 > v_query + 1e-9 and vv1 > v_query + 1e-9:
                continue
            dv = vv1 - vv0
            if abs(dv) < 1e-12:
                # Horizontal edge at exactly v_query -- don't define a
                # proper crossing. The endpoints are picked up by adjacent
                # edges; skip to avoid double-counting.
                continue
            t = (v_query - vv0) / dv
            if -1e-9 <= t <= 1.0 + 1e-9:
                out.append(u0 + t * (u1 - u0))
        out.sort()
        return out

    # Start just above the chosen edge (V=0+epsilon) so we get a real
    # crossing pair instead of catching the edge endpoints exactly.
    eps = max(v_max * 1e-3, 1e-3)
    init = _crossings_at_v(eps)
    if len(init) < 2:
        return None
    cur_u_lo = init[0]
    cur_u_hi = init[-1]

    best_area = -1.0
    best_box = None
    step = (v_max - eps) / float(samples)
    if step <= 0:
        return None
    for s in range(samples + 1):
        v_q = eps + s * step
        crs = _crossings_at_v(v_q)
        if len(crs) >= 2:
            if crs[0] > cur_u_lo:
                cur_u_lo = crs[0]
            if crs[-1] < cur_u_hi:
                cur_u_hi = crs[-1]
        w = cur_u_hi - cur_u_lo
        if w <= 0:
            break
        area = w * v_q
        if area > best_area:
            best_area = area
            best_box = (cur_u_lo, 0.0, cur_u_hi, v_q)
    return best_box


def _build_candidate_frames(curve, max_count=6):
    """Return up to `max_count` candidate frames, one per longest edge.
    Used to search across orientations for the largest inscribed
    rectangle. Each frame's U-axis is along the corresponding edge; V
    points into the polygon interior. Centroid sign-test mirrors
    _build_frame so downstream code can treat every frame uniformly.
    """
    tg = curve.TryGetPolyline()
    poly = None
    if isinstance(tg, tuple) and len(tg) == 2:
        if tg[0]:
            poly = tg[1]
    elif tg is not None and hasattr(tg, "Count"):
        poly = tg
    if poly is None or poly.Count < 3:
        return [(0.0, 0.0, 1.0, 0.0, 0.0, 1.0)]
    edges = []
    for i in range(poly.Count - 1):
        v0 = poly[i]
        v1 = poly[i + 1]
        elen = ((v1.X - v0.X) ** 2 + (v1.Y - v0.Y) ** 2) ** 0.5
        if elen < 1e-9:
            continue
        edges.append((elen, v0, v1))
    edges.sort(key=lambda e: -e[0])
    mp = rg.AreaMassProperties.Compute(curve)
    if mp is not None:
        cx, cy = mp.Centroid.X, mp.Centroid.Y
    else:
        cx, cy = poly[0].X, poly[0].Y
    frames = []
    for elen, v0, v1 in edges[:max_count]:
        ox = (v0.X + v1.X) / 2.0
        oy = (v0.Y + v1.Y) / 2.0
        ux = (v1.X - v0.X) / elen
        uy = (v1.Y - v0.Y) / elen
        # 90-deg CCW perpendicular -> flip if it points away from centroid.
        nx, ny = -uy, ux
        if (cx - ox) * nx + (cy - oy) * ny < 0:
            nx, ny = -nx, -ny
        frames.append((ox, oy, ux, uy, nx, ny))
    if not frames:
        return [(0.0, 0.0, 1.0, 0.0, 0.0, 1.0)]
    return frames


def _best_frame_and_rect(curve):
    """Search candidate orientations and return (frame, rect) with the
    largest inscribed (V=0-anchored) rectangle. The chosen edge becomes
    U; V points into the polygon, V=0 sits on the chosen edge.

    The 90-deg rotation that used to swap U/V when rect_d > rect_w was
    removed: rotating without re-running the centroid sign-test left V
    pointing OUT of the polygon for some parcels, which flipped the
    yard-strip semantic ("TOP" landed on the wrong side in world coords).
    Now U always = the chosen edge's direction, V always = into-polygon
    perpendicular, no matter the rectangle's aspect ratio.

    Returns (frame, (lu_min, lv_min, lu_max, lv_max)) or (None, None).
    """
    candidates = _build_candidate_frames(curve, max_count=6)
    best_frame = None
    best_rect = None
    best_area = -1.0
    for fr in candidates:
        rect = _inscribe_rectangle(curve, fr)
        if rect is None:
            continue
        u0, v0, u1, v1 = rect
        w = u1 - u0
        d = v1 - v0
        if w <= 0 or d <= 0:
            continue
        area = w * d
        if area > best_area:
            best_area = area
            best_frame = fr
            best_rect = rect
    return best_frame, best_rect


def _bake_box(layer_idx, cx, cy, w, d, h, frame=None, z0=0.0):
    """Bake a w x d x h ft Box. If `frame` is given the box is oriented to
    the frame and (cx, cy) are LOCAL coords; otherwise world axis-aligned.
    `z0` shifts the bottom of the box upward (used for multi-story stacking
    -- floor 0 has z0=0, floor 1 has z0=story_height, ...).
    """
    if layer_idx < 0:
        return None
    if frame is None:
        _pl = rg.Plane(rg.Point3d(cx, cy, 0), rg.Vector3d.ZAxis)
    else:
        wx, wy = _frame_l2w(cx, cy, frame)
        _, _, ux, uy, vx, vy = frame
        _pl = rg.Plane(
            rg.Point3d(wx, wy, 0),
            rg.Vector3d(ux, uy, 0),
            rg.Vector3d(vx, vy, 0),
        )
    _bx = rg.Box(
        _pl,
        rg.Interval(-w / 2.0, w / 2.0),
        rg.Interval(-d / 2.0, d / 2.0),
        rg.Interval(z0, z0 + h),
    )
    _brep = _bx.ToBrep()
    if _brep is None:
        return None
    _attrs = Rhino.DocObjects.ObjectAttributes()
    _attrs.LayerIndex = layer_idx
    return doc.Objects.AddBrep(_brep, _attrs)


def _bake_polyline_world(layer_idx, world_pts, z=2.0):
    """Bake a polyline through world-coord (x, y) points at elevation `z`.
    Used for circuit/flow lines; layer color determines the line color so
    each layer (CIRCUIT_POWER, CIRCUIT_COOLING) shows distinctly. Returns
    the new object's GUID, or None if the layer is missing or the point
    list is too short.
    """
    if layer_idx < 0:
        return None
    pts = [p for p in world_pts if p is not None]
    if len(pts) < 2:
        return None
    pl = rg.Polyline()
    for x, y in pts:
        pl.Add(x, y, z)
    attrs = Rhino.DocObjects.ObjectAttributes()
    attrs.LayerIndex = layer_idx
    return doc.Objects.AddPolyline(pl, attrs)


def _bake_textdot(layer_idx, x, y, z, text):
    """Bake a screen-aligned TextDot at (x, y, z). Used for short labels
    (equipment counts, setback distances) that need to stay readable
    regardless of camera angle/zoom. Returns the new GUID, or None.
    """
    if layer_idx < 0 or not text:
        return None
    try:
        td = rg.TextDot(text, rg.Point3d(x, y, z))
        attrs = Rhino.DocObjects.ObjectAttributes()
        attrs.LayerIndex = layer_idx
        return doc.Objects.AddTextDot(td, attrs)
    except Exception as ex:
        print "[GH] textdot bake failed:", ex
        return None


def _bake_text(layer_idx, x, y, z, text, height_ft=4.0):
    """Bake a 3D TextEntity on the World-XY plane at (x, y, z). Multi-line
    text uses '\\n' separators. Falls back to a TextDot if TextEntity
    construction fails on this Rhino build (older IronPython exposes a
    different ctor). Returns the new GUID, or None.
    """
    if layer_idx < 0 or not text:
        return None
    try:
        te = rg.TextEntity()
        # Use the doc's current dim style. Without this, AddText silently
        # drops the entity on some Rhino 7 builds.
        try:
            te.DimensionStyleId = doc.DimStyles.CurrentId
        except Exception:
            pass
        te.Plane = rg.Plane(rg.Point3d(x, y, z), rg.Vector3d.ZAxis)
        try:
            te.PlainText = text
        except Exception:
            te.Text = text
        try:
            te.TextHeight = height_ft
        except Exception:
            pass
        attrs = Rhino.DocObjects.ObjectAttributes()
        attrs.LayerIndex = layer_idx
        return doc.Objects.AddText(te, attrs)
    except Exception as ex:
        print "[GH] text bake failed, falling back to TextDot:", ex
        # Multi-line text won't render in a TextDot; flatten to first line.
        first_line = text.splitlines()[0] if text else ""
        return _bake_textdot(layer_idx, x, y, z, first_line)


def _box_inside_curve(curve, cx, cy, w, d, tol, frame=None):
    """Strict containment: centroid + 4 corners of the (frame-oriented) box
    must all sit inside `curve` in WORLD coords. Catches items whose centroid
    falls inside a non-rectangular boundary but whose corners overhang it.
    """
    if curve is None:
        return True
    pts = [
        (cx, cy),
        (cx - w / 2.0, cy - d / 2.0),
        (cx + w / 2.0, cy - d / 2.0),
        (cx - w / 2.0, cy + d / 2.0),
        (cx + w / 2.0, cy + d / 2.0),
    ]
    plane = rg.Plane.WorldXY
    for px, py in pts:
        if frame is not None:
            wx, wy = _frame_l2w(px, py, frame)
        else:
            wx, wy = px, py
        if curve.Contains(rg.Point3d(wx, wy, 0), plane, tol) != rg.PointContainment.Inside:
            return False
    return True


def _grid_dims(n, item_w, item_d, gap, aspect=1.0):
    """Compute (w, d, cols, rows) for n items packed in a rectangular grid.
    `aspect` biases the grid toward wider (>1) or taller (<1) layouts. Used
    to give each zone a tunable shape -- e.g., yard equipment uses aspect=4
    so it lays out as a long shallow strip rather than a chunky square.
    """
    if n <= 0:
        return (0.0, 0.0, 0, 0)
    cols = max(1, int(math.ceil(math.sqrt(n * aspect))))
    rows = (n + cols - 1) // cols
    w = cols * item_w + max(0, cols - 1) * gap
    d = rows * item_d + max(0, rows - 1) * gap
    return (w, d, cols, rows)


def _box_overlaps_curve(curve, cx, cy, w, d, tol, frame=None):
    """True if ANY of the 5 test points lies inside `curve` -- i.e., the
    box overlaps the curve's interior. Used as an exclusion check: pass
    `building_curve` to keep yard equipment out of the building footprint.
    """
    if curve is None:
        return False
    pts = [
        (cx, cy),
        (cx - w / 2.0, cy - d / 2.0),
        (cx + w / 2.0, cy - d / 2.0),
        (cx - w / 2.0, cy + d / 2.0),
        (cx + w / 2.0, cy + d / 2.0),
    ]
    plane = rg.Plane.WorldXY
    for px, py in pts:
        if frame is not None:
            wx, wy = _frame_l2w(px, py, frame)
        else:
            wx, wy = px, py
        if curve.Contains(rg.Point3d(wx, wy, 0), plane, tol) == rg.PointContainment.Inside:
            return True
    return False


def _pack_grid(origin, size, item_w, item_d, item_h, max_n, layer_idx,
               long_axis='x', containment_curve=None, gap=0.0, frame=None,
               exclusion_curve=None, z0=0.0):
    """Pack up to max_n boxes into a (size_w x size_d) rectangle.

    `containment_curve`: if given, only place items fully inside this curve.
    `exclusion_curve`:   if given, skip items that overlap this curve at all
                         (even partial overlap). Used for "place in parcel
                         BUT NOT in building" -- the L-notch fallback.
    `frame`: optional local frame; coords are local, bakes are oriented.
    `z0`: vertical offset for the bottom of every baked box (multi-story).

    long_axis 'x' marches items in +X first then wraps in +Y; 'y' is the swap.
    `gap` is spacing BETWEEN adjacent items only (not at the ends).
    """
    if max_n <= 0:
        return 0
    x0, y0 = origin
    w_tot, d_tot = size
    pitch_w = item_w + gap
    pitch_d = item_d + gap
    if pitch_w <= 0 or pitch_d <= 0:
        return 0
    if long_axis == 'x':
        n_per_row = int((w_tot + gap) / pitch_w)
        n_rows = int((d_tot + gap) / pitch_d)
    else:
        n_per_row = int((d_tot + gap) / pitch_w)
        n_rows = int((w_tot + gap) / pitch_d)
    if n_per_row < 1 or n_rows < 1:
        return 0
    placed = 0
    for r in range(n_rows):
        for c in range(n_per_row):
            if placed >= max_n:
                return placed
            if long_axis == 'x':
                cx = x0 + c * pitch_w + item_w / 2.0
                cy = y0 + r * pitch_d + item_d / 2.0
                w_e, d_e = item_w, item_d
            else:
                cy = y0 + c * pitch_w + item_w / 2.0
                cx = x0 + r * pitch_d + item_d / 2.0
                w_e, d_e = item_d, item_w
            if containment_curve is not None:
                if not _box_inside_curve(containment_curve, cx, cy, w_e, d_e, tol, frame=frame):
                    continue
            if exclusion_curve is not None:
                if _box_overlaps_curve(exclusion_curve, cx, cy, w_e, d_e, tol, frame=frame):
                    continue
            _bake_box(layer_idx, cx, cy, w_e, d_e, item_h, frame=frame, z0=z0)
            placed += 1
    return placed

try:
    layer_idx = doc.Layers.FindByFullPath("PARCEL", True)
    print "[GH] start, edges=", len(offsets), "H=", height, "ft, KW=", target_racks * KW_PER_RACK if target_racks else 0, "(", target_racks, "racks target)"

    best_crv = None
    if layer_idx >= 0:
        rh_objs = doc.Objects.FindByLayer("PARCEL")
        best_area = -1.0
        if rh_objs:
            for obj in rh_objs:
                crv = obj.Geometry
                if not isinstance(crv, rg.Curve):
                    continue
                if not crv.IsClosed:
                    continue
                mp = rg.AreaMassProperties.Compute(crv)
                if mp is None:
                    continue
                if mp.Area > best_area:
                    best_area = mp.Area
                    best_crv = crv

    if best_crv is None:
        print "[GH] no closed curve found on PARCEL layer"
    else:
        # Force CCW winding up front. _build_frame returns V as the INWARD
        # perpendicular of the longest edge. For a CCW polygon V is the
        # left-hand perpendicular of U so U x V = +Z; for a CW polygon V is
        # the right-hand perpendicular so U x V = -Z. _bake_box builds its
        # plane via rg.Plane(origin, U, V) -- if U x V = -Z the plane's Z
        # points DOWN, and Interval(z0, z0+h) extrudes the box into negative
        # world Z. Result: red volume goes UP from z=0 (Extrusion.Create on a
        # CCW-forced curve below) but every rack/CRAC/UPS/etc. goes DOWN, so
        # the components end up beneath the parcel plane while the red box
        # sits above them. Reversing here once means the offset algorithm,
        # frame, layout bake, and extrusion all share +Z handedness.
        if best_crv.ClosedCurveOrientation(rg.Plane.WorldXY) == rg.CurveOrientation.Clockwise:
            _rev_crv = best_crv.DuplicateCurve()
            _rev_crv.Reverse()
            best_crv = _rev_crv
            print "[GH] reversed parcel curve to CCW (components now extrude UP)"

        # IronPython 2 maps `out Polyline` as a tuple return: (bool, Polyline).
        # Passing a pre-allocated Polyline instance fails with "expected
        # strong box polyline" because the runtime wants a
        # StrongBox<Polyline> wrapper.
        try:
            tg = best_crv.TryGetPolyline()
        except Exception as ex_e:
            tg = None
            print "[GH] TryGetPolyline raised:", ex_e
        poly = None
        has_pl = False
        if isinstance(tg, tuple) and len(tg) == 2:
            has_pl, poly = tg
        elif tg is not None and hasattr(tg, "Count"):
            # Some Rhino builds return the Polyline directly (or None).
            poly = tg
            has_pl = poly.Count >= 2

        if not has_pl or poly is None:
            print "[GH] PARCEL curve is not a polyline; cannot do per-edge"
        else:
            n_edges = poly.Count - 1
            plane = rg.Plane.WorldXY
            orient = best_crv.ClosedCurveOrientation(plane)
            ccw = (orient == rg.CurveOrientation.CounterClockwise)

            # Per-vertex line-intersection offset.
            #
            # The earlier algorithm clipped the parcel by half-plane
            # rectangles (one per edge) via boolean intersection. That works
            # for CONVEX polygons but produces wrong results for non-convex
            # parcels: an L-shape gets its short arm clipped away, because
            # one edge's "inward" half-plane excludes geometry that another
            # edge does include.
            #
            # Instead, treat each edge as an oriented infinite line, shift
            # it inward by its slider's distance, and compute each new
            # vertex as the intersection of the two adjacent offset lines.
            # This handles non-convex parcels correctly: each new vertex is
            # determined from purely local information.
            offset_lines = []  # one (px, py, dx, dy) per edge; None if degenerate
            for i in range(n_edges):
                v0 = poly[i]
                v1 = poly[i + 1]
                ex = v1.X - v0.X
                ey = v1.Y - v0.Y
                elen = (ex * ex + ey * ey) ** 0.5
                if elen < 1e-9:
                    offset_lines.append(None)
                    continue
                ex = ex / elen
                ey = ey / elen
                # Inward normal: left-perpendicular for CCW, right for CW.
                if ccw:
                    nx, ny = -ey, ex
                else:
                    nx, ny = ey, -ex
                d = offsets[i] if i < len(offsets) else 0.0
                ox = v0.X + nx * d
                oy = v0.Y + ny * d
                offset_lines.append((ox, oy, ex, ey))

            new_pts = []
            for i in range(n_edges):
                prev_l = offset_lines[(i - 1) % n_edges]
                curr_l = offset_lines[i]
                if prev_l is None and curr_l is None:
                    continue
                if prev_l is None:
                    new_pts.append((curr_l[0], curr_l[1]))
                    continue
                if curr_l is None:
                    new_pts.append((prev_l[0], prev_l[1]))
                    continue
                p1x, p1y, d1x, d1y = prev_l
                p2x, p2y, d2x, d2y = curr_l
                denom = d1x * d2y - d1y * d2x
                if abs(denom) < 1e-9:
                    # Adjacent edges are collinear after offset (parallel
                    # lines). Use the offset position of the current edge
                    # as the vertex -- effectively a no-op miter.
                    new_pts.append((p2x, p2y))
                else:
                    dx = p2x - p1x
                    dy = p2y - p1y
                    t = (dx * d2y - dy * d2x) / denom
                    new_pts.append((p1x + t * d1x, p1y + t * d1y))

            current = None
            if len(new_pts) >= 3:
                out_pl = rg.Polyline()
                for px, py in new_pts:
                    out_pl.Add(px, py, 0.0)
                out_pl.Add(new_pts[0][0], new_pts[0][1], 0.0)
                cand = out_pl.ToPolylineCurve()
                if cand is not None and cand.IsClosed:
                    mp = rg.AreaMassProperties.Compute(cand)
                    if mp is not None and mp.Area > tol:
                        current = cand
                    else:
                        print "[GH] post-offset polygon has zero/neg area (over-offset?)"
                else:
                    print "[GH] post-offset polyline did not close"
            else:
                print "[GH] not enough vertices after offset:", len(new_pts)

            # Lot-coverage cap. If the post-offset footprint covers more than
            # max_cov of the parcel, uniformly shrink the polygon around its
            # centroid so coverage hits exactly max_cov. Why uniform scale and
            # not "add more inward offset to every edge": uniform scale
            # preserves the *relative* edge offsets the user dialed in, so an
            # edge they pulled in further still ends up further in than its
            # neighbors after the cap.
            max_cov = __MAX_LOT_COVERAGE__
            if current is not None and best_area > 0:
                cur_mp = rg.AreaMassProperties.Compute(current)
                if cur_mp is not None and cur_mp.Area > max_cov * best_area:
                    target = max_cov * best_area
                    scale = (target / cur_mp.Area) ** 0.5
                    cen = cur_mp.Centroid
                    plane = rg.Plane(rg.Point3d(cen.X, cen.Y, 0.0),
                                     rg.Vector3d.ZAxis)
                    xform = rg.Transform.Scale(plane, scale, scale, 1.0)
                    scaled = current.DuplicateCurve()
                    if scaled.Transform(xform):
                        current = scaled
                        print "[GH] coverage capped at", max_cov

            # FAR (gross-floor-area) cap. The volume is treated as
            # max(1, floor(H / story_height)) identical floors. If
            # footprint * n_stories exceeds max_far * parcel_area, shrink the
            # footprint with the same uniform-scale-around-centroid mechanism
            # so it lands exactly at the GFA cap. max_far == None means "no
            # FAR constraint configured for this district" -- skip the stage.
            max_far = __MAX_FAR__
            story_height = __STORY_HEIGHT_FT__
            if (max_far is not None and max_far > 0 and current is not None
                    and best_area > 0 and story_height > 0):
                n_stories = int(height / story_height)
                if n_stories < 1:
                    n_stories = 1
                cur_mp = rg.AreaMassProperties.Compute(current)
                if cur_mp is not None:
                    gfa = cur_mp.Area * n_stories
                    max_gfa = max_far * best_area
                    if gfa > max_gfa:
                        target = max_gfa / float(n_stories)
                        scale = (target / cur_mp.Area) ** 0.5
                        cen = cur_mp.Centroid
                        plane = rg.Plane(rg.Point3d(cen.X, cen.Y, 0.0),
                                         rg.Vector3d.ZAxis)
                        xform = rg.Transform.Scale(plane, scale, scale, 1.0)
                        scaled = current.DuplicateCurve()
                        if scaled.Transform(xform):
                            current = scaled
                            print "[GH] FAR capped:", n_stories, "stories at FAR", max_far

            setback = current

            # --- WP 144 hot/cold-aisle data-center layout ---------------------
            # GOAL: produce a real-DC-style orthogonal floor plan per
            # Schneider Electric WP 144 ("Data Center Projects: Establishing
            # a Floor Plan"). Aisle widths, row pitch, perimeter clearances
            # and cross-aisle spacing are FIXED dimensions (defined as
            # constants above); ONLY rack counts and the rectangle's
            # extents scale with parcel size.
            #
            # Pipeline:
            #   1. Build a local frame anchored to `current`'s longest edge.
            #   2. Inscribe the largest axis-aligned rectangle in `current`,
            #      anchored at V=0. Snap to the 2 ft tile grid.
            #   3. Carve a 4 ft perimeter clearance off all interior walls.
            #   4. Reserve a single contiguous ELECTRICAL ROOM block on one
            #      SHORT edge of the inner rectangle. Internal layout (along
            #      U from outer wall to IT hall):
            #         [ ATS_D | aisle | BATTERY_D | aisle | UPS_D ]
            #      UPS row hugs the IT-hall partition (per WP 144 layout).
            #   5. The remainder of the inner rectangle is the IT HALL
            #      (white space). A CRAC perimeter band lines the LONG WALLS
            #      of the IT hall (CRAC face -> first rack >= 4 ft clear).
            #   6. Inside the rack zone, lay row pairs running parallel to U
            #      (the building primary axis). Pair = 2 rows facing across
            #      a 4 ft cold aisle; backs face hot aisles of adjacent
            #      pairs. Pair pitch = 16 ft (2*RACK_D + COLD + HOT).
            #      Insert a 4 ft cross-aisle every 52 ft (16 m egress cap).
            #      Drop rows shorter than MIN_RACKS_PER_ROW.
            #   7. Yard: gens single-row on one long edge, chillers + paired
            #      drycoolers on a DIFFERENT edge for redundancy. 10 ft
            #      service clearance between the rectangle and any yard
            #      equipment.
            #   8. Bake zone-boundary outlines, equipment labels, setback
            #      dim lines, capacity readout, and CIRCUIT_POWER /
            #      CIRCUIT_COOLING flow polylines.
            #
            # Plan layout (in the inscribed rectangle's local frame; longest
            # edge of `current` lies along U at V=0):
            #
            #   +--- buildable polygon (red) ----------------------+
            #   |  yard: chillers + drycoolers on a SHORT edge     |
            #   |  +--- inscribed rect ---------------------------+ |
            #   |  | perim 4ft                                    | |
            #   |  | +-ELEC-+   +--CRAC perim band------------+   | |
            #   |  | |ATS  |   |     ROW PAIR  (cold inside)  |   | |
            #   |  | |  | aisl | ---- HOT AISLE ----          |   | |
            #   |  | |BATT |   |     ROW PAIR                 |   | |
            #   |  | |  | aisl |        ...                   |   | |
            #   |  | |UPS  |   +-------------------------------+   | |
            #   |  | +-----+                                      | |
            #   |  +----------------------------------------------+ |
            #   |  yard: generators along the TOP long edge        |
            #   +--------------------------------------------------+
            warnings = []
            if current is None:
                print "[GH] no post-offset polygon -- skipping DC layout"
            elif target_racks <= 0 and total_kw <= 0:
                print "[GH] no IT Load (KW) input -- skipping DC layout"
            else:
                # 1+2. Pick the (frame, inscribed rectangle) that maximises
                # building footprint over all candidate orientations (one
                # per longest edge, top-6). Rack rows align to U, so the
                # search also normalises so U is the rectangle's LONGER
                # side. b_frame.v points into the polygon interior.
                #
                # If the search comes up empty (degenerate polygon) fall
                # back to the longest-edge frame + OBB rectangle so the
                # rest of the pipeline still produces a (smaller) layout.
                b_frame, rect = _best_frame_and_rect(current)
                if b_frame is None:
                    b_frame = _build_frame(current)
                b_lu_min, b_lv_min, b_lu_max, b_lv_max = _local_bounds(current, b_frame)
                obb_w = b_lu_max - b_lu_min
                obb_d = b_lv_max - b_lv_min
                if rect is None:
                    print "[GH] could not inscribe rectangle -- using OBB fallback"
                    rect = (b_lu_min, b_lv_min, b_lu_max, b_lv_max)
                r_lu_min_raw, r_lv_min_raw, r_lu_max_raw, r_lv_max_raw = rect

                # 3. Snap rectangle to the 2 ft TILE grid (round inward).
                # Every component placement downstream snaps to this grid,
                # so anchoring the rectangle on tile boundaries makes the
                # whole layout grid-aligned without per-item correction.
                def _snap_in(lo, hi, tile):
                    lo_s = math.ceil(lo / tile) * tile
                    hi_s = math.floor(hi / tile) * tile
                    if hi_s <= lo_s:
                        return lo, hi
                    return lo_s, hi_s
                r_lu_min, r_lu_max = _snap_in(r_lu_min_raw, r_lu_max_raw, TILE_FT)
                r_lv_min, r_lv_max = _snap_in(r_lv_min_raw, r_lv_max_raw, TILE_FT)
                rect_w = r_lu_max - r_lu_min      # along longest axis (U)
                rect_d = r_lv_max - r_lv_min      # perpendicular into polygon (V)
                print "[GH] inscribed rect (tile-snapped): {0:.0f} x {1:.0f} ft  (parcel OBB {2:.0f} x {3:.0f} ft)".format(
                    rect_w, rect_d, obb_w, obb_d,
                )

                # 4. Apply 4 ft perimeter clearance from interior walls.
                inner_lu_min = r_lu_min + PERIMETER_CLEARANCE_FT
                inner_lu_max = r_lu_max - PERIMETER_CLEARANCE_FT
                inner_lv_min = r_lv_min + PERIMETER_CLEARANCE_FT
                inner_lv_max = r_lv_max - PERIMETER_CLEARANCE_FT
                inner_w = max(0.0, inner_lu_max - inner_lu_min)
                inner_d = max(0.0, inner_lv_max - inner_lv_min)

                # 5. Electrical room: full V depth at the U-low end of the
                # inner rectangle. Each "column" is a single
                #     [ ATS_D | aisle | UPS_D | BATTERY_D ]
                # lineup. Order matters: BATTERY sits at the column's
                # U-high end so battery cabinets butt directly against
                # the IT-hall partition (no aisle between BATTERY and
                # the racks), and UPS hugs BATTERY so the DC link stays
                # short. The room replicates the column along U as the
                # n_ats / n_ups / n_battery slider counts grow, so a
                # higher slider produces a visibly wider elec room (and
                # pushes the IT hall + rack zone over) instead of
                # invisibly stacking onto a phantom upper story.
                _single_col_w = ATS_D + ELEC_SERVICE_AISLE_FT + UPS_D + BATTERY_D
                ELEC_COL_AISLE_FT = ELEC_SERVICE_AISLE_FT  # service aisle between columns
                _inner_d_for_elec = max(0.0, inner_lv_max - inner_lv_min)

                def _per_col_v(item_w, gap):
                    pitch = item_w + gap
                    if pitch <= 0 or _inner_d_for_elec <= 0:
                        return 0
                    return max(0, int((_inner_d_for_elec + gap) / pitch))

                _ats_per_col  = _per_col_v(ATS_W,     ATS_GAP)
                _ups_per_col  = _per_col_v(UPS_W,     UPS_GAP)
                _batt_per_col = _per_col_v(BATTERY_W, BATTERY_GAP)

                def _cols_to_hold(n_units, per_col):
                    if per_col <= 0 or n_units <= 0:
                        return 0
                    return (n_units + per_col - 1) // per_col

                # Reserve at least one CRAC band + one rack pair worth of
                # U so the elec room can never push the rack zone off the
                # building entirely.
                _min_rack_zone_u = 2 * (CRAC_D + CRAC_RACK_CLEARANCE_FT) + ROW_PAIR_PITCH_FT + 2 * RACK_D
                _avail_u_for_elec = max(0.0, inner_w - _min_rack_zone_u)
                if _single_col_w + ELEC_COL_AISLE_FT > 0:
                    _max_cols_in_u = max(1, int((_avail_u_for_elec + ELEC_COL_AISLE_FT) / (_single_col_w + ELEC_COL_AISLE_FT)))
                else:
                    _max_cols_in_u = 1
                _cols_target = max(_cols_to_hold(n_ats,     _ats_per_col),
                                   _cols_to_hold(n_ups,     _ups_per_col),
                                   _cols_to_hold(n_battery, _batt_per_col),
                                   1)
                n_elec_columns = max(1, min(_cols_target, _max_cols_in_u))
                elec_room_w = n_elec_columns * _single_col_w + (n_elec_columns - 1) * ELEC_COL_AISLE_FT
                elec_room_w = math.ceil(elec_room_w / TILE_FT) * TILE_FT
                if elec_room_w > inner_w:
                    print "[GH] inner rect ({0:.0f} ft U) too narrow for {1:.0f} ft electrical room ({2} columns) -- shrinking".format(inner_w, elec_room_w, n_elec_columns)
                    elec_room_w = max(0.0, inner_w)
                elec_lu_min = inner_lu_min
                elec_lu_max = inner_lu_min + elec_room_w
                elec_lv_min = inner_lv_min
                elec_lv_max = inner_lv_max

                # 6. IT hall: rest of the inner rectangle. White space.
                it_lu_min = elec_lu_max
                it_lu_max = inner_lu_max
                it_lv_min = inner_lv_min
                it_lv_max = inner_lv_max
                it_hall_w = max(0.0, it_lu_max - it_lu_min)   # along U (now: pair-stack direction)
                it_hall_d = max(0.0, it_lv_max - it_lv_min)   # along V (now: row direction)

                # 7. CRAC perimeter band lines the SHORT WALLS of the IT
                # hall (U_min and U_max). Rack rows now run along V (the
                # parcel's short axis); CRACs face the cold-aisle ENDS,
                # which sit on the U-edges. Band depth (U) = CRAC_D plus
                # CRAC_RACK_CLEARANCE_FT clearance to the first rack row.
                crac_band_d = CRAC_D + CRAC_RACK_CLEARANCE_FT

                # 8. Rack zone: rest of the IT hall after the two CRAC bands.
                rack_zone_lu_min = it_lu_min + crac_band_d
                rack_zone_lu_max = it_lu_max - crac_band_d
                rack_zone_lv_min = it_lv_min
                rack_zone_lv_max = it_lv_max
                # Naming holdover: rack_zone_w spans U (long, now the
                # PAIR-stack direction); rack_zone_d spans V (short, now
                # the ROW direction). Layout fills V first, then U -- so
                # 67 racks on a long-and-narrow parcel land in one short
                # row across V instead of one long row down U.
                rack_zone_w = max(0.0, rack_zone_lu_max - rack_zone_lu_min)
                rack_zone_d = max(0.0, rack_zone_lv_max - rack_zone_lv_min)

                # 9. Number of row pairs across U (ROW_PAIR_PITCH = 16 ft):
                #   k pairs need k*PAIR_INNER + (k-1)*HOT_AISLE <= rack_zone_w
                #   -> k <= (rack_zone_w + HOT_AISLE) / ROW_PAIR_PITCH
                if ROW_PAIR_PITCH_FT > 0 and rack_zone_w >= PAIR_INNER_FT:
                    n_pairs_max = int((rack_zone_w + HOT_AISLE_FT) / ROW_PAIR_PITCH_FT)
                else:
                    n_pairs_max = 0

                # 10. Racks per row along V (short axis), with
                # CROSS_AISLE_FT every MAX_ROW_LEN_FT for egress. Returns
                # 0 if shorter than MIN_RACKS_PER_ROW (airflow-efficiency floor).
                def _racks_per_row(hall_len):
                    if hall_len < MIN_RACKS_PER_ROW * RACK_W:
                        return 0
                    racks = 0
                    u = 0.0
                    seg_racks = 0
                    max_seg = int(MAX_ROW_LEN_FT / RACK_W)
                    while u + RACK_W <= hall_len + 1e-6:
                        if seg_racks >= max_seg:
                            if u + CROSS_AISLE_FT + RACK_W > hall_len + 1e-6:
                                break
                            u += CROSS_AISLE_FT
                            seg_racks = 0
                        racks += 1
                        seg_racks += 1
                        u += RACK_W
                    return racks if racks >= MIN_RACKS_PER_ROW else 0

                racks_per_row = _racks_per_row(rack_zone_d)
                if racks_per_row == 0 and rack_zone_d > 0 and n_pairs_max > 0:
                    msg = "ROW-LIMITED: rack-zone short-axis {0:.0f} ft fits < {1} racks/row; rack rows skipped.".format(
                        rack_zone_d, MIN_RACKS_PER_ROW)
                    print "[GH] " + msg
                    warnings.append(msg)

                # 11. MULTI-STORY. Yard equipment stays at grade
                # (ventilation); rectangle interior (electrical + racks +
                # CRAC) stacks onto every floor.
                story_height = __STORY_HEIGHT_FT__
                n_stories = max(1, int(height / story_height))

                def _greedy_fill(total, n, cap_per_floor):
                    """Greedy floor distribution: pack floor 0 to its
                    capacity FIRST, overflow to floor 1, then floor 2, ...
                    Stops when TOTAL is exhausted or all n floors are full
                    (excess silently dropped -- caller is expected to have
                    capped TOTAL against total capacity already).

                    User intent: never start placing on level 2 until level
                    1 is at maximum sq-footage utilisation -- a real DC
                    fills the ground floor first, only spills up when forced.
                    """
                    if n <= 0 or total <= 0 or cap_per_floor <= 0:
                        return [0] * max(1, n)
                    out = []
                    rem = total
                    for f in range(n):
                        x = min(cap_per_floor, rem)
                        out.append(x)
                        rem -= x
                    return out

                racks_per_floor = n_pairs_max * 2 * racks_per_row
                max_racks_fit = racks_per_floor * n_stories
                requested_racks = target_racks
                if requested_racks > max_racks_fit:
                    target_racks = max_racks_fit
                    msg = "AREA-LIMITED: requested {0} racks ({1:.0f} kW IT) but only {2} fit ({3:.0f} kW max IT load over {4} stor{5}).".format(
                        requested_racks, total_kw, max_racks_fit, max_racks_fit * KW_PER_RACK,
                        n_stories, "ies" if n_stories != 1 else "y",
                    )
                    print "[GH] " + msg
                    warnings.append(msg)

                # 12. Recompute auto counts at the FITTED IT load so yard
                # equipment isn't oversized when target_racks got capped.
                # User-pinned overrides remain in effect via _apply_override.
                fitted_kw = target_racks * KW_PER_RACK if target_racks > 0 else total_kw
                if fitted_kw < total_kw and target_racks > 0:
                    print "[GH] resizing equipment to fitted load ({0:.0f} kW vs requested {1:.0f} kW)".format(fitted_kw, total_kw)
                    n_gen_a = _ceil_or_zero(fitted_kw / GEN_KW_PER_UNIT) + 1
                    n_chiller_a = _ceil_or_zero(fitted_kw * COOLING_FRACTION / CHILLER_KW_PER_UNIT) + 1
                    n_drycooler_a = n_chiller_a
                    n_ups_a = _ceil_or_zero(fitted_kw / UPS_KW_PER_UNIT) + 1
                    n_battery_a = n_ups_a * BATTERY_PER_UPS
                    n_ats_a = _ceil_or_zero(fitted_kw / ATS_KW_PER_UNIT)
                    n_crac_a = _ceil_or_zero(fitted_kw / CRAC_KW_PER_UNIT)
                    n_gen       = _apply_override(n_gen_a,       NG)
                    n_chiller   = _apply_override(n_chiller_a,   NC)
                    n_drycooler = _apply_override(n_drycooler_a, ND)
                    n_ups       = _apply_override(n_ups_a,       NU)
                    n_battery   = _apply_override(n_battery_a,   NB)
                    n_ats       = _apply_override(n_ats_a,       NA)
                    n_crac      = _apply_override(n_crac_a,      NR)

                # CRAC capacity check: max CRACs that fit on the perimeter
                # band = floor(it_hall_d / CRAC_W) per strip * 2 strips per
                # floor * n_stories. Strip now runs along V (short axis)
                # because the bands moved to the IT hall's short walls.
                # Cap requested n_crac before splitting.
                max_cracs_per_strip = int(it_hall_d / CRAC_W) if CRAC_W > 0 and it_hall_d >= CRAC_W else 0
                max_cracs_fit = max_cracs_per_strip * 2 * n_stories
                if n_crac > max_cracs_fit:
                    msg = "CRAC-LIMITED: requested {0}, only {1} fit on IT-hall perimeter band; capping.".format(
                        n_crac, max_cracs_fit)
                    print "[GH] " + msg
                    warnings.append(msg)
                    n_crac = max_cracs_fit

                # Per-floor placement capacity for each elec-room row +
                # CRAC band. Mirrors the formulas used downstream in
                # _place_row_along_v (capacity = floor((span+gap)/pitch))
                # and the CRAC strip logic (max_cracs_per_strip * 2 strips).
                # Computing them here lets _greedy_fill saturate floor 0
                # before spilling onto floor 1, instead of ceil-dividing
                # evenly across floors.
                _elec_span = max(0.0, elec_lv_max - elec_lv_min)
                def _row_cap(item_w, gap):
                    pitch = item_w + gap
                    if pitch <= 0 or _elec_span <= 0:
                        return 0
                    return int((_elec_span + gap) / pitch)
                # Per-floor capacity is per-column-row capacity times the
                # number of elec-room columns -- adding columns saturates
                # floor 0 before any spill onto floor 1.
                ats_cap_pf  = n_elec_columns * _row_cap(ATS_W,     ATS_GAP)
                batt_cap_pf = n_elec_columns * _row_cap(BATTERY_W, BATTERY_GAP)
                ups_cap_pf  = n_elec_columns * _row_cap(UPS_W,     UPS_GAP)
                crac_cap_pf = max_cracs_per_strip * 2

                ups_pf  = _greedy_fill(n_ups,     n_stories, ups_cap_pf)
                batt_pf = _greedy_fill(n_battery, n_stories, batt_cap_pf)
                ats_pf  = _greedy_fill(n_ats,     n_stories, ats_cap_pf)
                crac_pf = _greedy_fill(n_crac,    n_stories, crac_cap_pf)
                rack_floors = _greedy_fill(target_racks, n_stories, racks_per_floor)

                print "[GH] rect={0:.0f}x{1:.0f} ft  ELEC={2:.0f} ft wide  IT hall={3:.0f}x{4:.0f} ft  rack zone={5:.0f}x{6:.0f} ft  pairs={7}  racks/row={8}  stories={9}  racks/floor={10}  fitted IT={11:.0f} kW".format(
                    rect_w, rect_d, elec_room_w, it_hall_w, it_hall_d,
                    rack_zone_w, rack_zone_d,
                    n_pairs_max, racks_per_row, n_stories, racks_per_floor, fitted_kw,
                )

                # 13. Bake zone-boundary outlines: building rect, electrical
                # room, IT hall, CRAC perimeter bands. Drawn at z=0.5 so they
                # sit above the parcel plane in plan view but never collide
                # with the equipment Z=0..H bakes.
                zb_idx = dc_layer_idx.get("ZONE_BOUNDARY", -1)
                def _bake_local_rect(layer_idx, lu0, lv0, lu1, lv1, z=0.5):
                    if layer_idx < 0:
                        return
                    pts = [
                        _frame_l2w(lu0, lv0, b_frame),
                        _frame_l2w(lu1, lv0, b_frame),
                        _frame_l2w(lu1, lv1, b_frame),
                        _frame_l2w(lu0, lv1, b_frame),
                        _frame_l2w(lu0, lv0, b_frame),
                    ]
                    _bake_polyline_world(layer_idx, pts, z=z)
                _bake_local_rect(zb_idx, r_lu_min, r_lv_min, r_lu_max, r_lv_max)
                if elec_room_w > 0:
                    _bake_local_rect(zb_idx, elec_lu_min, elec_lv_min, elec_lu_max, elec_lv_max)
                if it_hall_w > 0 and it_hall_d > 0:
                    _bake_local_rect(zb_idx, it_lu_min, it_lv_min, it_lu_max, it_lv_max)
                    # CRAC band rectangles now run along the IT hall's
                    # SHORT walls (U_min and U_max) since rack rows go
                    # along V.
                    _bake_local_rect(zb_idx, it_lu_min, it_lv_min, it_lu_min + crac_band_d, it_lv_max)
                    _bake_local_rect(zb_idx, it_lu_max - crac_band_d, it_lv_min, it_lu_max, it_lv_max)

                # 14. Cluster centroids -- used for circuits + labels.
                # `centers` holds (wx, wy) world coords (consumed by the
                # CIRCUIT_POWER / CIRCUIT_COOLING polylines which expect
                # plain XY pairs). `centers_local` mirrors the same anchors
                # in (lu, lv) building-frame coords -- the label-baking
                # loop checks (lu, lv) against the inscribed rectangle so
                # labels for INTERIOR clusters (ATS / UPS / BATTERY / CRAC
                # / RACK) can be shifted outside the footprint, never
                # overlapping the red volume in plan view.
                centers = {}
                centers_local = {}
                def _record_center(name, lu_c, lv_c):
                    wx, wy = _frame_l2w(lu_c, lv_c, b_frame)
                    centers[name] = (wx, wy)
                    centers_local[name] = (lu_c, lv_c)

                # 15. Helper: place a single ROW of `n_target` identical
                # units along V at fixed U_center. Each unit's V-center
                # snaps to a tile center; clipped to [v_lo, v_hi]. item_w
                # is the unit's dimension along the row (V); item_d is the
                # depth perpendicular to the row (U). Bake-box swap
                # converts (item_w, item_d) -> local-frame (U-extent,
                # V-extent) = (item_d, item_w).
                def _place_row_along_v(n_target, u_center, v_lo, v_hi,
                                       item_w, item_d, item_h, gap, layer_idx, z0):
                    if n_target <= 0 or layer_idx < 0:
                        return 0
                    span = v_hi - v_lo
                    pitch = item_w + gap
                    if pitch <= 0:
                        return 0
                    capacity = int((span + gap) / pitch)
                    if capacity < 1:
                        return 0
                    n_place = min(n_target, capacity)
                    placed_n = 0
                    for i in range(n_place):
                        v_c = v_lo + i * pitch + item_w / 2.0
                        # snap V-center to nearest tile center.
                        v_c = round((v_c - TILE_FT / 2.0) / TILE_FT) * TILE_FT + TILE_FT / 2.0
                        if v_c - item_w / 2.0 < v_lo - 1e-6 or v_c + item_w / 2.0 > v_hi + 1e-6:
                            continue
                        _bake_box(layer_idx, u_center, v_c,
                                  item_d, item_w, item_h, frame=b_frame, z0=z0)
                        placed_n += 1
                    return placed_n

                # 16. ---- ELECTRICAL ROOM + CRAC BAND + RACK ROWS, per floor.
                a_total = u_total = b_total = 0
                placed = 0
                crac_total = 0

                # Multi-column elec room. Each column is a full
                # [ATS | aisle | UPS | BATTERY] lineup; column c starts
                # at U = elec_lu_min + c * (col_w + col_aisle). ATS hugs
                # the column's U-low (outer) wall, BATTERY hugs its
                # U-high wall (= IT hall partition, no aisle), and UPS
                # sits adjacent to BATTERY for a short DC link. The
                # per-floor split walks columns left-to-right so a
                # partial elec slider only populates the leftmost N
                # columns.
                elec_v_center = (elec_lv_min + elec_lv_max) / 2.0

                def _col_u_origin(c):
                    return elec_lu_min + c * (_single_col_w + ELEC_COL_AISLE_FT)
                def _col_ats_u(c):
                    return _col_u_origin(c) + ATS_D / 2.0
                def _col_ups_u(c):
                    return _col_u_origin(c) + ATS_D + ELEC_SERVICE_AISLE_FT + UPS_D / 2.0
                def _col_batt_u(c):
                    return _col_u_origin(c) + _single_col_w - BATTERY_D / 2.0

                def _split_per_col(n_floor, per_col):
                    out = []
                    rem = n_floor
                    for _c in range(n_elec_columns):
                        x = min(per_col, rem) if per_col > 0 else 0
                        out.append(x)
                        rem -= x
                    return out

                for f in range(n_stories):
                    z0 = f * story_height
                    n_ats_f  = ats_pf[f]  if f < len(ats_pf)  else 0
                    n_ups_f  = ups_pf[f]  if f < len(ups_pf)  else 0
                    n_batt_f = batt_pf[f] if f < len(batt_pf) else 0

                    ats_per_col_f  = _split_per_col(n_ats_f,  _ats_per_col)
                    ups_per_col_f  = _split_per_col(n_ups_f,  _ups_per_col)
                    batt_per_col_f = _split_per_col(n_batt_f, _batt_per_col)

                    for c in range(n_elec_columns):
                        if ats_per_col_f[c] > 0:
                            a_baked = _place_row_along_v(
                                ats_per_col_f[c], _col_ats_u(c), elec_lv_min, elec_lv_max,
                                ATS_W, ATS_D, ATS_H, ATS_GAP,
                                dc_layer_idx.get("ATS", -1), z0,
                            )
                            a_total += a_baked
                            if f == 0 and c == 0 and a_baked > 0:
                                _record_center("ATS", _col_ats_u(0), elec_v_center)
                        if batt_per_col_f[c] > 0:
                            b_baked = _place_row_along_v(
                                batt_per_col_f[c], _col_batt_u(c), elec_lv_min, elec_lv_max,
                                BATTERY_W, BATTERY_D, BATTERY_H, BATTERY_GAP,
                                dc_layer_idx.get("BATTERY", -1), z0,
                            )
                            b_total += b_baked
                            if f == 0 and c == 0 and b_baked > 0:
                                _record_center("BATTERY", _col_batt_u(0), elec_v_center)
                        if ups_per_col_f[c] > 0:
                            u_baked = _place_row_along_v(
                                ups_per_col_f[c], _col_ups_u(c), elec_lv_min, elec_lv_max,
                                UPS_W, UPS_D, UPS_H, UPS_GAP,
                                dc_layer_idx.get("UPS", -1), z0,
                            )
                            u_total += u_baked
                            if f == 0 and c == 0 and u_baked > 0:
                                _record_center("UPS", _col_ups_u(0), elec_v_center)

                    # CRAC perimeter band: TWO strips along the SHORT
                    # walls of the IT hall (U_min and U_max). CRAC center
                    # U = inset CRAC_D/2 from wall. Distribute evenly
                    # along V; snap to tile centers. The CRAC box is
                    # rotated 90deg vs the legacy long-wall layout so its
                    # depth points into the rack zone.
                    n_crac_f = crac_pf[f] if f < len(crac_pf) else 0
                    if n_crac_f > 0 and it_hall_d >= CRAC_W:
                        crac_left_u  = it_lu_min + CRAC_D / 2.0
                        crac_right_u = it_lu_max - CRAC_D / 2.0
                        n_right = n_crac_f // 2
                        n_left  = n_crac_f - n_right
                        for strip_u, n_strip in ((crac_left_u, n_left), (crac_right_u, n_right)):
                            if n_strip <= 0:
                                continue
                            pitch = it_hall_d / float(n_strip)
                            for i in range(n_strip):
                                cv = it_lv_min + (i + 0.5) * pitch
                                cv = round((cv - TILE_FT / 2.0) / TILE_FT) * TILE_FT + TILE_FT / 2.0
                                if cv - CRAC_W / 2.0 < it_lv_min - 1e-6 or cv + CRAC_W / 2.0 > it_lv_max + 1e-6:
                                    continue
                                _bake_box(dc_layer_idx.get("CRAC", -1), strip_u, cv,
                                          CRAC_D, CRAC_W, CRAC_H, frame=b_frame, z0=z0)
                                crac_total += 1

                    # Rack rows: hot/cold aisle row pairs along U (long
                    # axis); each row runs along V (short axis), so the
                    # layout fills the parcel's short direction first
                    # before adding pairs along the long direction.
                    #   pair k: pair_lu0 = rack_zone_lu_min + k * 16 ft
                    #     back row  U-center: pair_lu0 + RACK_D/2
                    #     cold aisle (4 ft) in middle
                    #     front row U-center: pair_lu0 + RACK_D + COLD + RACK_D/2
                    #   Adjacent pairs share a HOT aisle (4 ft).
                    floor_target = rack_floors[f] if f < len(rack_floors) else 0
                    floor_placed = 0
                    if floor_target > 0 and racks_per_row > 0:
                        max_seg = int(MAX_ROW_LEN_FT / RACK_W)
                        for pair_idx in range(n_pairs_max):
                            if floor_placed >= floor_target:
                                break
                            pair_lu0 = rack_zone_lu_min + pair_idx * ROW_PAIR_PITCH_FT
                            for row_idx in range(2):
                                if floor_placed >= floor_target:
                                    break
                                if row_idx == 0:
                                    row_lu = pair_lu0 + RACK_D / 2.0
                                else:
                                    row_lu = pair_lu0 + RACK_D + COLD_AISLE_FT + RACK_D / 2.0
                                v_cursor = rack_zone_lv_min
                                seg_racks = 0
                                row_placed = 0
                                while (v_cursor + RACK_W <= rack_zone_lv_max + 1e-6
                                       and floor_placed < floor_target
                                       and row_placed < racks_per_row):
                                    if seg_racks >= max_seg:
                                        if v_cursor + CROSS_AISLE_FT + RACK_W > rack_zone_lv_max + 1e-6:
                                            break
                                        v_cursor += CROSS_AISLE_FT
                                        seg_racks = 0
                                    rack_lv_c = v_cursor + RACK_W / 2.0
                                    # Rack rotated 90deg: depth (4 ft) along U,
                                    # width (2 ft) along V, so racks line up
                                    # shoulder-to-shoulder along the short axis.
                                    _bake_box(rack_layer_idx, row_lu, rack_lv_c,
                                              RACK_D, RACK_W, RACK_H,
                                              frame=b_frame, z0=z0)
                                    v_cursor += RACK_W
                                    seg_racks += 1
                                    row_placed += 1
                                    floor_placed += 1
                                    placed += 1

                if racks_per_floor > 0 and target_racks > 0:
                    _record_center("RACK",
                                   (rack_zone_lu_min + rack_zone_lu_max) / 2.0,
                                   (rack_zone_lv_min + rack_zone_lv_max) / 2.0)
                if crac_total > 0:
                    _record_center("CRAC",
                                   (it_lu_min + it_lu_max) / 2.0,
                                   (it_lv_min + it_lv_max) / 2.0)
                print "[GH] per-floor stack: ATS={0}/{1} UPS={2}/{3} BATT={4}/{5} CRAC={6}/{7} RACKS={8}/{9} ({10} pairs x {11} racks/row x {12} stories)".format(
                    a_total, n_ats, u_total, n_ups, b_total, n_battery, crac_total, n_crac,
                    placed, target_racks, n_pairs_max, racks_per_row, n_stories,
                )

                # 17. ---- YARD: gens single-row on one long edge of the
                # rectangle; chillers on a DIFFERENT edge (redundancy
                # separation per WP 144); dry coolers paired 1:1 ADJACENT
                # to chillers. 10 ft service clearance from rectangle wall.
                # The polygon's chosen edge is at V=0 (rect's bottom), so
                # only TOP can be a 'long' yard strip; LEFT/RIGHT are
                # 'short' yard strips along the rectangle's short edges.
                # Single yard strip on the V+ ("interior") side keeps yard
                # equipment visually behind the building (above the
                # imported linework in plan view), which matches the user
                # mental model after a previous BOTTOM-strip experiment
                # was sending gens to the wrong side of the parcel.
                top_d   = max(0.0, b_lv_max - (r_lv_max + MECH_YARD_CLEARANCE_FT))
                left_d  = max(0.0, (r_lu_min - MECH_YARD_CLEARANCE_FT) - b_lu_min)
                right_d = max(0.0, b_lu_max - (r_lu_max + MECH_YARD_CLEARANCE_FT))
                yard_strips = []
                if top_d > 0:
                    yard_strips.append({
                        'name': 'TOP', 'edge': 'long',
                        'origin_lu': r_lu_min,
                        'origin_lv': r_lv_max + MECH_YARD_CLEARANCE_FT,
                        'length':  rect_w, 'depth': top_d,
                        'depth_dir': 'v+', 'used_depth': 0.0,
                    })
                if left_d > 0:
                    yard_strips.append({
                        'name': 'LEFT', 'edge': 'short',
                        'origin_lu': r_lu_min - MECH_YARD_CLEARANCE_FT,
                        'origin_lv': r_lv_min,
                        'length':  rect_d, 'depth': left_d,
                        'depth_dir': 'u-', 'used_depth': 0.0,
                    })
                if right_d > 0:
                    yard_strips.append({
                        'name': 'RIGHT', 'edge': 'short',
                        'origin_lu': r_lu_max + MECH_YARD_CLEARANCE_FT,
                        'origin_lv': r_lv_min,
                        'length':  rect_d, 'depth': right_d,
                        'depth_dir': 'u+', 'used_depth': 0.0,
                    })

                # Rooftop strip: the entire building rectangle, at
                # z = height + parapet clearance. Used for chillers + dry
                # coolers (HVAC) so they sit on the red volume's roof
                # rather than crowd the yard. Plenty of plan area: rect_w
                # along U (one row direction) and rect_d of stacking
                # depth in V. Gens stay at grade for fuel/safety reasons.
                ROOF_CLEARANCE_FT = 2.0
                roof_strip = {
                    'name': 'ROOF', 'edge': 'long',
                    'origin_lu': r_lu_min,
                    'origin_lv': r_lv_min,
                    'length':  rect_w, 'depth': rect_d,
                    'depth_dir': 'v+', 'used_depth': 0.0,
                    'z0': height + ROOF_CLEARANCE_FT,
                }

                def _place_yard_row(strip, n, item_w, item_d, item_h, gap, layer_idx, name):
                    """Place a single row of `n` items along `strip`'s edge.
                    Items line up parallel to the edge (W along the edge,
                    D into the yard). Each unit's along-edge center snaps
                    to a tile center. strip['used_depth'] tracks how far
                    into the yard previous rows extend, so a follow-up call
                    on the same strip stacks one row deeper."""
                    if strip is None or n <= 0 or layer_idx < 0:
                        return 0
                    pitch = item_w + gap
                    if pitch <= 0:
                        return 0
                    cap = max(0, int((strip['length'] + gap) / pitch))
                    if cap <= 0:
                        return 0
                    n_place = min(n, cap)
                    lead_gap = gap if strip['used_depth'] > 0 else 0.0
                    rem_d = strip['depth'] - strip['used_depth'] - lead_gap
                    if rem_d < item_d:
                        return 0
                    # Bake-box extents in (U-dir, V-dir) depend on strip
                    # orientation: TOP strip runs along U (W in U, D in V);
                    # LEFT/RIGHT run along V (W in V, D in U) -- swapped.
                    if strip['depth_dir'] == 'v+':
                        bake_w, bake_d = item_w, item_d
                    else:
                        bake_w, bake_d = item_d, item_w
                    placed_n = 0
                    centers_acc = []
                    into = strip['used_depth'] + lead_gap + item_d / 2.0
                    for i in range(n_place):
                        along = i * pitch + item_w / 2.0
                        # snap along-edge center to tile center
                        along = round((along - TILE_FT / 2.0) / TILE_FT) * TILE_FT + TILE_FT / 2.0
                        if strip['depth_dir'] == 'v+':
                            cu = strip['origin_lu'] + along
                            cv = strip['origin_lv'] + into
                        elif strip['depth_dir'] == 'u-':
                            cu = strip['origin_lu'] - into
                            cv = strip['origin_lv'] + along
                        else:  # 'u+'
                            cu = strip['origin_lu'] + into
                            cv = strip['origin_lv'] + along
                        # Ground-level (yard) equipment must sit inside the
                        # building polygon's irregular regions; rooftop
                        # equipment (z0 > 0) sits on top of the inscribed
                        # rectangle by construction so it doesn't need the
                        # strict ground-polygon containment check, which
                        # rejects boxes whose corners touch the polygon
                        # boundary on near-rectangular parcels.
                        z_off = strip.get('z0', 0.0)
                        if z_off <= 0 and not _box_inside_curve(
                                current, cu, cv, bake_w, bake_d, tol, frame=b_frame):
                            continue
                        _bake_box(layer_idx, cu, cv, bake_w, bake_d, item_h,
                                  frame=b_frame, z0=z_off)
                        centers_acc.append((cu, cv))
                        placed_n += 1
                    if placed_n > 0:
                        strip['used_depth'] += lead_gap + item_d
                        if name not in centers and centers_acc:
                            cx_c = sum(p[0] for p in centers_acc) / len(centers_acc)
                            cy_c = sum(p[1] for p in centers_acc) / len(centers_acc)
                            _record_center(name, cx_c, cy_c)
                    return placed_n

                long_strips  = sorted([s for s in yard_strips if s['edge'] == 'long'  and s['depth'] > 0],
                                      key=lambda s: -s['length'])
                short_strips = sorted([s for s in yard_strips if s['edge'] == 'short' and s['depth'] > 0],
                                      key=lambda s: -s['length'])

                # Multi-row, multi-strip placement: stack rows deeper into
                # the same strip until either depth runs out or all units
                # are placed; if the strip exhausts, cascade to the next
                # candidate strip. This is what makes the GEN / CHILLER /
                # DRYCOOLER sliders visibly track their override values
                # past single-row capacity.
                def _place_yard_rows(strips_pool, n, item_w, item_d, item_h, gap,
                                     layer_idx, name):
                    if n <= 0 or layer_idx < 0 or not strips_pool:
                        return 0, None
                    placed_total = 0
                    last_strip = None
                    for strip in strips_pool:
                        if placed_total >= n:
                            break
                        if strip is None or strip['depth'] <= 0:
                            continue
                        # Stack rows on this strip until a row places nothing
                        # (depth exhausted) or we've placed enough.
                        while placed_total < n:
                            placed_this = _place_yard_row(
                                strip, n - placed_total,
                                item_w, item_d, item_h, gap,
                                layer_idx, name,
                            )
                            if placed_this <= 0:
                                break
                            placed_total += placed_this
                            last_strip = strip
                    return placed_total, last_strip

                # Generators: prefer the longest 'long' strip, fall back to
                # short strips if none. May cascade across additional strips
                # only if the gen count exceeds a single strip's row x depth
                # capacity -- gens stay grouped when possible.
                gen_pool = list(long_strips) + list(short_strips)
                gen_baked, gen_strip = _place_yard_rows(
                    gen_pool, n_gen, GEN_W, GEN_D, GEN_H, GEN_GAP,
                    dc_layer_idx.get("GEN", -1), "GEN",
                )
                gen_overflow = max(0, n_gen - gen_baked)

                # Chillers + dry coolers go on the ROOF (above the red
                # volume). Falls back to yard strips on a different edge
                # from gens only if the roof can't hold them (very rare
                # given the building rectangle's plan area). Gens stay
                # in the yard regardless.
                gen_name = gen_strip['name'] if gen_strip else None
                yard_overflow_pool = [s for s in (short_strips + long_strips)
                                      if s['name'] != gen_name and s['depth'] > 0]
                chill_pool = [roof_strip] + yard_overflow_pool
                chill_baked, chill_strip = _place_yard_rows(
                    chill_pool, n_chiller, CHILLER_W, CHILLER_D, CHILLER_H, CHILLER_GAP,
                    dc_layer_idx.get("CHILLER", -1), "CHILLER",
                )
                chill_overflow = max(0, n_chiller - chill_baked)

                # Dry coolers: paired 1:1 with chillers, in the SAME pool
                # so they share the rooftop with chillers (stacked
                # deeper via roof_strip's used_depth, advanced by the
                # chiller rows above).
                dry_pool = chill_pool if chill_pool else ([chill_strip] if chill_strip else [])
                dry_baked, dry_strip = _place_yard_rows(
                    dry_pool, n_drycooler, DRYCOOLER_W, DRYCOOLER_D, DRYCOOLER_H,
                    DRYCOOLER_GAP, dc_layer_idx.get("DRYCOOLER", -1), "DRYCOOLER",
                )
                dry_overflow = max(0, n_drycooler - dry_baked)

                print "[GH] yard strips: TOP={0:.0f}ft RIGHT={1:.0f}ft LEFT={2:.0f}ft  | GEN({3}) {4}/{5}  CHILLER({6}) {7}/{8}  DRYCOOLER {9}/{10}".format(
                    top_d, right_d, left_d,
                    gen_strip['name'] if gen_strip else "-",
                    gen_baked, n_gen,
                    chill_strip['name'] if chill_strip else "-",
                    chill_baked, n_chiller, dry_baked, n_drycooler,
                )
                for fam, want, got, over in (
                        ("GEN", n_gen, gen_baked, gen_overflow),
                        ("CHILLER", n_chiller, chill_baked, chill_overflow),
                        ("DRYCOOLER", n_drycooler, dry_baked, dry_overflow)):
                    if over > 0:
                        msg = "YARD-LIMITED {0}: requested {1}, fit {2} (over by {3}).".format(fam, want, got, over)
                        print "[GH] " + msg
                        warnings.append(msg)
                if gen_overflow > 0:   n_gen = gen_baked
                if chill_overflow > 0: n_chiller = chill_baked
                if dry_overflow > 0:   n_drycooler = dry_baked

                # 18. Equipment labels: SUPPRESSED. Cluster TextDots
                # ("3 x Chiller", etc.) used to bake to the LABELS layer
                # above each centroid. Removed per user request -- the
                # readout panel + analyzer report already carry counts,
                # and the dots cluttered the canvas. Layer kept (empty)
                # so existing scripts referencing it don't break.

                # 19. Setback dimension lines: one per parcel edge.
                sb_idx = dc_layer_idx.get("SETBACK_DIM", -1)
                if sb_idx >= 0 and offset_lines and poly is not None:
                    for i in range(n_edges):
                        ol = offset_lines[i]
                        if ol is None: continue
                        v0 = poly[i]
                        v1 = poly[i + 1]
                        mid_x = (v0.X + v1.X) / 2.0
                        mid_y = (v0.Y + v1.Y) / 2.0
                        ex = v1.X - v0.X
                        ey = v1.Y - v0.Y
                        elen = (ex * ex + ey * ey) ** 0.5
                        if elen < 1e-9: continue
                        ex /= elen; ey /= elen
                        if ccw:
                            nx, ny = -ey, ex
                        else:
                            nx, ny = ey, -ex
                        d_offset = offsets[i] if i < len(offsets) else 0.0
                        inner_x = mid_x + nx * d_offset
                        inner_y = mid_y + ny * d_offset
                        _bake_polyline_world(sb_idx, [(mid_x, mid_y), (inner_x, inner_y)], z=0.5)
                        _bake_textdot(
                            sb_idx,
                            (mid_x + inner_x) / 2.0,
                            (mid_y + inner_y) / 2.0,
                            1.0,
                            "Edge {0}: {1:.0f} ft".format(i + 1, d_offset),
                        )

                # 20. Site report. Defensive try/except so any failure
                # in here NEVER blocks the volume extrusion below; the
                # actual error message becomes the panel's content so we
                # can debug it visually.
                try:
                    cur_mp = rg.AreaMassProperties.Compute(current)
                    bld_area = cur_mp.Area if cur_mp is not None else 0.0
                    parcel_area = best_area if best_area > 0 else 0.0
                    coverage_pct = (bld_area / parcel_area * 100.0) if parcel_area > 0 else 0.0
                    gfa = bld_area * n_stories
                    far = gfa / parcel_area if parcel_area > 0 else 0.0

                    UTILIZATION = 0.70
                    WUE_LPERKWH = 1.0
                    INDUSTRIAL_RATE_USDPKWH = 0.08
                    fitted_facility_kw = fitted_kw * PUE
                    annual_mwh = fitted_facility_kw * 8760.0 * UTILIZATION / 1000.0
                    annual_cost_usd = annual_mwh * 1000.0 * INDUSTRIAL_RATE_USDPKWH
                    cool_kw_demand = fitted_kw * COOLING_FRACTION
                    annual_water_m3 = fitted_kw * 8760.0 * UTILIZATION * WUE_LPERKWH / 1000.0
                    gpd_water = annual_water_m3 * 264.172 / 365.0

                    max_kw_at_height = max_racks_fit * KW_PER_RACK
                    max_facility_kw = max_kw_at_height * PUE
                    max_annual_mwh = max_facility_kw * 8760.0 * UTILIZATION / 1000.0
                    max_annual_water = max_kw_at_height * 8760.0 * UTILIZATION * WUE_LPERKWH / 1000.0
                    max_annual_cost = max_annual_mwh * 1000.0 * INDUSTRIAL_RATE_USDPKWH

                    rl = []
                    rl.append("SITE REPORT  --  WP 144 LAYOUT")
                    rl.append("=" * 56)
                    rl.append("POSITIONING")
                    rl.append("  Parcel area:        {0:>10,.0f} ft^2  ({1:.2f} ac)".format(parcel_area, parcel_area / 43560.0))
                    rl.append("  Building footprint: {0:>10,.0f} ft^2  ({1:.0f}% coverage)".format(bld_area, coverage_pct))
                    rl.append("  Building rectangle: {0:>10.0f} x {1:.0f} ft".format(rect_w, rect_d))
                    rl.append("  Building height:    {0:>10.0f} ft  ({1} stor{2} @ {3:.0f} ft)".format(
                        height, n_stories, "ies" if n_stories != 1 else "y", story_height))
                    rl.append("  Gross floor area:   {0:>10,.0f} ft^2  (FAR {1:.2f})".format(gfa, far))
                    rl.append("  Row pairs / racks-per-row / pitch: {0} / {1} / {2:.0f} ft".format(
                        n_pairs_max, racks_per_row, ROW_PAIR_PITCH_FT))
                    rl.append("=" * 56)
                    rl.append("LOAD")
                    rl.append("  Target IT load:     {0:>10,.0f} kW  ({1} racks)".format(total_kw, requested_racks))
                    rl.append("  Fitted IT load:     {0:>10,.0f} kW  ({1} racks)".format(fitted_kw, target_racks))
                    rl.append("  Parcel ceiling:     {0:>10,.0f} kW".format(max_kw_at_height))
                    rl.append("=" * 56)
                    rl.append("EQUIPMENT (total across building)")
                    rl.append("  Server racks: {0:>4}".format(target_racks))
                    rl.append("  Generators:   {0:>4}  ({1:.1f} MW)".format(n_gen, n_gen * GEN_KW_PER_UNIT / 1000.0))
                    rl.append("  ATS lineups:  {0:>4}".format(n_ats))
                    rl.append("  UPS modules:  {0:>4}  ({1:.1f} MW)".format(n_ups, n_ups * UPS_KW_PER_UNIT / 1000.0))
                    rl.append("  Batteries:    {0:>4}".format(n_battery))
                    rl.append("  Chillers:     {0:>4}  ({1:.1f} MW cooling, on roof)".format(n_chiller, n_chiller * CHILLER_KW_PER_UNIT / 1000.0))
                    rl.append("  Dry coolers:  {0:>4}  (on roof)".format(n_drycooler))
                    rl.append("  CRAC units:   {0:>4}".format(n_crac))
                    rl.append("=" * 56)
                    rl.append("ENERGY & FACILITY DRAW")
                    rl.append("  IT load (fitted):   {0:>10,.0f} kW".format(fitted_kw))
                    rl.append("  PUE:                {0:>10.2f}".format(PUE))
                    rl.append("  Total facility:     {0:>10,.0f} kW".format(fitted_facility_kw))
                    rl.append("  Utilization:        {0:>10.0f} %".format(UTILIZATION * 100.0))
                    rl.append("  Annual energy:      {0:>10,.0f} MWh/yr".format(annual_mwh))
                    rl.append("  Industrial rate:    {0:>10.3f} $/kWh  (typical)".format(INDUSTRIAL_RATE_USDPKWH))
                    rl.append("  Annual energy cost: ${0:>13,.0f}  USD/yr".format(annual_cost_usd))
                    rl.append("=" * 56)
                    rl.append("COOLING & WATER  (climate-adjusted estimate)")
                    rl.append("  Cooling load:       {0:>10,.0f} kW  ({1:.0f}% of IT)".format(
                        cool_kw_demand, COOLING_FRACTION * 100.0))
                    rl.append("  WUE assumption:     {0:>10.2f}  L/kWh-IT".format(WUE_LPERKWH))
                    rl.append("  Annual water:       {0:>10,.0f} m^3/yr  ({1:,.0f} gal/day)".format(
                        annual_water_m3, gpd_water))
                    rl.append("  Note: site-dependent. Temperate/hybrid: 1.0-1.2;")
                    rl.append("        hot/dry (Phoenix-class): 1.8-2.5; closed-")
                    rl.append("        loop dry-cooling: 0.05-0.2.")

                    # ---- GRID / CAPEX / OPEX / REVENUE / PAYBACK ----
                    # Substituted from the analyzer report (None when
                    # the inbox JSON didn't carry those fields).
                    SUB_NAME = __SUBSTATION_NAME__
                    SUB_KV = __SUBSTATION_KV__
                    SUB_DIST_MI = __SUBSTATION_DISTANCE_MI__
                    RATE_OVERRIDE = __INDUSTRIAL_RATE_USDPKWH__
                    rate_used = RATE_OVERRIDE if RATE_OVERRIDE is not None else INDUSTRIAL_RATE_USDPKWH

                    # Industry-typical unit prices (USD). Swap with real
                    # quotes per project. Sources: Turner Construction
                    # cost index, JLL DC market reports, EIA Form 412.
                    PRICE_BUILDING_PER_SF = 350.0
                    PRICE_SITE_PER_AC = 250000.0
                    PRICE_RACK = 5000.0
                    PRICE_GEN_EACH = 450000.0          # SDMO 1.5 MW class
                    PRICE_UPS_EACH = 250000.0          # APC PX 500 kW
                    PRICE_BATTERY_EACH = 25000.0
                    PRICE_ATS_EACH = 80000.0
                    PRICE_CHILLER_EACH = 300000.0      # Emerson 750 kW
                    PRICE_DRYCOOLER_EACH = 80000.0
                    PRICE_CRAC_EACH = 40000.0
                    PRICE_INTERCONNECT_PER_MI = 1000000.0
                    PRICE_SUBSTATION_UPGRADE = 5000000.0
                    SOFT_COSTS_FRACTION = 0.15
                    WATER_RATE_USD_PER_M3 = 5.0
                    MAINT_FRACTION = 0.04
                    PROP_TAX_FRACTION = 0.012
                    INSURANCE_FRACTION = 0.005
                    STAFF_COUNT = 5
                    STAFF_FTE_USD = 120000.0
                    WHOLESALE_REV_PER_KW_MONTH = 180.0  # NE Tier-2 avg

                    interconnect_dist = float(SUB_DIST_MI) if SUB_DIST_MI is not None else 0.0
                    interconnect_capex = interconnect_dist * PRICE_INTERCONNECT_PER_MI + PRICE_SUBSTATION_UPGRADE

                    rl.append("=" * 56)
                    rl.append("GRID INTERCONNECT  (parcel-derived)")
                    if SUB_DIST_MI is not None:
                        rl.append("  Nearest tx-class sub: {0} {1} kV @ {2:.1f} mi".format(
                            SUB_NAME or "(unnamed)", SUB_KV if SUB_KV is not None else "?", SUB_DIST_MI))
                        rl.append("  Tap-in (est):                 ${0:>14,.0f}".format(interconnect_dist * PRICE_INTERCONNECT_PER_MI))
                        rl.append("  Substation upgrade:           ${0:>14,.0f}".format(PRICE_SUBSTATION_UPGRADE))
                        rl.append("  Interconnect total:           ${0:>14,.0f}".format(interconnect_capex))
                    else:
                        rl.append("  (no analyzer data; load via inbox JSON)")
                        interconnect_capex = 0.0

                    acres_for_capex = parcel_area / 43560.0 if parcel_area > 0 else 0.0
                    shell_capex = gfa * PRICE_BUILDING_PER_SF
                    site_capex = acres_for_capex * PRICE_SITE_PER_AC
                    racks_capex = target_racks * PRICE_RACK
                    gen_capex = n_gen * PRICE_GEN_EACH
                    ups_capex = n_ups * PRICE_UPS_EACH
                    batt_capex = n_battery * PRICE_BATTERY_EACH
                    ats_capex = n_ats * PRICE_ATS_EACH
                    chiller_capex = n_chiller * PRICE_CHILLER_EACH
                    drycooler_capex = n_drycooler * PRICE_DRYCOOLER_EACH
                    crac_capex = n_crac * PRICE_CRAC_EACH
                    hard_capex = (shell_capex + site_capex + racks_capex + gen_capex + ups_capex
                                  + batt_capex + ats_capex + chiller_capex + drycooler_capex
                                  + crac_capex + interconnect_capex)
                    soft_capex = hard_capex * SOFT_COSTS_FRACTION
                    total_capex = hard_capex + soft_capex

                    rl.append("=" * 56)
                    rl.append("CAPEX BREAKDOWN  (industry typicals)")
                    rl.append("  Shell ({0:,.0f} sf @ ${1:.0f}/sf):  ${2:>14,.0f}".format(gfa, PRICE_BUILDING_PER_SF, shell_capex))
                    rl.append("  Site work ({0:.2f} ac):              ${1:>14,.0f}".format(acres_for_capex, site_capex))
                    rl.append("  Racks ({0} @ ${1:,.0f}):              ${2:>14,.0f}".format(target_racks, PRICE_RACK, racks_capex))
                    rl.append("  Generators ({0}):                     ${1:>14,.0f}".format(n_gen, gen_capex))
                    rl.append("  UPS modules ({0}):                    ${1:>14,.0f}".format(n_ups, ups_capex))
                    rl.append("  Batteries ({0}):                      ${1:>14,.0f}".format(n_battery, batt_capex))
                    rl.append("  ATS lineups ({0}):                    ${1:>14,.0f}".format(n_ats, ats_capex))
                    rl.append("  Chillers ({0}):                       ${1:>14,.0f}".format(n_chiller, chiller_capex))
                    rl.append("  Dry coolers ({0}):                    ${1:>14,.0f}".format(n_drycooler, drycooler_capex))
                    rl.append("  CRAC units ({0}):                     ${1:>14,.0f}".format(n_crac, crac_capex))
                    rl.append("  Utility interconnect:                 ${0:>14,.0f}".format(interconnect_capex))
                    rl.append("  Soft costs (15%):                     ${0:>14,.0f}".format(soft_capex))
                    rl.append("  " + "-" * 50)
                    rl.append("  TOTAL CAPEX:                          ${0:>14,.0f}".format(total_capex))

                    opex_energy = annual_mwh * 1000.0 * rate_used
                    opex_water = annual_water_m3 * WATER_RATE_USD_PER_M3
                    opex_maint = total_capex * MAINT_FRACTION
                    opex_proptax = total_capex * PROP_TAX_FRACTION
                    opex_insurance = total_capex * INSURANCE_FRACTION
                    opex_staff = STAFF_COUNT * STAFF_FTE_USD
                    opex_total = opex_energy + opex_water + opex_maint + opex_proptax + opex_insurance + opex_staff

                    rl.append("=" * 56)
                    rl.append("OPEX  (annual)")
                    rl.append("  Energy ({0:.3f} $/kWh x {1:,.0f} MWh):  ${2:>11,.0f}/yr".format(rate_used, annual_mwh, opex_energy))
                    rl.append("  Water ({0:,.0f} m^3 @ ${1:.0f}/m^3):       ${2:>11,.0f}/yr".format(annual_water_m3, WATER_RATE_USD_PER_M3, opex_water))
                    rl.append("  Maintenance (4% of capex):             ${0:>11,.0f}/yr".format(opex_maint))
                    rl.append("  Property tax (1.2% of capex):          ${0:>11,.0f}/yr".format(opex_proptax))
                    rl.append("  Insurance (0.5% of capex):             ${0:>11,.0f}/yr".format(opex_insurance))
                    rl.append("  Staff ({0} FTE @ ${1:,.0f}):              ${2:>11,.0f}/yr".format(STAFF_COUNT, STAFF_FTE_USD, opex_staff))
                    rl.append("  " + "-" * 50)
                    rl.append("  TOTAL OPEX:                           ${0:>11,.0f}/yr".format(opex_total))

                    annual_revenue = fitted_kw * WHOLESALE_REV_PER_KW_MONTH * 12.0
                    rl.append("=" * 56)
                    rl.append("REVENUE  (wholesale colo, est)")
                    rl.append("  IT capacity:                          {0:>5.1f} MW".format(fitted_kw / 1000.0))
                    rl.append("  $/kW-month:                           ${0:>4.0f}  (NE Tier-2 avg)".format(WHOLESALE_REV_PER_KW_MONTH))
                    rl.append("  Annual revenue:                       ${0:>11,.0f}/yr".format(annual_revenue))

                    net_annual = annual_revenue - opex_total
                    rl.append("=" * 56)
                    rl.append("PAYBACK")
                    if net_annual > 0:
                        payback_years = total_capex / net_annual
                        rl.append("  Net annual cash:                      ${0:>11,.0f}/yr".format(net_annual))
                        rl.append("  Simple payback:                       {0:>5.1f} years".format(payback_years))
                    else:
                        rl.append("  Net annual cash:                      ${0:>11,.0f}/yr  (NEGATIVE)".format(net_annual))
                        rl.append("  Not viable at current IT scale.")
                        rl.append("  Slide IT Load up; payback flips positive at higher MW.")

                    rl.append("=" * 56)
                    rl.append("MAX-OUTPUT RECOMMENDATION  (at current height)")
                    rl.append("  IT Load slider:     {0:>10,.0f} kW".format(max_kw_at_height))
                    rl.append("  Override sliders:           all 0  (auto-derive)")
                    rl.append("  Edge offsets:       {0:>10.0f} ft  each (minimum)".format(1.0))
                    rl.append("  Building height:    {0:>10.0f} ft  (slider max for more)".format(height))
                    rl.append("  At max settings:")
                    rl.append("    {0:,} racks   {1:,.0f} kW total facility".format(max_racks_fit, max_facility_kw))
                    rl.append("    {0:,.0f} MWh/yr   {1:,.0f} m^3/yr water".format(max_annual_mwh, max_annual_water))
                    rl.append("    ~${0:,.0f}/yr energy".format(max_annual_cost))
                    if requested_racks > max_racks_fit:
                        rl.append("=" * 56)
                        rl.append("** AREA-LIMITED at {0:,.0f} kW. Increase parcel,".format(max_kw_at_height))
                        rl.append("   reduce setbacks, or raise the height slider.")
                    if warnings:
                        rl.append("=" * 56)
                        rl.append("WARNINGS:")
                        for w in warnings:
                            rl.append("  ! " + w)
                    parcel_readout_lines = __PARCEL_READOUT_LINES__
                    if parcel_readout_lines:
                        rl.append("=" * 56)
                        rl.extend(parcel_readout_lines)
                    report = "\\n".join(rl)
                except Exception as _rep_e:
                    import traceback as _rep_tb
                    report = ("SITE REPORT  --  builder failed\\n"
                              + "=" * 56 + "\\n"
                              + repr(_rep_e) + "\\n\\n"
                              + _rep_tb.format_exc())
                    print "[GH] report builder failed:", _rep_e

                # 21. Circuit / flow lines. Polylines at z=2 ft connecting
                # cluster centroids. POWER (red): GEN -> ATS -> UPS ->
                # RACKS, and UPS -> BATTERY. COOLING (cyan): DRYCOOLER ->
                # CHILLER -> CRAC -> RACKS.
                power_idx = dc_layer_idx.get("CIRCUIT_POWER", -1)
                cool_idx = dc_layer_idx.get("CIRCUIT_COOLING", -1)
                power_chain = [centers.get(k) for k in ("GEN", "ATS", "UPS", "RACK")]
                _bake_polyline_world(power_idx, power_chain)
                if centers.get("UPS") and centers.get("BATTERY"):
                    _bake_polyline_world(power_idx, [centers["UPS"], centers["BATTERY"]])
                cool_chain = [centers.get(k) for k in ("DRYCOOLER", "CHILLER", "CRAC", "RACK")]
                _bake_polyline_world(cool_idx, cool_chain)

                try:
                    doc.Views.Redraw()
                except:
                    pass


            if current is not None:
                try:
                    # Extrusion.Create extrudes along the curve's plane
                    # normal, which flips with the polyline's winding. The
                    # PARCEL curve is forced to CCW up front (see top of
                    # try-block) so `current` -- built from CCW offset
                    # vertices -- inherits CCW. This Reverse is now
                    # defensive: if any future stage flips winding, +height
                    # still extrudes UP from z=0, matching the component
                    # bakes which use the same V=+Z handedness.
                    if current.ClosedCurveOrientation(rg.Plane.WorldXY) == rg.CurveOrientation.Clockwise:
                        current.Reverse()
                    extr = rg.Extrusion.Create(current, abs(height), True)
                    if extr is not None:
                        a = extr.ToBrep()
                except Exception as e:
                    print "[GH] extrusion failed:", e
except Exception as fatal:
    print "[GH] FATAL:", fatal
    print traceback.format_exc()

# Explicit output binding. GhPython auto-binds the FIRST output to the
# script's local var matching its name (so `a` -> Brep is fine), but
# manually-registered subsequent outputs do NOT always pick up their
# matching local var. Write `report` into output[1] directly so the
# panel always receives the latest text regardless of the binding quirk.
try:
    if ghenv.Component.Params.Output.Count > 1:
        import Grasshopper as _G
        _op = ghenv.Component.Params.Output[1]
        _op.ClearData()
        _op.AddVolatileData(_G.Kernel.Data.GH_Path(0), 0,
                            _G.Kernel.Types.GH_String(str(report or "")))
except Exception as _eo:
    print "[GH] forced output set failed:", _eo

print "[GH] done. volume=", a is not None
'''


def _render_volume_script(meta):
    """Substitute per-parcel zoning values into the GH script template.

    Falls back to the module defaults (MAX_LOT_COVERAGE, no FAR cap,
    STORY_HEIGHT_FT) when the DXF carried no metadata or the field is missing.

    Also substitutes the parcel-feasibility readout lines (from the
    analyzer's `recommendationReadout` field) into the READOUT bake block.
    Empty list when the inbox JSON didn't carry one, in which case the
    bake omits the appended parcel-feasibility section entirely.
    """
    meta = meta or {}
    cap = meta.get("max_lot_coverage")
    if cap is None or cap <= 0 or cap > 1:
        cap = MAX_LOT_COVERAGE
    far = meta.get("max_far")
    far_literal = repr(float(far)) if (far is not None and far > 0) else "None"

    # Render the readout as an IronPython list literal so the substitution
    # is unambiguous regardless of how many newlines / quotes the formatter
    # emits. `repr()` on each line handles any embedded quote characters.
    readout = meta.get("recommendationReadout")
    if isinstance(readout, str) and readout.strip():
        lines = [ln for ln in readout.split("\n")]
        readout_literal = "[" + ", ".join(repr(ln) for ln in lines) + "]"
    else:
        readout_literal = "[]"

    # Structured fields used by the CAPEX / OPEX / interconnect sections
    # of the live report. Pulled from the analyzer's report dict and
    # rendered as IronPython literals (None when missing -- the script's
    # fallbacks handle that).
    grid = meta.get("grid") or {}
    nearest_tx = grid.get("nearestTransmissionSubstation") or {}
    sub_name = nearest_tx.get("name")
    sub_kv = nearest_tx.get("maxVoltageKv")
    sub_dist_mi = nearest_tx.get("distanceMi")
    rate_cents = (meta.get("power") or {}).get("industrialRateCentsPerKwh")
    rate_usd_per_kwh = (rate_cents / 100.0) if rate_cents is not None else None
    sub_name_literal = repr(sub_name) if isinstance(sub_name, str) else "None"
    sub_kv_literal = repr(int(sub_kv)) if isinstance(sub_kv, (int, float)) else "None"
    sub_dist_literal = repr(float(sub_dist_mi)) if isinstance(sub_dist_mi, (int, float)) else "None"
    rate_literal = repr(float(rate_usd_per_kwh)) if isinstance(rate_usd_per_kwh, (int, float)) else "None"

    return (GHPY_VOLUME_SCRIPT_TEMPLATE
            .replace("__MAX_LOT_COVERAGE__", repr(float(cap)))
            .replace("__MAX_FAR__", far_literal)
            .replace("__STORY_HEIGHT_FT__", repr(float(STORY_HEIGHT_FT)))
            .replace("__PARCEL_READOUT_LINES__", readout_literal)
            .replace("__SUBSTATION_NAME__", sub_name_literal)
            .replace("__SUBSTATION_KV__", sub_kv_literal)
            .replace("__SUBSTATION_DISTANCE_MI__", sub_dist_literal)
            .replace("__INDUSTRIAL_RATE_USDPKWH__", rate_literal))


def _ensure_grasshopper_loaded():
    """Load the Grasshopper plugin if it isn't already, return the module.

    On a cold Rhino session the plugin loads but `Instances.ActiveCanvas` and
    `Instances.DocumentEditor` come up asynchronously -- they can be None for
    a second or two after `_-Grasshopper` returns. We poll up to ~6s so the
    caller can rely on both being non-None.
    """
    try:
        import Grasshopper  # noqa: F401
    except ImportError:
        # `_Grasshopper` (no dash) opens the editor window; `_-Grasshopper`
        # (scripted) loads the plugin without showing UI, which is what we
        # used to do -- but on a fresh session that left the canvas/editor
        # un-instantiated, so the rest of the integration silently no-op'd.
        Rhino.RhinoApp.RunScript("_Grasshopper", False)
        System.Threading.Thread.Sleep(800)
        import Grasshopper  # noqa: F401

    # Wait for canvas + editor to actually exist. They are created on the UI
    # thread asynchronously after the plugin reports loaded.
    deadline = time.time() + 6.0
    while time.time() < deadline:
        canvas = Grasshopper.Instances.ActiveCanvas
        editor = Grasshopper.Instances.DocumentEditor
        if canvas is not None and editor is not None:
            return Grasshopper
        # If only the editor is missing, force-open it via the user-facing
        # command (no dash) so its WPF window gets constructed.
        if canvas is None or editor is None:
            try:
                Rhino.RhinoApp.RunScript("_Grasshopper", False)
            except Exception:
                pass
        System.Threading.Thread.Sleep(250)

    canvas = Grasshopper.Instances.ActiveCanvas
    editor = Grasshopper.Instances.DocumentEditor
    if canvas is None or editor is None:
        raise RuntimeError(
            "Grasshopper loaded but canvas={0} editor={1} after 6s".format(
                canvas is not None, editor is not None,
            )
        )
    return Grasshopper


def _fit_canvas_to_doc(canvas, ghdoc):
    """Zoom the canvas so the entire document is visible.

    The slider count varies per parcel (one per perimeter edge) so layouts
    can be tall; without this, the OFF wire trails off-canvas to sliders the
    user can't see. Tries the GH_Canvas / GH_Viewport convenience methods
    first, falls back to manually setting MidPoint + Zoom.
    """
    try:
        canvas.ZoomToFit()
        return
    except Exception:
        pass
    try:
        canvas.Viewport.ZoomToFit()
        return
    except Exception:
        pass
    try:
        bounds = ghdoc.BoundingBox(False)
        if bounds.Width <= 0 or bounds.Height <= 0:
            return
        cw = float(canvas.Width or 1000)
        ch = float(canvas.Height or 700)
        margin = 80.0
        sx = max(cw - 2 * margin, 200.0) / float(bounds.Width)
        sy = max(ch - 2 * margin, 200.0) / float(bounds.Height)
        new_zoom = min(sx, sy)
        if new_zoom <= 0:
            new_zoom = 1.0
        if new_zoom > 1.5:
            new_zoom = 1.5
        canvas.Viewport.MidPoint = System.Drawing.PointF(
            bounds.X + bounds.Width / 2.0,
            bounds.Y + bounds.Height / 2.0,
        )
        canvas.Viewport.Zoom = new_zoom
        canvas.Refresh()
    except Exception:
        # Worst case: user can press Ctrl+E in the GH canvas to zoom-fit.
        pass


def _edge_slider_range(meta):
    """Pick (min, default, max) for the per-edge offset sliders.

    With zoning metadata: default = the largest of front/side/rear setbacks
    (most conservative starting point -- treats every edge like the rear);
    min = smallest setback (so user can relax non-front edges down to the
    side/rear minimum); max = max(EDGE_OFFSET_MAX, largest setback * 1.5)
    so the slider can still go beyond zoning for an extra buffer.

    Without metadata: fall back to the module defaults.
    """
    if not meta:
        return EDGE_OFFSET_MIN, EDGE_OFFSET_DEFAULT, EDGE_OFFSET_MAX
    setbacks = [meta.get("front_setback_ft"), meta.get("side_setback_ft"),
                meta.get("rear_setback_ft")]
    setbacks = [s for s in setbacks if s is not None and s > 0]
    if not setbacks:
        return EDGE_OFFSET_MIN, EDGE_OFFSET_DEFAULT, EDGE_OFFSET_MAX
    s_min = float(min(setbacks))
    s_max = float(max(setbacks))
    slider_max = max(EDGE_OFFSET_MAX, s_max * 1.5)
    return s_min, s_max, slider_max


def _height_slider_range(meta):
    """Pick (min, default, max) for the height slider.

    Default ALWAYS = HEIGHT_MIN so the slider opens at its leftmost stop --
    initial state is a single-story building (n_stories = max(1, int(H /
    STORY_HEIGHT_FT)) = 1 when H <= STORY_HEIGHT_FT) and NO components are
    stacked above any other when the user first opens the canvas. The user
    drags up to add stories.

    slider_max scales to the zoning ceiling (with a small headroom margin)
    when meta provides max_height_ft, so the dial range still reaches the
    full legal envelope; only the *starting* value is pinned to the floor.
    """
    if not meta:
        return HEIGHT_MIN, HEIGHT_MIN, HEIGHT_MAX
    h = meta.get("max_height_ft")
    if h is None or h <= 0:
        return HEIGHT_MIN, HEIGHT_MIN, HEIGHT_MAX
    h = float(h)
    return HEIGHT_MIN, HEIGHT_MIN, max(HEIGHT_MAX, h * 1.2)


def _build_cube_grid_definition(edge_count, meta=None):
    """Construct the GH definition: N edge sliders + height + GhPython volume.

    edge_count is the number of distinct edges in the parcel polyline (one
    slider per edge). Sliders are wired to the GhPython component's `OFF`
    input as a list, so OFF[i] is the offset for edge i+1. `meta` is the
    PLINTH_META dict parsed from the DXF -- when present, it pins slider
    defaults to real zoning setbacks/height and pushes the lot-coverage and
    FAR caps into the GhPython script.
    """
    Grasshopper = _ensure_grasshopper_loaded()
    import Grasshopper.Kernel as gh
    import Grasshopper.Kernel.Special as ghs
    import GhPython

    ghdoc = gh.GH_Document()

    # Layout: top of column 1 has the 9 main sliders (height, IT-load,
    # 7 component-count overrides) stacked vertically; edge-setback
    # sliders sit BELOW them, split into 2 sub-columns; GhPython
    # component goes to the right of the edge columns.
    col1_x = 60
    col2_x = 290
    top_y = 80
    row_dy = 26
    slider_w = 200
    slider_h = 22
    gap_after_kw = 8

    edge_count = max(int(edge_count), 0)
    edges_per_col = (edge_count + 1) // 2

    edge_min, edge_def, edge_max = _edge_slider_range(meta)
    h_min, h_def, h_max = _height_slider_range(meta)

    # --- Building Max Height (top of column 1)
    h_slider = ghs.GH_NumberSlider()
    h_slider.SetInitCode("{0:.1f} < {1:.1f} < {2:.1f}".format(
        h_min, h_def, h_max
    ))
    h_slider.NickName = "Building Max Height [ft]"
    h_slider.CreateAttributes()
    h_slider.Attributes.Pivot = System.Drawing.PointF(col1_x, top_y)
    h_slider.Attributes.ExpireLayout()
    ghdoc.AddObject(h_slider, False)

    # --- IT Load slider, just below Max Height.
    kw_slider = ghs.GH_NumberSlider()
    kw_slider.SetInitCode("100.0 < 1000.0 < 20000.0")
    kw_slider.NickName = "Target IT Load [kW] (drives all counts)"
    kw_slider.CreateAttributes()
    kw_slider.Attributes.Pivot = System.Drawing.PointF(
        col1_x, top_y + row_dy + gap_after_kw
    )
    kw_slider.Attributes.ExpireLayout()
    ghdoc.AddObject(kw_slider, False)

    # --- Per-component count-override sliders. Default 0 = "auto",
    # N > 0 = pin exactly N units of that family.
    override_specs = [
        ("Generators [SDMO 1.5 MW class, N+1] - 0=auto",   50.0,  "ng_slider"),
        ("UPS Modules [APC PX 500 kW, N+1] - 0=auto",     100.0,  "nu_slider"),
        ("Battery Cabinets [4 per UPS] - 0=auto",         500.0,  "nb_slider"),
        ("ATS Lineups [1 per 2 MW IT] - 0=auto",           50.0,  "na_slider"),
        ("Chillers [Emerson 750 kW cooling, N+1] - 0=auto", 50.0, "nc_slider"),
        ("Dry Coolers [LU-VE, 1:1 with chillers] - 0=auto", 50.0, "nd_slider"),
        ("CRAC Units [100 kW each, in data hall] - 0=auto", 300.0, "nr_slider"),
    ]
    override_sliders = {}
    override_y0 = top_y + 2 * row_dy + gap_after_kw
    for i, (label, mx, varname) in enumerate(override_specs):
        s = ghs.GH_NumberSlider()
        s.SetInitCode("0 < 0 < {0}".format(int(mx)))
        s.NickName = label
        s.CreateAttributes()
        s.Attributes.Pivot = System.Drawing.PointF(
            col1_x, override_y0 + i * row_dy
        )
        s.Attributes.ExpireLayout()
        ghdoc.AddObject(s, False)
        override_sliders[varname] = s
    ng_slider = override_sliders["ng_slider"]
    nu_slider = override_sliders["nu_slider"]
    nb_slider = override_sliders["nb_slider"]
    na_slider = override_sliders["na_slider"]
    nc_slider = override_sliders["nc_slider"]
    nd_slider = override_sliders["nd_slider"]
    nr_slider = override_sliders["nr_slider"]

    # --- Edge-setback sliders BELOW the override stack, in 2 sub-columns.
    # Wired to OFF in edge order regardless of column.
    overrides_end_y = override_y0 + len(override_specs) * row_dy
    edges_y0 = overrides_end_y + 24
    edge_sliders = []
    for i in range(edge_count):
        if i < edges_per_col:
            sx, srow = col1_x, i
        else:
            sx, srow = col2_x, i - edges_per_col
        s = ghs.GH_NumberSlider()
        s.SetInitCode("{0:.1f} < {1:.1f} < {2:.1f}".format(
            edge_min, edge_def, edge_max
        ))
        s.NickName = "Edge {0} Setback [ft]".format(i + 1)
        s.CreateAttributes()
        s.Attributes.Pivot = System.Drawing.PointF(
            sx, edges_y0 + srow * row_dy
        )
        s.Attributes.ExpireLayout()
        ghdoc.AddObject(s, False)
        edge_sliders.append(s)

    # GhPython component goes to the right of the (potentially wide)
    # edge columns; legacy var name `comp_x` is kept since it's used by
    # the panel positioning + row-y references downstream.
    comp_x = col2_x + slider_w + 60
    row_y0 = top_y  # legacy alias for downstream y-coords

    # GhPython component: kept under "DC Cube Grid" name (now produces
    # a volume + bakes racks to the RACK layer). Inputs are registered from
    # scratch below -- we no longer rely on whatever defaults this build
    # ships with, because that count drifted between Rhino versions and the
    # KW input went missing.
    ghpy = GhPython.Component.ZuiPythonComponent()
    ghpy.Code = _render_volume_script(meta)
    ghpy.NickName = "DC Cube Grid"
    ghpy.CreateAttributes()
    # Place the component to the RIGHT of the right-most slider column so
    # wires flow naturally left-to-right (slider output -> component input).
    ghpy.Attributes.Pivot = System.Drawing.PointF(
        comp_x + slider_w + 80, row_y0 + 120
    )
    ghpy.Attributes.ExpireLayout()

    # ZuiPythonComponent's default input count is unreliable across Rhino
    # builds (we have observed both 2 and 3). Don't assume -- strip every
    # input down to zero, then register exactly the three we need by name.
    # Any failure here is logged loudly because the rack layout depends on
    # the KW input being live; a silent partial setup is the worst case.
    target_inputs = [
        ("OFF", gh.GH_ParamAccess.list),
        ("H",   gh.GH_ParamAccess.item),
        ("KW",  gh.GH_ParamAccess.item),
        ("NG",  gh.GH_ParamAccess.item),  # gen count override
        ("NU",  gh.GH_ParamAccess.item),  # ups
        ("NB",  gh.GH_ParamAccess.item),  # battery
        ("NA",  gh.GH_ParamAccess.item),  # ats
        ("NC",  gh.GH_ParamAccess.item),  # chiller
        ("ND",  gh.GH_ParamAccess.item),  # dry cooler
        ("NR",  gh.GH_ParamAccess.item),  # CRAC
    ]
    try:
        from Grasshopper.Kernel.Parameters import Param_GenericObject
    except Exception as imp_err:
        Param_GenericObject = None
        Rhino.RhinoApp.WriteLine(
            "[GH] cannot import Param_GenericObject: {0}".format(imp_err)
        )

    # Strip everything Grasshopper added by default.
    while ghpy.Params.Input.Count > 0:
        try:
            ghpy.Params.UnregisterInputParameter(
                ghpy.Params.Input[ghpy.Params.Input.Count - 1]
            )
        except Exception:
            break

    # Register the three we want, in order. Param_GenericObject takes any
    # type GH knows how to coerce (numbers from sliders -> float in script).
    if Param_GenericObject is not None:
        for name, access in target_inputs:
            try:
                p = Param_GenericObject()
                p.Name = name
                p.NickName = name
                try:
                    p.Access = access
                except Exception:
                    pass
                ghpy.Params.RegisterInputParam(p)
            except Exception as e:
                Rhino.RhinoApp.WriteLine(
                    "[GH] failed to register input '{0}': {1}".format(
                        name, e
                    )
                )

    Rhino.RhinoApp.WriteLine(
        "[GH] component inputs after build: count={0}, names=[{1}]".format(
            ghpy.Params.Input.Count,
            ", ".join([ip.Name for ip in ghpy.Params.Input]),
        )
    )

    # Output `a` -> single Brep volume (item access).
    if ghpy.Params.Output.Count >= 1:
        out0 = ghpy.Params.Output[0]
        out0.Name = "a"
        out0.NickName = "a"
        try:
            out0.Access = gh.GH_ParamAccess.item
        except Exception:
            pass

    # Output `report` -> multi-line site-report string. RegisterOutputParam
    # is reliable for string outputs (the earlier no-op was specifically a
    # geometry-output issue). The script's top-level `report` variable
    # feeds this; a panel below the component displays it.
    if Param_GenericObject is not None:
        try:
            rp = Param_GenericObject()
            rp.Name = "report"
            rp.NickName = "report"
            try:
                rp.Access = gh.GH_ParamAccess.item
            except Exception:
                pass
            ghpy.Params.RegisterOutputParam(rp)
        except Exception as e:
            Rhino.RhinoApp.WriteLine(
                "[GH] failed to register output 'report': {0}".format(e)
            )

    try:
        ghpy.Params.OnParametersChanged()
    except Exception:
        pass
    try:
        ghpy.VariableParameterMaintenance()
    except Exception:
        pass

    ghdoc.AddObject(ghpy, False)

    # Wire all edge sliders to OFF (in edge order). GhPython concatenates
    # list-access input from sources in AddSource order, so OFF[i] maps to
    # the i-th edge slider built above.
    # Look up inputs by NAME rather than by index -- index-based wiring is
    # brittle when input registration order ever drifts.
    inputs_by_name = {ip.Name: ip for ip in ghpy.Params.Input}
    off_in = inputs_by_name.get("OFF")
    h_in = inputs_by_name.get("H")
    kw_in = inputs_by_name.get("KW")

    if off_in is not None:
        for s in edge_sliders:
            off_in.AddSource(s)
    else:
        Rhino.RhinoApp.WriteLine(
            "[GH] WARNING: OFF input missing; edge sliders unwired"
        )
    if h_in is not None:
        h_in.AddSource(h_slider)
    else:
        Rhino.RhinoApp.WriteLine(
            "[GH] WARNING: H input missing; height slider unwired"
        )
    if kw_in is not None:
        kw_in.AddSource(kw_slider)
    else:
        Rhino.RhinoApp.WriteLine(
            "[GH] WARNING: KW input missing; IT Load slider unwired -- "
            "rack layout will compute zero racks"
        )

    # Override sliders -> their respective inputs by name.
    override_pairs = [
        ("NG", ng_slider), ("NU", nu_slider), ("NB", nb_slider),
        ("NA", na_slider), ("NC", nc_slider), ("ND", nd_slider),
        ("NR", nr_slider),
    ]
    wired_overrides = []
    for name, slider in override_pairs:
        ip = inputs_by_name.get(name)
        if ip is not None:
            ip.AddSource(slider)
            wired_overrides.append(name)
    Rhino.RhinoApp.WriteLine(
        "[GH] sliders wired: KW + overrides=[{0}]".format(
            ", ".join(wired_overrides)
        )
    )

    # Site-report panel: positioned to the RIGHT of the GhPython
    # component, sized roomy enough to read the multi-line report
    # without scrolling. Source is the `report` output param. Created
    # fresh on every rebuild so the panel survives canvas wipes.
    site_panel = None
    try:
        from Grasshopper.Kernel.Special import GH_Panel
        site_panel = GH_Panel()
        site_panel.NickName = "Site Report"
        site_panel.CreateAttributes()
        site_panel.Attributes.Pivot = System.Drawing.PointF(
            comp_x + slider_w + 80 + 280, row_y0 + 60
        )
        try:
            site_panel.Attributes.Bounds = System.Drawing.RectangleF(
                site_panel.Attributes.Pivot.X,
                site_panel.Attributes.Pivot.Y,
                460.0, 760.0,
            )
        except Exception:
            pass
        site_panel.Attributes.ExpireLayout()
        ghdoc.AddObject(site_panel, False)

        # Wire report -> panel. GH_Panel acts as a downstream string
        # consumer; AddSource on the panel itself is the supported call.
        outputs_by_name = {op.Name: op for op in ghpy.Params.Output}
        report_out = outputs_by_name.get("report")
        if report_out is not None:
            try:
                site_panel.AddSource(report_out)
            except Exception as wire_err:
                Rhino.RhinoApp.WriteLine(
                    "[GH] panel.AddSource(report) failed: {0}".format(wire_err)
                )
        else:
            Rhino.RhinoApp.WriteLine(
                "[GH] WARNING: 'report' output missing; panel will stay empty"
            )
    except Exception as panel_err:
        Rhino.RhinoApp.WriteLine(
            "[GH] site-report panel build failed: {0}".format(panel_err)
        )

    # Mark everything dirty so the very first canvas solution actually
    # computes (otherwise the wires are set up after the initial solve and
    # the GhPython component reads OFF=None and exits).
    try:
        for s in edge_sliders:
            s.ExpireSolution(False)
        for s in (h_slider, kw_slider, ng_slider, nu_slider, nb_slider,
                  na_slider, nc_slider, nd_slider, nr_slider):
            s.ExpireSolution(False)
        ghpy.ExpireSolution(True)
        if site_panel is not None:
            try:
                site_panel.ExpireSolution(False)
            except Exception:
                pass
    except Exception:
        pass

    return ghdoc


def _open_or_refresh_cube_grid(edge_count, meta=None):
    """Open the volume definition in Grasshopper, building it from scratch.

    Always rebuilds: the slider count depends on edge_count which varies
    per parcel, so any cached definition would be wrong. `meta` is the
    PLINTH_META dict from the DXF (or None) -- forwarded to the GH builder
    so slider defaults and caps reflect real zoning.
    """
    Rhino.RhinoApp.WriteLine(
        "[GH] building GH definition ({0} edges, meta keys: {1})".format(
            edge_count, sorted((meta or {}).keys())
        )
    )
    try:
        Grasshopper = _ensure_grasshopper_loaded()
        canvas = Grasshopper.Instances.ActiveCanvas
        editor = Grasshopper.Instances.DocumentEditor

        # Cancel any in-flight solve on the previous document and disable it
        # so it can't fire again as we swap. Without this the old solve can
        # race the new doc's NewSolution() and (in past sessions) leak
        # geometry until Rhino runs out of memory.
        prev = canvas.Document
        if prev is not None:
            try:
                prev.RequestAbortSolution()
            except Exception:
                pass
            try:
                prev.Enabled = False
            except Exception:
                pass

        ghdoc = _build_cube_grid_definition(edge_count, meta)

        canvas.Document = ghdoc
        sc.sticky["plinth_gh_doc"] = ghdoc
        # Force a full re-solve. `False` only re-solves objects that have
        # changed since the last solve, but in a freshly assigned doc some
        # builds skip the initial computation -- `True` guarantees it runs.
        ghdoc.NewSolution(True)
        editor.Show()
        _fit_canvas_to_doc(canvas, ghdoc)
        Rhino.RhinoApp.WriteLine(
            "[GH] volume builder opened in Grasshopper "
            "({0} edge slider(s))".format(edge_count)
        )
        return True
    except Exception as e:
        # Include the traceback so an IronPython error in the GH builder
        # (e.g. a typo in the script template, a None where a float was
        # expected) doesn't show up as just a one-line message with no clue
        # which line failed.
        Rhino.RhinoApp.WriteLine(
            "[GH] Grasshopper integration FAILED: {0}".format(e)
        )
        try:
            for line in traceback.format_exc().splitlines():
                Rhino.RhinoApp.WriteLine("[GH]   {0}".format(line))
        except Exception:
            pass
        return False


def _import_dxf(path):
    """Read the DXF in Python, add geometry directly to the active doc.

    Bypasses Rhino's DXF importer entirely so we never trip the
    "Set DWG import options" command-line prompt -- that prompt blocks
    BatchMode and would freeze the watcher.
    """
    parcel_idx = _ensure_parcel_layer()
    cleared = _clear_layer_objects(parcel_idx)
    if cleared:
        Rhino.RhinoApp.WriteLine(
            "[GH] cleared previous parcel ({0} objects)".format(cleared)
        )
    doc = Rhino.RhinoDoc.ActiveDoc
    raw_rings = _parse_lwpolylines(path)
    meta = _parse_plinth_metadata(path)
    if meta:
        # Log a one-line summary so the user can confirm the right zoning
        # envelope was picked up before the GH definition opens.
        district = meta.get("district_key") or "?"
        front = meta.get("front_setback_ft")
        side = meta.get("side_setback_ft")
        rear = meta.get("rear_setback_ft")
        height = meta.get("max_height_ft")
        cov = meta.get("max_lot_coverage")
        far = meta.get("max_far")
        Rhino.RhinoApp.WriteLine(
            ("[GH] zoning envelope: district={0}, "
             "setbacks F/S/R={1}/{2}/{3} ft, h<={4} ft, "
             "coverage<={5}, FAR<={6}").format(
                district, front, side, rear, height, cov, far,
            )
        )
    else:
        Rhino.RhinoApp.WriteLine(
            "[GH] no PLINTH_META block in DXF -- using slider defaults"
        )
    # Strip near-collinear vertices BEFORE the polyline reaches Rhino. The
    # GhPython script reads PARCEL straight from the doc, so simplifying
    # here means slider count, the script's per-edge offset loop, and what
    # the user sees in the viewport all agree.
    rings = []
    for r in raw_rings:
        simplified = _simplify_collinear_ring(r)
        rings.append(simplified)
    raw_total = sum(max(len(r) - 1, 0) for r in raw_rings)
    simp_total = sum(max(len(r) - 1, 0) for r in rings)
    Rhino.RhinoApp.WriteLine(
        "[GH] simplify: {0} -> {1} vertices "
        "(angle<{2:.1f}deg OR dev<{3:.1f}ft); "
        "tune SIMPLIFY_ANGLE_DEG / SIMPLIFY_DEV_FT in the watcher to "
        "be more or less aggressive".format(
            raw_total, simp_total, SIMPLIFY_ANGLE_DEG, SIMPLIFY_DEV_FT
        )
    )

    parcel_attrs = Rhino.DocObjects.ObjectAttributes()
    parcel_attrs.LayerIndex = parcel_idx

    added = 0
    bbox = Rhino.Geometry.BoundingBox.Empty
    for ring in rings:
        poly = Rhino.Geometry.Polyline()
        for x, y in ring:
            poly.Add(x, y, 0.0)
        first = ring[0]
        last = ring[-1]
        if first[0] != last[0] or first[1] != last[1]:
            poly.Add(first[0], first[1], 0.0)

        guid = doc.Objects.AddPolyline(poly, parcel_attrs)
        if guid == System.Guid.Empty:
            continue
        added += 1
        obj_bbox = poly.BoundingBox
        if obj_bbox.IsValid:
            bbox.Union(obj_bbox)

    edge_count = _largest_ring_edge_count(rings)
    Rhino.RhinoApp.WriteLine(
        "[GH] {0} parcel polyline(s); largest ring has {1} edge(s)".format(
            added, edge_count
        )
    )

    # Drop any selection state. Without this, freshly imported polylines
    # render in Rhino's selection-yellow which makes them look like an
    # unrelated overlay on top of the parcel.
    try:
        doc.Objects.UnselectAll()
    except Exception:
        pass

    if added > 0 and bbox.IsValid:
        view = doc.Views.ActiveView
        if view is not None:
            view.ActiveViewport.ZoomBoundingBox(bbox)

    doc.Views.Redraw()

    # Open Grasshopper with the per-edge volume definition. Failures here
    # don't stop the import.
    if added > 0 and edge_count >= 3:
        ok = _open_or_refresh_cube_grid(edge_count, meta)
        if not ok:
            # Surface a single, easy-to-spot summary line AFTER all the
            # GH-builder logs so the user doesn't have to scroll through
            # the traceback to know it didn't come up.
            Rhino.RhinoApp.WriteLine(
                "[GH] >> parcel imported but Grasshopper did not open. "
                "Check the lines above for the cause."
            )
    elif added > 0:
        Rhino.RhinoApp.WriteLine(
            "[GH] skipped GH (parcel has only {0} edges; need >=3)".format(
                edge_count
            )
        )


def _is_stable(path):
    """Skip files that are still being written: require two equal sizes
    across consecutive polls."""
    try:
        size = os.path.getsize(path)
    except OSError:
        return False
    prev = _seen_sizes.get(path)
    _seen_sizes[path] = size
    return prev is not None and prev == size


def _scan(sender, args):
    global _scanning
    if _scanning:
        # Previous scan still running; skip this tick rather than reenter.
        return
    _scanning = True
    try:
        if not os.path.isdir(INBOX):
            return
        for name in os.listdir(INBOX):
            if not name.lower().endswith(".dxf"):
                continue
            src = os.path.join(INBOX, name)
            if not os.path.isfile(src):
                continue
            if not _is_stable(src):
                continue
            base, ext = os.path.splitext(name)
            import_ok = True
            err_msg = None
            try:
                _import_dxf(src)
            except Exception as e:
                import_ok = False
                err_msg = "{0}\n{1}".format(e, traceback.format_exc())

            # CRITICAL: always move the file out of the inbox, even on
            # failure. Otherwise the watcher retries the same broken DXF
            # every poll_seconds, rebuilding the GH document each time --
            # which leaks memory and eventually crashes Rhino. With a
            # _FAILED suffix the user can still inspect what blew up.
            if import_ok:
                dest_name = name
            else:
                dest_name = "{0}_FAILED_{1}{2}".format(base, int(time.time()), ext)
            dest = os.path.join(PROCESSED, dest_name)
            if os.path.exists(dest):
                dest = os.path.join(
                    PROCESSED,
                    "{0}_{1}{2}".format(
                        os.path.splitext(dest_name)[0],
                        int(time.time()),
                        os.path.splitext(dest_name)[1],
                    ),
                )
            try:
                shutil.move(src, dest)
            except Exception as move_e:
                Rhino.RhinoApp.WriteLine(
                    "[GH] could not move {0} after import: {1}".format(
                        name, move_e
                    )
                )
                # Last resort: drop tracking so we don't keep re-stat'ing.
                _seen_sizes.pop(src, None)
                continue
            _seen_sizes.pop(src, None)
            if import_ok:
                Rhino.RhinoApp.WriteLine("[GH] imported {0}".format(name))
            else:
                Rhino.RhinoApp.WriteLine(
                    "[GH] failed to import {0}: {1}".format(name, err_msg)
                )
    except Exception:
        Rhino.RhinoApp.WriteLine(
            "[GH] scan error:\n{0}".format(traceback.format_exc())
        )
    finally:
        _scanning = False


def start():
    global _timer
    _ensure_dirs()
    # If a previous run left a timer behind, stop it so re-running the script
    # replaces (rather than duplicates) the watcher.
    existing = _timer or sc.sticky.get("plinth_inbox_timer")
    if existing is not None:
        try:
            existing.Stop()
        except Exception:
            pass
        _timer = None
        sc.sticky.pop("plinth_inbox_timer", None)
        Rhino.RhinoApp.WriteLine("[GH] restarted watcher.")
    t = UITimer()
    t.Interval = POLL_SECONDS
    t.Elapsed += _scan
    t.Start()
    _timer = t
    sc.sticky["plinth_inbox_timer"] = t
    Rhino.RhinoApp.WriteLine("[GH] inbox watcher running")
    Rhino.RhinoApp.WriteLine("[GH] inbox:    {0}".format(INBOX))
    Rhino.RhinoApp.WriteLine("[GH] imported: {0}".format(PROCESSED))


def stop():
    global _timer
    t = _timer or sc.sticky.get("plinth_inbox_timer")
    if t is None:
        Rhino.RhinoApp.WriteLine("[GH] watcher not running.")
        return
    try:
        t.Stop()
    except Exception:
        pass
    _timer = None
    sc.sticky.pop("plinth_inbox_timer", None)
    Rhino.RhinoApp.WriteLine("[GH] watcher stopped.")


# If a previous run left a timer in sticky storage, recover it so re-running
# this script doesn't pile up duplicate timers.
if _timer is None and "plinth_inbox_timer" in sc.sticky:
    _timer = sc.sticky["plinth_inbox_timer"]

if __name__ == "__main__":
    start()
