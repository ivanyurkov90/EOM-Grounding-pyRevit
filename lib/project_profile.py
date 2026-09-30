# -*- coding: utf-8 -*-
from __future__ import print_function

import json


SCHEMA_GUID = "28f13246-a9b2-4e84-933b-4e5e11ca8d61"
SCHEMA_NAME = "EOMGroundingProjectProfile"
FIELD_NAME = "ProfileJson"

OBJECT_TYPES = (u"Дом", u"Квартира")
GROUNDING_SYSTEMS = (u"TN-C-S", u"TN-S", u"TT")
INCOMING_DEVICE_TYPES = (
    u"Автоматический выключатель",
    u"Ограничитель мощности",
    u"Предохранитель",
    u"Другое",
)

PROFILE_KEYS = (
    "project_object_type",
    "project_address",
    "allocated_power_kw",
    "system",
    "incoming_device_type",
    "incoming_breaker_rating_a",
    "incoming_breaker_curve",
)


def _text(value):
    return u"" if value is None else u"{}".format(value).strip()


def empty_profile():
    return {
        "project_object_type": OBJECT_TYPES[0],
        "project_address": u"",
        "allocated_power_kw": u"",
        "system": GROUNDING_SYSTEMS[0],
        "incoming_device_type": INCOMING_DEVICE_TYPES[0],
        "incoming_breaker_rating_a": u"",
        "incoming_breaker_curve": u"C",
    }


def normalize_profile(data):
    source = data or {}
    result = empty_profile()
    for key in PROFILE_KEYS:
        if key in source and source.get(key) is not None:
            result[key] = _text(source.get(key))
    if result["project_object_type"] not in OBJECT_TYPES:
        result["project_object_type"] = OBJECT_TYPES[0]
    if result["system"] not in GROUNDING_SYSTEMS:
        result["system"] = GROUNDING_SYSTEMS[0]
    if result["incoming_device_type"] not in INCOMING_DEVICE_TYPES:
        result["incoming_device_type"] = INCOMING_DEVICE_TYPES[-1]
    if not result["incoming_breaker_curve"]:
        result["incoming_breaker_curve"] = u"C"
    return result


def profile_to_json(data):
    return json.dumps(normalize_profile(data), ensure_ascii=False, sort_keys=True)


def profile_from_json(payload):
    if not payload:
        return empty_profile()
    try:
        parsed = json.loads(payload)
    except Exception:
        return empty_profile()
    return normalize_profile(parsed if isinstance(parsed, dict) else {})


def _schema(DB, create=False):
    from System import Guid, String

    schema = DB.ExtensibleStorage.Schema.Lookup(Guid(SCHEMA_GUID))
    if schema is not None or not create:
        return schema
    builder = DB.ExtensibleStorage.SchemaBuilder(Guid(SCHEMA_GUID))
    builder.SetSchemaName(SCHEMA_NAME)
    builder.SetReadAccessLevel(DB.ExtensibleStorage.AccessLevel.Public)
    builder.SetWriteAccessLevel(DB.ExtensibleStorage.AccessLevel.Public)
    builder.AddSimpleField(FIELD_NAME, String)
    return builder.Finish()


def read_project_profile(doc, DB=None):
    if doc is None:
        return empty_profile()
    # Lightweight hook used only by offline tests and non-Revit callers.
    if DB is None:
        return normalize_profile(getattr(doc, "_eom_grounding_project_profile", {}))
    try:
        from System import String

        schema = _schema(DB, create=False)
        info = getattr(doc, "ProjectInformation", None)
        if schema is None or info is None:
            return empty_profile()
        entity = info.GetEntity(schema)
        if entity is None or not entity.IsValid():
            return empty_profile()
        field = schema.GetField(FIELD_NAME)
        return profile_from_json(entity.Get[String](field))
    except Exception:
        return empty_profile()


def _write_project_address(info, DB, address):
    if info is None or not address:
        return
    try:
        parameter = info.get_Parameter(DB.BuiltInParameter.PROJECT_ADDRESS)
        if parameter is not None and not parameter.IsReadOnly:
            parameter.Set(address)
            return
    except Exception:
        pass
    try:
        info.Address = address
    except Exception:
        pass


def write_project_profile(doc, DB, data):
    if doc is None or DB is None:
        raise ValueError(u"Нет открытого документа Revit.")
    from System import String

    profile = normalize_profile(data)
    info = getattr(doc, "ProjectInformation", None)
    if info is None:
        raise ValueError(u"В проекте отсутствуют сведения о проекте.")

    transaction = DB.Transaction(doc, u"ЭОМ: данные проекта")
    transaction.Start()
    try:
        schema = _schema(DB, create=True)
        entity = DB.ExtensibleStorage.Entity(schema)
        entity.Set[String](schema.GetField(FIELD_NAME), String(profile_to_json(profile)))
        info.SetEntity(entity)
        _write_project_address(info, DB, profile.get("project_address"))
        transaction.Commit()
    except Exception:
        transaction.RollBack()
        raise
    return profile
