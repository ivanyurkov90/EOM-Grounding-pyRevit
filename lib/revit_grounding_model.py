# -*- coding: utf-8 -*-
from __future__ import division, print_function

import json
import math
import uuid
import os
import clr
import System
from System.Collections.Generic import List
from perf_trace import mark as trace_mark, mark_exception as trace_exception, exception_text
from plugin_version import VERSION as PLUGIN_VERSION, BUILD_ID as PLUGIN_BUILD_ID
from grounding_topology import (FITTING_CATALOG, NODE_VERTICAL_ELECTRODE,
                                vertical_electrode_node, aggregate_bom,
                                distribute_perimeter_electrodes, perimeter_route_nodes,
                                perimeter_length, trim_open_route,
                                distribute_open_route_electrodes)
from grounding_core import optimize_open_contour_length, number_value
from guide_path import (order_guide_segments, order_perimeter_segments,
                        connected_guide_component, simplify_closed_route,
                        simplify_open_route, orient_open_guide)

M_TO_FT = 1.0 / 0.3048
FT_TO_M = 0.3048

LAYOUT_LINE = u"Линия"
LAYOUT_TRIANGLE = u"Треугольник"
LAYOUT_MANUAL = u"Точки вручную"
LAYOUT_PERIMETER = u"Контур по периметру"
LAYOUT_OPEN_PERIMETER = u"Незамкнутый контур"
PLACEMENT_MANUAL = u"Вручную (PickPoint)"
PLACEMENT_GUIDE = u"По направляющим Revit (Model Lines)"

APP_ID = "EOM_GROUNDING_PYREVIT"
SCHEMA_GUID = "57E02D72-6721-4E6D-97E8-00A62A36A64B"
SCHEMA_NAME = "EOMGroundingDataV1"
SCHEMA_FIELD = "DataJson"

# BIM family contract for the vertical modular electrode.
ROD_FAMILY_DEFAULT = u"EZETEK 90136 — Ø16×1500 мм"
ROD_EZETEK_90136_ALIAS = ROD_FAMILY_DEFAULT
ROD_EZETEK_90136_FILE = u"EZETEK_90136.rfa"
ROD_EZETEK_90136_DIAMETER_MM = 16.0
ROD_EZETEK_90136_MODULE_M = 1.5
EZETEK_90227_DIAMETER_MM = 24.0
EZETEK_90227_LENGTH_M = 0.070
EZETEK_90326_DIAMETER_MM = 24.0
EZETEK_90326_LENGTH_M = 0.045
EZETEK_90540_ARTICLE = u"90540"
# 90540 is modeled as a lightweight four-bolt cross clamp according to the
# EZETEK typical-solutions album envelope (70 x 50 mm). Detailed dimensions
# live in grounding_topology.FITTING_CATALOG so future perimeter fittings use
# the same rule/catalog layer.
ROD_PARAM_LENGTH = u"ЭОМ_Длина"
ROD_PARAM_DIAMETER = u"ЭОМ_Диаметр"
ROD_PARAM_TOP_DEPTH = u"ЭОМ_Глубина_верха"
ROD_PARAM_MATERIAL = u"ЭОМ_Материал"
ROD_PARAM_GROUP = u"ЭОМ_Группа_ЗУ"
ROD_PARAM_ROLE = u"ЭОМ_Роль"
ROD_PARAM_NUMBER = u"ЭОМ_Номер"
ROD_PARAM_MODULE_COUNT = u"ЭОМ_Количество_секций"
ROD_PARAM_MODULE_LENGTH = u"ЭОМ_Длина_секции"
ROD_REQUIRED_GEOMETRY_PARAMS = (ROD_PARAM_LENGTH, ROD_PARAM_DIAMETER, ROD_PARAM_TOP_DEPTH)




def m_to_ft(value):
    return float(value) * M_TO_FT


def ft_to_m(value):
    return float(value) * FT_TO_M


def _xy_direction(DB, p0, p1):
    vector = DB.XYZ(p1.X - p0.X, p1.Y - p0.Y, 0.0)
    if vector.GetLength() < m_to_ft(0.05):
        raise ValueError(u"Вторая точка должна отличаться от первой минимум на 50 мм.")
    return vector.Normalize()


def pick_layout_points(uidoc, DB, data):
    """Запрашивает у пользователя точки расположения электродов.

    Возвращает (points, close_loop). Точки считаются отметкой поверхности земли.
    """
    mode = data.get("layout_mode", LAYOUT_LINE)
    count = int(number_value(data.get("vertical_count"), 1))
    spacing = m_to_ft(number_value(data.get("vertical_spacing"), 0.0))

    if count < 1:
        raise ValueError(u"Количество электродов должно быть не менее одного.")

    if mode == LAYOUT_MANUAL:
        points = []
        for idx in range(count):
            try:
                point = uidoc.Selection.PickPoint(
                    u"ЭОМ: укажите точку электрода {} из {}".format(idx + 1, count))
            except Exception:
                return None, False
            points.append(point)
        return points, bool(data.get("close_loop", False)) and count > 2

    try:
        start = uidoc.Selection.PickPoint(u"ЭОМ: укажите первую точку заземлителя")
        direction_point = uidoc.Selection.PickPoint(u"ЭОМ: укажите направление размещения электродов")
    except Exception:
        return None, False

    direction = _xy_direction(DB, start, direction_point)

    if mode == LAYOUT_TRIANGLE:
        if count != 3:
            raise ValueError(u"Для режима «Треугольник» необходимо принять ровно 3 вертикальных электрода.")
        if spacing <= 0:
            raise ValueError(u"Для треугольника шаг должен быть больше 0.")
        side = DB.XYZ(-direction.Y, direction.X, 0.0)
        p1 = start
        p2 = start + direction.Multiply(spacing)
        p3 = start + direction.Multiply(spacing * 0.5) + side.Multiply(spacing * math.sqrt(3.0) / 2.0)
        return [p1, p2, p3], True

    if spacing <= 0 and count > 1:
        raise ValueError(u"Для линейного размещения шаг должен быть больше 0.")
    points = []
    for idx in range(count):
        points.append(start + direction.Multiply(spacing * idx))
    return points, False




def _xyz_tuple_m(point):
    return (ft_to_m(point.X), ft_to_m(point.Y), ft_to_m(point.Z))


def _tuple_m_xyz(DB, point):
    return DB.XYZ(m_to_ft(point[0]), m_to_ft(point[1]), m_to_ft(point[2]))




def _curve_endpoints_m(curve):
    return (_xyz_tuple_m(curve.GetEndPoint(0)), _xyz_tuple_m(curve.GetEndPoint(1)))


def _validate_model_guide_element(element, DB):
    if element is None or not isinstance(element, DB.ModelCurve):
        raise ValueError(u"Выберите линии модели Revit (Model Lines), а не линии детализации или другие элементы.")
    curve = element.GeometryCurve
    if curve is None:
        raise ValueError(u"У выбранной направляющей отсутствует геометрическая кривая.")
    if not isinstance(curve, DB.Line):
        raise ValueError(u"В этой версии направляющая должна состоять из прямых Model Lines. Дуги будут добавлены позже.")
    return curve


def _id_key(value):
    try:
        return int(value.IntegerValue)
    except Exception:
        try:
            return int(value.Value)
        except Exception:
            return u"{}".format(value)


def _model_line_filter(UI, DB):
    class _Filter(UI.Selection.ISelectionFilter):
        def AllowElement(self, element):
            try:
                return isinstance(element, DB.ModelCurve) and isinstance(element.GeometryCurve, DB.Line)
            except Exception:
                return False
        def AllowReference(self, reference, point):
            return False
    return _Filter()


def _same_guide_scope(seed, candidate):
    """Keep auto-tracing inside the seed line style and geometric plane.

    Revit can assign a new SketchPlane element when a line is redrawn or
    trim/extend is used.  Comparing SketchPlane element ids therefore rejects a
    visually coplanar replacement segment.  The dedicated edit style scopes the
    group, while endpoint elevations provide the actual plane check.
    """
    try:
        seed_style = seed.LineStyle
        cand_style = candidate.LineStyle
        if seed_style is not None and cand_style is not None:
            if _id_key(seed_style.Id) != _id_key(cand_style.Id):
                return False
    except Exception:
        pass
    try:
        seed_curve = seed.GeometryCurve
        candidate_curve = candidate.GeometryCurve
        seed_z = (seed_curve.GetEndPoint(0).Z + seed_curve.GetEndPoint(1).Z) / 2.0
        for endpoint_idx in (0, 1):
            if abs(candidate_curve.GetEndPoint(endpoint_idx).Z - seed_z) > m_to_ft(0.005):
                return False
    except Exception:
        return False
    return True


def _candidate_model_guide_segments(doc, DB, seed):
    segments = []
    # ModelCurve is an API-only wrapper and cannot be passed to OfClass().
    # CurveElementFilter is Revit's supported native filter for its subclasses.
    # Apply the selected line's category first so a large project is not fully
    # expanded into Python wrappers before the ModelCurve test is evaluated.
    collector = DB.FilteredElementCollector(doc).WhereElementIsNotElementType()
    try:
        category = seed.Category
        if category is not None:
            collector = collector.OfCategoryId(category.Id)
    except Exception:
        pass
    model_curve_filter = DB.CurveElementFilter(DB.CurveElementType.ModelCurve)
    collector = collector.WherePasses(model_curve_filter)
    for element in collector:
        if element is None or not isinstance(element, DB.ModelCurve):
            continue
        if not _same_guide_scope(seed, element):
            continue
        try:
            curve = _validate_model_guide_element(element, DB)
            a, b = _curve_endpoints_m(curve)
            segments.append({"id": element.UniqueId, "start": a, "end": b})
        except Exception:
            continue
    return segments


def _guide_route_from_seed(doc, DB, seed, require_closed=True, auto_close_open=False,
                           start_hint=None):
    _validate_model_guide_element(seed, DB)
    trace_mark("GUIDE_SCAN_START", seed.UniqueId)
    candidates = _candidate_model_guide_segments(doc, DB, seed)
    trace_mark("GUIDE_SCAN_END", len(candidates))
    component = connected_guide_component(candidates, seed.UniqueId, tolerance_m=0.005)
    trace_mark("GUIDE_COMPONENT_END", len(component))
    if require_closed:
        ordered = order_perimeter_segments(
            component, tolerance_m=0.005, auto_close_open=auto_close_open)
    else:
        ordered = order_guide_segments(component, tolerance_m=0.005, require_closed=False)
        if start_hint is not None:
            ordered = orient_open_guide(ordered, _xyz_tuple_m(start_hint))
            trace_mark("GUIDE_START_ORIENTATION",
                       u"reversed={}".format(bool(ordered.get("start_reversed"))))
    trace_mark("GUIDE_ORDER_END", len(ordered.get("segment_ids") or []))
    if ordered.get("auto_closed"):
        trace_mark("GUIDE_AUTO_CLOSE", u"{:.3f} m".format(
            float(ordered.get("closing_length_m") or 0.0)))
    return {
        "points": [_tuple_m_xyz(DB, point) for point in ordered["points"]],
        "closed": bool(ordered["closed"]),
        "length_m": float(ordered["length_m"]),
        "guide_unique_ids": list(ordered["segment_ids"]),
        "guide_count": len(ordered["segment_ids"]),
        "auto_closed": bool(ordered.get("auto_closed")),
        "closing_length_m": float(ordered.get("closing_length_m") or 0.0),
        "start_reversed": bool(ordered.get("start_reversed")),
        "source": PLACEMENT_GUIDE,
    }


def pick_model_guide_route(uidoc, doc, DB, UI, require_closed=True):
    """Pick one seed Model Line and auto-trace its connected route.

    The previous PickObjects + Done workflow was deliberately removed because
    Revit could appear frozen immediately after Done while the result was being
    validated.  One seed line is enough; connected straight Model Lines on the
    same sketch plane/line style are resolved automatically.
    """
    if UI is None:
        raise ValueError(u"Для выбора направляющих требуется Revit UI API.")
    try:
        trace_mark("GUIDE_SEED_PICK_START")
        ref = uidoc.Selection.PickObject(
            UI.Selection.ObjectType.Element,
            _model_line_filter(UI, DB),
            u"ЭОМ: выберите Model Line ближе к нужному началу трассы; вся цепочка будет найдена автоматически")
        trace_mark("GUIDE_SEED_PICK_END")
    except Exception as ex:
        trace_exception("GUIDE_SEED_PICK_CANCEL", ex)
        return None
    seed = doc.GetElement(ref.ElementId)
    start_hint = None
    try:
        start_hint = ref.GlobalPoint
    except Exception:
        start_hint = None
    try:
        return _guide_route_from_seed(
            doc, DB, seed, require_closed=require_closed,
            start_hint=start_hint)
    except Exception as ex:
        trace_exception("GUIDE_ROUTE_ERROR", ex)
        raise ValueError(u"Некорректная направляющая: {}".format(ex))


def pick_single_rod_guide(uidoc, doc, DB, UI):
    """Pick one Model Line and a position; project the rod exactly to that guide."""
    if UI is None:
        raise ValueError(u"Для выбора направляющей требуется Revit UI API.")
    try:
        ref = uidoc.Selection.PickObject(
            UI.Selection.ObjectType.Element,
            u"ЭОМ: выберите Model Line, задающую положение/направление одиночного электрода")
    except Exception:
        return None
    element = doc.GetElement(ref.ElementId)
    curve = _validate_model_guide_element(element, DB)
    try:
        picked = uidoc.Selection.PickPoint(u"ЭОМ: укажите положение электрода рядом с направляющей")
    except Exception:
        return None
    projection = curve.Project(picked)
    if projection is None or projection.XYZPoint is None:
        raise ValueError(u"Не удалось спроецировать точку электрода на выбранную направляющую.")
    point = projection.XYZPoint
    p0 = curve.GetEndPoint(0); p1 = curve.GetEndPoint(1)
    direction = _xy_direction(DB, p0, p1)
    return {
        "point": point,
        "direction": direction,
        "guide_unique_ids": [element.UniqueId],
        "source": PLACEMENT_GUIDE,
    }


def pick_perimeter_route(uidoc, DB, closed=True):
    """Pick a closed or open route; ESC completes the point sequence."""
    points = []
    while True:
        try:
            if not points:
                prompt = u"ЭОМ: укажите первую точку {} контура".format(
                    u"замкнутого" if closed else u"незамкнутого")
            else:
                prompt = u"ЭОМ: следующая точка контура; ESC — завершить"
            p = uidoc.Selection.PickPoint(prompt)
        except Exception:
            break
        if closed and points and len(points) >= 3 and p.DistanceTo(points[0]) <= m_to_ft(0.15):
            break
        if points and p.DistanceTo(points[-1]) <= m_to_ft(0.05):
            continue
        points.append(p)
    if len(points) < (3 if closed else 2):
        return None
    return points


def pick_grounding_layout(uidoc, doc, DB, UI, data):
    """Return electrodes, strip route and directions for any supported layout."""
    layout_mode = data.get("layout_mode")
    if layout_mode not in (LAYOUT_PERIMETER, LAYOUT_OPEN_PERIMETER):
        points, close_loop = pick_layout_points(uidoc, DB, data)
        if not points:
            return None
        return {
            "electrode_points": points,
            "strip_points": points,
            "electrode_directions": None,
            "close_loop": bool(close_loop),
            "mode": data.get("layout_mode"),
            "spacing_min_m": 0.0,
            "spacing_avg_m": number_value(data.get("vertical_spacing"), 0.0),
            "spacing_max_m": 0.0,
            "route_nodes": [],
        }

    closed_route = layout_mode == LAYOUT_PERIMETER
    placement_source = data.get("placement_source", PLACEMENT_MANUAL)
    guide_info = None
    if placement_source == PLACEMENT_GUIDE:
        guide_info = pick_model_guide_route(
            uidoc, doc, DB, UI, require_closed=closed_route)
        if not guide_info:
            return None
        route = guide_info["points"]
    else:
        route = pick_perimeter_route(uidoc, DB, closed=closed_route)
        if not route:
            return None
    max_spacing = number_value(data.get("vertical_spacing"), 0.0)
    plain_route = [_xyz_tuple_m(p) for p in route]
    open_design = None
    fixed_open_length = bool(data.get("open_contour_fixed_length", False))
    if closed_route:
        layout = distribute_perimeter_electrodes(
            plain_route, max_spacing, closed=True, rods_at_corners=True)
    elif fixed_open_length:
        layout = distribute_perimeter_electrodes(
            plain_route, max_spacing, closed=False, rods_at_corners=True)
    else:
        available_length = perimeter_length(plain_route, closed=False)
        open_design = optimize_open_contour_length(data, available_length)
        plain_route = trim_open_route(plain_route, open_design["used_length_m"])
        route = [_tuple_m_xyz(DB, point) for point in plain_route]
        layout = distribute_open_route_electrodes(
            plain_route, open_design["count"])
    electrodes = [_tuple_m_xyz(DB, p) for p in layout["points"]]
    directions = [DB.XYZ(d[0], d[1], 0.0) for d in layout["directions"]]
    # Persist the exact strip polyline independently from electrode positions.
    # On an optimized open route, a bend does not have to coincide with a rod.
    data["guide_route_json"] = json.dumps(plain_route, ensure_ascii=False)
    return {
        "electrode_points": electrodes,
        "strip_points": route,
        "electrode_directions": directions,
        "close_loop": closed_route,
        "mode": layout_mode,
        "spacing_min_m": layout["spacing_min"],
        "spacing_avg_m": layout["spacing_avg"],
        "spacing_max_m": layout["spacing_max"],
        "perimeter_length_m": layout["perimeter_length"],
        "route_nodes": perimeter_route_nodes(plain_route, closed_route),
        "placement_source": placement_source,
        "guide_unique_ids": list((guide_info or {}).get("guide_unique_ids") or []),
        "guide_count": int((guide_info or {}).get("guide_count") or 0),
        "open_design": open_design,
        "open_contour_fixed_length": bool(fixed_open_length),
    }


def route_length_ft(points, close_loop=False):
    if not points or len(points) < 2:
        return 0.0
    total = 0.0
    for idx in range(len(points) - 1):
        total += points[idx].DistanceTo(points[idx + 1])
    if close_loop and len(points) > 2:
        total += points[-1].DistanceTo(points[0])
    return total


def route_length_m(points, close_loop=False):
    return ft_to_m(route_length_ft(points, close_loop))


def spacing_metrics_m(points):
    """Возвращает min/avg/max расстояний между соседними электродами и min всех пар."""
    if not points or len(points) < 2:
        return {"min": 0.0, "avg": 0.0, "max": 0.0, "pair_min": 0.0}
    adjacent = [ft_to_m(points[i].DistanceTo(points[i + 1])) for i in range(len(points) - 1)]
    all_pairs = []
    for i in range(len(points)):
        for j in range(i + 1, len(points)):
            all_pairs.append(ft_to_m(points[i].DistanceTo(points[j])))
    return {
        "min": min(adjacent),
        "avg": sum(adjacent) / len(adjacent),
        "max": max(adjacent),
        "pair_min": min(all_pairs)
    }


def _curve_loop(DB, curves):
    loop = DB.CurveLoop()
    for curve in curves:
        loop.Append(curve)
    loops = List[DB.CurveLoop]()
    loops.Add(loop)
    return loops


def _rod_solid(DB, ground_point, top_depth_m, length_m, diameter_mm):
    radius = m_to_ft(float(diameter_mm) / 2000.0)
    top_z = ground_point.Z - m_to_ft(top_depth_m)
    center = DB.XYZ(ground_point.X, ground_point.Y, top_z)

    left = center + DB.XYZ(-radius, 0, 0)
    right = center + DB.XYZ(radius, 0, 0)
    top = center + DB.XYZ(0, radius, 0)
    bottom = center + DB.XYZ(0, -radius, 0)

    arc1 = DB.Arc.Create(left, right, top)
    arc2 = DB.Arc.Create(right, left, bottom)
    loops = _curve_loop(DB, [arc1, arc2])
    return DB.GeometryCreationUtilities.CreateExtrusionGeometry(
        loops, DB.XYZ(0, 0, -1), m_to_ft(length_m))



def _circle_loop_at_z(DB, x, y, z, radius_ft):
    center = DB.XYZ(x, y, z)
    left = center + DB.XYZ(-radius_ft, 0, 0)
    right = center + DB.XYZ(radius_ft, 0, 0)
    top = center + DB.XYZ(0, radius_ft, 0)
    bottom = center + DB.XYZ(0, -radius_ft, 0)
    loop = DB.CurveLoop()
    loop.Append(DB.Arc.Create(left, right, top))
    loop.Append(DB.Arc.Create(right, left, bottom))
    return loop


def _cylinder_solid(DB, x, y, top_z, length_m, diameter_mm):
    radius = m_to_ft(float(diameter_mm) / 2000.0)
    loops = List[DB.CurveLoop]()
    loops.Add(_circle_loop_at_z(DB, x, y, top_z, radius))
    return DB.GeometryCreationUtilities.CreateExtrusionGeometry(
        loops, DB.XYZ(0, 0, -1), m_to_ft(length_m))


def _tip_proxy_solid(DB, x, y, top_z):
    """Lightweight BIM proxy for EZETEK 90326 (24x24x45 mm published envelope).

    The real RFA is not published in the current EZETEK BIM library, therefore the
    proxy deliberately models only the installation envelope: a short cylindrical
    shoulder and a tapered point. It is tagged PRODUCT=90326 and can be replaced by
    the exact family later without changing group logic.
    """
    radius = m_to_ft(EZETEK_90326_DIAMETER_MM / 2000.0)
    total = m_to_ft(EZETEK_90326_LENGTH_M)
    shoulder = min(m_to_ft(0.018), total * 0.45)
    try:
        profiles = List[DB.CurveLoop]()
        profiles.Add(_circle_loop_at_z(DB, x, y, top_z, radius))
        profiles.Add(_circle_loop_at_z(DB, x, y, top_z - shoulder, radius))
        profiles.Add(_circle_loop_at_z(DB, x, y, top_z - total, m_to_ft(0.0005)))
        options = DB.SolidOptions(DB.ElementId.InvalidElementId, DB.ElementId.InvalidElementId)
        return DB.GeometryCreationUtilities.CreateLoftGeometry(profiles, options)
    except Exception:
        # Conservative fallback if Loft is unavailable in a specific Revit build.
        return _cylinder_solid(DB, x, y, top_z, EZETEK_90326_LENGTH_M, EZETEK_90326_DIAMETER_MM)


def _create_ezetek_90136_accessories(doc, DB, ground_point, top_z, joint_zs, bottom_z, data, group_id, idx, close_loop):
    """Create EZETEK 90227 couplings and one 90326 starting tip as BIM proxies.

    EZETEK grounding sets contain one 90227 coupling per 90136 rod, not only one
    per inter-rod joint.  Therefore the installed model contains:
      * one TOP coupling centred on the physical top end of the upper rod;
      * one coupling at every physical inter-module joint;
      * one 90326 starting tip at the physical bottom of the last rod.

    Every location is derived from the actually placed 90136 FamilyInstances, never
    from the family insertion point.
    """
    created = []
    coupling_len_ft = m_to_ft(EZETEK_90227_LENGTH_M)
    coupling_planes = [(u"TOP", top_z)] + [(u"JOINT{}".format(i), z) for i, z in enumerate(joint_zs, 1)]
    for coupling_idx, pair in enumerate(coupling_planes, 1):
        joint_name, joint_z = pair
        coupling_top = joint_z + coupling_len_ft / 2.0
        solid = _cylinder_solid(DB, ground_point.X, ground_point.Y, coupling_top,
                                EZETEK_90227_LENGTH_M, EZETEK_90227_DIAMETER_MM)
        node_id = "VE-{}".format(idx + 1)
        comments = (u"EOM_GROUNDING;GROUP={};ROLE=COUPLING;INDEX={};COUPLING={};LOCATION={};PARENT_INDEX={};"
                    u"NODE_TYPE=VERTICAL_ELECTRODE;NODE_ID={};MODE=BIM_PROXY;PRODUCT=90227;"
                    u"FOR_PRODUCT=90136;D_MM=24.0;L_MM=70"
                    .format(group_id, idx + 1, coupling_idx, joint_name, idx + 1, node_id))
        mark = u"ЗУ-В{}-МУФ{}".format(idx + 1, coupling_idx)
        created.append(_new_direct_shape(
            doc, DB, solid, "{}:COUPLING:{}:{}".format(group_id, idx + 1, coupling_idx),
            comments, mark, data=data, group_id=group_id, close_loop=close_loop,
            preferred_bic=DB.BuiltInCategory.OST_ElectricalEquipment))

    tip = _tip_proxy_solid(DB, ground_point.X, ground_point.Y, bottom_z)
    node_id = "VE-{}".format(idx + 1)
    comments = (u"EOM_GROUNDING;GROUP={};ROLE=TIP;INDEX={};PARENT_INDEX={};"
                u"NODE_TYPE=VERTICAL_ELECTRODE;NODE_ID={};MODE=BIM_PROXY;PRODUCT=90326;"
                u"FOR_PRODUCT=90136;D_MM=24.0;L_MM=45"
                .format(group_id, idx + 1, idx + 1, node_id))
    mark = u"ЗУ-В{}-НАК".format(idx + 1)
    created.append(_new_direct_shape(
        doc, DB, tip, "{}:TIP:{}".format(group_id, idx + 1), comments, mark,
        data=data, group_id=group_id, close_loop=close_loop,
        preferred_bic=DB.BuiltInCategory.OST_ElectricalEquipment))
    return created

def _strip_solid(DB, ground_start, ground_end, depth_m, width_mm, thickness_mm):
    start = DB.XYZ(ground_start.X, ground_start.Y, ground_start.Z - m_to_ft(depth_m))
    end = DB.XYZ(ground_end.X, ground_end.Y, ground_end.Z - m_to_ft(depth_m))
    axis = end - start
    length = axis.GetLength()
    if length < m_to_ft(0.01):
        raise ValueError(u"Сегмент горизонтального электрода короче 10 мм.")
    direction = axis.Normalize()

    global_up = DB.XYZ.BasisZ
    side = global_up.CrossProduct(direction)
    if side.GetLength() < 1e-9:
        side = DB.XYZ.BasisX
    else:
        side = side.Normalize()
    profile_up = direction.CrossProduct(side).Normalize()

    half_w = m_to_ft(float(width_mm) / 2000.0)
    half_t = m_to_ft(float(thickness_mm) / 2000.0)

    p1 = start + side.Multiply(half_w) + profile_up.Multiply(half_t)
    p2 = start - side.Multiply(half_w) + profile_up.Multiply(half_t)
    p3 = start - side.Multiply(half_w) - profile_up.Multiply(half_t)
    p4 = start + side.Multiply(half_w) - profile_up.Multiply(half_t)

    curves = [
        DB.Line.CreateBound(p1, p2),
        DB.Line.CreateBound(p2, p3),
        DB.Line.CreateBound(p3, p4),
        DB.Line.CreateBound(p4, p1),
    ]
    loops = _curve_loop(DB, curves)
    return DB.GeometryCreationUtilities.CreateExtrusionGeometry(loops, direction, length)


def _shape_list(DB, solid):
    items = List[DB.GeometryObject]()
    items.Add(solid)
    return items


def _set_parameter(element, DB, bip, text):
    try:
        parameter = element.get_Parameter(bip)
        if parameter and not parameter.IsReadOnly:
            parameter.Set(text)
    except Exception:
        pass


def _get_parameter_text(element, DB, bip):
    try:
        parameter = element.get_Parameter(bip)
        if parameter:
            return parameter.AsString() or u""
    except Exception:
        pass
    return u""


def _get_schema(DB):
    guid = System.Guid(SCHEMA_GUID)
    schema = DB.ExtensibleStorage.Schema.Lookup(guid)
    if schema is not None:
        return schema
    builder = DB.ExtensibleStorage.SchemaBuilder(guid)
    builder.SetSchemaName(SCHEMA_NAME)
    builder.SetDocumentation("EOM Grounding pyRevit calculation and BIM group data")
    builder.SetReadAccessLevel(DB.ExtensibleStorage.AccessLevel.Public)
    builder.SetWriteAccessLevel(DB.ExtensibleStorage.AccessLevel.Public)
    builder.AddSimpleField(SCHEMA_FIELD, System.String)
    return builder.Finish()


def _json_safe_data(data, group_id, close_loop):
    payload = {"group_id": group_id, "close_loop": bool(close_loop), "version": PLUGIN_VERSION, "build_id": PLUGIN_BUILD_ID, "data": {}}
    for key, value in data.items():
        if value is None or isinstance(value, (bool, int, float)):
            payload["data"][key] = value
        else:
            payload["data"][key] = u"{}".format(value)
    return payload


def _set_metadata(element, DB, data, group_id, close_loop):
    try:
        schema = _get_schema(DB)
        entity = DB.ExtensibleStorage.Entity(schema)
        text = json.dumps(_json_safe_data(data, group_id, close_loop), ensure_ascii=False)
        entity.Set[System.String](schema.GetField(SCHEMA_FIELD), text)
        element.SetEntity(entity)
    except Exception as ex:
        # Геометрия не должна падать из-за служебных метаданных, но причина должна быть видна в trace.
        trace_exception("METADATA_WRITE_ERROR", ex, u"group={}".format(group_id))


def _read_metadata(element, DB):
    try:
        schema = _get_schema(DB)
        entity = element.GetEntity(schema)
        if not entity.IsValid():
            return None
        text = entity.Get[System.String](schema.GetField(SCHEMA_FIELD))
        if not text:
            return None
        return json.loads(text)
    except Exception:
        return None


def _new_direct_shape(doc, DB, solid, app_data_id, comments, mark, data=None, group_id=None, close_loop=False, preferred_bic=None):
    preferred_bic = preferred_bic or DB.BuiltInCategory.OST_GenericModel
    category_id = DB.ElementId(preferred_bic)
    if not DB.DirectShape.IsValidCategoryId(category_id, doc):
        category_id = DB.ElementId(DB.BuiltInCategory.OST_GenericModel)
    if not DB.DirectShape.IsValidCategoryId(category_id, doc):
        raise Exception(u"Не найдена допустимая категория Revit для DirectShape элемента ЗУ.")
    element = DB.DirectShape.CreateElement(doc, category_id)
    element.ApplicationId = APP_ID
    element.ApplicationDataId = app_data_id
    element.SetShape(_shape_list(DB, solid))
    _set_parameter(element, DB, DB.BuiltInParameter.ALL_MODEL_INSTANCE_COMMENTS, comments)
    _set_parameter(element, DB, DB.BuiltInParameter.ALL_MODEL_MARK, mark)
    if data is not None and group_id:
        _set_metadata(element, DB, data, group_id, close_loop)
    return element


def _parse_kv(text):
    result = {}
    for part in (text or u"").split(";"):
        if "=" in part:
            key, value = part.split("=", 1)
            result[key.strip()] = value.strip()
    return result


def _role_and_index(element, DB=None):
    # DirectShape v0.x stores role/index in ApplicationDataId. Family instances
    # store the same information in comments so both representations remain compatible.
    try:
        parts = (element.ApplicationDataId or "").split(":")
        role = parts[-2] if len(parts) >= 2 else ""
        index = int(parts[-1]) if parts and parts[-1].isdigit() else 9999
        if role:
            return role, index
    except Exception:
        pass
    if DB is not None:
        try:
            comments = _get_parameter_text(element, DB, DB.BuiltInParameter.ALL_MODEL_INSTANCE_COMMENTS)
            kv = _parse_kv(comments)
            role = kv.get("ROLE", "")
            index = int(kv.get("INDEX", "9999"))
            return role, index
        except Exception:
            pass
    return "", 9999


def _group_id_of(element, DB):
    try:
        meta = _read_metadata(element, DB)
        if meta and meta.get("group_id"):
            return meta.get("group_id")
    except Exception:
        pass
    comments = _get_parameter_text(element, DB, DB.BuiltInParameter.ALL_MODEL_INSTANCE_COMMENTS)
    return _parse_kv(comments).get("GROUP")


def _is_grounding_element(element, DB=None):
    try:
        if element.ApplicationId == APP_ID:
            return True
    except Exception:
        pass
    if DB is not None:
        try:
            meta = _read_metadata(element, DB)
            if meta and meta.get("group_id"):
                return True
        except Exception:
            pass
        try:
            comments = _get_parameter_text(element, DB, DB.BuiltInParameter.ALL_MODEL_INSTANCE_COMMENTS)
            return comments.startswith(u"EOM_GROUNDING;")
        except Exception:
            pass
    return False


def collect_group_elements(doc, DB, group_id):
    result = []
    # Keep legacy DirectShape elements and new FamilyInstance rods in the same BIM group.
    collectors = [
        DB.FilteredElementCollector(doc).OfClass(DB.DirectShape),
        DB.FilteredElementCollector(doc).OfClass(DB.FamilyInstance),
    ]
    seen = set()
    for collector in collectors:
        for element in collector:
            try:
                eid = element.Id.IntegerValue
                if eid in seen:
                    continue
                seen.add(eid)
            except Exception:
                pass
            if not _is_grounding_element(element, DB):
                continue
            if _group_id_of(element, DB) == group_id:
                result.append(element)
    return result


def _active_contour_group_id_for_line(doc, DB, element):
    if element is None or not isinstance(element, DB.ModelCurve):
        return None
    try:
        selected_unique_id = element.UniqueId
    except Exception:
        selected_unique_id = None
    try:
        selected_style = element.LineStyle.Name if element.LineStyle is not None else u""
    except Exception:
        selected_style = u""
    collectors = [
        DB.FilteredElementCollector(doc).OfClass(DB.DirectShape),
        DB.FilteredElementCollector(doc).OfClass(DB.FamilyInstance),
    ]
    seen_groups = set()
    for collector in collectors:
        for candidate in collector:
            if not _is_grounding_element(candidate, DB):
                continue
            metadata = _read_metadata(candidate, DB)
            group_id = (metadata or {}).get("group_id")
            if not group_id or group_id in seen_groups:
                continue
            seen_groups.add(group_id)
            data = (metadata or {}).get("data") or {}
            if not bool(data.get("contour_edit_active")):
                continue
            guide_ids = [value for value in (data.get("contour_edit_guide_ids") or u"").split(u"|") if value]
            if selected_unique_id and selected_unique_id in guide_ids:
                return group_id
            if selected_style and selected_style == (data.get("contour_edit_style") or u""):
                return group_id
    return None


def _group_id_from_any_grounding_or_edit_element(doc, DB, element):
    if element is None:
        return None
    if _is_grounding_element(element, DB):
        return _group_id_of(element, DB)
    return _active_contour_group_id_for_line(doc, DB, element)


def group_from_selection(uidoc, doc, DB, UI):
    """Return a group from a grounding element or its temporary contour line."""
    selected = list(uidoc.Selection.GetElementIds())
    element = None
    group_id = None
    for element_id in selected:
        candidate = doc.GetElement(element_id)
        candidate_group_id = _group_id_from_any_grounding_or_edit_element(doc, DB, candidate)
        if candidate_group_id:
            element = candidate
            group_id = candidate_group_id
            break
    if group_id is None:
        try:
            ref = uidoc.Selection.PickObject(
                UI.Selection.ObjectType.Element,
                u"ЭОМ: выберите элемент ЗУ или временную Model Line редактируемого контура")
            element = doc.GetElement(ref.ElementId)
        except Exception:
            return None, None
        group_id = _group_id_from_any_grounding_or_edit_element(doc, DB, element)
    if not group_id:
        raise ValueError(u"Выбранный элемент не относится к ЗУ, созданному плагином ЭОМ.")
    return group_id, element


def _rod_ground_point(element, DB, top_depth_m):
    # Для штатного параметрического семейства ЭОМ точка вставки является поверхностью земли.
    # Для заводской секции EZETEK 90136 положение поверхности восстанавливаем по верхней
    # границе фактической геометрии, т.к. исходную точку вставки производителя не меняем.
    try:
        comments = _get_parameter_text(element, DB, DB.BuiltInParameter.ALL_MODEL_INSTANCE_COMMENTS)
        if isinstance(element, DB.FamilyInstance) and u"MODE=EZETEK90136" not in comments:
            loc = element.Location
            if isinstance(loc, DB.LocationPoint):
                return loc.Point
    except Exception:
        pass
    # DirectShape and manufacturer family: for FamilyInstance prefer PHYSICAL solids
    # so symbolic/detail geometry cannot shift the recovered grounding point.
    if isinstance(element, DB.FamilyInstance):
        bounds = _family_solid_bounds(element, DB)
        if bounds is not None:
            xmin, ymin, zmin, xmax, ymax, zmax = bounds
            return DB.XYZ((xmin + xmax) / 2.0, (ymin + ymax) / 2.0,
                          zmax + m_to_ft(top_depth_m))
    bbox = element.get_BoundingBox(None)
    if bbox is None:
        raise ValueError(u"Не удалось определить геометрию вертикального электрода.")
    x = (bbox.Min.X + bbox.Max.X) / 2.0
    y = (bbox.Min.Y + bbox.Max.Y) / 2.0
    ground_z = bbox.Max.Z + m_to_ft(top_depth_m)
    return DB.XYZ(x, y, ground_z)


def extract_group(doc, DB, group_id):
    elements = collect_group_elements(doc, DB, group_id)
    if not elements:
        raise ValueError(u"Элементы группы ЗУ не найдены в проекте.")

    rods = []
    strips = []
    metadata = None
    for element in elements:
        role, index = _role_and_index(element, DB)
        if role == "ROD":
            rods.append((index, element))
        elif role == "STRIP":
            strips.append((index, element))
        if metadata is None:
            metadata = _read_metadata(element, DB)

    rods.sort(key=lambda item: item[0])
    strips.sort(key=lambda item: item[0])
    if not rods:
        raise ValueError(u"В группе ЗУ не найдено вертикальных электродов.")

    first_rod_comments = _get_parameter_text(rods[0][1], DB, DB.BuiltInParameter.ALL_MODEL_INSTANCE_COMMENTS)
    rod_kv = _parse_kv(first_rod_comments)
    top_depth = float(rod_kv.get("TOP_M", 0.5))
    length_m = float(rod_kv.get("L_M", 3.0))
    diameter_mm = float(rod_kv.get("D_MM", 16.0))

    points = [_rod_ground_point(item[1], DB, top_depth) for item in rods]
    metrics = spacing_metrics_m(points)

    close_loop = False
    if metadata:
        close_loop = bool(metadata.get("close_loop", False))
    elif len(points) > 2 and len(strips) >= len(points):
        close_loop = True

    data = dict((metadata or {}).get("data") or {})
    stored_route_plain = None
    try:
        parsed_route = json.loads(data.get("guide_route_json") or u"")
        if isinstance(parsed_route, list) and len(parsed_route) >= 2:
            stored_route_plain = [tuple(float(value) for value in point[:3]) for point in parsed_route]
    except Exception:
        stored_route_plain = None
    stored_route = ([_tuple_m_xyz(DB, point) for point in stored_route_plain]
                    if stored_route_plain else None)
    data["vertical_length"] = length_m
    data["vertical_diameter_mm"] = diameter_mm
    data["vertical_top_depth"] = top_depth
    data["vertical_count"] = len(points)
    # Для эвристики v0.3 используем минимальное расстояние между любой парой электродов — консервативно.
    data["vertical_spacing"] = metrics["pair_min"] if len(points) > 1 else 0.0
    data["horizontal_enabled"] = bool(strips)
    if strips and stored_route_plain:
        data["horizontal_length"] = perimeter_length(stored_route_plain, close_loop)
    else:
        data["horizontal_length"] = route_length_m(points, close_loop) if strips else 0.0
    data["close_loop"] = close_loop
    data["create_model"] = True
    data["auto_optimize"] = False

    if strips:
        strip_comments = _get_parameter_text(strips[0][1], DB, DB.BuiltInParameter.ALL_MODEL_INSTANCE_COMMENTS)
        strip_kv = _parse_kv(strip_comments)
        data["horizontal_width_mm"] = float(strip_kv.get("W_MM", 40.0))
        data["horizontal_thickness_mm"] = float(strip_kv.get("T_MM", 4.0))
        data["horizontal_depth"] = float(strip_kv.get("DEPTH_M", 0.7))

    return {
        "group_id": group_id,
        "elements": elements,
        "rods": [item[1] for item in rods],
        "strips": [item[1] for item in strips],
        "points": points,
        "route_points": stored_route,
        "close_loop": close_loop,
        "data": data,
        "spacing": metrics,
        "metadata_found": metadata is not None,
    }


def contour_edit_is_active(group_info):
    data = (group_info or {}).get("data") or {}
    return bool(data.get("contour_edit_active"))


def _contour_edit_style(doc, DB, style_name):
    lines_category = doc.Settings.Categories.get_Item(DB.BuiltInCategory.OST_Lines)
    subcategory = None
    for item in lines_category.SubCategories:
        if item.Name == style_name:
            subcategory = item
            break
    if subcategory is None:
        subcategory = doc.Settings.Categories.NewSubcategory(lines_category, style_name)
    return subcategory.GetGraphicsStyle(DB.GraphicsStyleType.Projection)


def begin_contour_edit(doc, DB, group_info):
    """Restore editable temporary Model Lines for an existing grounding route."""
    if contour_edit_is_active(group_info) and _find_contour_edit_seed(doc, DB, group_info) is not None:
        raise ValueError(u"Редактирование контура этой группы уже запущено.")
    points = list((group_info or {}).get("points") or [])
    stored_route = list((group_info or {}).get("route_points") or [])
    close_loop = bool(group_info.get("close_loop"))
    if len(points) < (3 if close_loop else 2):
        raise ValueError(u"В существующем контуре недостаточно точек для редактирования.")
    raw_route = [_xyz_tuple_m(point) for point in (stored_route or points)]
    plain_route = (simplify_closed_route(raw_route, tolerance_m=0.005) if close_loop else
                   simplify_open_route(raw_route, tolerance_m=0.005))
    elevation_m = sum(point[2] for point in plain_route) / float(len(plain_route))
    plain_route = [(point[0], point[1], elevation_m) for point in plain_route]
    route = [_tuple_m_xyz(DB, point) for point in plain_route]
    style_name = u"ЭОМ_Контур_{}".format(group_info["group_id"][:8])

    transaction = DB.Transaction(doc, u"ЭОМ: начать редактирование контура")
    transaction.Start()
    try:
        plane = DB.Plane.CreateByNormalAndOrigin(DB.XYZ.BasisZ, route[0])
        sketch_plane = DB.SketchPlane.Create(doc, plane)
        line_style = _contour_edit_style(doc, DB, style_name)
        model_lines = []
        segment_count = len(route) if close_loop else len(route) - 1
        for idx in range(segment_count):
            geometry = DB.Line.CreateBound(route[idx], route[(idx + 1) % len(route)])
            model_line = doc.Create.NewModelCurve(geometry, sketch_plane)
            model_line.LineStyle = line_style
            model_lines.append(model_line)

        data = dict(group_info.get("data") or {})
        data["contour_edit_active"] = True
        data["contour_edit_style"] = style_name
        data["contour_edit_guide_ids"] = u"|".join(line.UniqueId for line in model_lines)
        data["guide_route_json"] = json.dumps(plain_route, ensure_ascii=False)
        for element in group_info.get("elements") or []:
            _set_metadata(element, DB, data, group_info["group_id"], True)
        transaction.Commit()
    except Exception:
        transaction.RollBack()
        raise
    return {
        "element_ids": [line.Id for line in model_lines],
        "guide_unique_ids": [line.UniqueId for line in model_lines],
        "route_points": route,
        "style_name": style_name,
    }


def _find_contour_edit_seed(doc, DB, group_info):
    data = (group_info or {}).get("data") or {}
    for unique_id in (data.get("contour_edit_guide_ids") or u"").split(u"|"):
        if not unique_id:
            continue
        element = doc.GetElement(unique_id)
        if element is not None and isinstance(element, DB.ModelCurve):
            return element
    style_name = data.get("contour_edit_style") or u""
    if style_name:
        curve_filter = DB.CurveElementFilter(DB.CurveElementType.ModelCurve)
        collector = DB.FilteredElementCollector(doc).WhereElementIsNotElementType().WherePasses(curve_filter)
        for element in collector:
            try:
                if element.LineStyle is not None and element.LineStyle.Name == style_name:
                    return element
            except Exception:
                continue
    return None


def contour_edit_guides_exist(doc, DB, group_info):
    return _find_contour_edit_seed(doc, DB, group_info) is not None


def contour_edit_route(doc, DB, group_info):
    if not contour_edit_is_active(group_info):
        raise ValueError(u"Для этой группы не создан временный редактируемый контур.")
    seed = _find_contour_edit_seed(doc, DB, group_info)
    if seed is None:
        raise ValueError(u"Временные Model Lines не найдены. Запустите редактирование контура заново.")
    close_loop = bool(group_info.get("close_loop"))
    start_hint = None
    if not close_loop:
        stored_route = list((group_info or {}).get("route_points") or [])
        if stored_route:
            start_hint = stored_route[0]
    # Only a formerly closed contour may heal one gap automatically. An open
    # route must keep its two intentional ends separate.
    return _guide_route_from_seed(
        doc, DB, seed, require_closed=close_loop,
        auto_close_open=close_loop, start_hint=start_hint)


def _family_symbol_by_name(doc, DB, family_name):
    wanted = (family_name or ROD_FAMILY_DEFAULT).strip().lower()
    for symbol in DB.FilteredElementCollector(doc).OfClass(DB.FamilySymbol):
        try:
            family = symbol.Family
            fname = (family.Name or u"").strip().lower() if family else u""
            if fname == wanted:
                return symbol
            # Поставляемое семейство производителя может получить имя из файла.
            if u"90136" in wanted and u"90136" in fname:
                return symbol
        except Exception:
            continue
    return None


def _packaged_90136_path():
    ext_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(ext_root, "resources", "families", ROD_EZETEK_90136_FILE)


def _family_load_options(DB):
    """Revit family-load policy: keep project parameter values and avoid UI prompts."""
    class _SafeFamilyLoadOptions(DB.IFamilyLoadOptions):
        def OnFamilyFound(self, familyInUse, overwriteParameterValues):
            try:
                overwriteParameterValues.Value = False
            except Exception:
                pass
            return True

        def OnSharedFamilyFound(self, sharedFamily, familyInUse, source, overwriteParameterValues):
            try:
                source.Value = DB.FamilySource.Project
            except Exception:
                pass
            try:
                overwriteParameterValues.Value = False
            except Exception:
                pass
            return True
    return _SafeFamilyLoadOptions()


def _load_family_safely(doc, DB, path, trace_label):
    """Load RFA with an explicit no-overwrite policy and no confirmation dialogs."""
    fam_ref = clr.Reference[DB.Family]()
    options = _family_load_options(DB)
    trace_mark("FAMILY_LOAD_START", u"{} | {}".format(trace_label, path))
    try:
        loaded = doc.LoadFamily(path, options, fam_ref)
        trace_mark("FAMILY_LOAD_END", u"{} | loaded={}".format(trace_label, bool(loaded)))
        return fam_ref.Value
    except Exception as ex:
        trace_exception("FAMILY_LOAD_ERROR", ex, trace_label)
        raise


def _connection_direction_for_point(DB, points, idx, close_loop):
    if not points or len(points) < 2:
        return DB.XYZ.BasisX
    if close_loop and len(points) > 2:
        other = points[(idx + 1) % len(points)]
        vec = DB.XYZ(other.X - points[idx].X, other.Y - points[idx].Y, 0.0)
    elif idx < len(points) - 1:
        other = points[idx + 1]
        vec = DB.XYZ(other.X - points[idx].X, other.Y - points[idx].Y, 0.0)
    else:
        other = points[idx - 1]
        vec = DB.XYZ(points[idx].X - other.X, points[idx].Y - other.Y, 0.0)
    return vec.Normalize() if vec.GetLength() > 1e-9 else DB.XYZ.BasisX


def _oriented_box_solid(DB, center, direction, length_mm, width_mm, height_mm):
    """Very small rectangular prism centred on *center* and aligned to XY direction."""
    desired = direction or DB.XYZ.BasisX
    desired = DB.XYZ(desired.X, desired.Y, 0.0)
    if desired.GetLength() < 1e-9:
        desired = DB.XYZ.BasisX
    desired = desired.Normalize()
    side = DB.XYZ.BasisZ.CrossProduct(desired)
    if side.GetLength() < 1e-9:
        side = DB.XYZ.BasisY
    else:
        side = side.Normalize()

    half_len = m_to_ft(float(length_mm) / 2000.0)
    half_w = m_to_ft(float(width_mm) / 2000.0)
    half_h = m_to_ft(float(height_mm) / 2000.0)
    start = center - desired.Multiply(half_len)

    p1 = start + side.Multiply(half_w) + DB.XYZ.BasisZ.Multiply(half_h)
    p2 = start - side.Multiply(half_w) + DB.XYZ.BasisZ.Multiply(half_h)
    p3 = start - side.Multiply(half_w) - DB.XYZ.BasisZ.Multiply(half_h)
    p4 = start + side.Multiply(half_w) - DB.XYZ.BasisZ.Multiply(half_h)
    curves = [
        DB.Line.CreateBound(p1, p2),
        DB.Line.CreateBound(p2, p3),
        DB.Line.CreateBound(p3, p4),
        DB.Line.CreateBound(p4, p1),
    ]
    loops = _curve_loop(DB, curves)
    return DB.GeometryCreationUtilities.CreateExtrusionGeometry(
        loops, desired, m_to_ft(float(length_mm) / 1000.0))


def _vertical_plate_solid(DB, center, direction, length_mm, height_mm, thickness_mm, side_offset_mm=0.0):
    """Rectangular prism in the vertical plane defined by route direction + Z."""
    desired = direction or DB.XYZ.BasisX
    desired = DB.XYZ(desired.X, desired.Y, 0.0)
    if desired.GetLength() < 1e-9:
        desired = DB.XYZ.BasisX
    desired = desired.Normalize()
    side = DB.XYZ.BasisZ.CrossProduct(desired)
    if side.GetLength() < 1e-9:
        side = DB.XYZ.BasisY
    else:
        side = side.Normalize()
    c = center + side.Multiply(m_to_ft(float(side_offset_mm) / 1000.0))
    half_len = m_to_ft(float(length_mm) / 2000.0)
    half_h = m_to_ft(float(height_mm) / 2000.0)
    half_t = m_to_ft(float(thickness_mm) / 2000.0)
    p1 = c - desired.Multiply(half_len) - DB.XYZ.BasisZ.Multiply(half_h) - side.Multiply(half_t)
    p2 = c + desired.Multiply(half_len) - DB.XYZ.BasisZ.Multiply(half_h) - side.Multiply(half_t)
    p3 = c + desired.Multiply(half_len) + DB.XYZ.BasisZ.Multiply(half_h) - side.Multiply(half_t)
    p4 = c - desired.Multiply(half_len) + DB.XYZ.BasisZ.Multiply(half_h) - side.Multiply(half_t)
    curves = [DB.Line.CreateBound(p1, p2), DB.Line.CreateBound(p2, p3),
              DB.Line.CreateBound(p3, p4), DB.Line.CreateBound(p4, p1)]
    loops = _curve_loop(DB, curves)
    return DB.GeometryCreationUtilities.CreateExtrusionGeometry(loops, side, 2.0 * half_t)


def _cross_rod_strip_clamp_solids(DB, center, direction, article):
    """Light but recognisable 4-bolt rod/strip clamp (EZETEK album sheet 6).

    The proxy keeps the published 70 x 50 mm envelope, two clamping plates,
    central saddle and four bolt heads.  It deliberately avoids holes/threads and
    imported BRep geometry to keep Revit redraw stable.
    """
    spec = FITTING_CATALOG.get(article) or FITTING_CATALOG["90540"]
    length = float(spec.get("envelope_length_mm") or 70.0)
    height = float(spec.get("envelope_height_mm") or 50.0)
    plate_t = float(spec.get("plate_thickness_mm") or 2.0)
    gap = float(spec.get("plate_gap_mm") or 8.0)
    ox = float(spec.get("bolt_offset_x_mm") or 25.0)
    oz = float(spec.get("bolt_offset_z_mm") or 15.0)
    bolt = float(spec.get("bolt_head_mm") or 9.0)
    bolt_depth = float(spec.get("bolt_depth_mm") or 5.0)

    desired = direction or DB.XYZ.BasisX
    desired = DB.XYZ(desired.X, desired.Y, 0.0)
    if desired.GetLength() < 1e-9:
        desired = DB.XYZ.BasisX
    desired = desired.Normalize()
    side = DB.XYZ.BasisZ.CrossProduct(desired).Normalize()

    shapes = []
    # Two thin plates around the rod/strip plane.
    offset = gap / 2.0 + plate_t / 2.0
    shapes.append(_vertical_plate_solid(DB, center, desired, length, height, plate_t, -offset))
    shapes.append(_vertical_plate_solid(DB, center, desired, length, height, plate_t, offset))

    # Central raised saddles make the proxy read as a real clamp rather than a box.
    saddle_len = min(32.0, length * 0.46)
    saddle_h = min(18.0, height * 0.36)
    saddle_t = max(3.0, plate_t * 1.5)
    shapes.append(_vertical_plate_solid(DB, center, desired, saddle_len, saddle_h, saddle_t, -(gap / 2.0 + plate_t + saddle_t / 2.0)))
    shapes.append(_vertical_plate_solid(DB, center, desired, saddle_len, saddle_h, saddle_t,  (gap / 2.0 + plate_t + saddle_t / 2.0)))

    # Four bolt/nut heads on both faces; simple prisms are much lighter than threaded solids.
    face_offset = gap / 2.0 + plate_t + bolt_depth / 2.0
    for sx in (-1.0, 1.0):
        for sz in (-1.0, 1.0):
            bc = center + desired.Multiply(m_to_ft((sx * ox) / 1000.0)) + DB.XYZ.BasisZ.Multiply(m_to_ft((sz * oz) / 1000.0))
            shapes.append(_oriented_box_solid(DB, bc + side.Multiply(m_to_ft(face_offset / 1000.0)), desired, bolt, bolt_depth, bolt))
            shapes.append(_oriented_box_solid(DB, bc - side.Multiply(m_to_ft(face_offset / 1000.0)), desired, bolt, bolt_depth, bolt))
    return shapes


def _create_grounding_fitting(doc, DB, article, point, direction, data, group_id, idx, close_loop, node_type, node_id, warnings=None):
    """Universal lightweight fitting factory used by current and future contour nodes."""
    warnings = warnings if warnings is not None else []
    spec = FITTING_CATALOG.get(str(article))
    if not spec:
        warnings.append(u"Неизвестный артикул соединительной детали: {}".format(article))
        return None
    geometry_kind = spec.get("geometry")
    if geometry_kind != "CROSS_ROD_STRIP_4BOLT":
        warnings.append(u"Для изделия {} пока не реализована BIM-геометрия {}.".format(article, geometry_kind))
        return None
    shapes = _cross_rod_strip_clamp_solids(DB, point, direction, str(article))
    comments = (u"EOM_GROUNDING;GROUP={};ROLE=CLAMP;INDEX={};PARENT_INDEX={};"
                u"NODE_TYPE={};NODE_ID={};MODE=BIM_PROXY;PRODUCT={};"
                u"FITTING_KIND={};GEOMETRY={};ENVELOPE_MM={}x{}"
                .format(group_id, idx + 1, idx + 1, node_type, node_id, article,
                        spec.get("kind"), geometry_kind,
                        spec.get("envelope_length_mm"), spec.get("envelope_height_mm")))
    mark = u"ЗУ-В{}-ЗАЖ".format(idx + 1)
    return _new_direct_shape_geometry(
        doc, DB, shapes, "{}:CLAMP:{}".format(group_id, idx + 1),
        comments, mark, data=data, group_id=group_id, close_loop=close_loop,
        preferred_bic=DB.BuiltInCategory.OST_ElectricalEquipment)


def _create_ezetek_90540_clamp(doc, DB, ground_point, direction, data, group_id, idx, close_loop, warnings):
    """Create the upper fitting through the universal topology/fitting factory."""
    top_depth = number_value(data.get("vertical_top_depth"), 0.5)
    if data.get("horizontal_enabled", True):
        connection_depth = number_value(data.get("horizontal_depth"), top_depth)
        connection_depth = max(top_depth, min(connection_depth, top_depth + ROD_EZETEK_90136_MODULE_M - 0.03))
    else:
        connection_depth = top_depth + 0.05
    center = DB.XYZ(ground_point.X, ground_point.Y, ground_point.Z - m_to_ft(connection_depth))
    node = vertical_electrode_node(idx, 1, EZETEK_90540_ARTICLE)
    return _create_grounding_fitting(
        doc, DB, EZETEK_90540_ARTICLE, center, direction, data, group_id, idx, close_loop,
        NODE_VERTICAL_ELECTRODE, node.get("id"), warnings)


def _first_symbol_of_family(DB, family, doc):
    if family is None:
        return None
    try:
        ids = list(family.GetFamilySymbolIds())
        if ids:
            return doc.GetElement(ids[0])
    except Exception:
        pass
    return None


def _ensure_ezetek_90136_symbol(doc, DB, warnings):
    # Сначала ищем уже загруженное семейство по артикулу.
    for symbol in DB.FilteredElementCollector(doc).OfClass(DB.FamilySymbol):
        try:
            family = symbol.Family
            if family and u"90136" in (family.Name or u""):
                return symbol
        except Exception:
            continue

    path = _packaged_90136_path()
    if not os.path.isfile(path):
        warnings.append(u"В установочном пакете отсутствует BIM-семейство EZETEK 90136: {}".format(path))
        return None
    try:
        family = _load_family_safely(doc, DB, path, u"EZETEK 90136")
        symbol = _first_symbol_of_family(DB, family, doc)
        if symbol is not None:
            return symbol
        warnings.append(u"Файл EZETEK 90136 загружен, но тип семейства не найден.")
        trace_mark("FAMILY_SYMBOL_MISSING", u"EZETEK 90136")
    except Exception as ex:
        warnings.append(u"Не удалось автоматически загрузить EZETEK 90136: {}".format(exception_text(ex)))
    return None


def _is_ezetek_90136_request(family_name):
    text = (family_name or u"").lower()
    return u"90136" in text or text == ROD_EZETEK_90136_ALIAS.lower()


def _named_param(element, name):
    try:
        return element.LookupParameter(name)
    except Exception:
        return None


def _set_named_value(element, DB, name, value, kind="text"):
    p = _named_param(element, name)
    if p is None or p.IsReadOnly:
        return False
    try:
        if kind == "length_m":
            p.Set(m_to_ft(float(value)))
        elif kind == "length_mm":
            p.Set(m_to_ft(float(value) / 1000.0))
        elif kind == "integer":
            p.Set(int(value))
        elif kind == "number":
            p.Set(float(value))
        else:
            p.Set(u"{}".format(value if value is not None else u""))
        return True
    except Exception:
        return False


def _iter_solid_vertices(geometry, DB):
    """Yield model-coordinate points belonging to real 3D solids only.

    Family bounding boxes may include symbolic/detail geometry.  For placement of
    the EZETEK rod we deliberately ignore it and inspect tessellated edges of
    solids returned by instance geometry.
    """
    if geometry is None:
        return
    for obj in geometry:
        try:
            if isinstance(obj, DB.GeometryInstance):
                nested = obj.GetInstanceGeometry()
                for point in _iter_solid_vertices(nested, DB):
                    yield point
                continue
        except Exception:
            pass
        try:
            if isinstance(obj, DB.Solid) and obj.Volume > 1e-12:
                for edge in obj.Edges:
                    try:
                        for point in edge.Tessellate():
                            yield point
                    except Exception:
                        pass
        except Exception:
            pass



def _iter_physical_solids(geometry, DB):
    """Yield real 3D solids in model coordinates from family instance geometry."""
    if geometry is None:
        return
    for obj in geometry:
        try:
            if isinstance(obj, DB.GeometryInstance):
                nested = obj.GetInstanceGeometry()
                for solid in _iter_physical_solids(nested, DB):
                    yield solid
                continue
        except Exception:
            pass
        try:
            if isinstance(obj, DB.Solid) and obj.Volume > 1e-12:
                yield obj
        except Exception:
            pass


def _new_direct_shape_geometry(doc, DB, geometry_objects, app_data_id, comments, mark,
                               data=None, group_id=None, close_loop=False, preferred_bic=None):
    preferred_bic = preferred_bic or DB.BuiltInCategory.OST_GenericModel
    category_id = DB.ElementId(preferred_bic)
    if not DB.DirectShape.IsValidCategoryId(category_id, doc):
        category_id = DB.ElementId(DB.BuiltInCategory.OST_GenericModel)
    if not DB.DirectShape.IsValidCategoryId(category_id, doc):
        raise Exception(u"Не найдена допустимая категория Revit для DirectShape элемента ЗУ.")
    element = DB.DirectShape.CreateElement(doc, category_id)
    element.ApplicationId = APP_ID
    element.ApplicationDataId = app_data_id
    items = List[DB.GeometryObject]()
    for obj in geometry_objects or []:
        items.Add(obj)
    if items.Count == 0:
        doc.Delete(element.Id)
        raise Exception(u"Не удалось получить 3D-геометрию изделия для DirectShape.")
    element.SetShape(items)
    _set_parameter(element, DB, DB.BuiltInParameter.ALL_MODEL_INSTANCE_COMMENTS, comments)
    _set_parameter(element, DB, DB.BuiltInParameter.ALL_MODEL_MARK, mark)
    if data is not None and group_id:
        _set_metadata(element, DB, data, group_id, close_loop)
    return element


def _family_solid_bounds(instance, DB):
    """Return xmin,ymin,zmin,xmax,ymax,zmax for physical family solids.

    Falls back to Element BoundingBox only when the family exposes no solids.
    """
    points = []
    try:
        options = DB.Options()
        options.IncludeNonVisibleObjects = False
        options.DetailLevel = DB.ViewDetailLevel.Fine
        geometry = instance.get_Geometry(options)
        points = list(_iter_solid_vertices(geometry, DB))
    except Exception as ex:
        trace_exception("FAMILY_BOUNDS_GEOMETRY_ERROR", ex, u"solid bounds")
        points = []
    if points:
        return (min(p.X for p in points), min(p.Y for p in points), min(p.Z for p in points),
                max(p.X for p in points), max(p.Y for p in points), max(p.Z for p in points))
    bbox = instance.get_BoundingBox(None)
    if bbox is None:
        return None
    return (bbox.Min.X, bbox.Min.Y, bbox.Min.Z, bbox.Max.X, bbox.Max.Y, bbox.Max.Z)


def _rod_visible_bounds(instance, DB):
    """Return trustworthy model-coordinate bounds for EZETEK 90136.

    The manufacturer's RFA has nested geometry and an insertion origin that is not
    guaranteed to coincide with the physical rod axis.  Element BoundingBox is the
    most reliable post-placement check because Revit reports it in model coordinates.
    We accept it only when its proportions are plausible for one 1.5 m rod; otherwise
    fall back to physical-solid bounds.
    """
    try:
        bbox = instance.get_BoundingBox(None)
        if bbox is not None:
            bounds = (bbox.Min.X, bbox.Min.Y, bbox.Min.Z, bbox.Max.X, bbox.Max.Y, bbox.Max.Z)
            sx = abs(bounds[3]-bounds[0])
            sy = abs(bounds[4]-bounds[1])
            sz = abs(bounds[5]-bounds[2])
            # Rod plus threaded collars should remain compact in XY and about 1.5 m high.
            if sx < m_to_ft(0.40) and sy < m_to_ft(0.40) and m_to_ft(1.0) < sz < m_to_ft(2.0):
                return bounds
    except Exception:
        pass
    return _family_solid_bounds(instance, DB)


def _location_point_xyz(instance, DB):
    try:
        loc = instance.Location
        if isinstance(loc, DB.LocationPoint):
            return loc.Point
    except Exception:
        pass
    return None


def _calibrate_90136_symbol(doc, DB, symbol, sample_point):
    """Measure the manufacturer's insertion-origin offsets once.

    Legacy RFAs can have an insertion origin that is offset from the physical rod
    axis/end. Repeated Regenerate+Move loops are expensive in production models.
    We instead create one temporary instance, regenerate once, measure the physical
    envelope relative to its LocationPoint, and use those fixed offsets for every
    real module of this FamilySymbol in the current transaction.
    """
    temp = doc.Create.NewFamilyInstance(sample_point, symbol, DB.Structure.StructuralType.NonStructural)
    doc.Regenerate()
    bounds = _rod_visible_bounds(temp, DB)
    loc = _location_point_xyz(temp, DB) or sample_point
    if bounds is None:
        try:
            doc.Delete(temp.Id)
        except Exception:
            pass
        return None
    xmin, ymin, zmin, xmax, ymax, zmax = bounds
    cx = (xmin + xmax) / 2.0
    cy = (ymin + ymax) / 2.0
    calibration = {
        "axis_dx": cx - loc.X,
        "axis_dy": cy - loc.Y,
        "top_dz": zmax - loc.Z,
        "bottom_dz": zmin - loc.Z,
        "height": zmax - zmin,
        "width_x": xmax - xmin,
        "width_y": ymax - ymin,
    }
    try:
        doc.Delete(temp.Id)
    except Exception:
        pass
    if calibration["height"] <= m_to_ft(1.0) or calibration["height"] >= m_to_ft(2.0):
        return None
    return calibration


def _create_90136_at_physical_top(doc, DB, symbol, calibration, x, y, top_z):
    """Create an instance whose PHYSICAL axis/top lands exactly at x,y,top_z.

    This uses the one-time calibrated insertion-origin offsets and therefore does
    not require any post-placement MoveElement or per-module document regeneration.
    """
    insertion = DB.XYZ(
        x - calibration["axis_dx"],
        y - calibration["axis_dy"],
        top_z - calibration["top_dz"],
    )
    instance = doc.Create.NewFamilyInstance(insertion, symbol, DB.Structure.StructuralType.NonStructural)
    bottom_z = top_z - calibration["height"]
    bounds = (
        x - calibration["width_x"] / 2.0,
        y - calibration["width_y"] / 2.0,
        bottom_z,
        x + calibration["width_x"] / 2.0,
        y + calibration["width_y"] / 2.0,
        top_z,
    )
    return instance, bounds


def _create_ezetek_90136_modules(doc, DB, symbol, ground_point, data, group_id, idx, close_loop, warnings):
    total_length = number_value(data.get("vertical_length"))
    top_depth = number_value(data.get("vertical_top_depth"))
    diameter = number_value(data.get("vertical_diameter_mm"), ROD_EZETEK_90136_DIAMETER_MM)
    modules_float = total_length / ROD_EZETEK_90136_MODULE_M
    module_count = int(round(modules_float))
    if module_count < 1 or abs(total_length - module_count * ROD_EZETEK_90136_MODULE_M) > 0.002:
        raise ValueError(u"EZETEK 90136 имеет фиксированную длину секции 1,50 м; общая длина должна быть кратна 1,50 м.")
    if abs(diameter - ROD_EZETEK_90136_DIAMETER_MM) > 0.2:
        raise ValueError(u"EZETEK 90136 имеет Ø16 мм, а в расчете задано {:.1f} мм. Измените диаметр на 16 мм либо отключите BIM-изделие 90136.".format(diameter))

    if not symbol.IsActive:
        symbol.Activate()
        doc.Regenerate()

    # ONE calibration regeneration replaces the previous 5x regeneration loop for
    # every module.  This is critical in large Revit projects.
    calibration = _calibrate_90136_symbol(doc, DB, symbol, ground_point)
    if not calibration:
        raise ValueError(u"Не удалось откалибровать точку вставки EZETEK 90136 по физической 3D-геометрии.")

    created = []
    target_first_top = ground_point.Z - m_to_ft(top_depth)
    first_top_z = target_first_top
    previous_bottom_z = None
    joint_zs = []
    actual_bounds = []

    for module_idx in range(module_count):
        target_top = target_first_top - calibration["height"] * module_idx
        instance, bounds = _create_90136_at_physical_top(
            doc, DB, symbol, calibration, ground_point.X, ground_point.Y, target_top)
        xmin, ymin, zmin, xmax, ymax, zmax = bounds
        if module_idx > 0:
            joint_zs.append(zmax)
        previous_bottom_z = zmin
        actual_bounds.append(bounds)

        role = u"ROD" if module_idx == 0 else u"ROD_MODULE"
        node_id = "VE-{}".format(idx + 1)
        comments = (u"EOM_GROUNDING;GROUP={};ROLE={};INDEX={};MODULE={};PARENT_INDEX={};"
                    u"NODE_TYPE=VERTICAL_ELECTRODE;NODE_ID={};MODE=EZETEK90136;PRODUCT=90136;"
                    u"ASSEMBLY=CALIBRATED_ORIGIN;L_M={:.3f};D_MM=16.0;TOP_M={:.3f}"
                    .format(group_id, role, idx + 1, module_idx + 1, idx + 1, node_id, total_length, top_depth))
        mark = u"ЗУ-В{}".format(idx + 1) if module_idx == 0 else u"ЗУ-В{}-М{}".format(idx + 1, module_idx + 1)
        _set_parameter(instance, DB, DB.BuiltInParameter.ALL_MODEL_INSTANCE_COMMENTS, comments)
        _set_parameter(instance, DB, DB.BuiltInParameter.ALL_MODEL_MARK, mark)
        _set_metadata(instance, DB, data, group_id, close_loop)
        created.append(instance)

    axis_x, axis_y = ground_point.X, ground_point.Y
    assembly_ground_point = DB.XYZ(axis_x, axis_y, ground_point.Z)
    created.extend(_create_ezetek_90136_accessories(
        doc, DB, assembly_ground_point, first_top_z, joint_zs, previous_bottom_z,
        data, group_id, idx, close_loop))

    measured_h_m = ft_to_m(calibration["height"])
    warnings.append(u"EZETEK 90136: точка вставки откалибрована один раз; физический габарит секции {:.3f} м; построчные регенерации отключены.".format(measured_h_m))
    if module_count > 1:
        warnings.append(u"EZETEK 90136: муфты 90227 (включая верхнюю), наконечник 90326 и верхний зажим 90540 создаются как облегченные BIM-элементы по каталогу узлов; это штатный режим для устойчивой модели.")
    else:
        warnings.append(u"EZETEK 90136: верхняя муфта 90227, наконечник 90326 и верхний зажим 90540 создаются как облегченные BIM-элементы по каталогу узлов; это штатный режим для устойчивой модели.")

    assembly_info = {
        "axis_x": axis_x, "axis_y": axis_y, "ground_z": ground_point.Z,
        "top_z": first_top_z, "bottom_z": previous_bottom_z, "joint_zs": list(joint_zs),
        "picked_x": ground_point.X, "picked_y": ground_point.Y,
        "residual_xy_m": 0.0,
    }
    return created, assembly_info

def _create_ezetek_90136_proxy_modules(doc, DB, ground_point, data, group_id, idx, close_loop, warnings):
    """Create a lightweight EZETEK 90136 assembly without loading manufacturer RFA.

    The normal placement path must stay predictable in Revit. Manufacturer-family
    loading, activation, regeneration and nested geometry inspection are therefore
    deliberately excluded here. Each 1.50 m module is represented by a lightweight
    DirectShape cylinder with the real article/module metadata; 90227/90326/90540
    accessory proxies are created by the existing helpers.
    """
    total_length = number_value(data.get("vertical_length"))
    top_depth = number_value(data.get("vertical_top_depth"))
    diameter = number_value(data.get("vertical_diameter_mm"), ROD_EZETEK_90136_DIAMETER_MM)
    modules_float = total_length / ROD_EZETEK_90136_MODULE_M
    module_count = int(round(modules_float))
    if module_count < 1 or abs(total_length - module_count * ROD_EZETEK_90136_MODULE_M) > 0.002:
        raise ValueError(u"EZETEK 90136 имеет фиксированную длину секции 1,50 м; общая длина должна быть кратна 1,50 м.")
    if abs(diameter - ROD_EZETEK_90136_DIAMETER_MM) > 0.2:
        raise ValueError(u"EZETEK 90136 имеет Ø16 мм, а в расчете задано {:.1f} мм. Измените диаметр на 16 мм либо отключите BIM-изделие 90136.".format(diameter))

    created = []
    first_top_z = ground_point.Z - m_to_ft(top_depth)
    previous_bottom_z = first_top_z
    joint_zs = []

    for module_idx in range(module_count):
        module_top_z = first_top_z - m_to_ft(ROD_EZETEK_90136_MODULE_M * module_idx)
        module_bottom_z = module_top_z - m_to_ft(ROD_EZETEK_90136_MODULE_M)
        if module_idx > 0:
            joint_zs.append(module_top_z)

        solid = _cylinder_solid(
            DB, ground_point.X, ground_point.Y, module_top_z,
            ROD_EZETEK_90136_MODULE_M, ROD_EZETEK_90136_DIAMETER_MM)

        role = u"ROD" if module_idx == 0 else u"ROD_MODULE"
        node_id = "VE-{}".format(idx + 1)
        comments = (u"EOM_GROUNDING;GROUP={};ROLE={};INDEX={};MODULE={};PARENT_INDEX={};"
                    u"NODE_TYPE=VERTICAL_ELECTRODE;NODE_ID={};MODE=EZETEK90136_PROXY;PRODUCT=90136;"
                    u"L_M={:.3f};MODULE_L_M=1.500;D_MM=16.0;TOP_M={:.3f}"
                    .format(group_id, role, idx + 1, module_idx + 1, idx + 1,
                            node_id, total_length, top_depth))
        mark = u"ЗУ-В{}".format(idx + 1) if module_idx == 0 else u"ЗУ-В{}-М{}".format(idx + 1, module_idx + 1)
        ds = _new_direct_shape(
            doc, DB, solid, "{}:ROD:{}:MODULE:{}".format(group_id, idx + 1, module_idx + 1),
            comments, mark, data=data, group_id=group_id, close_loop=close_loop,
            preferred_bic=DB.BuiltInCategory.OST_ElectricalEquipment)
        created.append(ds)
        previous_bottom_z = module_bottom_z

    created.extend(_create_ezetek_90136_accessories(
        doc, DB, ground_point, first_top_z, joint_zs, previous_bottom_z,
        data, group_id, idx, close_loop))

    warnings.append(
        u"EZETEK 90136: использована облегченная BIM-модель без загрузки RFA; "
        u"секции 90136, муфты 90227, наконечник 90326 и зажим 90540 сохраняют артикулы и метаданные.")

    assembly_info = {
        "axis_x": ground_point.X, "axis_y": ground_point.Y, "ground_z": ground_point.Z,
        "top_z": first_top_z, "bottom_z": previous_bottom_z, "joint_zs": list(joint_zs),
        "picked_x": ground_point.X, "picked_y": ground_point.Y,
        "residual_xy_m": 0.0,
    }
    return created, assembly_info


def _create_rod_family_instance(doc, DB, symbol, ground_point, data, group_id, idx, close_loop):
    if not symbol.IsActive:
        symbol.Activate()
        doc.Regenerate()
    instance = doc.Create.NewFamilyInstance(ground_point, symbol, DB.Structure.StructuralType.NonStructural)
    # Geometry-driving parameters must be writable INSTANCE parameters. Type parameters would change
    # every electrode that shares the type, including electrodes from other grounding groups.
    missing = []
    for name in ROD_REQUIRED_GEOMETRY_PARAMS:
        p = _named_param(instance, name)
        if p is None or p.IsReadOnly:
            missing.append(name)
    if missing:
        doc.Delete(instance.Id)
        raise ValueError(u"Семейство '{}' не соответствует контракту ЭОМ. Нужны изменяемые параметры экземпляра: {}".format(
            symbol.Family.Name, u", ".join(missing)))

    length_m = number_value(data.get("vertical_length"))
    diameter_mm = number_value(data.get("vertical_diameter_mm"))
    top_depth_m = number_value(data.get("vertical_top_depth"))
    module_len = number_value(data.get("rod_module_step"), 0.0)
    module_count = int(round(length_m / module_len)) if module_len > 1e-9 else 1
    comments = (u"EOM_GROUNDING;GROUP={};ROLE=ROD;INDEX={};L_M={:.3f};D_MM={:.1f};TOP_M={:.3f}"
                .format(group_id, idx + 1, length_m, diameter_mm, top_depth_m))
    mark = u"ЗУ-В{}".format(idx + 1)
    _set_parameter(instance, DB, DB.BuiltInParameter.ALL_MODEL_INSTANCE_COMMENTS, comments)
    _set_parameter(instance, DB, DB.BuiltInParameter.ALL_MODEL_MARK, mark)
    _set_named_value(instance, DB, ROD_PARAM_LENGTH, length_m, "length_m")
    _set_named_value(instance, DB, ROD_PARAM_DIAMETER, diameter_mm, "length_mm")
    _set_named_value(instance, DB, ROD_PARAM_TOP_DEPTH, top_depth_m, "length_m")
    _set_named_value(instance, DB, ROD_PARAM_MATERIAL, data.get("electrode_material") or u"", "text")
    _set_named_value(instance, DB, ROD_PARAM_GROUP, group_id, "text")
    _set_named_value(instance, DB, ROD_PARAM_ROLE, u"ROD", "text")
    _set_named_value(instance, DB, ROD_PARAM_NUMBER, idx + 1, "integer")
    _set_named_value(instance, DB, ROD_PARAM_MODULE_COUNT, module_count, "integer")
    if module_len > 0:
        _set_named_value(instance, DB, ROD_PARAM_MODULE_LENGTH, module_len, "length_m")
    _set_metadata(instance, DB, data, group_id, close_loop)
    return instance


def _create_rod_element(doc, DB, data, point, group_id, idx, close_loop, warnings):
    use_family = bool(data.get("use_rod_family", True))
    family_name = data.get("rod_family_name") or ROD_FAMILY_DEFAULT
    if use_family:
        if _is_ezetek_90136_request(family_name):
            try:
                trace_mark("EZETEK_PROXY_START", str(idx + 1))
                rod_elements, assembly_info = _create_ezetek_90136_proxy_modules(
                    doc, DB, point, data, group_id, idx, close_loop, warnings)
                trace_mark("EZETEK_PROXY_END", str(idx + 1))
                return rod_elements, "EZETEK90136", assembly_info
            except Exception as ex:
                trace_exception("ROD_PROXY_FALLBACK", ex, u"EZETEK 90136")
                warnings.append(u"EZETEK 90136: {} Использован упрощенный единый DirectShape.".format(exception_text(ex)))
        else:
            symbol = _family_symbol_by_name(doc, DB, family_name)
            if symbol is not None:
                try:
                    return [_create_rod_family_instance(doc, DB, symbol, point, data, group_id, idx, close_loop)], "FAMILY", None
                except Exception as ex:
                    trace_exception("ROD_FAMILY_FALLBACK", ex, family_name)
                    warnings.append(u"Семейство '{}': {}. Использован DirectShape.".format(family_name, exception_text(ex)))
            else:
                warnings.append(u"Семейство '{}' не загружено. Использован DirectShape.".format(family_name))

    length_m = number_value(data.get("vertical_length"))
    diameter_mm = number_value(data.get("vertical_diameter_mm"))
    top_depth_m = number_value(data.get("vertical_top_depth"))
    solid = _rod_solid(DB, point, top_depth_m, length_m, diameter_mm)
    comments = (u"EOM_GROUNDING;GROUP={};ROLE=ROD;INDEX={};L_M={:.3f};D_MM={:.1f};TOP_M={:.3f}"
                .format(group_id, idx + 1, length_m, diameter_mm, top_depth_m))
    mark = u"ЗУ-В{}".format(idx + 1)
    ds = _new_direct_shape(doc, DB, solid, "{}:ROD:{}".format(group_id, idx + 1), comments, mark,
                           data=data, group_id=group_id, close_loop=close_loop)
    return [ds], "DIRECTSHAPE", None

def _create_strips_in_transaction(doc, DB, data, points, close_loop, group_id):
    created = []
    if not data.get("horizontal_enabled", True) or len(points) < 2:
        return created
    depth_m = number_value(data.get("horizontal_depth"))
    width_mm = number_value(data.get("horizontal_width_mm"))
    thickness_mm = number_value(data.get("horizontal_thickness_mm"), 4.0)
    segments = [(points[i], points[i + 1]) for i in range(len(points) - 1)]
    if close_loop and len(points) > 2:
        segments.append((points[-1], points[0]))
    for idx, pair in enumerate(segments):
        solid = _strip_solid(DB, pair[0], pair[1], depth_m, width_mm, thickness_mm)
        seg_len_m = ft_to_m(pair[0].DistanceTo(pair[1]))
        to_idx = ((idx + 1) % len(points)) + 1 if close_loop else (idx + 2)
        comments = (u"EOM_GROUNDING;GROUP={};ROLE=STRIP;INDEX={};FROM_NODE=R-{};TO_NODE=R-{};"
                    u"L_M={:.3f};W_MM={:.1f};T_MM={:.1f};DEPTH_M={:.3f}"
                    .format(group_id, idx + 1, idx + 1, to_idx, seg_len_m, width_mm, thickness_mm, depth_m))
        mark = u"ЗУ-Г{}".format(idx + 1)
        created.append(_new_direct_shape(
            doc, DB, solid, "{}:STRIP:{}".format(group_id, idx + 1), comments, mark,
            data=data, group_id=group_id, close_loop=close_loop))
    return created


def _delete_guide_model_lines_in_transaction(doc, DB, unique_ids):
    """Delete only the Model Lines explicitly used as the perimeter guide."""
    element_ids = List[DB.ElementId]()
    seen = set()
    for unique_id in unique_ids or []:
        key = u"{}".format(unique_id or u"")
        if not key or key in seen:
            continue
        seen.add(key)
        element = doc.GetElement(key)
        if element is None or not isinstance(element, DB.ModelCurve):
            continue
        element_ids.Add(element.Id)
    if element_ids.Count:
        doc.Delete(element_ids)
    return element_ids.Count


def create_grounding_model(doc, DB, data, points, close_loop=False, route_points=None,
                           electrode_directions=None, route_nodes=None, group_id=None):
    """Создает 3D-модель ЗУ и сохраняет исходные данные в RVT."""
    if not points:
        raise ValueError(u"Не заданы точки размещения заземлителя.")
    strip_points = route_points or points
    route_nodes = list(route_nodes or [])

    group_id = group_id or str(uuid.uuid4())
    created = []
    deleted_guide_count = 0
    transaction = DB.Transaction(doc, u"ЭОМ: создать модель ЗУ")
    transaction.Start()
    try:
        trace_mark("MODEL_TX_STARTED")
        length_m = number_value(data.get("vertical_length"))
        diameter_mm = number_value(data.get("vertical_diameter_mm"))
        top_depth_m = number_value(data.get("vertical_top_depth"))

        warnings = []
        rod_modes = []
        rod_assembly_info = []
        for idx, point in enumerate(points):
            trace_mark("ROD_START", str(idx+1))
            rod_elements, mode, assembly_info = _create_rod_element(doc, DB, data, point, group_id, idx, close_loop, warnings)
            created.extend(rod_elements)
            rod_modes.append(mode)
            rod_assembly_info.append(assembly_info)
            trace_mark("ROD_END", str(idx+1))

        # One lightweight EZETEK 90540 proxy per vertical electrode on the upper module.
        # The proxy follows the same stable primitive-solid approach as 90227/90326.
        # No manufacturer 90540 RFA is loaded or transformed.
        for idx, point in enumerate(points):
            if idx < len(rod_modes) and rod_modes[idx] == "EZETEK90136":
                if electrode_directions and idx < len(electrode_directions):
                    direction = electrode_directions[idx]
                else:
                    direction = _connection_direction_for_point(DB, points, idx, close_loop)
                info = rod_assembly_info[idx] if idx < len(rod_assembly_info) else None
                if info:
                    clamp_point = DB.XYZ(info["axis_x"], info["axis_y"], info["ground_z"])
                else:
                    clamp_point = point
                trace_mark("CLAMP_START", str(idx+1))
                trace_mark("CLAMP_PROXY_START", str(idx+1))
                clamp = _create_ezetek_90540_clamp(
                    doc, DB, clamp_point, direction, data, group_id, idx, close_loop, warnings)
                if clamp is not None:
                    created.append(clamp)
                trace_mark("CLAMP_PROXY_END", str(idx+1))
                trace_mark("CLAMP_END", str(idx+1))

        trace_mark("STRIPS_START")
        created.extend(_create_strips_in_transaction(doc, DB, data, strip_points, close_loop, group_id))
        trace_mark("STRIPS_END")
        guide_ids = [x for x in (data.get("guide_source_unique_ids") or u"").split(u"|") if x]
        if (data.get("placement_source") == PLACEMENT_GUIDE and
                data.get("layout_mode") in (LAYOUT_PERIMETER, LAYOUT_OPEN_PERIMETER) and
                guide_ids):
            trace_mark("GUIDE_DELETE_START", len(guide_ids))
            deleted_guide_count = _delete_guide_model_lines_in_transaction(doc, DB, guide_ids)
            trace_mark("GUIDE_DELETE_END", deleted_guide_count)
        trace_mark("MODEL_COMMIT_START")
        transaction.Commit()
        trace_mark("MODEL_COMMIT_END")
    except Exception:
        transaction.RollBack()
        raise

    topology_nodes = []
    if rod_modes:
        try:
            modules = max(1, int(round(number_value(data.get("vertical_length"), 1.5) / ROD_EZETEK_90136_MODULE_M)))
        except Exception:
            modules = 1
        for node_idx, mode in enumerate(rod_modes):
            if mode == "EZETEK90136":
                topology_nodes.append(vertical_electrode_node(node_idx, modules, EZETEK_90540_ARTICLE))

    return {
        "group_id": group_id,
        "element_ids": [e.Id.IntegerValue for e in created if e is not None],
        "rod_count": len(points),
        "strip_length_m": route_length_m(strip_points, close_loop) if data.get("horizontal_enabled", True) else 0.0,
        "close_loop": bool(close_loop),
        "rod_model_mode": (rod_modes[0] if rod_modes and len(set(rod_modes)) == 1 else u"MIXED"),
        "topology_nodes": topology_nodes + route_nodes,
        "route_nodes": route_nodes,
        "bom": aggregate_bom(topology_nodes + route_nodes),
        "placement_source": data.get("placement_source", PLACEMENT_MANUAL),
        "guide_source_unique_ids": [x for x in (data.get("guide_source_unique_ids") or u"").split(u"|") if x],
        "deleted_guide_count": deleted_guide_count,
        "warnings": warnings,
    }


def apply_contour_edit(doc, DB, group_info):
    """Rebuild one grounding group from its edited temporary Model Line loop."""
    route_info = contour_edit_route(doc, DB, group_info)
    route = list(route_info.get("points") or [])
    plain_route = [_xyz_tuple_m(point) for point in route]
    data = dict(group_info.get("data") or {})
    close_loop = bool(route_info.get("closed"))
    open_design = None
    fixed_open_length = bool(data.get("open_contour_fixed_length", False))
    if close_loop:
        max_spacing = number_value(data.get("perimeter_max_spacing_m") or
                                   data.get("vertical_spacing") or
                                   (group_info.get("spacing") or {}).get("avg"), 3.0)
        layout = distribute_perimeter_electrodes(
            plain_route, max_spacing, closed=True, rods_at_corners=True)
    elif fixed_open_length:
        max_spacing = number_value(data.get("perimeter_max_spacing_m") or
                                   data.get("vertical_spacing") or
                                   (group_info.get("spacing") or {}).get("avg"), 3.0)
        layout = distribute_perimeter_electrodes(
            plain_route, max_spacing, closed=False, rods_at_corners=True)
    else:
        open_design = optimize_open_contour_length(
            data, perimeter_length(plain_route, closed=False))
        plain_route = trim_open_route(plain_route, open_design["used_length_m"])
        route = [_tuple_m_xyz(DB, point) for point in plain_route]
        layout = distribute_open_route_electrodes(
            plain_route, open_design["count"])
        max_spacing = float(layout["spacing_avg"])
    electrode_points = [_tuple_m_xyz(DB, point) for point in layout["points"]]
    electrode_directions = [DB.XYZ(item[0], item[1], 0.0) for item in layout["directions"]]
    route_nodes = perimeter_route_nodes(plain_route, close_loop)
    guide_ids = list(route_info.get("guide_unique_ids") or [])

    data["placement_source"] = PLACEMENT_GUIDE
    data["layout_mode"] = LAYOUT_PERIMETER if close_loop else LAYOUT_OPEN_PERIMETER
    data["close_loop"] = close_loop
    data["vertical_count"] = len(electrode_points)
    data["vertical_spacing"] = float(layout.get("spacing_avg") or max_spacing)
    data["perimeter_max_spacing_m"] = max_spacing
    data["horizontal_length"] = float(layout["perimeter_length"])
    data["guide_source_unique_ids"] = u"|".join(guide_ids)
    data["guide_source_count"] = len(guide_ids)
    data["guide_route_json"] = json.dumps(plain_route, ensure_ascii=False)
    data["contour_edit_active"] = False
    data["contour_edit_guide_ids"] = u""
    data["open_contour_fixed_length"] = bool(fixed_open_length)
    if open_design:
        data["open_contour_available_length_m"] = float(open_design["available_length_m"])
        data["open_contour_used_length_m"] = float(open_design["used_length_m"])
        data["open_contour_target_r"] = float(open_design["target_r"])
        data["open_contour_reserve_target_r"] = float(open_design["reserve_target_r"])
        data["open_contour_design_r"] = float(open_design["result"]["total_r"])

    transaction_group = DB.TransactionGroup(doc, u"ЭОМ: применить измененный контур")
    transaction_group.Start()
    try:
        delete_transaction = DB.Transaction(doc, u"ЭОМ: заменить старую геометрию ЗУ")
        delete_transaction.Start()
        try:
            old_ids = List[DB.ElementId]()
            for element in group_info.get("elements") or []:
                old_ids.Add(element.Id)
            if old_ids.Count:
                doc.Delete(old_ids)
            delete_transaction.Commit()
        except Exception:
            delete_transaction.RollBack()
            raise

        model_info = create_grounding_model(
            doc, DB, data, electrode_points, close_loop,
            route_points=route,
            electrode_directions=electrode_directions,
            route_nodes=route_nodes,
            group_id=group_info["group_id"])
        transaction_group.Assimilate()
    except Exception:
        try:
            transaction_group.RollBack()
        except Exception:
            pass
        raise
    return {
        "data": data,
        "model_info": model_info,
        "route_info": route_info,
        "layout": layout,
        "electrode_points": electrode_points,
        "route_points": route,
        "open_design": open_design,
    }


def sync_group_after_rod_moves(doc, DB, group_info, data):
    """Перестраивает соединительную полосу по текущим координатам стержней и обновляет метаданные."""
    group_id = group_info["group_id"]
    points = group_info["points"]
    strip_points = list(group_info.get("route_points") or points)
    close_loop = group_info["close_loop"]
    old_strips = group_info["strips"]
    rods = group_info["rods"]
    old_clamps = []
    for element in group_info.get("elements", []):
        role, _ = _role_and_index(element, DB)
        if role == "CLAMP":
            old_clamps.append(element)

    transaction = DB.Transaction(doc, u"ЭОМ: обновить геометрию ЗУ")
    transaction.Start()
    try:
        for element in old_strips + old_clamps:
            doc.Delete(element.Id)
        for rod in rods:
            _set_metadata(rod, DB, data, group_id, close_loop)
        new_strips = _create_strips_in_transaction(doc, DB, data, strip_points, close_loop, group_id)
        # Recreate top clamps from the current rod coordinates.
        comments = _get_parameter_text(rods[0], DB, DB.BuiltInParameter.ALL_MODEL_INSTANCE_COMMENTS) if rods else u""
        use_ezetek = u"MODE=EZETEK90136" in comments
        if use_ezetek:
            for idx, point in enumerate(points):
                direction = _connection_direction_for_point(DB, points, idx, close_loop)
                _create_ezetek_90540_clamp(doc, DB, point, direction, data, group_id, idx, close_loop, [])
        transaction.Commit()
    except Exception:
        transaction.RollBack()
        raise
    return new_strips


def pick_single_rod_placement(uidoc, doc, DB, UI, data):
    source = data.get("placement_source", PLACEMENT_MANUAL)
    if source == PLACEMENT_GUIDE:
        return pick_single_rod_guide(uidoc, doc, DB, UI)
    try:
        point = uidoc.Selection.PickPoint(u"ЭОМ: укажите точку одиночного модульного заземлителя")
    except Exception:
        return None
    return {"point": point, "direction": None, "guide_unique_ids": [], "source": PLACEMENT_MANUAL}


