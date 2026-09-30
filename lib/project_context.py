# -*- coding: utf-8 -*-
from __future__ import division, print_function

"""Чтение адреса проекта и справочный автоподбор исходных данных.

Без настройки онлайн-доступа модуль работает только с локальной базой. После
явного включения пользователем адрес может нормализоваться через DaData, а
координаты использоваться для SoilGrids и Open-Meteo. Фактический грунт и rho
по адресу достоверно определить нельзя, поэтому такие значения всегда помечены
как предварительное допущение.
"""

import re

from project_profile import read_project_profile


PROJECT_PARAMETER_NAMES = {
    "soil_type": [u"Тип грунта", u"Грунт", u"Преобладающий грунт"],
    "rho": [u"Удельное сопротивление грунта", u"ρ грунта", u"Ро грунта", u"УЭС грунта"],
    "seasonal_factor": [u"Сезонный коэффициент", u"Коэффициент сезонности ЗУ", u"kсез"],
    "frost_depth": [u"Глубина промерзания", u"Нормативная глубина промерзания"],
    "climate_region": [u"Климатический район", u"Климатический подрайон", u"Климатический регион"],
    "climate_station": [u"Климатическая станция", u"Метеостанция"],
}

ADDRESS_PARAMETER_NAMES = [
    u"Адрес проекта", u"Адрес объекта", u"Адрес строительства", u"Адрес",
    u"Project Address", u"ADSK_Адрес объекта", u"ADSK_Адрес проекта"
]


# Небольшая офлайн-база первой версии. Она предназначена для выбора безопасных
# стартовых значений, а не для подмены ИГИ. Более точные данные заказчика,
# записанные в параметры сведений о проекте, всегда имеют приоритет.
LOCATION_PROFILES = [
    {
        "id": "omsk",
        "keywords": [u"омск", u"омская область", u"исиль-куль", u"тара", u"черлак"],
        "name": u"Омская область / Западная Сибирь",
        "climate_region": u"IВ (предварительно; проверить по карте СП 131.13330.2025)",
        "climate_station": u"Омск или ближайшая станция СП 131.13330.2025",
        "soil_type": u"Суглинок (справочное допущение)",
        "rho": 100.0,
        "seasonal_factor": 1.30,
        "frost_depth": 2.00,
    },
    {
        "id": "west_siberia",
        "keywords": [u"новосибирск", u"новосибирская область", u"томск", u"томская область",
                     u"кемерово", u"кузбасс", u"алтайский край", u"барнаул", u"республика алтай"],
        "name": u"Западная Сибирь",
        "climate_region": u"Западно-Сибирский профиль (подрайон уточнить по СП 131.13330.2025)",
        "climate_station": u"Ближайшая станция СП 131.13330.2025",
        "soil_type": u"Суглинок (справочное допущение)",
        "rho": 100.0,
        "seasonal_factor": 1.35,
        "frost_depth": 2.00,
    },
    {
        "id": "tyumen",
        "keywords": [u"тюмень", u"тюменская область", u"ханты-мансий", u"хмао", u"югра",
                     u"ямало-ненец", u"янао", u"сургут", u"нижневартовск"],
        "name": u"Тюменский север / Западная Сибирь",
        "climate_region": u"Север Западной Сибири (подрайон уточнить по СП 131.13330.2025)",
        "climate_station": u"Ближайшая станция СП 131.13330.2025",
        "soil_type": u"Суглинок или водонасыщенная супесь (справочно)",
        "rho": 100.0,
        "seasonal_factor": 1.45,
        "frost_depth": 2.30,
    },
    {
        "id": "east_siberia",
        "keywords": [u"красноярск", u"красноярский край", u"иркутск", u"иркутская область",
                     u"хакасия", u"тыва", u"бурятия", u"забайкаль"],
        "name": u"Восточная Сибирь",
        "climate_region": u"Восточно-Сибирский профиль (подрайон уточнить по СП 131.13330.2025)",
        "climate_station": u"Ближайшая станция СП 131.13330.2025",
        "soil_type": u"Суглинок/супесь (справочное допущение)",
        "rho": 150.0,
        "seasonal_factor": 1.50,
        "frost_depth": 2.30,
    },
    {
        "id": "ural",
        "keywords": [u"екатеринбург", u"свердловская область", u"челябинск", u"челябинская область",
                     u"курган", u"пермский край", u"пермь", u"уфа", u"башкортостан"],
        "name": u"Урал",
        "climate_region": u"Уральский профиль (подрайон уточнить по СП 131.13330.2025)",
        "climate_station": u"Ближайшая станция СП 131.13330.2025",
        "soil_type": u"Суглинок (справочное допущение)",
        "rho": 100.0,
        "seasonal_factor": 1.35,
        "frost_depth": 1.90,
    },
    {
        "id": "moscow_center",
        "keywords": [u"москва", u"московская область", u"подмосков", u"калуга", u"калужская область",
                     u"тверь", u"тверская область", u"тула", u"тульская область", u"рязань",
                     u"рязанская область", u"владимир", u"владимирская область", u"ярославль",
                     u"ярославская область", u"иваново", u"ивановская область", u"смоленск",
                     u"смоленская область", u"кострома", u"костромская область"],
        "name": u"Центральная Россия",
        "climate_region": u"Центральный профиль (подрайон уточнить по СП 131.13330.2025)",
        "climate_station": u"Ближайшая станция СП 131.13330.2025",
        "soil_type": u"Суглинок (справочное допущение)",
        "rho": 100.0,
        "seasonal_factor": 1.30,
        "frost_depth": 1.40,
    },
    {
        "id": "northwest",
        "keywords": [u"санкт-петербург", u"петербург", u"ленинградская область", u"псков",
                     u"новгород", u"карелия", u"вологда", u"архангельск", u"мурманск"],
        "name": u"Северо-Запад России",
        "climate_region": u"Северо-Западный профиль (подрайон уточнить по СП 131.13330.2025)",
        "climate_station": u"Ближайшая станция СП 131.13330.2025",
        "soil_type": u"Влажный суглинок/супесь (справочно)",
        "rho": 80.0,
        "seasonal_factor": 1.30,
        "frost_depth": 1.40,
    },
    {
        "id": "volga",
        "keywords": [u"казань", u"татарстан", u"самара", u"самарская область", u"саратов",
                     u"ульяновск", u"нижний новгород", u"чуваш", u"марий эл", u"мордов"],
        "name": u"Поволжье",
        "climate_region": u"Поволжский профиль (подрайон уточнить по СП 131.13330.2025)",
        "climate_station": u"Ближайшая станция СП 131.13330.2025",
        "soil_type": u"Суглинок (справочное допущение)",
        "rho": 100.0,
        "seasonal_factor": 1.30,
        "frost_depth": 1.60,
    },
    {
        "id": "south",
        "keywords": [u"краснодар", u"ростов", u"ставрополь", u"адыгея", u"калмыкия",
                     u"астрахань", u"волгоград", u"крым", u"севастополь", u"дагестан",
                     u"чечен", u"ингуш", u"кабардино", u"карачаево", u"осетия"],
        "name": u"Юг России / Северный Кавказ",
        "climate_region": u"Южный профиль (подрайон уточнить по СП 131.13330.2025)",
        "climate_station": u"Ближайшая станция СП 131.13330.2025",
        "soil_type": u"Суглинок (справочное допущение)",
        "rho": 100.0,
        "seasonal_factor": 1.20,
        "frost_depth": 0.80,
    },
    {
        "id": "far_east",
        "keywords": [u"хабаровск", u"приморский край", u"владивосток", u"амурская область",
                     u"сахалин", u"камчат", u"магадан", u"чукот", u"якут", u"саха"],
        "name": u"Дальний Восток",
        "climate_region": u"Дальневосточный профиль (подрайон уточнить по СП 131.13330.2025)",
        "climate_station": u"Ближайшая станция СП 131.13330.2025",
        "soil_type": u"Суглинок/супесь (справочное допущение)",
        "rho": 150.0,
        "seasonal_factor": 1.45,
        "frost_depth": 2.00,
    },
]


def _norm(value):
    text = u"" if value is None else u"{}".format(value)
    text = text.lower().replace(u"ё", u"е")
    return re.sub(r"\s+", u" ", text).strip()


def _number(value):
    if value is None:
        return None
    match = re.search(r"[-+]?\d+(?:[\.,]\d+)?", u"{}".format(value))
    if not match:
        return None
    return float(match.group(0).replace(",", "."))


def infer_from_address(address):
    normalized = _norm(address)
    if not normalized:
        return None
    best = None
    best_length = -1
    for profile in LOCATION_PROFILES:
        for keyword in profile["keywords"]:
            key = _norm(keyword)
            if key and key in normalized and len(key) > best_length:
                best = profile
                best_length = len(key)
    return dict(best) if best else None


def _parameter_value(parameter):
    if parameter is None:
        return None
    try:
        value = parameter.AsString()
    except Exception:
        value = None
    if not value:
        try:
            value = parameter.AsValueString()
        except Exception:
            value = None
    if value and u"{}".format(value).strip():
        return u"{}".format(value).strip()
    return None


def _parameter_name(parameter):
    try:
        return u"{}".format(parameter.Definition.Name)
    except Exception:
        return u""


def _parameter_text(element, names):
    if element is None:
        return None
    for name in names:
        try:
            parameter = element.LookupParameter(name)
            value = _parameter_value(parameter)
            if value:
                return value
        except Exception:
            continue
    return None


def _address_name_matches(name):
    normalized = _norm(name)
    if not normalized:
        return False
    exact = [_norm(item) for item in ADDRESS_PARAMETER_NAMES]
    if normalized in exact:
        return True
    return u"адрес" in normalized and (u"проект" in normalized or u"объект" in normalized or u"строитель" in normalized)


def _find_address_on_element(element, source):
    if element is None:
        return None

    for name in ADDRESS_PARAMETER_NAMES:
        try:
            parameter = element.LookupParameter(name)
            value = _parameter_value(parameter)
            if value:
                return {"value": value, "source": source, "parameter_name": _parameter_name(parameter) or name}
        except Exception:
            pass

    try:
        for parameter in element.Parameters:
            name = _parameter_name(parameter)
            if not _address_name_matches(name):
                continue
            value = _parameter_value(parameter)
            if value:
                return {"value": value, "source": source, "parameter_name": name}
    except Exception:
        pass
    return None


def _element_id_text(element):
    try:
        return u"{}".format(element.Id.IntegerValue)
    except Exception:
        try:
            return u"{}".format(element.Id.Value)
        except Exception:
            return u"?"


def _titleblocks(doc, DB, active_only=False):
    if doc is None or DB is None:
        return []
    try:
        if active_only:
            view = getattr(doc, "ActiveView", None)
            if view is None:
                return []
            collector = DB.FilteredElementCollector(doc, view.Id)
        else:
            collector = DB.FilteredElementCollector(doc)
        return list(collector.OfCategory(DB.BuiltInCategory.OST_TitleBlocks).WhereElementIsNotElementType())
    except Exception:
        return []


def _sheets(doc, DB):
    if doc is None or DB is None:
        return []
    try:
        return list(DB.FilteredElementCollector(doc).OfClass(DB.ViewSheet).WhereElementIsNotElementType())
    except Exception:
        return []


def read_project_address_details(doc, DB=None):
    """Ищет адрес во всех распространенных местах хранения в RVT.

    Порядок: встроенный параметр ProjectInformation, остальные параметры
    ProjectInformation, активный лист, основная надпись активного листа, затем
    все основные надписи. Возвращает значение и диагностический источник.
    """
    if doc is None:
        return {"value": u"", "source": u"Документ не открыт", "parameter_name": u""}

    info = getattr(doc, "ProjectInformation", None)
    if info is not None:
        try:
            property_value = getattr(info, "Address", None)
            if property_value and u"{}".format(property_value).strip():
                return {"value": u"{}".format(property_value).strip(),
                        "source": u"Сведения о проекте: свойство Address",
                        "parameter_name": u"PROJECT_ADDRESS"}
        except Exception:
            pass

        if DB is not None:
            try:
                parameter = info.get_Parameter(DB.BuiltInParameter.PROJECT_ADDRESS)
                value = _parameter_value(parameter)
                if value:
                    return {"value": value,
                            "source": u"Сведения о проекте: встроенный параметр",
                            "parameter_name": _parameter_name(parameter) or u"PROJECT_ADDRESS"}
            except Exception:
                pass

        found = _find_address_on_element(info, u"Сведения о проекте")
        if found:
            return found

    active_view = getattr(doc, "ActiveView", None)
    found = _find_address_on_element(active_view, u"Активный лист/вид")
    if found:
        return found

    for sheet in _sheets(doc, DB):
        found = _find_address_on_element(sheet, u"Лист {}, ID {}".format(
            getattr(sheet, "SheetNumber", u"?"), _element_id_text(sheet)))
        if found:
            return found

    seen = set()
    for titleblock in _titleblocks(doc, DB, active_only=True) + _titleblocks(doc, DB, active_only=False):
        element_id = _element_id_text(titleblock)
        if element_id in seen:
            continue
        seen.add(element_id)
        found = _find_address_on_element(titleblock, u"Основная надпись, ID {}".format(element_id))
        if found:
            return found
        try:
            symbol = doc.GetElement(titleblock.GetTypeId())
        except Exception:
            symbol = None
        found = _find_address_on_element(symbol, u"Тип основной надписи, экземпляр ID {}".format(element_id))
        if found:
            return found

    return {"value": u"", "source": u"Параметр адреса не найден", "parameter_name": u""}


def address_debug_candidates(doc, DB=None, limit=12):
    """Список непустых параметров с «адрес» для диагностики нестандартных шаблонов."""
    result = []
    elements = []
    info = getattr(doc, "ProjectInformation", None) if doc is not None else None
    if info is not None:
        elements.append((info, u"Сведения о проекте"))
    active = getattr(doc, "ActiveView", None) if doc is not None else None
    if active is not None:
        elements.append((active, u"Активный лист/вид"))
    for sheet in _sheets(doc, DB):
        elements.append((sheet, u"Лист {}, ID {}".format(
            getattr(sheet, "SheetNumber", u"?"), _element_id_text(sheet))))
    for item in _titleblocks(doc, DB, active_only=False):
        elements.append((item, u"Основная надпись ID {}".format(_element_id_text(item))))

    for element, source in elements:
        try:
            parameters = element.Parameters
        except Exception:
            continue
        try:
            for parameter in parameters:
                name = _parameter_name(parameter)
                if u"адрес" not in _norm(name) and "address" not in _norm(name):
                    continue
                value = _parameter_value(parameter)
                result.append(u"{} | {} = {}".format(source, name, value or u"<пусто>"))
                if len(result) >= limit:
                    return result
        except Exception:
            continue
    return result


def _customer_project_values(info):
    result = {}
    for key, names in PROJECT_PARAMETER_NAMES.items():
        raw = _parameter_text(info, names)
        if raw is None:
            continue
        if key in ("rho", "seasonal_factor", "frost_depth"):
            numeric = _number(raw)
            if numeric is None:
                continue
            # Нулевое/отрицательное значение глубины промерзания в шаблоне Revit
            # означает «не заполнено» и не должно перетирать автоматически
            # определенное значение по климатическим данным/резервному профилю.
            if key == "frost_depth" and numeric <= 0:
                continue
            result[key] = u"{}".format(numeric)
        else:
            result[key] = raw
    return result


def resolve_project_context(doc, DB=None, force_online=False):
    """Возвращает значения для формы с приоритетом данных заказчика.

    Приоритет: параметры сведений о проекте -> онлайн-кэш/источники -> профиль
    по адресу -> нейтральные стартовые значения. Поля source_* нужны для отчета.
    """
    info = getattr(doc, "ProjectInformation", None) if doc is not None else None
    project_profile = read_project_profile(doc, DB)
    address_details = read_project_address_details(doc, DB)
    address = project_profile.get("project_address") or address_details.get("value") or u""
    if project_profile.get("project_address"):
        address_details = {
            "value": address,
            "source": u"Профиль «Данные проекта» EOM Grounding",
            "parameter_name": u"EOMGroundingProjectProfile",
        }
    profile = infer_from_address(address)
    customer_values = _customer_project_values(info)
    values = {
        "project_address": address,
        "project_address_source": address_details.get("source") or u"",
        "project_address_parameter": address_details.get("parameter_name") or u"",
        "climate_region": u"",
        "climate_station": u"",
        "soil_type": u"",
        "rho": "100",
        "seasonal_factor": "1.30",
        "frost_depth": None,
        "frost_depth_auto": None,
        "frost_depth_auto_source": u"",
        "frost_depth_source": u"Не определена",
        "frost_depth_reason": u"",
        "frost_zero_is_climatic": False,
        "frost_depth_project_confirmed": False,
        "frost_depth_project_ignored": u"",
        "location_profile_id": None,
        "location_profile_name": None,
        "location_data_source": u"Ручной ввод / значения по умолчанию",
        "soil_data_quality": "DEFAULT",
        "location_warning": u"",
        "normalized_address": u"",
        "latitude": None,
        "longitude": None,
        "online_updated_utc": u"",
        "online_errors": u"",
        "data_provenance": u"",
        "project_object_type": project_profile.get("project_object_type") or u"Дом",
        "allocated_power_kw": project_profile.get("allocated_power_kw") or u"",
        "system": project_profile.get("system") or u"TN-C-S",
        "incoming_device_type": project_profile.get("incoming_device_type") or u"Автоматический выключатель",
        "incoming_breaker_rating_a": project_profile.get("incoming_breaker_rating_a") or u"",
        "incoming_breaker_curve": project_profile.get("incoming_breaker_curve") or u"C",
        "project_profile_source": u"Extensible Storage RVT",
    }

    if profile:
        values.update({
            "climate_region": profile["climate_region"],
            "climate_station": profile["climate_station"],
            "soil_type": profile["soil_type"],
            "rho": u"{}".format(profile["rho"]),
            "seasonal_factor": u"{:.2f}".format(profile["seasonal_factor"]),
            "frost_depth": u"{:.2f}".format(profile["frost_depth"]),
            "frost_depth_auto": u"{:.2f}".format(profile["frost_depth"]),
            "frost_depth_auto_source": u"Справочный офлайн-профиль региона: {}".format(profile["name"]),
            "frost_depth_source": u"Справочный офлайн-профиль региона: {}".format(profile["name"]),
            "location_profile_id": profile["id"],
            "location_profile_name": profile["name"],
            "location_data_source": u"Справочный офлайн-профиль по адресу проекта ({})".format(
                address_details.get("source") or u"источник не определен"),
            "soil_data_quality": "ADDRESS_REFERENCE",
            "location_warning": (u"Грунт и ρ подобраны только как справочное допущение по региону. "
                                 u"Подтвердить по ИГИ или измерением на объекте."),
        })

    online = None
    online_error = u""
    if address:
        # Если заказчик уже задал основные инженерные данные, сеть не вызывается;
        # свежий локальный кэш при этом все равно может дополнить служебные поля.
        essential = ("rho", "soil_type", "frost_depth", "climate_region")
        allow_network = not all(key in customer_values for key in essential)
        try:
            from online_context import resolve_online_context
            online, online_error = resolve_online_context(
                address, force=bool(force_online), allow_network=allow_network)
        except Exception as ex:
            try:
                from online_context import exception_text
                online_error = exception_text(ex)
            except Exception:
                online_error = u"Ошибка онлайн-контекста"

    if online:
        online_fields = (
            "normalized_address", "latitude", "longitude", "qc_geo", "fias_id", "dadata_method",
            "climate_region", "climate_station", "soil_type", "rho",
            "seasonal_factor", "frost_depth", "frost_depth_auto",
            "frost_depth_auto_source", "frost_depth_source", "location_profile_id",
            "location_profile_name", "location_data_source", "soil_data_quality",
            "location_warning", "online_errors", "online_updated_utc",
            "data_provenance", "soil_clay_pct", "soil_sand_pct", "soil_silt_pct",
            "freezing_index_mt", "frost_depth_reason", "frost_zero_is_climatic",
        )
        for key in online_fields:
            if key in online and online.get(key) not in (None, u""):
                values[key] = online.get(key)
        if online_error:
            existing = values.get("online_errors") or u""
            values["online_errors"] = u"; ".join(item for item in (existing, online_error) if item)
            values["location_data_source"] += u"; использован кэш после ошибки обновления"
        # Совместимость со старым онлайн-кэшем: до v0.9.2 в кэше не было
        # отдельных полей автоматической глубины и ее источника.
        if values.get("frost_depth") not in (None, u"") and values.get("frost_depth_auto") in (None, u""):
            values["frost_depth_auto"] = values.get("frost_depth")
        # Если прочитан старый кэш без новых полей источника, определяем источник
        # именно по содержимому кэша, а не по уже примененному офлайн-профилю.
        if online.get("frost_depth") not in (None, u"") and not online.get("frost_depth_auto_source"):
            station = online.get("climate_station") or values.get("climate_station") or u""
            if u"ERA5" in station:
                compat_source = u"ERA5-Land 1991–2020, предварительный расчет по температурному индексу"
            elif online.get("location_profile_name") or values.get("location_profile_name"):
                compat_source = u"Справочный офлайн-профиль региона: {}".format(
                    online.get("location_profile_name") or values.get("location_profile_name"))
            else:
                compat_source = u"Автоматический контекст проекта"
            values["frost_depth_auto_source"] = compat_source
            values["frost_depth_source"] = compat_source
        elif values.get("frost_depth_auto") not in (None, u"") and not values.get("frost_depth_auto_source"):
            values["frost_depth_auto_source"] = u"Автоматический контекст проекта"
            values["frost_depth_source"] = values.get("frost_depth_source") or values["frost_depth_auto_source"]
        if values.get("frost_depth_auto") not in (None, u""):
            try:
                _fd = float(u"{}".format(values.get("frost_depth_auto")).replace(",", "."))
                _mt = float(values.get("freezing_index_mt") or 0.0)
                if _fd <= 0.0 and _mt <= 1e-9 and not values.get("frost_depth_reason"):
                    values["frost_zero_is_climatic"] = True
                    values["frost_depth_reason"] = (u"Mt = 0: среднемесячные температуры не содержат отрицательных "
                                                     u"значений; dfn по формуле СП 22 равно 0 м")
            except Exception:
                pass
    elif address and not profile:
        values["location_data_source"] = u"Адрес прочитан, но профиль региона не найден"
        values["location_warning"] = (u"Офлайн-профиль не найден. Настройте команду «Онлайн-данные» "
                                      u"или задайте климат и грунт вручную.")
        if online_error:
            values["online_errors"] = online_error
    elif not address:
        values["location_warning"] = (u"В сведениях о проекте не заполнен параметр «Адрес проекта». "
                                      u"Автоподбор не выполнен.")

    # Значения заказчика имеют приоритет над справочниками только если они
    # действительно заполнены. Для глубины промерзания 0/отрицательное значение
    # трактуется как «не задано» и не перекрывает автоматическое значение.
    raw_project_frost = _parameter_text(info, PROJECT_PARAMETER_NAMES.get("frost_depth", []))
    raw_project_frost_num = _number(raw_project_frost) if raw_project_frost is not None else None
    invalid_project_frost = (raw_project_frost_num is not None and raw_project_frost_num <= 0)

    values.update(customer_values)
    customer_fields = list(customer_values.keys())

    if "frost_depth" in customer_values:
        values["frost_depth_project_confirmed"] = True
        values["frost_depth_source"] = u"Параметр сведений о проекте Revit — подтвержденное проектное значение"
    elif invalid_project_frost:
        values["frost_depth_project_ignored"] = u"{}".format(raw_project_frost)
        auto_source = values.get("frost_depth_auto_source") or values.get("frost_depth_source") or u"автоматический источник"
        msg = (u"Параметр проекта «Глубина промерзания» имеет нулевое/неположительное значение и проигнорирован; "
               u"использовано автоматическое значение ({})".format(auto_source))
        current_warning = values.get("location_warning") or u""
        values["location_warning"] = u" ".join(x for x in (current_warning, msg) if x)

    if customer_fields:
        if online:
            values["location_data_source"] = (u"Параметры заказчика/проекта; недостающие поля — "
                                              u"из онлайн-кэша/источников")
        elif profile:
            values["location_data_source"] = (u"Параметры заказчика/проекта; недостающие поля — "
                                              u"справочно по адресу")
        else:
            values["location_data_source"] = u"Параметры заказчика/проекта"
        if "rho" in customer_fields and "soil_type" in customer_fields:
            values["soil_data_quality"] = "PROJECT_DATA"
            values["location_warning"] = (u"Данные грунта прочитаны из сведений о проекте. "
                                          u"Ответственный инженер должен подтвердить их актуальность.")

    values["customer_project_fields"] = u", ".join(customer_fields)
    return values


def add_context_warnings(data, warnings):
    quality = data.get("soil_data_quality")
    message = data.get("location_warning")
    if message and message not in warnings:
        warnings.append(message)
    if quality == "ADDRESS_REFERENCE":
        warnings.append(
            u"Автоматически выбранный по адресу тип грунта не является результатом инженерно-геологических изысканий."
        )
    if quality == "ONLINE_REFERENCE":
        warnings.append(
            u"Тип верхнего слоя получен из глобальной модели SoilGrids; электрическое ρ принято таблично и требует измерения."
        )
    online_errors = data.get("online_errors")
    if online_errors:
        warnings.append(u"Часть онлайн-источников недоступна: {}".format(online_errors))
    return warnings
