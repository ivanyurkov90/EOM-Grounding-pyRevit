# -*- coding: utf-8 -*-
from __future__ import division, print_function

import math
import System

from revit_output import create_or_update_named_calculation_view
from revit_grounding_model import collect_group_elements
from perf_trace import mark as trace_mark, mark_exception as trace_exception


DOC_TAG = u"EOM_GROUNDING_DOC"
MARKER_SCHEMA_GUID = "f7350ac7-bd65-4c2d-a726-a95fd8a4e041"
MARKER_SCHEMA_NAME = "EOM_Grounding_Documentation"
MARKER_GROUP_FIELD = "GroupId"
MARKER_ROLE_FIELD = "Role"
MODEL_ROLES = ("PLAN", "SECTION_1", "SECTION_2", "AXON")
KNOWN_ROLES = MODEL_ROLES + ("CALCULATION", "SHEET")
LEGACY_ROLE_MAP = {
    "PLAN": "PLAN",
    "SECTION_MAIN": "SECTION_1",
    "SECTION_CROSS": "SECTION_2",
    "AXO": "AXON",
    "CALC": "CALCULATION",
    "SHEET": "SHEET",
}
READABLE_TITLES = {
    "PLAN": u"План ЗУ",
    "SECTION_1": u"Разрез 1-1",
    "SECTION_2": u"Разрез 2-2",
    "AXON": u"Аксонометрия ЗУ",
    "CALCULATION": u"Расчёт ЗУ",
    "SHEET": u"",
}
SCALE_CANDIDATES = (10, 20, 25, 50, 100, 200)
A1_WIDTH_MM = 841.0
A1_HEIGHT_MM = 594.0
A0_WIDTH_MM = 1189.0
A0_HEIGHT_MM = 841.0
SHEET_MARGIN_MM = 15.0
CALC_ZONE_RATIO = 0.48
CALC_ZONE_MAX_RATIO = 0.56
GRAPHICS_GAP_MM = 10.0
TITLEBLOCK_RESERVED_HEIGHT_MM = 65.0
MIN_GRAPHICS_WIDTH_MM = 220.0


def _mm(value):
    return float(value) / 304.8


def _ft_to_mm(value):
    return float(value) * 304.8


def _short_id(group_id):
    text = u"{}".format(group_id or u"NOID")
    return text[:8]


def _artifact_names(group_id):
    short = _short_id(group_id)
    return {
        "PLAN": u"ЭОМ_ЗУ_{}_План".format(short),
        "SECTION_1": u"ЭОМ_ЗУ_{}_Разрез 1-1".format(short),
        "SECTION_2": u"ЭОМ_ЗУ_{}_Разрез 2-2".format(short),
        "AXON": u"ЭОМ_ЗУ_{}_3D".format(short),
        "CALCULATION": u"ЭОМ_Расчет ЗУ_{}_Лист".format(short),
    }


def _canonical_role(role):
    text = u"{}".format(role or u"")
    if text in KNOWN_ROLES:
        return text
    return LEGACY_ROLE_MAP.get(text)


def _parse_legacy_marker(text):
    """Parse only the exact r30 description marker; substrings are not ownership."""
    parts = u"{}".format(text or u"").split(u";")
    if len(parts) != 3 or parts[0] != DOC_TAG:
        return None
    if not parts[1].startswith(u"GROUP=") or not parts[2].startswith(u"ROLE="):
        return None
    group_id = parts[1][len(u"GROUP="):]
    legacy_role = parts[2][len(u"ROLE="):]
    role = LEGACY_ROLE_MAP.get(legacy_role)
    if not group_id or role is None:
        return None
    return {"group_id": group_id, "role": role, "legacy_role": legacy_role}


def _legacy_identity_matches(text, group_id, role, actual_name=None,
                             expected_name=None, allow_renamed=False):
    marker = _parse_legacy_marker(text)
    canonical = _canonical_role(role)
    if marker is None or canonical is None:
        return False
    if marker.get("group_id") != u"{}".format(group_id or u""):
        return False
    if marker.get("role") != canonical:
        return False
    if not allow_renamed and expected_name is not None:
        return u"{}".format(actual_name or u"") == u"{}".format(expected_name)
    return True


def _description(view, DB):
    try:
        p = view.get_Parameter(DB.BuiltInParameter.VIEW_DESCRIPTION)
        if p:
            return p.AsString() or u""
    except Exception:
        pass
    return u""


def _set_description(view, DB, text):
    try:
        p = view.get_Parameter(DB.BuiltInParameter.VIEW_DESCRIPTION)
        if p and not p.IsReadOnly:
            p.Set(text)
            return True
    except Exception:
        pass
    return False


def _marker_schema(DB, create=False):
    guid = System.Guid(MARKER_SCHEMA_GUID)
    schema = DB.ExtensibleStorage.Schema.Lookup(guid)
    if schema is not None or not create:
        return schema
    builder = DB.ExtensibleStorage.SchemaBuilder(guid)
    builder.SetSchemaName(MARKER_SCHEMA_NAME)
    builder.SetDocumentation("Generated EOM grounding documentation ownership marker")
    builder.SetReadAccessLevel(DB.ExtensibleStorage.AccessLevel.Public)
    builder.SetWriteAccessLevel(DB.ExtensibleStorage.AccessLevel.Public)
    builder.AddSimpleField(MARKER_GROUP_FIELD, System.String)
    builder.AddSimpleField(MARKER_ROLE_FIELD, System.String)
    return builder.Finish()


def _set_marker(element, DB, group_id, role):
    canonical = _canonical_role(role)
    if canonical is None:
        raise ValueError(u"Неизвестная роль документации: {}".format(role))
    schema = _marker_schema(DB, create=True)
    entity = DB.ExtensibleStorage.Entity(schema)
    entity.Set[System.String](schema.GetField(MARKER_GROUP_FIELD), u"{}".format(group_id))
    entity.Set[System.String](schema.GetField(MARKER_ROLE_FIELD), canonical)
    element.SetEntity(entity)


def _read_marker(element, DB):
    try:
        schema = _marker_schema(DB, create=False)
        if schema is None:
            return None
        entity = element.GetEntity(schema)
        if not entity.IsValid():
            return None
        group_id = u"{}".format(entity.Get[System.String](schema.GetField(MARKER_GROUP_FIELD)))
        role = _canonical_role(entity.Get[System.String](schema.GetField(MARKER_ROLE_FIELD)))
        if not group_id or role is None:
            return None
        return {"group_id": group_id, "role": role}
    except Exception:
        return None


def _is_owned(element, DB, group_id, role=None):
    marker = _read_marker(element, DB)
    if marker is None or marker.get("group_id") != u"{}".format(group_id or u""):
        return False
    canonical = _canonical_role(role)
    return canonical is None or marker.get("role") == canonical


def _set_readable_title(view, DB, role):
    canonical = _canonical_role(role)
    text = READABLE_TITLES.get(canonical)
    if text is not None:
        _set_description(view, DB, text)


def _expected_artifact_type(element, DB, role):
    canonical = _canonical_role(role)
    if canonical == "SHEET":
        return isinstance(element, DB.ViewSheet)
    if canonical == "CALCULATION":
        return isinstance(element, DB.ViewDrafting)
    if canonical in ("PLAN", "AXON"):
        return isinstance(element, DB.View3D)
    if canonical in ("SECTION_1", "SECTION_2"):
        return isinstance(element, DB.ViewSection)
    return False


def _migrate_legacy_ownership(element, DB, group_id, role,
                              expected_name=None, allow_renamed=False):
    if element is None or not _expected_artifact_type(element, DB, role):
        return False
    actual_name = getattr(element, "Name", u"")
    if not _legacy_identity_matches(
            _description(element, DB), group_id, role,
            actual_name=actual_name, expected_name=expected_name,
            allow_renamed=allow_renamed):
        return False
    _set_marker(element, DB, group_id, role)
    _set_readable_title(element, DB, role)
    return True


def _require_owned_or_migrate(element, DB, group_id, role,
                              expected_name=None, allow_renamed=False):
    if _is_owned(element, DB, group_id, role):
        return
    if _migrate_legacy_ownership(
            element, DB, group_id, role,
            expected_name=expected_name, allow_renamed=allow_renamed):
        return
    marker = _read_marker(element, DB) or _parse_legacy_marker(_description(element, DB))
    if marker and marker.get("group_id") != u"{}".format(group_id or u""):
        raise Exception(
            u"Совпадение короткого ID группы: объект «{}» принадлежит группе {}, а выбранная группа — {}."
            .format(getattr(element, "Name", u"?"), marker.get("group_id"), group_id))
    raise Exception(
        u"Объект «{}» уже существует, но не имеет подтверждённого маркера владения этой группы ЗУ."
        .format(getattr(element, "Name", u"?")))


def _all_views(doc, DB):
    return list(DB.FilteredElementCollector(doc).OfClass(DB.View))


def _find_view_by_name(doc, DB, name):
    for view in _all_views(doc, DB):
        try:
            if view.IsTemplate:
                continue
        except Exception:
            pass
        try:
            if view.Name == name:
                return view
        except Exception:
            pass
    return None


def _same_element(first, second):
    if first is second:
        return True
    try:
        return first.Id == second.Id
    except Exception:
        return False


def _matches_artifact_identity(element, DB, group_id, role, expected_name):
    if element is None or not _expected_artifact_type(element, DB, role):
        return False
    if _is_owned(element, DB, group_id, role):
        return True
    return _legacy_identity_matches(
        _description(element, DB), group_id, role,
        actual_name=getattr(element, "Name", u""),
        expected_name=expected_name, allow_renamed=False)


def _generated_view_candidates(doc, DB, group_id, role, expected_name):
    result = []
    for view in _all_views(doc, DB):
        try:
            if view.IsTemplate:
                continue
        except Exception:
            pass
        if _matches_artifact_identity(
                view, DB, group_id, role, expected_name):
            result.append(view)
    return result


def _raise_name_collision(view, DB, group_id):
    marker = _read_marker(view, DB) or _parse_legacy_marker(_description(view, DB))
    if marker and marker.get("group_id") != u"{}".format(group_id or u""):
        raise Exception(
            u"Совпадение короткого ID группы: объект «{}» принадлежит группе {}, а выбранная группа — {}."
            .format(getattr(view, "Name", u"?"), marker.get("group_id"), group_id))
    raise Exception(
        u"В проекте уже существует пользовательский вид «{}». Переименуйте его перед формированием листа ЗУ."
        .format(getattr(view, "Name", u"?")))


def _assert_expected_name_available(doc, DB, group_id, expected_name, candidates):
    existing = _find_view_by_name(doc, DB, expected_name)
    if existing is None:
        return
    for candidate in candidates:
        if _same_element(existing, candidate):
            return
    _raise_name_collision(existing, DB, group_id)


def _calculation_view_candidate(doc, DB, group_id, expected_name):
    candidates = _generated_view_candidates(
        doc, DB, group_id, "CALCULATION", expected_name)
    if len(candidates) > 1:
        raise Exception(
            u"Найдено несколько автоматических расчётных видов для группы {}. "
            u"Удалите дубликаты и повторите команду.".format(group_id))
    _assert_expected_name_available(
        doc, DB, group_id, expected_name, candidates)
    return candidates[0] if candidates else None


def _sheet_evidence_roles(doc, DB, sheet, group_id, artifact_names):
    roles = set()
    try:
        viewports = DB.FilteredElementCollector(doc, sheet.Id).OfClass(DB.Viewport)
    except Exception:
        viewports = []
    for viewport in viewports:
        try:
            view = doc.GetElement(viewport.ViewId)
        except Exception:
            view = None
        for role in MODEL_ROLES + ("CALCULATION",):
            if _matches_artifact_identity(
                    view, DB, group_id, role, artifact_names[role]):
                roles.add(role)
                break
    return roles


def _find_generated_sheet(doc, DB, group_id, artifact_names):
    sheets = list(DB.FilteredElementCollector(doc).OfClass(DB.ViewSheet))
    owned = [sheet for sheet in sheets if _is_owned(sheet, DB, group_id, "SHEET")]
    if len(owned) > 1:
        raise Exception(u"Найдено несколько автоматических листов для одной группы ЗУ.")
    if owned:
        return owned[0]

    legacy_marked = []
    for sheet in sheets:
        if (isinstance(sheet, DB.ViewSheet) and
                _legacy_identity_matches(
                    _description(sheet, DB), group_id, "SHEET",
                    actual_name=getattr(sheet, "Name", u""),
                    expected_name=None, allow_renamed=True)):
            legacy_marked.append(sheet)
    if len(legacy_marked) > 1:
        raise Exception(
            u"Найдено несколько старых листов r30 с точной меткой группы {}. "
            u"Оставьте один лист и повторите команду.".format(group_id))
    if legacy_marked:
        sheet = legacy_marked[0]
        if not _migrate_legacy_ownership(
                sheet, DB, group_id, "SHEET", allow_renamed=True):
            raise Exception(u"Не удалось перенести точную метку старого листа r30.")
        return sheet

    required_roles = set(MODEL_ROLES + ("CALCULATION",))
    migration_candidates = []
    partial_candidates = []
    for sheet in sheets:
        roles = _sheet_evidence_roles(doc, DB, sheet, group_id, artifact_names)
        if roles == required_roles:
            migration_candidates.append(sheet)
        elif roles:
            partial_candidates.append((sheet, roles))

    if len(migration_candidates) > 1:
        raise Exception(
            u"Найдено несколько старых листов r30 с полным комплектом видов группы {}. "
            u"Оставьте один лист и повторите команду.".format(group_id))
    if migration_candidates:
        sheet = migration_candidates[0]
        _set_marker(sheet, DB, group_id, "SHEET")
        return sheet
    if partial_candidates:
        sheet, roles = partial_candidates[0]
        raise Exception(
            u"Найден неполный старый лист r30 «{}» для группы {} (роли: {}). "
            u"Удалите его служебные виды/viewport'ы или восстановите полный комплект и повторите команду."
            .format(getattr(sheet, "Name", u"?"), group_id,
                    u", ".join(sorted(roles))))
    return None


def _assert_active_generated_artifact_inactive(uidoc, DB, group_id):
    try:
        active = uidoc.ActiveView
    except Exception:
        active = None
    if active is None:
        return
    marker = _read_marker(active, DB)
    if marker is None:
        marker = _parse_legacy_marker(_description(active, DB))
    role = _canonical_role(marker.get("role")) if marker is not None else None
    is_generated = bool(
        marker is not None and
        marker.get("group_id") == u"{}".format(group_id or u"") and
        role in KNOWN_ROLES and role != "SHEET")
    if not is_generated:
        return
    raise Exception(
        u"Автоматический объект документации «{}» сейчас открыт. "
        u"Откройте другой модельный вид или лист и повторите команду «Лист ЗУ»."
        .format(getattr(active, "Name", u"?")))


def _bounding_box(doc, DB, group_info):
    elements = list((group_info or {}).get("elements") or [])
    if not elements:
        elements = collect_group_elements(doc, DB, (group_info or {}).get("group_id"))
    xmin = ymin = zmin = None
    xmax = ymax = zmax = None
    for element in elements:
        try:
            box = element.get_BoundingBox(None)
        except Exception:
            box = None
        if box is None:
            continue
        try:
            lo = box.Min
            hi = box.Max
        except Exception:
            continue
        xmin = lo.X if xmin is None else min(xmin, lo.X)
        ymin = lo.Y if ymin is None else min(ymin, lo.Y)
        zmin = lo.Z if zmin is None else min(zmin, lo.Z)
        xmax = hi.X if xmax is None else max(xmax, hi.X)
        ymax = hi.Y if ymax is None else max(ymax, hi.Y)
        zmax = hi.Z if zmax is None else max(zmax, hi.Z)
    if xmin is None:
        raise ValueError(u"Не удалось определить габариты BIM-группы ЗУ.")

    sx = max(0.0, xmax - xmin)
    sy = max(0.0, ymax - ymin)
    sz = max(0.0, zmax - zmin)
    mx = max(_mm(500.0), sx * 0.10)
    my = max(_mm(500.0), sy * 0.10)
    mz = max(_mm(300.0), sz * 0.10)
    return {
        "min": DB.XYZ(xmin - mx, ymin - my, zmin - mz),
        "max": DB.XYZ(xmax + mx, ymax + my, zmax + mz),
        "raw_min": DB.XYZ(xmin, ymin, zmin),
        "raw_max": DB.XYZ(xmax, ymax, zmax),
    }


def _bbox_center(DB, bbox):
    lo = bbox["min"]
    hi = bbox["max"]
    return DB.XYZ((lo.X + hi.X) / 2.0, (lo.Y + hi.Y) / 2.0, (lo.Z + hi.Z) / 2.0)


def _bbox_corners(DB, bbox):
    lo = bbox["min"]
    hi = bbox["max"]
    result = []
    for x in (lo.X, hi.X):
        for y in (lo.Y, hi.Y):
            for z in (lo.Z, hi.Z):
                result.append(DB.XYZ(x, y, z))
    return result


def _xy_direction(DB, p0, p1):
    if p0 is None or p1 is None:
        return None
    dx = p1.X - p0.X
    dy = p1.Y - p0.Y
    length = math.sqrt(dx * dx + dy * dy)
    if length < 1e-9:
        return None
    return DB.XYZ(dx / length, dy / length, 0.0)


def _main_direction(DB, group_info):
    route = list((group_info or {}).get("route_points") or [])
    best = None
    best_len = 0.0
    if len(route) >= 2:
        for idx in range(len(route) - 1):
            direction = _xy_direction(DB, route[idx], route[idx + 1])
            if direction is None:
                continue
            dx = route[idx + 1].X - route[idx].X
            dy = route[idx + 1].Y - route[idx].Y
            length = math.sqrt(dx * dx + dy * dy)
            if length > best_len:
                best_len = length
                best = direction
        if bool((group_info or {}).get("close_loop")) and len(route) > 2:
            direction = _xy_direction(DB, route[-1], route[0])
            if direction is not None:
                dx = route[0].X - route[-1].X
                dy = route[0].Y - route[-1].Y
                length = math.sqrt(dx * dx + dy * dy)
                if length > best_len:
                    best_len = length
                    best = direction
    if best is not None:
        return best

    points = list((group_info or {}).get("points") or [])
    for i in range(len(points)):
        for j in range(i + 1, len(points)):
            direction = _xy_direction(DB, points[i], points[j])
            if direction is None:
                continue
            dx = points[j].X - points[i].X
            dy = points[j].Y - points[i].Y
            length = math.sqrt(dx * dx + dy * dy)
            if length > best_len:
                best_len = length
                best = direction
    return best if best is not None else DB.XYZ.BasisX


def _three_d_type(doc, DB):
    for item in DB.FilteredElementCollector(doc).OfClass(DB.ViewFamilyType):
        try:
            if item.ViewFamily == DB.ViewFamily.ThreeDimensional:
                return item
        except Exception:
            pass
    return None


def _section_type(doc, DB):
    for item in DB.FilteredElementCollector(doc).OfClass(DB.ViewFamilyType):
        try:
            if item.ViewFamily == DB.ViewFamily.Section:
                return item
        except Exception:
            pass
    return None


def _set_model_view_style(view, DB):
    try:
        view.DetailLevel = DB.ViewDetailLevel.Fine
    except Exception:
        pass
    try:
        view.DisplayStyle = DB.DisplayStyle.HiddenLine
    except Exception:
        pass
    try:
        view.CropBoxVisible = False
    except Exception:
        pass


def _world_section_box(DB, bbox):
    box = DB.BoundingBoxXYZ()
    try:
        box.Enabled = True
    except Exception:
        pass
    box.Min = bbox["min"]
    box.Max = bbox["max"]
    box.Transform = DB.Transform.Identity
    return box


def _clear_view_template(view, DB):
    try:
        view.ViewTemplateId = DB.ElementId.InvalidElementId
    except Exception as ex:
        raise Exception(
            u"Не удалось снять шаблон с автоматически созданного вида «{}»: {}"
            .format(getattr(view, "Name", u"?"), ex))


def _create_top_view(doc, DB, group_id, name, bbox):
    view_type = _three_d_type(doc, DB)
    if view_type is None:
        raise Exception(u"В проекте отсутствует тип 3D-вида.")
    view = DB.View3D.CreateIsometric(doc, view_type.Id)
    view.Name = name
    _clear_view_template(view, DB)
    _set_marker(view, DB, group_id, "PLAN")
    _set_readable_title(view, DB, "PLAN")
    _set_model_view_style(view, DB)
    view.SetSectionBox(_world_section_box(DB, bbox))

    center = _bbox_center(DB, bbox)
    height = max(_mm(1000.0), bbox["max"].Z - bbox["min"].Z)
    eye = center + DB.XYZ.BasisZ.Multiply(height * 4.0)
    orientation = DB.ViewOrientation3D(eye, DB.XYZ.BasisY, DB.XYZ(0.0, 0.0, -1.0))
    try:
        view.SetOrientation(orientation)
    except Exception:
        pass
    return view


def _isometric_orientation(DB, bbox):
    center = _bbox_center(DB, bbox)
    forward = DB.XYZ(-1.0, -1.0, -0.72).Normalize()
    right = forward.CrossProduct(DB.XYZ.BasisZ)
    if right.GetLength() < 1e-9:
        right = DB.XYZ.BasisX
    else:
        right = right.Normalize()
    up = right.CrossProduct(forward).Normalize()
    span = max(
        bbox["max"].X - bbox["min"].X,
        bbox["max"].Y - bbox["min"].Y,
        bbox["max"].Z - bbox["min"].Z,
        _mm(1000.0))
    eye = center - forward.Multiply(span * 4.0)
    return DB.ViewOrientation3D(eye, up, forward)


def _create_axo_view(doc, DB, group_id, name, bbox):
    view_type = _three_d_type(doc, DB)
    if view_type is None:
        raise Exception(u"В проекте отсутствует тип 3D-вида.")
    view = DB.View3D.CreateIsometric(doc, view_type.Id)
    view.Name = name
    _clear_view_template(view, DB)
    _set_marker(view, DB, group_id, "AXON")
    _set_readable_title(view, DB, "AXON")
    _set_model_view_style(view, DB)
    view.SetSectionBox(_world_section_box(DB, bbox))
    try:
        view.SetOrientation(_isometric_orientation(DB, bbox))
    except Exception:
        pass
    return view


def _projection_half_extent(DB, bbox, center, axis):
    maximum = 0.0
    for point in _bbox_corners(DB, bbox):
        value = abs((point - center).DotProduct(axis))
        maximum = max(maximum, value)
    return maximum


def _create_section_view(doc, DB, group_id, name, bbox, right_axis, role):
    section_type = _section_type(doc, DB)
    if section_type is None:
        raise Exception(u"В проекте отсутствует тип разреза ViewFamily.Section.")

    right = DB.XYZ(right_axis.X, right_axis.Y, 0.0)
    if right.GetLength() < 1e-9:
        right = DB.XYZ.BasisX
    else:
        right = right.Normalize()
    up = DB.XYZ.BasisZ
    view_dir = right.CrossProduct(up)
    if view_dir.GetLength() < 1e-9:
        view_dir = DB.XYZ.BasisY
    else:
        view_dir = view_dir.Normalize()

    center = _bbox_center(DB, bbox)
    half_w = max(_mm(500.0), _projection_half_extent(DB, bbox, center, right))
    half_h = max(_mm(500.0), (bbox["max"].Z - bbox["min"].Z) / 2.0)
    half_d = max(_mm(500.0), _projection_half_extent(DB, bbox, center, view_dir))

    # Put the section's near plane just in front of the complete grounding group.
    # This keeps the vertical section readable even for a non-linear route while
    # still using a native ViewSection with a bounded depth.
    origin = center - view_dir.Multiply(half_d)
    transform = DB.Transform.Identity
    transform.Origin = origin
    transform.BasisX = right
    transform.BasisY = up
    transform.BasisZ = view_dir

    box = DB.BoundingBoxXYZ()
    try:
        box.Enabled = True
    except Exception:
        pass
    box.Transform = transform
    box.Min = DB.XYZ(-half_w, -half_h, 0.0)
    box.Max = DB.XYZ(half_w, half_h, max(_mm(1000.0), half_d * 2.0))

    view = DB.ViewSection.CreateSection(doc, section_type.Id, box)
    view.Name = name
    _clear_view_template(view, DB)
    _set_marker(view, DB, group_id, role)
    _set_readable_title(view, DB, role)
    _set_model_view_style(view, DB)
    return view


def _viewports_for_view(doc, DB, view_id):
    result = []
    for viewport in DB.FilteredElementCollector(doc).OfClass(DB.Viewport):
        try:
            if viewport.ViewId == view_id:
                result.append(viewport)
        except Exception:
            pass
    return result


def _assert_generated_view_safe_to_replace(doc, DB, view, sheet_id, group_id,
                                           role, expected_name):
    if view is None:
        return
    _require_owned_or_migrate(
        view, DB, group_id, role, expected_name=expected_name)
    for viewport in _viewports_for_view(doc, DB, view.Id):
        if viewport.SheetId != sheet_id:
            other_sheet = doc.GetElement(viewport.SheetId)
            number = getattr(other_sheet, "SheetNumber", u"?") if other_sheet is not None else u"?"
            raise Exception(
                u"Автоматический вид «{}» размещен на другом листе {}. "
                u"Плагин не удаляет пользовательские размещения. Снимите этот вид с другого листа и повторите команду."
                .format(view.Name, number))


def _delete_generated_model_views(doc, DB, name, group_id, sheet_id, role):
    views = _generated_view_candidates(doc, DB, group_id, role, name)
    _assert_expected_name_available(doc, DB, group_id, name, views)
    for view in views:
        placed_on_owned_sheet = any(
            viewport.SheetId == sheet_id
            for viewport in _viewports_for_view(doc, DB, view.Id))
        if (u"{}".format(getattr(view, "Name", u"")) != u"{}".format(name) and
                not placed_on_owned_sheet):
            raise Exception(
                u"Найдена непоставленная копия автоматического вида «{}» с меткой группы {}. "
                u"Плагин не удаляет такую копию, поскольку она может содержать пользовательские аннотации. "
                u"Снимите с неё служебную метку или удалите копию вручную."
                .format(getattr(view, "Name", u"?"), group_id))
        _assert_generated_view_safe_to_replace(
            doc, DB, view, sheet_id, group_id, role, name)
        for viewport in _viewports_for_view(doc, DB, view.Id):
            if viewport.SheetId == sheet_id:
                doc.Delete(viewport.Id)
        doc.Delete(view.Id)


def _view_outline_size(view):
    try:
        outline = view.Outline
        width = max(0.0, outline.Max.U - outline.Min.U)
        height = max(0.0, outline.Max.V - outline.Min.V)
        if width > 1e-9 and height > 1e-9:
            return width, height
    except Exception:
        pass
    return 0.0, 0.0


def _sheet_size(sheet):
    outline = sheet.Outline
    return (max(0.0, outline.Max.U - outline.Min.U),
            max(0.0, outline.Max.V - outline.Min.V))


def _parameter_double(element, DB, built_in_name, fallback_names):
    try:
        bip = getattr(DB.BuiltInParameter, built_in_name)
        parameter = element.get_Parameter(bip)
        if parameter and parameter.HasValue and parameter.AsDouble() > 0:
            return parameter.AsDouble()
    except Exception:
        pass
    for name in fallback_names:
        try:
            parameter = element.LookupParameter(name)
            if parameter and parameter.HasValue and parameter.AsDouble() > 0:
                return parameter.AsDouble()
        except Exception:
            pass
    return None


def _symbol_label(symbol):
    parts = []
    for value in (getattr(symbol, "FamilyName", None), getattr(symbol, "Name", None)):
        if value:
            parts.append(u"{}".format(value))
    try:
        parts.append(u"{}".format(symbol.Family.Name))
    except Exception:
        pass
    return u" ".join(parts).upper().replace(u"А", "A")


def _title_block_dimensions(symbol, DB):
    width = _parameter_double(
        symbol, DB, "SHEET_WIDTH", (u"Sheet Width", u"Width", u"Ширина листа", u"Ширина"))
    height = _parameter_double(
        symbol, DB, "SHEET_HEIGHT", (u"Sheet Height", u"Height", u"Высота листа", u"Высота"))
    return width, height


def _large_landscape(width, height):
    if not width or not height or width <= height:
        return False
    tolerance = _mm(5.0)
    for expected_w, expected_h in ((A1_WIDTH_MM, A1_HEIGHT_MM),
                                   (A0_WIDTH_MM, A0_HEIGHT_MM)):
        if (abs(width - _mm(expected_w)) <= tolerance and
                abs(height - _mm(expected_h)) <= tolerance):
            return True
    return False


def _title_block_on_sheet(doc, DB, sheet):
    if sheet is None:
        return None
    collector = (DB.FilteredElementCollector(doc, sheet.Id)
                 .OfCategory(DB.BuiltInCategory.OST_TitleBlocks)
                 .WhereElementIsNotElementType())
    for instance in collector:
        symbol = doc.GetElement(instance.GetTypeId())
        if symbol is not None:
            return symbol
    return None


def _title_block_rank(symbol, DB):
    width, height = _title_block_dimensions(symbol, DB)
    label = _symbol_label(symbol)
    if width and height:
        if not _large_landscape(width, height):
            return None
        a1_error = abs(width - _mm(A1_WIDTH_MM)) + abs(height - _mm(A1_HEIGHT_MM))
        a0_error = abs(width - _mm(A0_WIDTH_MM)) + abs(height - _mm(A0_HEIGHT_MM))
        return (0, 0 if a1_error <= a0_error else 1, min(a1_error, a0_error))
    if "A1" in label:
        return (1, 0, 100.0)
    if "A0" in label:
        return (1, 1, 100.0)
    return None


def select_title_block(doc, DB, active_view=None):
    """Prefer an adequate active title block, then loaded A1/A0 types."""
    if active_view is not None and isinstance(active_view, DB.ViewSheet):
        width, height = _sheet_size(active_view)
        symbol = _title_block_on_sheet(doc, DB, active_view)
        if symbol is not None and _large_landscape(width, height):
            return symbol

    ranked = []
    collector = (DB.FilteredElementCollector(doc)
                 .OfCategory(DB.BuiltInCategory.OST_TitleBlocks)
                 .WhereElementIsElementType())
    for symbol in collector:
        rank = _title_block_rank(symbol, DB)
        if rank is not None:
            try:
                identity = symbol.Id.IntegerValue
            except Exception:
                identity = u"{}".format(symbol.Id)
            ranked.append((rank, identity, symbol))
    if not ranked:
        raise Exception(
            u"Для листа ЗУ требуется загруженная горизонтальная основная надпись A1 или A0.")
    ranked.sort(key=lambda item: (item[0], item[1]))
    return ranked[0][2]

def _unique_sheet_number(doc, DB, base, exclude_id=None):
    used = set()
    for sheet in DB.FilteredElementCollector(doc).OfClass(DB.ViewSheet):
        try:
            if exclude_id is not None and sheet.Id == exclude_id:
                continue
            used.add(sheet.SheetNumber)
        except Exception:
            pass
    if base not in used:
        return base
    index = 2
    while u"{}-{}".format(base, index) in used:
        index += 1
    return u"{}-{}".format(base, index)


def _view_projected_size(view_kind, bbox, main_dir, DB):
    sx = max(_mm(100.0), bbox["max"].X - bbox["min"].X)
    sy = max(_mm(100.0), bbox["max"].Y - bbox["min"].Y)
    sz = max(_mm(100.0), bbox["max"].Z - bbox["min"].Z)
    center = _bbox_center(DB, bbox)
    if view_kind == "PLAN":
        return sx, sy
    if view_kind == "AXON":
        horizontal = math.sqrt(sx * sx + sy * sy)
        return horizontal, sz + horizontal * 0.35
    if view_kind == "SECTION_1":
        width = 2.0 * _projection_half_extent(DB, bbox, center, main_dir)
        return max(_mm(100.0), width), sz
    perp = DB.XYZ(-main_dir.Y, main_dir.X, 0.0)
    width = 2.0 * _projection_half_extent(DB, bbox, center, perp)
    return max(_mm(100.0), width), sz


def _pick_scale(model_width, model_height, paper_width, paper_height):
    usable_w = max(_mm(20.0), paper_width - _mm(8.0))
    usable_h = max(_mm(20.0), paper_height - _mm(14.0))
    for scale in SCALE_CANDIDATES:
        if model_width / float(scale) <= usable_w and model_height / float(scale) <= usable_h:
            return scale, False
    return SCALE_CANDIDATES[-1], True


def _set_scale(view, scale):
    requested = int(scale)
    view.Scale = requested
    actual = int(view.Scale)
    if actual != requested:
        raise Exception(
            u"Revit назначил виду «{}» масштаб 1:{}, хотя требовался 1:{}."
            .format(getattr(view, "Name", u"?"), actual, requested))
    return actual


def _remove_viewport_on_sheet(doc, DB, sheet, view, allow_other_sheet=False):
    for viewport in _viewports_for_view(doc, DB, view.Id):
        if viewport.SheetId == sheet.Id:
            doc.Delete(viewport.Id)
        elif not allow_other_sheet:
            other_sheet = doc.GetElement(viewport.SheetId)
            number = getattr(other_sheet, "SheetNumber", u"?") if other_sheet is not None else u"?"
            raise Exception(
                u"Вид «{}» уже размещен на другом листе {}. Плагин не удаляет пользовательское размещение."
                .format(view.Name, number))


def _place_view(doc, DB, sheet, view, center):
    if not DB.Viewport.CanAddViewToSheet(doc, sheet.Id, view.Id):
        raise Exception(u"Вид «{}» нельзя разместить на листе {}.".format(view.Name, sheet.SheetNumber))
    return DB.Viewport.Create(doc, sheet.Id, view.Id, center)


def _layout_centers(DB, sheet, calc_width=0.0):
    """Readable A1/A0 composition: calculation at left, graphics at right.

    Graphics are no longer four narrow slots stacked vertically. The plan gets
    a large full-width top slot, the two sections share the middle row, and the
    axonometry gets a full-width bottom slot.
    """
    outline = sheet.Outline
    u0 = outline.Min.U + _mm(SHEET_MARGIN_MM)
    u1 = outline.Max.U - _mm(SHEET_MARGIN_MM)
    v0 = outline.Min.V + _mm(SHEET_MARGIN_MM)
    v1 = outline.Max.V - _mm(SHEET_MARGIN_MM)
    width = u1 - u0
    height = v1 - v0
    gap = _mm(GRAPHICS_GAP_MM)

    min_right = min(_mm(MIN_GRAPHICS_WIDTH_MM), max(_mm(140.0), width * 0.44))
    desired_left = max(width * CALC_ZONE_RATIO, float(calc_width or 0.0) + _mm(8.0))
    max_left = max(_mm(160.0), width - gap - min_right)
    left_w = min(max_left, desired_left, width * CALC_ZONE_MAX_RATIO)
    left_w = max(_mm(160.0), left_w)

    right_x0 = u0 + left_w + gap
    right_w = max(_mm(120.0), u1 - right_x0)
    graphics_v0 = min(v1 - _mm(180.0), v0 + _mm(TITLEBLOCK_RESERVED_HEIGHT_MM))
    graphics_h = max(_mm(180.0), v1 - graphics_v0)

    gap_y = _mm(8.0)
    gap_x = _mm(8.0)
    available_h = max(_mm(150.0), graphics_h - gap_y * 2.0)

    plan_h = available_h * 0.40
    sections_h = available_h * 0.30
    axo_h = available_h - plan_h - sections_h

    plan_top = v1
    plan_bottom = plan_top - plan_h
    sections_top = plan_bottom - gap_y
    sections_bottom = sections_top - sections_h
    axo_top = sections_bottom - gap_y
    axo_bottom = max(graphics_v0, axo_top - axo_h)

    half_w = max(_mm(50.0), (right_w - gap_x) / 2.0)
    centers = [
        DB.XYZ(right_x0 + right_w / 2.0, (plan_top + plan_bottom) / 2.0, 0.0),
        DB.XYZ(right_x0 + half_w / 2.0, (sections_top + sections_bottom) / 2.0, 0.0),
        DB.XYZ(right_x0 + half_w + gap_x + half_w / 2.0, (sections_top + sections_bottom) / 2.0, 0.0),
        DB.XYZ(right_x0 + right_w / 2.0, (axo_top + axo_bottom) / 2.0, 0.0),
    ]
    widths = [right_w, half_w, half_w, right_w]
    heights = [
        max(_mm(50.0), plan_h),
        max(_mm(50.0), sections_h),
        max(_mm(50.0), sections_h),
        max(_mm(50.0), axo_top - axo_bottom),
    ]
    calc_center = DB.XYZ(u0 + left_w / 2.0, v0 + height / 2.0, 0.0)
    return {
        "calc": calc_center,
        "graphics": centers,
        "graphics_widths": widths,
        "graphics_heights": heights,
    }

def _source_table(report_tables, prefix):
    for table in (report_tables or {}).get("tables") or []:
        title = u"{}".format(table.get("title", u""))
        if title.startswith(prefix):
            return table
    return None


def _rows_by_labels(table, labels):
    wanted = set(labels or [])
    result = []
    for row in (table or {}).get("rows") or []:
        if not row:
            continue
        if u"{}".format(row[0]) in wanted:
            result.append(list(row))
    return result


def _scale_widths(widths_mm, width_limit_mm):
    widths = [float(x) for x in widths_mm]
    total = sum(widths)
    if not width_limit_mm or total <= float(width_limit_mm) or total <= 1e-9:
        return widths
    factor = float(width_limit_mm) / total
    return [max(45.0, x * factor) for x in widths]


def _join_result_and_note(value, note):
    value = u"{}".format(value or u"").strip()
    note = u"{}".format(note or u"").strip()
    if note and note not in (u"—", u"-"):
        if value:
            return u"{}\n{}".format(value, note)
        return note
    return value


def _readable_sheet_tables(report_tables, width_limit_mm):
    """Build three readable sheet sections: given data, calculation, results."""
    source = report_tables or {}
    input_table = _source_table(source, u"1.")
    calc_table = _source_table(source, u"2.")
    conductor_table = _source_table(source, u"3.")
    decision_table = _source_table(source, u"5.")
    bim_table = _source_table(source, u"7.")
    summary_table = _source_table(source, u"0.")

    input_labels = (
        u"Панель с ГЗШ", u"Адрес объекта", u"Система заземления", u"Назначение ЗУ",
        u"Тип ввода", u"Тип грунта", u"Удельное сопротивление грунта",
        u"Сезонный коэффициент", u"Принятая глубина промерзания",
        u"Расчетное удельное сопротивление", u"Вертикальные электроды",
        u"Горизонтальный электрод", u"Материал электрода", u"Длина стержня",
        u"Диаметр стержня", u"Глубина до верха", u"Длина модульной секции",
    )
    input_rows = []
    for row in _rows_by_labels(input_table, input_labels):
        note = _join_result_and_note(row[1] if len(row) > 1 else u"",
                                     row[3] if len(row) > 3 else u"")
        input_rows.append([row[0] if len(row) > 0 else u"",
                           row[2] if len(row) > 2 else u"", note])
    for row in (bim_table or {}).get("rows") or []:
        if not row:
            continue
        input_rows.append([
            row[0] if len(row) > 0 else u"",
            row[2] if len(row) > 2 else u"",
            _join_result_and_note(
                row[1] if len(row) > 1 else u"",
                row[3] if len(row) > 3 else u"")])

    calc_rows = []
    for row in (calc_table or {}).get("rows") or []:
        if not row:
            continue
        calc_rows.append([row[0] if len(row) > 0 else u"",
                          row[1] if len(row) > 1 else u"",
                          _join_result_and_note(row[2] if len(row) > 2 else u"",
                                                row[3] if len(row) > 3 else u"")])

    conductor_labels = (u"Термическое сечение", u"Нормативный минимум", u"ПРИНЯТОЕ СЕЧЕНИЕ ЗУ–ГЗШ")
    for row in _rows_by_labels(conductor_table, conductor_labels):
        calc_rows.append([u"Проводник: {}".format(row[0] if len(row) > 0 else u""),
                          row[1] if len(row) > 1 else u"",
                          _join_result_and_note(row[2] if len(row) > 2 else u"",
                                                row[3] if len(row) > 3 else u"")])

    result_rows = []
    for row in (decision_table or {}).get("rows") or []:
        if not row:
            continue
        result_rows.append([row[0] if len(row) > 0 else u"",
                            row[2] if len(row) > 2 else u"",
                            row[3] if len(row) > 3 else u""])

    existing_labels = set(u"{}".format(r[0]) for r in result_rows if r)
    if u"Итог проверки" not in existing_labels:
        for row in (summary_table or {}).get("rows") or []:
            if row and u"{}".format(row[0]) == u"Итог проверки":
                result_rows.append([row[0], row[1] if len(row) > 1 else u"",
                                    _join_result_and_note(row[2] if len(row) > 2 else u"",
                                                          row[3] if len(row) > 3 else u"")])
                break

    return {
        "title": u"РАСЧЁТ ЗАЗЕМЛЯЮЩЕГО УСТРОЙСТВА",
        "subtitle": source.get("subtitle", u""),
        "sheet_profile": u"readable",
        "tables": [
            {"title": u"1. ОБЪЕКТ И ИСХОДНЫЕ ДАННЫЕ",
             "columns": [u"Параметр", u"Принятое значение", u"Примечание"],
             "rows": input_rows,
             "col_widths_mm": _scale_widths([120.0, 105.0, 125.0], width_limit_mm),
             "section_fill_key": "section_input"},
            {"title": u"2. РАСЧЁТ",
             "columns": [u"Расчётный этап", u"Формула / условие", u"Результат"],
             "rows": calc_rows,
             "col_widths_mm": _scale_widths([120.0, 120.0, 110.0], width_limit_mm),
             "section_fill_key": "section_calc"},
            {"title": u"3. ИТОГИ И ПРИНЯТОЕ РЕШЕНИЕ",
             "columns": [u"Показатель", u"Итоговое значение", u"Комментарий / статус"],
             "rows": result_rows,
             "col_widths_mm": _scale_widths([120.0, 95.0, 135.0], width_limit_mm),
             "section_fill_key": "section_result"},
        ],
    }


def _sheet_report_tables(report_tables, level, width_limit_mm):
    """Return a dedicated readable three-block sheet report."""
    result = _readable_sheet_tables(report_tables, width_limit_mm)
    if level <= 0:
        return result

    compact = {
        "title": result.get("title"),
        "subtitle": result.get("subtitle"),
        "sheet_profile": result.get("sheet_profile"),
        "tables": [],
    }
    for table in result.get("tables") or []:
        cloned = dict(table)
        rows = list(table.get("rows") or [])
        if level == 1:
            limit = 10 if table.get("title", u"").startswith(u"1.") else 8
        else:
            limit = 7 if table.get("title", u"").startswith(u"1.") else 6
        cloned["rows"] = rows[:limit]
        compact["tables"].append(cloned)
    return compact

def _calc_fits(view, max_width, max_height):
    width, height = _view_outline_size(view)
    if width <= 0.0 or height <= 0.0:
        return False, width, height
    return (width <= max_width + _mm(2.0) and height <= max_height + _mm(2.0)), width, height


def _create_fitted_calculation_view(doc, uidoc, DB, report_text, calc_name,
                                    report_tables, group_id, sheet):
    """Render the calculation block at the richest level that fits this sheet."""
    sheet_w, sheet_h = _sheet_size(sheet)
    usable_w = max(_mm(120.0), sheet_w - _mm(SHEET_MARGIN_MM * 2.0))
    usable_h = max(_mm(120.0), sheet_h - _mm(SHEET_MARGIN_MM * 2.0))
    gap = _mm(GRAPHICS_GAP_MM)
    min_right = min(_mm(MIN_GRAPHICS_WIDTH_MM), max(_mm(60.0), usable_w * 0.34))
    max_calc_w = max(_mm(180.0), min(usable_w * CALC_ZONE_MAX_RATIO,
                                     usable_w - gap - min_right))
    width_limit_mm = max(220.0, _ft_to_mm(max_calc_w) - 6.0)

    def validate_ownership(view):
        _require_owned_or_migrate(
            view, DB, group_id, "CALCULATION", expected_name=calc_name)

    def write_ownership(view):
        _set_marker(view, DB, group_id, "CALCULATION")
        _set_readable_title(view, DB, "CALCULATION")

    existing_view = _calculation_view_candidate(
        doc, DB, group_id, calc_name)
    last = None
    for level in (0, 1, 2):
        tables = _sheet_report_tables(report_tables, level, width_limit_mm)
        view = create_or_update_named_calculation_view(
            doc, uidoc, DB, report_text, calc_name,
            report_tables=tables,
            ownership_validator=validate_ownership,
            ownership_writer=write_ownership,
            existing_view=existing_view,
            activate=False)
        existing_view = view
        try:
            doc.Regenerate()
        except Exception:
            pass
        fits, width, height = _calc_fits(view, max_calc_w, usable_h)
        last = (view, level, width, height)
        if fits:
            return view, level, width, height, False

    # Even on very small company title blocks, keep an essential calculation block
    # instead of aborting the complete sheet.  The caller reports a warning.
    return last[0], last[1], last[2], last[3], True

def create_or_update_documentation_sheet(doc, uidoc, DB, group_info, report_text, report_tables):
    """Create/update one documentation sheet for one EOM grounding BIM group.

    The sheet uses the richest calculation-table representation that physically
    fits the loaded title block.  Full calculation data is never modified.
    """
    group_id = (group_info or {}).get("group_id")
    if not group_id:
        raise ValueError(u"Не задан group_id ЗУ для формирования листа.")

    short = _short_id(group_id)
    artifact_names = _artifact_names(group_id)
    calc_name = artifact_names["CALCULATION"]
    sheet_name = u"Заземляющее устройство {}".format(short)
    sheet_number_base = u"ЭОМ-ЗУ-{}".format(short)
    names = dict((role, artifact_names[role]) for role in MODEL_ROLES)

    _assert_active_generated_artifact_inactive(uidoc, DB, group_id)
    bbox = _bounding_box(doc, DB, group_info)
    main_dir = _main_direction(DB, group_info)
    warnings = []

    tg = DB.TransactionGroup(doc, u"ЭОМ: лист ЗУ")
    tg.Start()
    try:
        # Step 1. Create/reuse the sheet first.  Calculation fitting depends on its
        # real ViewSheet.Outline, not on hard-coded A1 dimensions.
        t_sheet = DB.Transaction(doc, u"ЭОМ: подготовить лист ЗУ")
        t_sheet.Start()
        try:
            sheet = _find_generated_sheet(doc, DB, group_id, artifact_names)
            if sheet is None:
                titleblock = select_title_block(
                    doc, DB, active_view=getattr(uidoc, "ActiveView", None))
                sheet = DB.ViewSheet.Create(doc, titleblock.Id)
                sheet.Name = sheet_name
                sheet.SheetNumber = _unique_sheet_number(doc, DB, sheet_number_base)
            _set_marker(sheet, DB, group_id, "SHEET")
            doc.Regenerate()
            width, height = _sheet_size(sheet)
            if not _large_landscape(width, height):
                raise Exception(
                    u"Лист ЗУ должен использовать горизонтальную основную надпись A1 или A0.")
            t_sheet.Commit()
        except Exception:
            t_sheet.RollBack()
            raise

        # Step 2. Render the calculation view adaptively.  A complete report is
        # attempted first; if it is taller than one sheet, a compact sheet report
        # is generated automatically instead of rejecting A1/A0.
        trace_mark("DOC_CALC_VIEW_START")
        calc_view, calc_level, calc_w, calc_h, calc_clipped = _create_fitted_calculation_view(
            doc, uidoc, DB, report_text, calc_name, report_tables,
            group_id, sheet)
        trace_mark("DOC_CALC_VIEW_END")
        if calc_level == 1:
            warnings.append(u"Для выбранного формата второстепенные строки листового расчета сокращены; блоки «Дано / Расчет / Итоги» сохранены.")
        elif calc_level >= 2:
            warnings.append(u"Для текущего формата листовой расчет сокращен до основных исходных данных, расчетных этапов и итогового решения.")
        if calc_clipped:
            warnings.append(u"Расчетный блок близок к предельному размеру выбранного формата; рекомендуется A1/A0.")

        t = DB.Transaction(doc, u"ЭОМ: виды и компоновка листа ЗУ")
        t.Start()
        try:
            _remove_viewport_on_sheet(doc, DB, sheet, calc_view, allow_other_sheet=False)

            for role, name in names.items():
                _delete_generated_model_views(doc, DB, name, group_id, sheet.Id, role)

            doc.Regenerate()
            plan = _create_top_view(doc, DB, group_id, names["PLAN"], bbox)
            section_main = _create_section_view(doc, DB, group_id, names["SECTION_1"], bbox, main_dir, u"SECTION_1")
            perp = DB.XYZ(-main_dir.Y, main_dir.X, 0.0)
            section_cross = _create_section_view(doc, DB, group_id, names["SECTION_2"], bbox, perp, u"SECTION_2")
            axo = _create_axo_view(doc, DB, group_id, names["AXON"], bbox)

            layout = _layout_centers(DB, sheet, calc_width=calc_w)
            graphics = [
                ("PLAN", plan),
                ("SECTION_1", section_main),
                ("SECTION_2", section_cross),
                ("AXON", axo),
            ]
            scales = {}
            for idx, pair in enumerate(graphics):
                kind, view = pair
                model_w, model_h = _view_projected_size(kind, bbox, main_dir, DB)
                scale, clipped = _pick_scale(
                    model_w, model_h,
                    layout["graphics_widths"][idx],
                    layout["graphics_heights"][idx])
                scales[kind] = _set_scale(view, scale)
                if clipped:
                    warnings.append(u"{}: геометрия может не полностью помещаться при максимальном масштабе 1:200.".format(view.Name))

            try:
                calc_view.Scale = 1
            except Exception:
                pass

            doc.Regenerate()
            _place_view(doc, DB, sheet, calc_view, layout["calc"])
            for idx, pair in enumerate(graphics):
                _place_view(doc, DB, sheet, pair[1], layout["graphics"][idx])

            t.Commit()
        except Exception:
            t.RollBack()
            raise

        tg.Assimilate()
        trace_mark("DOC_SHEET_DONE", u"{} {}".format(sheet.SheetNumber, sheet.Name))
    except Exception as ex:
        trace_exception("DOC_SHEET_ERROR", ex)
        try:
            tg.RollBack()
        except Exception:
            pass
        raise

    return {
        "sheet": sheet,
        "calculation_view": calc_view,
        "calculation_level": calc_level,
        "plan_view": plan,
        "section_main_view": section_main,
        "section_cross_view": section_cross,
        "axo_view": axo,
        "scales": scales,
        "warnings": warnings,
    }
