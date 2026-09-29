# -*- coding: utf-8 -*-
from __future__ import division, print_function

"""Pure helpers for grounding placement guides.

The Revit layer converts ModelCurve geometry to plain metre tuples and this
module validates/orders the resulting chain.  Keeping the topology pure makes
it testable without Revit and prepares the future "Update by guide" command.
"""

import math


def _pt3(value):
    if value is None or len(value) < 2:
        raise ValueError("Guide point must contain at least X and Y")
    return (float(value[0]), float(value[1]), float(value[2]) if len(value) > 2 else 0.0)


def _dist(a, b):
    a = _pt3(a); b = _pt3(b)
    return math.sqrt((a[0]-b[0])**2 + (a[1]-b[1])**2 + (a[2]-b[2])**2)


def _xy_orient(a, b, c):
    return (b[0]-a[0]) * (c[1]-a[1]) - (b[1]-a[1]) * (c[0]-a[0])


def _xy_on_segment(a, b, p, tol):
    return (min(a[0], b[0]) - tol <= p[0] <= max(a[0], b[0]) + tol and
            min(a[1], b[1]) - tol <= p[1] <= max(a[1], b[1]) + tol)


def _segments_intersect_xy(a, b, c, d, tol=1e-9):
    o1 = _xy_orient(a, b, c); o2 = _xy_orient(a, b, d)
    o3 = _xy_orient(c, d, a); o4 = _xy_orient(c, d, b)
    if ((o1 > tol and o2 < -tol) or (o1 < -tol and o2 > tol)) and \
       ((o3 > tol and o4 < -tol) or (o3 < -tol and o4 > tol)):
        return True
    if abs(o1) <= tol and _xy_on_segment(a, b, c, tol): return True
    if abs(o2) <= tol and _xy_on_segment(a, b, d, tol): return True
    if abs(o3) <= tol and _xy_on_segment(c, d, a, tol): return True
    if abs(o4) <= tol and _xy_on_segment(c, d, b, tol): return True
    return False


def simplify_closed_route(points, tolerance_m=0.005):
    """Remove duplicate and collinear intermediate vertices from a closed route."""
    tolerance_m = max(1e-9, float(tolerance_m))
    items = [_pt3(point) for point in (points or [])]
    cleaned = []
    for point in items:
        if not cleaned or _dist(cleaned[-1], point) > tolerance_m:
            cleaned.append(point)
    if len(cleaned) > 1 and _dist(cleaned[0], cleaned[-1]) <= tolerance_m:
        cleaned.pop()
    if len(cleaned) < 3:
        raise ValueError("Closed route requires at least three distinct vertices")

    changed = True
    while changed and len(cleaned) > 3:
        changed = False
        result = []
        count = len(cleaned)
        for idx, current in enumerate(cleaned):
            previous = cleaned[(idx - 1) % count]
            following = cleaned[(idx + 1) % count]
            ax = current[0] - previous[0]; ay = current[1] - previous[1]
            bx = following[0] - current[0]; by = following[1] - current[1]
            alen = math.sqrt(ax * ax + ay * ay)
            blen = math.sqrt(bx * bx + by * by)
            cross = abs(ax * by - ay * bx)
            dot = ax * bx + ay * by
            collinear = (alen > tolerance_m and blen > tolerance_m and
                         cross <= tolerance_m * max(alen, blen) and dot > 0.0)
            if collinear:
                changed = True
                continue
            result.append(current)
        if len(result) < 3:
            break
        cleaned = result
    return cleaned


def simplify_open_route(points, tolerance_m=0.005):
    """Remove duplicate and collinear intermediate vertices from an open route."""
    tolerance_m = max(1e-9, float(tolerance_m))
    items = [_pt3(point) for point in (points or [])]
    cleaned = []
    for point in items:
        if not cleaned or _dist(cleaned[-1], point) > tolerance_m:
            cleaned.append(point)
    if len(cleaned) < 2:
        raise ValueError("Open route requires at least two distinct vertices")

    changed = True
    while changed and len(cleaned) > 2:
        changed = False
        result = [cleaned[0]]
        for idx in range(1, len(cleaned) - 1):
            previous = result[-1]
            current = cleaned[idx]
            following = cleaned[idx + 1]
            ax = current[0] - previous[0]; ay = current[1] - previous[1]
            bx = following[0] - current[0]; by = following[1] - current[1]
            alen = math.sqrt(ax * ax + ay * ay)
            blen = math.sqrt(bx * bx + by * by)
            cross = abs(ax * by - ay * bx)
            dot = ax * bx + ay * by
            collinear = (alen > tolerance_m and blen > tolerance_m and
                         cross <= tolerance_m * max(alen, blen) and dot > 0.0)
            if collinear:
                changed = True
                continue
            result.append(current)
        result.append(cleaned[-1])
        cleaned = result
    return cleaned


def validate_guide_planarity(segments, tolerance_z_m=0.005):
    """Require all guide endpoints to lie in one horizontal plane."""
    zs = []
    for segment in segments or []:
        zs.extend([_pt3(segment["start"])[2], _pt3(segment["end"])[2]])
    if not zs:
        raise ValueError("No guide segments")
    if max(zs) - min(zs) > float(tolerance_z_m):
        raise ValueError("Guide lines are not in one horizontal plane")
    return sum(zs) / float(len(zs))


def _node_for_point(nodes, point, tolerance_m):
    for idx, existing in enumerate(nodes):
        if _dist(existing, point) <= tolerance_m:
            return idx
    nodes.append(_pt3(point))
    return len(nodes) - 1


def _validate_self_intersections(points, closed, tolerance_m):
    pts = [_pt3(p) for p in points]
    count = len(pts) if closed else len(pts) - 1
    edges = []
    for i in range(count):
        edges.append((i, (i + 1) % len(pts), pts[i], pts[(i + 1) % len(pts)]))
    for i in range(len(edges)):
        ia, ib, a, b = edges[i]
        for j in range(i + 1, len(edges)):
            ja, jb, c, d = edges[j]
            # Adjacent edges legitimately meet at a common route vertex.
            if ia in (ja, jb) or ib in (ja, jb):
                continue
            if _segments_intersect_xy(a, b, c, d, tolerance_m):
                raise ValueError("Guide route self-intersects")



def connected_guide_component(segments, seed_id, tolerance_m=0.005):
    """Return only the connected component containing ``seed_id``.

    This is intentionally pure Python so the Revit layer can scan candidate
    Model Lines once, then resolve the selected route without repeated API
    calls or a multi-select/Done workflow.
    """
    items = list(segments or [])
    if not items:
        raise ValueError("No guide segments available")
    seed_text = str(seed_id)
    nodes = []
    edge_nodes = []
    seed_index = None
    for idx, segment in enumerate(items):
        a = _pt3(segment["start"]); b = _pt3(segment["end"])
        if _dist(a, b) <= tolerance_m:
            continue
        na = _node_for_point(nodes, a, tolerance_m)
        nb = _node_for_point(nodes, b, tolerance_m)
        edge_nodes.append((idx, na, nb))
        if str(segment.get("id", idx)) == seed_text:
            seed_index = idx
    if seed_index is None:
        raise ValueError("Selected guide line was not found in candidate set")

    by_node = {}
    for idx, na, nb in edge_nodes:
        by_node.setdefault(na, []).append(idx)
        by_node.setdefault(nb, []).append(idx)
    idx_nodes = {idx: (na, nb) for idx, na, nb in edge_nodes}

    queue = [seed_index]
    used = set()
    while queue:
        idx = queue.pop()
        if idx in used or idx not in idx_nodes:
            continue
        used.add(idx)
        na, nb = idx_nodes[idx]
        for node in (na, nb):
            for other in by_node.get(node, []):
                if other not in used:
                    queue.append(other)
    return [items[idx] for idx in sorted(used)]

def order_guide_segments(segments, tolerance_m=0.005, require_closed=None):
    """Order connected straight guide segments into one deterministic route.

    ``segments`` items: {id, start=(x,y,z), end=(x,y,z)} in metres.
    Returns ordered route points without duplicated closing point plus ordered
    source ids. Branching is intentionally rejected in this foundation stage;
    T-branches will be handled by the next topology layer.
    """
    items = list(segments or [])
    if not items:
        raise ValueError("No guide segments selected")
    validate_guide_planarity(items, max(0.001, float(tolerance_m)))

    nodes = []
    edges = []
    adjacency = {}
    for idx, segment in enumerate(items):
        a = _pt3(segment["start"]); b = _pt3(segment["end"])
        if _dist(a, b) <= tolerance_m:
            raise ValueError("Guide contains a zero-length segment")
        na = _node_for_point(nodes, a, tolerance_m)
        nb = _node_for_point(nodes, b, tolerance_m)
        if na == nb:
            raise ValueError("Guide contains a zero-length segment")
        edge = {"index": idx, "a": na, "b": nb, "id": str(segment.get("id", idx))}
        edges.append(edge)
        adjacency.setdefault(na, []).append(idx)
        adjacency.setdefault(nb, []).append(idx)

    branch_nodes = [node for node, edge_ids in adjacency.items() if len(edge_ids) > 2]
    if branch_nodes:
        raise ValueError("Guide contains a branch; select one simple chain/contour")

    endpoints = [node for node, edge_ids in adjacency.items() if len(edge_ids) == 1]
    closed = len(endpoints) == 0
    if not closed and len(endpoints) != 2:
        raise ValueError("Guide segments do not form one continuous chain")
    if require_closed is True and not closed:
        raise ValueError("Perimeter guide must be a closed chain")
    if require_closed is False and closed:
        raise ValueError("Guide must be an open chain")

    start = min(endpoints) if endpoints else 0
    ordered_points = [nodes[start]]
    ordered_ids = []
    used = set()
    current = start
    previous_edge = None

    while len(used) < len(edges):
        candidates = [eid for eid in adjacency.get(current, []) if eid not in used]
        if not candidates:
            break
        # At a simple chain node there is at most one unused outgoing edge.
        eid = min(candidates)
        edge = edges[eid]
        used.add(eid)
        ordered_ids.append(edge["id"])
        nxt = edge["b"] if edge["a"] == current else edge["a"]
        if closed and nxt == start and len(used) == len(edges):
            current = nxt
            break
        ordered_points.append(nodes[nxt])
        current = nxt
        previous_edge = eid

    if len(used) != len(edges):
        raise ValueError("Selected guide lines contain disconnected fragments")
    if closed and len(ordered_points) < 3:
        raise ValueError("Closed guide requires at least three vertices")
    if not closed and len(ordered_points) < 2:
        raise ValueError("Open guide requires at least two vertices")

    _validate_self_intersections(ordered_points, closed, tolerance_m * 0.2)
    length = 0.0
    for idx in range(len(ordered_points) - 1):
        length += _dist(ordered_points[idx], ordered_points[idx + 1])
    if closed:
        length += _dist(ordered_points[-1], ordered_points[0])

    return {
        "points": ordered_points,
        "segment_ids": ordered_ids,
        "closed": bool(closed),
        "length_m": length,
        "elevation_m": sum(p[2] for p in ordered_points) / float(len(ordered_points)),
    }


def order_perimeter_segments(segments, tolerance_m=0.005, auto_close_open=False):
    """Order a perimeter and optionally close one open chain geometrically.

    Revit Model Lines do not keep neighbouring endpoints constrained when a user
    drags one endpoint.  Consequently an otherwise valid edited loop can arrive
    as one open chain.  For the contour-edit workflow the intended closing edge
    is unambiguous: it is the straight segment between the chain endpoints.
    Self-intersection is validated again with that implicit edge included.
    """
    ordered = order_guide_segments(
        segments, tolerance_m=tolerance_m, require_closed=None)
    if ordered["closed"]:
        ordered["auto_closed"] = False
        ordered["closing_length_m"] = 0.0
        return ordered
    if not auto_close_open:
        raise ValueError("Perimeter guide must be a closed chain")

    points = list(ordered.get("points") or [])
    if len(points) < 3:
        raise ValueError("Perimeter guide requires at least three vertices")
    _validate_self_intersections(points, True, float(tolerance_m) * 0.2)
    closing_length = _dist(points[-1], points[0])
    ordered["closed"] = True
    ordered["auto_closed"] = True
    ordered["closing_length_m"] = closing_length
    ordered["length_m"] = float(ordered["length_m"]) + closing_length
    return ordered


def orient_open_guide(ordered, start_hint):
    """Orient an open chain so the endpoint nearest the selection click is first."""
    result = dict(ordered or {})
    points = list(result.get("points") or [])
    segment_ids = list(result.get("segment_ids") or [])
    result["start_reversed"] = False
    if result.get("closed") or len(points) < 2 or start_hint is None:
        return result
    hint = _pt3(start_hint)
    if _dist(hint, points[-1]) + 1e-9 < _dist(hint, points[0]):
        points.reverse()
        segment_ids.reverse()
        result["points"] = points
        result["segment_ids"] = segment_ids
        result["start_reversed"] = True
    return result
