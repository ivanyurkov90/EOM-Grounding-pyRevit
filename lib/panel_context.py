# -*- coding: utf-8 -*-
from __future__ import division, print_function

import json
import System

from plugin_version import VERSION as PLUGIN_VERSION, BUILD_ID as PLUGIN_BUILD_ID
from perf_trace import mark as trace_mark, mark_exception as trace_exception

PANEL_SCHEMA_GUID = "8D7AE748-067D-44F6-B2CC-D61655B34D73"
PANEL_SCHEMA_NAME = "EOMGroundingPanelLinkV1"
PANEL_SCHEMA_FIELD = "DataJson"


def _text(value):
    return u"" if value is None else u"{}".format(value)


def _panel_navigation_trace_detail(stage, data=None, element=None):
    """Compact high-level context for a final panel-navigation failure."""
    data = data or {}
    panel_name = (data.get("gzsh_panel_name") or
                  data.get("gzsh_panel_display_name") or
                  data.get("gzsh_panel_mark") or u"")
    element_id = data.get("gzsh_panel_element_id")
    unique_id = data.get("gzsh_panel_unique_id") or u""
    if element is not None:
        try:
            element_id = _id_value(getattr(element, "Id", None))
        except Exception:
            pass
        try:
            unique_id = _text(getattr(element, "UniqueId", unique_id))
        except Exception:
            pass
    return u"stage={}; panel={}; element_id={}; unique_id={}".format(
        _text(stage), _text(panel_name), _text(element_id), _text(unique_id))


def _trace_panel_navigation_failure(stage, data=None, element=None, ex=None):
    detail = _panel_navigation_trace_detail(stage, data, element)
    if ex is None:
        trace_mark("PANEL_NAV_FAIL", detail)
    else:
        trace_exception("PANEL_NAV_FAIL", ex, detail)


def _id_value(element_id):
    if element_id is None:
        return None
    try:
        return int(element_id.Value)
    except Exception:
        try:
            return int(element_id.IntegerValue)
        except Exception:
            return None


def _param_text(element, names):
    for name in names:
        try:
            p = element.LookupParameter(name)
            if p:
                value = p.AsString()
                if value:
                    return value
                value = p.AsValueString()
                if value:
                    return value
        except Exception:
            pass
    return u""


def _builtin_param_text(element, DB, names):
    for name in names:
        try:
            bip = getattr(DB.BuiltInParameter, name)
            p = element.get_Parameter(bip)
            if p:
                value = p.AsString()
                if value:
                    return value
                value = p.AsValueString()
                if value:
                    return value
        except Exception:
            pass
    return u""


def _is_electrical_equipment(element, DB):
    try:
        category = element.Category
        if category is None:
            return False
        cat_id = _id_value(category.Id)
        target = _id_value(DB.ElementId(DB.BuiltInCategory.OST_ElectricalEquipment))
        return cat_id == target
    except Exception:
        return False


def _raw_panel_name(element, DB):
    """Return Revit's actual Panel Name, without family/type fallbacks."""
    value = _builtin_param_text(element, DB, ["RBS_ELEC_PANEL_NAME"])
    if value:
        return value.strip()
    value = _param_text(element, [u"Имя панели", "Panel Name"])
    return value.strip() if value else u""


def _has_panel_signature(element, DB):
    """Legacy helper: panel-like electrical parameters exposed by Revit."""
    names = (
        "RBS_ELEC_PANEL_NUMPHASES_PARAM",
        "RBS_ELEC_PANEL_NUMWIRES_PARAM",
        "RBS_ELEC_PANEL_MCB_RATING_PARAM",
        "RBS_ELEC_PANEL_GROUND_BUS_PARAM",
        "RBS_ELEC_PANEL_MAINSTYPE_PARAM",
    )
    found = 0
    for name in names:
        try:
            bip = getattr(DB.BuiltInParameter, name)
            if element.get_Parameter(bip) is not None:
                found += 1
        except Exception:
            pass
    return found >= 2


def _panel_name_hint(element, DB):
    """Positive fallback only; never makes ordinary equipment a panel by itself."""
    parts = [_raw_panel_name(element, DB)]
    try:
        symbol = getattr(element, "Symbol", None)
        if symbol is not None:
            parts.append(_text(getattr(symbol, "FamilyName", u"")))
            parts.append(_text(getattr(symbol, "Name", u"")))
    except Exception:
        pass
    text = u" ".join(parts).lower()
    keywords = (u"щит", u"панел", u"panel", u"panelboard", u"грщ", u"вру", u"щр", u"щс", u"щэ", u"щк")
    return any(k in text for k in keywords)


def _collection_has_items(collection):
    if collection is None:
        return False
    try:
        return collection.Count > 0
    except Exception:
        try:
            return len(collection) > 0
        except Exception:
            try:
                for _ in collection:
                    return True
            except Exception:
                pass
    return False


def _assigned_system_panel(element):
    """True when Revit MEPModel reports systems assigned to this panel."""
    try:
        mep = getattr(element, "MEPModel", None)
        if mep is None:
            return False
        method = getattr(mep, "GetAssignedElectricalSystems", None)
        if method is None:
            return False
        return _collection_has_items(method())
    except Exception:
        return False


def _panel_reference_ids(doc, DB):
    """Panel ids proven by native Revit electrical relationships.

    Sources:
    1) BaseEquipment of existing ElectricalSystem circuits;
    2) panel referenced by existing PanelScheduleView.
    These are much closer to the native circuit panel selector than filtering
    the broad Electrical Equipment category.
    """
    result = set()

    # Panels actually used as base equipment of electrical circuits.
    try:
        electrical_system_type = getattr(getattr(DB, "Electrical", None), "ElectricalSystem", None)
        if electrical_system_type is not None:
            for system in DB.FilteredElementCollector(doc).OfClass(electrical_system_type):
                try:
                    base = getattr(system, "BaseEquipment", None)
                    if base is not None:
                        eid = _id_value(base.Id)
                        if eid is not None:
                            result.add(eid)
                except Exception:
                    pass
    except Exception:
        pass

    # Panels with a real panel schedule view.
    try:
        psv_type = getattr(getattr(DB, "Electrical", None), "PanelScheduleView", None)
        if psv_type is not None:
            for view in DB.FilteredElementCollector(doc).OfClass(psv_type):
                try:
                    eid = _id_value(view.GetPanel())
                    if eid is not None:
                        result.add(eid)
                except Exception:
                    pass
    except Exception:
        pass

    return result


def _is_revit_panel(element, DB, doc=None, proven_ids=None):
    """Strict practical filter for electrical panels in Revit 2023+.

    Electrical Equipment is intentionally NOT sufficient: loads and custom
    equipment can expose many of the same built-in parameters. Native Revit
    evidence (BaseEquipment or PanelScheduleView) is authoritative. A narrow
    naming fallback keeps empty custom distribution boards selectable.
    """
    if not _is_electrical_equipment(element, DB):
        return False

    eid = _id_value(element.Id)
    if proven_ids is None:
        try:
            proven_ids = _panel_reference_ids(doc or element.Document, DB)
        except Exception:
            proven_ids = set()
    if eid is not None and eid in proven_ids:
        return True

    # Revit can report assigned systems directly on the MEP panel model.
    if _assigned_system_panel(element):
        return True

    # Fallback for a newly placed / empty custom board. Require all three:
    # actual Panel Name, panel parameter signature, and positive board naming.
    if not _raw_panel_name(element, DB):
        return False
    if not _has_panel_signature(element, DB):
        return False
    if not _panel_name_hint(element, DB):
        return False
    return True

def panel_info(doc, DB, element):
    if element is None:
        return None
    if not _is_revit_panel(element, DB, doc=doc):
        raise ValueError(u"Элемент не является электрической панелью Revit.")

    element_id = _id_value(element.Id)
    unique_id = _text(getattr(element, "UniqueId", u""))
    mark = _builtin_param_text(element, DB, ["ALL_MODEL_MARK"]) or _param_text(
        element, [u"Марка", "Mark"])

    # For the selector and the link use Revit's real Panel Name only.
    # Do not substitute family/type: that previously made non-panel equipment
    # look like a selectable panel.
    panel_name = _raw_panel_name(element, DB)

    family_name = u""
    type_name = u""
    try:
        symbol = getattr(element, "Symbol", None)
        if symbol is not None:
            family_name = _text(getattr(symbol, "FamilyName", u""))
            type_name = _text(getattr(symbol, "Name", u""))
    except Exception:
        pass

    level_name = u""
    try:
        level_id = getattr(element, "LevelId", None)
        if level_id is not None:
            level = doc.GetElement(level_id)
            if level is not None:
                level_name = _text(level.Name)
    except Exception:
        pass
    if not level_name:
        level_name = _builtin_param_text(element, DB, ["FAMILY_LEVEL_PARAM", "INSTANCE_REFERENCE_LEVEL_PARAM"])

    room_name = _builtin_param_text(element, DB, ["ELEM_ROOM_NAME", "ELEM_ROOM_NUMBER"])
    if not room_name:
        room_name = _param_text(element, [u"Помещение", "Room"])

    designation = panel_name or mark or (u"ID {}".format(element_id) if element_id is not None else u"Панель")
    display_name = designation

    return {
        "gzsh_panel_unique_id": unique_id,
        "gzsh_panel_element_id": element_id,
        "gzsh_panel_name": panel_name,
        "gzsh_panel_mark": mark,
        "gzsh_panel_display_name": display_name,
        "gzsh_panel_level": level_name,
        "gzsh_panel_room": room_name,
        "gzsh_panel_family": family_name,
        "gzsh_panel_type": type_name,
    }


def format_panel_choice(data):
    """Dropdown text: only the panel name, as requested by the user."""
    data = data or {}
    return (data.get("gzsh_panel_name") or
            data.get("gzsh_panel_display_name") or
            data.get("gzsh_panel_mark") or
            u"Панель")


def list_gzsh_panels(doc, DB):
    """Return native/credible electrical panels for GZSH linking.

    Primary candidates are the same elements Revit already uses as circuit
    base equipment, plus panels referenced by panel schedules. A conservative
    fallback admits empty custom boards only when they look unmistakably like
    a distribution board.
    """
    result = []
    seen = set()
    proven_ids = _panel_reference_ids(doc, DB)
    try:
        collector = (DB.FilteredElementCollector(doc)
                     .OfCategory(DB.BuiltInCategory.OST_ElectricalEquipment)
                     .WhereElementIsNotElementType())
        for element in collector:
            try:
                if not _is_revit_panel(element, DB, doc=doc, proven_ids=proven_ids):
                    continue
                info = panel_info(doc, DB, element)
                uid = info.get("gzsh_panel_unique_id") or u"ID:{}".format(info.get("gzsh_panel_element_id"))
                if uid in seen:
                    continue
                seen.add(uid)
                info["gzsh_panel_choice_text"] = format_panel_choice(info)
                result.append(info)
            except Exception:
                continue
    except Exception:
        return []
    result.sort(key=lambda x: (
        _text(x.get("gzsh_panel_name")).lower(),
        x.get("gzsh_panel_element_id") or 0))
    return result

def resolve_gzsh_panel(doc, DB, data):
    if not data:
        return None
    unique_id = data.get("gzsh_panel_unique_id")
    if unique_id:
        try:
            element = doc.GetElement(unique_id)
            if element is not None and _is_revit_panel(element, DB, doc=doc):
                return element
        except Exception:
            pass
    element_id = data.get("gzsh_panel_element_id")
    if element_id is not None:
        try:
            element = doc.GetElement(DB.ElementId(int(element_id)))
            if element is not None and _is_revit_panel(element, DB, doc=doc):
                return element
        except Exception:
            pass
    return None


def refresh_panel_info(doc, DB, data):
    element = resolve_gzsh_panel(doc, DB, data)
    if element is None:
        return None
    return panel_info(doc, DB, element)


# Session-only cache: document + panel -> ViewId.
# It is intentionally not stored in the RVT.  AppDomain storage keeps the
# cache alive even when pyRevit executes commands in different script engines;
# the module dict is a safe fallback for test doubles/hosts without AppDomain.
_PANEL_VIEW_CACHE = {}
_PANEL_VIEW_CACHE_SLOT = u"EOMGrounding.PanelViewCache.v1"


def _read_panel_view_cache():
    try:
        domain = System.AppDomain.CurrentDomain
        raw = domain.GetData(_PANEL_VIEW_CACHE_SLOT)
        if raw:
            parsed = json.loads(_text(raw))
            if isinstance(parsed, dict):
                _PANEL_VIEW_CACHE.clear()
                _PANEL_VIEW_CACHE.update(parsed)
    except Exception:
        pass
    return _PANEL_VIEW_CACHE


def _write_panel_view_cache(cache):
    snapshot = dict(cache or {})
    _PANEL_VIEW_CACHE.clear()
    _PANEL_VIEW_CACHE.update(snapshot)
    try:
        System.AppDomain.CurrentDomain.SetData(
            _PANEL_VIEW_CACHE_SLOT, json.dumps(_PANEL_VIEW_CACHE, ensure_ascii=False))
    except Exception:
        pass


def _document_cache_key(doc):
    """Return a best-effort identity for the current Revit Document instance."""
    try:
        runtime_id = int(doc.GetHashCode())
    except Exception:
        runtime_id = id(doc)
    try:
        path = _text(getattr(doc, "PathName", u""))
    except Exception:
        path = u""
    try:
        title = _text(getattr(doc, "Title", u""))
    except Exception:
        title = u""
    return u"{}|{}|{}".format(runtime_id, path, title)


def _panel_cache_key(doc, element):
    try:
        uid = _text(getattr(element, "UniqueId", u""))
    except Exception:
        uid = u""
    if not uid:
        uid = u"ID:{}".format(_id_value(getattr(element, "Id", None)))
    return u"{}||{}".format(_document_cache_key(doc), uid)


def _is_perspective_3d(view, DB):
    """True only for perspective View3D; safe for non-3D views and test doubles."""
    try:
        view3d_type = getattr(DB, "View3D", None)
        if view3d_type is not None and isinstance(view, view3d_type):
            return bool(getattr(view, "IsPerspective", False))
    except Exception:
        pass
    # Test doubles and some API wrappers may not support isinstance reliably.
    try:
        return bool(getattr(view, "IsPerspective", False))
    except Exception:
        return False


def _view_can_show_element(view, element, DB=None):
    try:
        if view is None or getattr(view, "IsTemplate", False):
            return False
        if DB is not None and _is_perspective_3d(view, DB):
            return False
        bbox = element.get_BoundingBox(view)
        return bbox is not None
    except Exception:
        return False


def _cached_panel_view(doc, DB, element):
    """Return a previously resolved view only if it is still valid now."""
    key = _panel_cache_key(doc, element)
    cache = _read_panel_view_cache()
    view_id_value = cache.get(key)
    if view_id_value is None:
        return None
    try:
        view = doc.GetElement(DB.ElementId(int(view_id_value)))
    except Exception:
        view = None
    if view is None or not _view_can_show_element(view, element, DB):
        cache.pop(key, None)
        _write_panel_view_cache(cache)
        return None
    return view


def _cache_panel_view(doc, element, view):
    if view is None:
        return
    value = _id_value(getattr(view, "Id", None))
    if value is None:
        return
    cache = _read_panel_view_cache()
    cache[_panel_cache_key(doc, element)] = value
    _write_panel_view_cache(cache)


def clear_panel_view_cache(doc=None):
    """Clear session navigation cache; useful for diagnostics/document changes."""
    cache = _read_panel_view_cache()
    if doc is None:
        cache.clear()
        _write_panel_view_cache(cache)
        return
    prefix = _document_cache_key(doc) + u"||"
    for key in list(cache.keys()):
        try:
            if _text(key).startswith(prefix):
                cache.pop(key, None)
        except Exception:
            continue
    _write_panel_view_cache(cache)


def _find_panel_view(doc, DB, element):
    """Find a safe graphical view for the selected panel.

    Search order after the caller has rejected the active view:
    1) previously validated session-cache view;
    2) floor/engineering plan on the panel level;
    3) orthographic non-template 3D view.

    Perspective cameras are deliberately never selected for automatic panel
    navigation.  Native UIView.ZoomToFit is reliable only after a suitable
    model view has been activated.
    """
    cached = _cached_panel_view(doc, DB, element)
    if cached is not None:
        return cached

    level_id = getattr(element, "LevelId", None)

    # Prefer a floor / engineering plan on the same level.
    try:
        for view in DB.FilteredElementCollector(doc).OfClass(DB.ViewPlan):
            try:
                if view.IsTemplate:
                    continue
                if level_id is not None:
                    gen_level = getattr(view, "GenLevel", None)
                    if gen_level is None or _id_value(gen_level.Id) != _id_value(level_id):
                        continue
                if _view_can_show_element(view, element, DB):
                    _cache_panel_view(doc, element, view)
                    return view
            except Exception:
                continue
    except Exception:
        pass

    # Then an orthographic non-template 3D view. Never auto-select perspective.
    try:
        for view in DB.FilteredElementCollector(doc).OfClass(DB.View3D):
            try:
                if view.IsTemplate or getattr(view, "IsPerspective", False):
                    continue
                if _view_can_show_element(view, element, DB):
                    _cache_panel_view(doc, element, view)
                    return view
            except Exception:
                continue
    except Exception:
        pass
    return None


def _ui_view_for(uidoc, view):
    """Return the open UIView corresponding to a Revit DB.View."""
    if view is None:
        return None
    try:
        target_id = _id_value(view.Id)
        for ui_view in uidoc.GetOpenUIViews():
            try:
                if _id_value(ui_view.ViewId) == target_id:
                    return ui_view
            except Exception:
                continue
    except Exception:
        pass
    return None


def _zoom_to_panel(uidoc, DB, element, view):
    """Use Revit's native *Zoom to Fit* for stable navigation."""
    try:
        uidoc.RefreshActiveView()
    except Exception:
        pass
    ui_view = _ui_view_for(uidoc, view)
    if ui_view is None:
        return False
    try:
        ui_view.ZoomToFit()
        return True
    except Exception:
        return False


def show_gzsh_panel(uidoc, doc, DB, data):
    """Select the linked panel, switch only to a safe view, then ZoomToFit."""
    element = resolve_gzsh_panel(doc, DB, data)
    if element is None:
        _trace_panel_navigation_failure("resolve_panel", data)
        return False

    try:
        ids = System.Collections.Generic.List[DB.ElementId]()
        ids.Add(element.Id)
        uidoc.Selection.SetElementIds(ids)
    except Exception:
        pass

    try:
        active = uidoc.ActiveView
    except Exception:
        active = None

    # Keep the user's current view when it is already a safe model view.
    if _view_can_show_element(active, element, DB):
        target = active
        _cache_panel_view(doc, element, target)
    else:
        target = _find_panel_view(doc, DB, element)

    if target is None:
        _trace_panel_navigation_failure("resolve_view", data, element)
        return True

    if active is None or _id_value(target.Id) != _id_value(active.Id):
        try:
            uidoc.ActiveView = target
            uidoc.RefreshActiveView()
        except Exception as ex:
            _trace_panel_navigation_failure("activate_view", data, element, ex)
            return True

    if not _zoom_to_panel(uidoc, DB, element, target):
        _trace_panel_navigation_failure("zoom_to_fit", data, element)
    try:
        uidoc.RefreshActiveView()
    except Exception:
        pass
    return True


def _get_panel_schema(DB):
    guid = System.Guid(PANEL_SCHEMA_GUID)
    schema = DB.ExtensibleStorage.Schema.Lookup(guid)
    if schema is not None:
        return schema
    builder = DB.ExtensibleStorage.SchemaBuilder(guid)
    builder.SetSchemaName(PANEL_SCHEMA_NAME)
    builder.SetDocumentation("EOM Grounding: GZSH panel link to grounding calculation")
    builder.SetReadAccessLevel(DB.ExtensibleStorage.AccessLevel.Public)
    builder.SetWriteAccessLevel(DB.ExtensibleStorage.AccessLevel.Public)
    builder.AddSimpleField(PANEL_SCHEMA_FIELD, System.String)
    return builder.Finish()


def _set_existing_parameter(element, name, value):
    try:
        p = element.LookupParameter(name)
        if p is None or p.IsReadOnly:
            return False
        st_name = _text(p.StorageType)
        if "Integer" in st_name:
            if isinstance(value, bool):
                p.Set(1 if value else 0)
            else:
                try:
                    p.Set(int(value))
                except Exception:
                    p.Set(1)
        else:
            p.Set(_text(value))
        return True
    except Exception:
        return False


def link_panel_to_grounding(doc, DB, data, group_id=None):
    """Stores reverse link on the selected panel.

    Extensible Storage is authoritative. If optional project parameters
    'ЭОМ_ГЗШ' / 'ЭОМ_РасчетЗУ_ID' already exist, they are filled as well.
    The function intentionally does not create/bind project parameters.
    """
    element = resolve_gzsh_panel(doc, DB, data)
    if element is None:
        return False
    transaction = DB.Transaction(doc, u"ЭОМ: связать ГЗШ с расчетом ЗУ")
    transaction.Start()
    try:
        schema = _get_panel_schema(DB)
        entity = DB.ExtensibleStorage.Entity(schema)
        payload = {
            "is_gzsh": True,
            "grounding_group_id": group_id or u"",
            "purpose": _text(data.get("purpose")),
            "panel_unique_id": _text(getattr(element, "UniqueId", u"")),
            "version": PLUGIN_VERSION,
            "build_id": PLUGIN_BUILD_ID,
        }
        entity.Set[System.String](schema.GetField(PANEL_SCHEMA_FIELD), json.dumps(payload, ensure_ascii=False))
        element.SetEntity(entity)
        _set_existing_parameter(element, u"ЭОМ_ГЗШ", True)
        if group_id:
            _set_existing_parameter(element, u"ЭОМ_РасчетЗУ_ID", group_id)
        transaction.Commit()
        return True
    except Exception:
        transaction.RollBack()
        return False
