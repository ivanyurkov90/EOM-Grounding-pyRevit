# -*- coding: utf-8 -*-
from __future__ import division, print_function

"""Pure-data topology and fitting rules for EOM grounding models.

This module intentionally contains no Revit API calls.  The model layer consumes
these node/fitting descriptors and creates lightweight BIM geometry.  Keeping the
rules separate makes the future perimeter-contour workflow deterministic: geometry,
BOM and update logic all use the same topology.
"""

NODE_VERTICAL_ELECTRODE = "VERTICAL_ELECTRODE"
NODE_JOIN = "JOIN"
NODE_CORNER = "CORNER"
NODE_T_BRANCH = "T_BRANCH"
NODE_CROSSING = "CROSSING"
NODE_HOLDER = "HOLDER"
NODE_END = "END"

FIT_COUPLING = "COUPLING"
FIT_TIP = "TIP"
FIT_ROD_STRIP_CLAMP = "ROD_STRIP_CLAMP"
FIT_STRIP_STRIP_CLAMP = "STRIP_STRIP_CLAMP"
FIT_DIAGONAL_ROD_STRIP_CLAMP = "DIAGONAL_ROD_STRIP_CLAMP"
FIT_HOLDER = "HOLDER"

# Geometry envelopes / article mapping used by the lightweight BIM layer.
# 90540 geometry follows the 2020 EZETEK typical-solutions album, sheet 6:
# cross clamp (rod, strip/round conductor), 70 x 50 mm, four-bolt layout.
FITTING_CATALOG = {
    "90227": {
        "kind": FIT_COUPLING,
        "name": u"Муфта соединительная",
        "geometry": "COUPLING_PROXY",
        "diameter_mm": 24.0,
        "length_mm": 70.0,
    },
    "90326": {
        "kind": FIT_TIP,
        "name": u"Наконечник стартовый",
        "geometry": "TIP_PROXY",
        "diameter_mm": 24.0,
        "length_mm": 45.0,
    },
    "90540": {
        "kind": FIT_ROD_STRIP_CLAMP,
        "material": u"Латунь",
        "name": u"Зажим крестообразный (стержень, полоса/пруток)",
        "geometry": "CROSS_ROD_STRIP_4BOLT",
        "envelope_length_mm": 70.0,
        "envelope_height_mm": 50.0,
        "plate_thickness_mm": 2.0,
        "plate_gap_mm": 8.0,
        "bolt_offset_x_mm": 25.0,
        "bolt_offset_z_mm": 15.0,
        "bolt_head_mm": 9.0,
        "bolt_depth_mm": 5.0,
    },
    # Reserved for the perimeter-contour phase.  These entries allow the same
    # topology/BOM engine to select fittings later without changing model code.
    "90530/2": {
        "kind": FIT_STRIP_STRIP_CLAMP,
        "material": u"Сталь оцинкованная",
        "name": u"Зажим крестообразный (полоса/пруток, полоса/пруток)",
        "geometry": "CROSS_STRIP_STRIP_4BOLT",
        "envelope_length_mm": 70.0,
        "envelope_height_mm": 50.0,
    },
    "90540/2": {
        "kind": FIT_STRIP_STRIP_CLAMP,
        "material": u"Латунь",
        "name": u"Зажим крестообразный (полоса/пруток, полоса/пруток)",
        "geometry": "CROSS_STRIP_STRIP_4BOLT",
        "envelope_length_mm": 70.0,
        "envelope_height_mm": 50.0,
    },
    "90530": {
        "kind": FIT_ROD_STRIP_CLAMP,
        "material": u"Сталь оцинкованная",
        "name": u"Зажим крестообразный (стержень, полоса/пруток)",
        "geometry": "CROSS_ROD_STRIP_4BOLT",
        "envelope_length_mm": 70.0,
        "envelope_height_mm": 50.0,
    },
    "90531": {
        "kind": FIT_DIAGONAL_ROD_STRIP_CLAMP,
        "material": u"Сталь оцинкованная",
        "name": u"Зажим диагональный (стержень, полоса/пруток)",
        "geometry": "DIAGONAL_ROD_STRIP_2BOLT",
        "envelope_length_mm": 96.0,
        "envelope_height_mm": 30.0,
        "bolt_spacing_mm": 72.0,
    },
    "90188": {
        "kind": FIT_HOLDER,
        "material": u"Сталь оцинкованная",
        "name": u"Держатель шин заземления",
        "geometry": "HOLDER_PROXY",
        "envelope_length_mm": 54.0,
        "envelope_height_mm": 64.0,
        "envelope_depth_mm": 23.0,
    },
}

# Article mapping from the supplied EZETEK 2020 album.  The perimeter tool will
# ask the user/material rules which variant to use; it will not infer material silently.
ALBUM_CONNECTION_VARIANTS = {
    "STRIP_STRIP_CROSS": {"GALVANIZED_STEEL": "90530/2", "BRASS": "90540/2"},
    "ROD_STRIP_CROSS": {"GALVANIZED_STEEL": "90530", "BRASS": "90540"},
    "ROD_STRIP_DIAGONAL": {"GALVANIZED_STEEL": "90531"},
    "STRIP_HOLDER": {"GALVANIZED_STEEL": "90188"},
}


def album_article(connection_key, material_key):
    variants = ALBUM_CONNECTION_VARIANTS.get(str(connection_key) or "", {})
    return variants.get(str(material_key) or "")


def make_node(node_type, node_id, point_key=None, directions=None, fittings=None, meta=None):
    return {
        "type": node_type,
        "id": str(node_id),
        "point_key": point_key,
        "directions": list(directions or []),
        "fittings": list(fittings or []),
        "meta": dict(meta or {}),
    }


def vertical_electrode_node(index, module_count, clamp_article="90540"):
    """Return the deterministic assembly for one modular vertical electrode."""
    modules = max(1, int(module_count or 1))
    fittings = [
        {"article": "90136", "role": "ROD_MODULE", "qty": modules},
        # Current 90136 assembly uses one 90227 per module: top coupling plus
        # the physical joints between modules.
        {"article": "90227", "role": FIT_COUPLING, "qty": modules},
        {"article": "90326", "role": FIT_TIP, "qty": 1},
        {"article": clamp_article, "role": FIT_ROD_STRIP_CLAMP, "qty": 1},
    ]
    return make_node(
        NODE_VERTICAL_ELECTRODE,
        "VE-{}".format(int(index) + 1),
        point_key=int(index),
        fittings=fittings,
        meta={"module_count": modules, "clamp_article": clamp_article},
    )


def vertical_electrode_bom(module_count, electrode_count=1, clamp_article="90540"):
    nodes = [vertical_electrode_node(i, module_count, clamp_article) for i in range(max(1, int(electrode_count or 1)))]
    return aggregate_bom(nodes)


def aggregate_bom(nodes):
    result = {}
    for node in nodes or []:
        for item in node.get("fittings") or []:
            article = str(item.get("article") or "").strip()
            if not article:
                continue
            qty = int(item.get("qty") or 0)
            if qty <= 0:
                continue
            result[article] = result.get(article, 0) + qty
    return result


def classify_route_degree(degree, collinear=False):
    """Small topology primitive for the future perimeter-route graph.

    Degree is the number of conductive route edges meeting at a node.  It is kept
    intentionally geometric-agnostic so Revit curves, picked points or imported
    routes can all feed the same classifier later.
    """
    degree = int(degree or 0)
    if degree <= 1:
        return NODE_END
    if degree == 2:
        return NODE_JOIN if collinear else NODE_CORNER
    if degree == 3:
        return NODE_T_BRANCH
    return NODE_CROSSING



def _pt3(point):
    """Return a plain (x, y, z) tuple for topology math."""
    if isinstance(point, (list, tuple)) and len(point) >= 2:
        return (float(point[0]), float(point[1]), float(point[2]) if len(point) > 2 else 0.0)
    raise ValueError("Point must be a tuple/list with at least X and Y")


def _dist3(a, b):
    a = _pt3(a); b = _pt3(b)
    return ((a[0]-b[0])**2 + (a[1]-b[1])**2 + (a[2]-b[2])**2) ** 0.5


def _lerp_point(a, b, t):
    a = _pt3(a); b = _pt3(b); t = float(t)
    return (a[0] + (b[0]-a[0])*t,
            a[1] + (b[1]-a[1])*t,
            a[2] + (b[2]-a[2])*t)


def perimeter_length(route_points, closed=True):
    pts = [_pt3(p) for p in (route_points or [])]
    if len(pts) < 2:
        return 0.0
    total = sum(_dist3(pts[i], pts[i+1]) for i in range(len(pts)-1))
    if closed and len(pts) > 2:
        total += _dist3(pts[-1], pts[0])
    return total


def trim_open_route(route_points, used_length):
    """Return the prefix of an open polyline with the requested arc length."""
    pts = [_pt3(p) for p in (route_points or [])]
    if len(pts) < 2:
        raise ValueError("Open route requires at least two points")
    available = perimeter_length(pts, closed=False)
    target = float(used_length or 0.0)
    if target <= 0.0:
        raise ValueError("Used open-route length must be positive")
    if target > available + 1e-9:
        raise ValueError("Requested open-route length exceeds the guide")
    if target >= available - 1e-9:
        return pts

    result = [pts[0]]
    remaining = target
    for idx in range(len(pts) - 1):
        a = pts[idx]
        b = pts[idx + 1]
        segment_length = _dist3(a, b)
        if segment_length <= 1e-12:
            continue
        if remaining >= segment_length - 1e-12:
            result.append(b)
            remaining -= segment_length
            if remaining <= 1e-9:
                break
            continue
        result.append(_lerp_point(a, b, remaining / segment_length))
        remaining = 0.0
        break
    if len(result) < 2:
        raise ValueError("Could not trim open route")
    return result


def distribute_open_route_electrodes(route_points, count):
    """Place a fixed electrode count uniformly by arc length on an open route."""
    pts = [_pt3(p) for p in (route_points or [])]
    count = int(count or 0)
    if len(pts) < 2:
        raise ValueError("Open route requires at least two points")
    if count < 2:
        raise ValueError("Open route requires at least two electrodes")
    lengths = [_dist3(pts[idx], pts[idx + 1]) for idx in range(len(pts) - 1)]
    total = sum(lengths)
    if total <= 1e-9:
        raise ValueError("Open route length must be positive")
    spacing = total / float(count - 1)
    points = []
    directions = []
    segment_index = []
    for electrode_idx in range(count):
        distance = total if electrode_idx == count - 1 else spacing * electrode_idx
        walked = 0.0
        chosen = len(lengths) - 1
        for idx, segment_length in enumerate(lengths):
            if distance <= walked + segment_length + 1e-9:
                chosen = idx
                break
            walked += segment_length
        segment_length = lengths[chosen]
        if segment_length <= 1e-12:
            point = pts[chosen]
            direction = (1.0, 0.0, 0.0)
        else:
            local = max(0.0, min(segment_length, distance - walked))
            point = _lerp_point(pts[chosen], pts[chosen + 1], local / segment_length)
            a = pts[chosen]; b = pts[chosen + 1]
            direction = ((b[0] - a[0]) / segment_length,
                         (b[1] - a[1]) / segment_length,
                         (b[2] - a[2]) / segment_length)
        points.append(point)
        directions.append(direction)
        segment_index.append(chosen)
    return {
        "points": points,
        "directions": directions,
        "segment_index": segment_index,
        "route_points": pts,
        "closed": False,
        "spacing_min": spacing,
        "spacing_avg": spacing,
        "spacing_max": spacing,
        "perimeter_length": total,
    }


def distribute_perimeter_electrodes(route_points, max_spacing, closed=True, rods_at_corners=True):
    """Distribute electrodes along a perimeter polyline using plain coordinates.

    The first perimeter implementation deliberately treats ``max_spacing`` as a
    maximum, not an exact pitch.  With rods_at_corners=True every route vertex gets
    a vertical electrode and extra electrodes are inserted on long segments so no
    along-route gap exceeds max_spacing.  This keeps the strip polyline exact and
    makes the later node/fitting classifier deterministic.
    """
    pts = [_pt3(p) for p in (route_points or [])]
    minimum_points = 3 if closed else 2
    if len(pts) < minimum_points:
        raise ValueError("Closed route requires at least three points" if closed else
                         "Open route requires at least two points")
    spacing = float(max_spacing or 0.0)
    if spacing <= 0.0:
        raise ValueError("Maximum perimeter electrode spacing must be positive")
    # Remove a duplicated closing point if the user picked the start point again.
    if len(pts) > 3 and _dist3(pts[0], pts[-1]) < 1e-9:
        pts = pts[:-1]

    seg_count = len(pts) if closed else len(pts) - 1
    result = []
    directions = []
    gaps = []
    segment_index = []
    for i in range(seg_count):
        a = pts[i]
        b = pts[(i + 1) % len(pts)]
        length = _dist3(a, b)
        if length <= 1e-9:
            continue
        divisions = max(1, int(__import__('math').ceil(length / spacing)))
        # For this first implementation route vertices are always preserved when
        # rods_at_corners=True.  This is intentionally conservative for node logic.
        start_j = 0 if rods_at_corners else (0 if not result else 1)
        for j in range(start_j, divisions):
            t = float(j) / float(divisions)
            p = _lerp_point(a, b, t)
            if result and _dist3(result[-1], p) < 1e-9:
                continue
            result.append(p)
            dx, dy, dz = b[0]-a[0], b[1]-a[1], b[2]-a[2]
            norm = (dx*dx + dy*dy + dz*dz) ** 0.5
            directions.append((dx/norm, dy/norm, dz/norm))
            segment_index.append(i)
        gap = length / float(divisions)
        gaps.extend([gap] * divisions)

    if not closed and result:
        if _dist3(result[-1], pts[-1]) > 1e-9:
            result.append(pts[-1])
            directions.append(directions[-1] if directions else (1.0, 0.0, 0.0))
            segment_index.append(max(0, seg_count - 1))

    if not result:
        raise ValueError("Could not distribute electrodes on perimeter route")
    return {
        "points": result,
        "directions": directions,
        "segment_index": segment_index,
        "route_points": pts,
        "closed": bool(closed),
        "spacing_min": min(gaps) if gaps else 0.0,
        "spacing_avg": (sum(gaps) / float(len(gaps))) if gaps else 0.0,
        "spacing_max": max(gaps) if gaps else 0.0,
        "perimeter_length": perimeter_length(pts, closed),
    }


def perimeter_route_nodes(route_points, closed=True, tolerance=1e-6):
    """Classify the vertices of a simple perimeter polyline as JOIN/CORNER nodes."""
    import math
    pts = [_pt3(p) for p in (route_points or [])]
    if len(pts) < 2:
        return []
    result = []
    n = len(pts)
    for i, p in enumerate(pts):
        if not closed and (i == 0 or i == n-1):
            node_type = NODE_END
            result.append(make_node(node_type, "R-{}".format(i+1), point_key=i, meta={"route_vertex": True}))
            continue
        prev = pts[(i-1) % n]; nxt = pts[(i+1) % n]
        v1 = (p[0]-prev[0], p[1]-prev[1])
        v2 = (nxt[0]-p[0], nxt[1]-p[1])
        l1 = max(tolerance, math.hypot(v1[0], v1[1])); l2 = max(tolerance, math.hypot(v2[0], v2[1]))
        cross = abs((v1[0]/l1)*(v2[1]/l2) - (v1[1]/l1)*(v2[0]/l2))
        node_type = NODE_JOIN if cross <= tolerance else NODE_CORNER
        result.append(make_node(node_type, "R-{}".format(i+1), point_key=i,
                                meta={"route_vertex": True, "collinear": node_type == NODE_JOIN}))
    return result


def perimeter_vertical_nodes(module_count, electrode_count, clamp_article="90540"):
    return [vertical_electrode_node(i, module_count, clamp_article)
            for i in range(max(0, int(electrode_count or 0)))]
