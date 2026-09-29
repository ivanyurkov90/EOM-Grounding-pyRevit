# -*- coding: utf-8 -*-
from __future__ import division, print_function

"""Онлайн-источники для адреса, климата и предварительного типа грунта.

Ключи DaData хранятся только в профиле текущего пользователя Windows и
шифруются DPAPI (CurrentUser). В исходный код и RVT они не записываются.
"""

import datetime
import hashlib
import io
import json
import math
import os

from plugin_version import VERSION as PLUGIN_VERSION


APP_DIR_NAME = "EOM_Grounding"
SETTINGS_NAME = "online_settings.json"
CACHE_NAME = "online_context_cache.json"

DEFAULT_SETTINGS = {
    "online_enabled": False,
    "auto_update": True,
    "use_soilgrids": True,
    "use_openmeteo": True,
    "cache_days": 30,
}


def _app_dir():
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    return os.path.join(base, APP_DIR_NAME)


def settings_path():
    return os.path.join(_app_dir(), SETTINGS_NAME)


def cache_path():
    return os.path.join(_app_dir(), CACHE_NAME)


def _ensure_dir():
    path = _app_dir()
    if not os.path.isdir(path):
        os.makedirs(path)
    return path


def _read_json(path, fallback):
    try:
        with io.open(path, "r", encoding="utf-8") as stream:
            value = json.load(stream)
        return value if isinstance(value, dict) else fallback
    except Exception:
        return fallback


def _write_json(path, value):
    _ensure_dir()
    temp_path = path + ".tmp"
    with io.open(temp_path, "w", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, sort_keys=True)
    if os.path.exists(path):
        os.remove(path)
    os.rename(temp_path, path)


def exception_text(ex):
    """Стабильный текст Python/.NET-исключения для IronPython.

    В pyRevit форматирование некоторых .NET-исключений через ``str`` или
    ``format`` возвращает ``{}``, поэтому сначала читается свойство Message.
    """
    parts = []
    current = ex
    depth = 0
    while current is not None and depth < 4:
        message = None
        try:
            message = current.Message
        except Exception:
            pass
        if not message:
            try:
                message = str(current)
            except Exception:
                pass
        if message and message not in ("{}", u"{}"):
            text = u"{}".format(message).strip()
            if text and text not in parts:
                parts.append(text)
        try:
            current = current.InnerException
        except Exception:
            current = None
        depth += 1
    if parts:
        return u" → ".join(parts)
    try:
        return u"{}".format(type(ex).__name__)
    except Exception:
        return u"Неизвестная ошибка"


def _protect_text(value):
    try:
        import clr
        clr.AddReference("System.Security")
        from System import Convert
        from System.Text import Encoding
        from System.Security.Cryptography import ProtectedData, DataProtectionScope
        raw = Encoding.UTF8.GetBytes(value or u"")
        protected = ProtectedData.Protect(raw, None, DataProtectionScope.CurrentUser)
        return u"{}".format(Convert.ToBase64String(protected))
    except Exception as ex:
        raise RuntimeError(u"Windows DPAPI недоступен: {}".format(exception_text(ex)))


def _unprotect_text(value):
    if not value:
        return u""
    try:
        import clr
        clr.AddReference("System.Security")
        from System import Convert
        from System.Text import Encoding
        from System.Security.Cryptography import ProtectedData, DataProtectionScope
        protected = Convert.FromBase64String(value)
        raw = ProtectedData.Unprotect(protected, None, DataProtectionScope.CurrentUser)
        return u"{}".format(Encoding.UTF8.GetString(raw))
    except Exception as ex:
        raise RuntimeError(u"Не удалось расшифровать ключи текущего пользователя: {}".format(
            exception_text(ex)))


def _strip_label(value, labels):
    text = u"{}".format(value or u"").strip()
    lowered = text.lower()
    for label in labels:
        if lowered.startswith(label) and len(text) > len(label) + 12:
            return text[len(label):].strip()
    return text


def load_settings():
    raw = _read_json(settings_path(), {})
    result = dict(DEFAULT_SETTINGS)
    for key in DEFAULT_SETTINGS:
        if key in raw:
            result[key] = raw[key]
    result["dadata_token"] = u""
    result["dadata_secret"] = u""
    result["credentials_error"] = u""
    try:
        result["dadata_token"] = _unprotect_text(raw.get("dadata_token_dpapi"))
        result["dadata_secret"] = _unprotect_text(raw.get("dadata_secret_dpapi"))
    except Exception as ex:
        result["credentials_error"] = exception_text(ex)
    return result


def save_settings(token, secret, online_enabled=True, auto_update=True,
                  use_soilgrids=True, use_openmeteo=True, cache_days=30):
    token = _strip_label(token, (u"api-", u"token-", u"токен-"))
    secret = _strip_label(secret, (u"ключ-", u"secret-", u"секрет-"))
    if online_enabled and not token:
        raise ValueError(u"Для DaData необходим API-ключ. Секретный ключ нужен только для резервного метода стандартизации.")
    try:
        days = max(1, min(365, int(cache_days)))
    except Exception:
        days = 30
    payload = {
        "online_enabled": bool(online_enabled),
        "auto_update": bool(auto_update),
        "use_soilgrids": bool(use_soilgrids),
        "use_openmeteo": bool(use_openmeteo),
        "cache_days": days,
        "dadata_token_dpapi": _protect_text(token) if token else u"",
        "dadata_secret_dpapi": _protect_text(secret) if secret else u"",
    }
    _write_json(settings_path(), payload)
    return load_settings()


def credentials_configured(settings=None):
    settings = settings or load_settings()
    return bool(settings.get("online_enabled") and settings.get("dadata_token") and
                not settings.get("credentials_error"))


def _request_json(url, method="GET", payload=None, headers=None, timeout_ms=25000):
    """HTTP JSON без внешних Python-пакетов; работает в IronPython/pyRevit."""
    try:
        import clr
        clr.AddReference("System")
        from System.Net import WebRequest, ServicePointManager, SecurityProtocolType
        from System.Text import Encoding
        from System.IO import StreamReader
    except Exception as ex:
        raise RuntimeError(u".NET HTTP-клиент недоступен: {}".format(exception_text(ex)))

    try:
        ServicePointManager.SecurityProtocol = ServicePointManager.SecurityProtocol | SecurityProtocolType.Tls12
    except Exception:
        pass

    request = WebRequest.Create(url)
    request.Method = method
    request.Timeout = timeout_ms
    try:
        request.ReadWriteTimeout = timeout_ms
    except Exception:
        pass
    request.ContentType = "application/json; charset=utf-8"
    request.Accept = "application/json"
    try:
        request.UserAgent = "EOM-Grounding-pyRevit/{}".format(PLUGIN_VERSION)
    except Exception:
        pass
    for key, value in (headers or {}).items():
        request.Headers[key] = value

    if payload is not None:
        body = json.dumps(payload, ensure_ascii=False)
        raw = Encoding.UTF8.GetBytes(body)
        request.ContentLength = raw.Length
        stream = request.GetRequestStream()
        try:
            stream.Write(raw, 0, raw.Length)
        finally:
            stream.Close()

    response = None
    reader = None
    try:
        response = request.GetResponse()
        reader = StreamReader(response.GetResponseStream(), Encoding.UTF8)
        text = reader.ReadToEnd()
        return json.loads(u"{}".format(text))
    except Exception as ex:
        error_response = None
        try:
            error_response = ex.Response
        except Exception:
            pass
        status = u""
        body_text = u""
        if error_response is not None:
            try:
                status = u"HTTP {} {}".format(
                    int(error_response.StatusCode), error_response.StatusDescription)
            except Exception:
                status = u"HTTP-ошибка"
            error_reader = None
            try:
                error_reader = StreamReader(error_response.GetResponseStream(), Encoding.UTF8)
                body_text = u"{}".format(error_reader.ReadToEnd()).strip()
            except Exception:
                pass
            finally:
                if error_reader is not None:
                    error_reader.Close()
                try:
                    error_response.Close()
                except Exception:
                    pass
        details = exception_text(ex)
        pieces = [item for item in (status, details, body_text[:800]) if item]
        raise RuntimeError(u"HTTP-запрос не выполнен: {}".format(u" | ".join(pieces)))
    finally:
        if reader is not None:
            reader.Close()
        if response is not None:
            response.Close()


def _geocode_result(item, address, suggestion=False):
    data = (item.get("data") or {}) if suggestion else item
    lat = data.get("geo_lat")
    lon = data.get("geo_lon")
    if lat in (None, "") or lon in (None, ""):
        return None
    if suggestion:
        normalized = item.get("unrestricted_value") or item.get("value")
    else:
        normalized = item.get("result") or item.get("source")
    return {
        "normalized_address": normalized or address,
        "region": data.get("region_with_type") or data.get("region") or u"",
        "city": data.get("city_with_type") or data.get("settlement_with_type") or
                data.get("city") or data.get("settlement") or u"",
        "latitude": float(lat),
        "longitude": float(lon),
        "qc_geo": data.get("qc_geo"),
        "fias_id": data.get("fias_id") or u"",
    }


def geocode_dadata_suggest(address, token):
    result = _request_json(
        "https://suggestions.dadata.ru/suggestions/api/4_1/rs/suggest/address",
        method="POST",
        payload={"query": address, "count": 10},
        headers={"Authorization": "Token " + token},
    )
    suggestions = (result or {}).get("suggestions") or []
    if not suggestions:
        raise RuntimeError(u"API подсказок DaData не нашел адрес.")
    for item in suggestions:
        parsed = _geocode_result(item or {}, address, suggestion=True)
        if parsed:
            parsed["dadata_method"] = "suggest"
            return parsed
    raise RuntimeError(u"DaData нашла варианты адреса, но не вернула координаты.")


def geocode_dadata_clean(address, token, secret):
    result = _request_json(
        "https://cleaner.dadata.ru/api/v1/clean/address",
        method="POST",
        payload=[address],
        headers={"Authorization": "Token " + token, "X-Secret": secret},
    )
    if not isinstance(result, list) or not result:
        raise RuntimeError(u"DaData не вернула результат для адреса.")
    parsed = _geocode_result(result[0] or {}, address, suggestion=False)
    if not parsed:
        raise RuntimeError(u"DaData распознала адрес, но не определила координаты.")
    parsed["dadata_method"] = "clean"
    return parsed


def geocode_dadata(address, token, secret=None):
    """Сначала бесплатнее/проще API подсказок, затем платная стандартизация."""
    errors = []
    try:
        return geocode_dadata_suggest(address, token)
    except Exception as ex:
        errors.append(u"подсказки: {}".format(exception_text(ex)))
    if secret:
        try:
            return geocode_dadata_clean(address, token, secret)
        except Exception as ex:
            errors.append(u"стандартизация: {}".format(exception_text(ex)))
    raise RuntimeError(u"DaData не ответила корректно ({})".format(u"; ".join(errors)))


def _urlencode(params):
    try:
        from urllib import urlencode
    except ImportError:
        from urllib.parse import urlencode
    return urlencode(params, doseq=True)


def _soil_value_percent(value):
    if value is None:
        return None
    number = float(value)
    # SoilGrids clay/sand/silt обычно возвращает g/kg с фактором преобразования 10.
    return number / 10.0 if number > 100.0 else number


def _soil_layer_mean(layer):
    weighted = 0.0
    total_weight = 0.0
    weights = {"0-5cm": 5.0, "5-15cm": 10.0, "15-30cm": 15.0}
    for depth in layer.get("depths") or []:
        label = depth.get("label") or u""
        mean = (depth.get("values") or {}).get("mean")
        if mean is None:
            continue
        weight = weights.get(label, 1.0)
        weighted += _soil_value_percent(mean) * weight
        total_weight += weight
    return weighted / total_weight if total_weight else None


def classify_soil_texture(clay_pct, sand_pct, silt_pct):
    clay = float(clay_pct or 0.0)
    sand = float(sand_pct or 0.0)
    if clay >= 40.0:
        return "clay", u"Глина"
    if sand >= 70.0 and clay < 15.0:
        return "sand", u"Песок"
    if sand >= 50.0 and clay < 20.0:
        return "sandy_loam", u"Супесь / песчаный суглинок"
    if clay >= 20.0:
        return "loam", u"Суглинок"
    return "loam", u"Суглинок / супесь"


def reference_rho_for_texture(texture_code):
    # Консервативные стартовые значения; это не замена измерениям на площадке.
    return {
        "clay": 60.0,
        "loam": 100.0,
        "sandy_loam": 200.0,
        "sand": 500.0,
    }.get(texture_code, 100.0)


def query_soilgrids(latitude, longitude):
    params = [
        ("lon", "{:.6f}".format(float(longitude))),
        ("lat", "{:.6f}".format(float(latitude))),
        ("property", "clay"), ("property", "sand"), ("property", "silt"),
        ("depth", "0-5cm"), ("depth", "5-15cm"), ("depth", "15-30cm"),
        ("value", "mean"),
    ]
    data = _request_json("https://rest.isric.org/soilgrids/v2.0/properties/query?" + _urlencode(params))
    layers = ((data or {}).get("properties") or {}).get("layers") or []
    values = {}
    for layer in layers:
        name = layer.get("name")
        if name in ("clay", "sand", "silt"):
            values[name] = _soil_layer_mean(layer)
    if values.get("clay") is None or values.get("sand") is None:
        raise RuntimeError(u"SoilGrids не вернул гранулометрический состав для точки.")
    texture_code, texture_name = classify_soil_texture(
        values.get("clay"), values.get("sand"), values.get("silt"))
    return {
        "texture_code": texture_code,
        "soil_type": u"{} (SoilGrids, слой 0–30 см; справочно)".format(texture_name),
        "rho": reference_rho_for_texture(texture_code),
        "clay_pct": round(values.get("clay") or 0.0, 1),
        "sand_pct": round(values.get("sand") or 0.0, 1),
        "silt_pct": round(values.get("silt") or 0.0, 1),
    }


def frost_coefficient(texture_code):
    # d0 для предварительной оценки dfn=d0*sqrt(Mt).
    return {
        "clay": 0.23,
        "loam": 0.23,
        "sandy_loam": 0.28,
        "sand": 0.30,
    }.get(texture_code, 0.23)


def frost_depth_from_daily(times, temperatures, texture_code):
    month_sum = dict((month, 0.0) for month in range(1, 13))
    month_count = dict((month, 0) for month in range(1, 13))
    for day, temperature in zip(times or [], temperatures or []):
        if temperature is None:
            continue
        try:
            month = int(u"{}".format(day)[5:7])
            month_sum[month] += float(temperature)
            month_count[month] += 1
        except Exception:
            continue
    monthly = []
    for month in range(1, 13):
        if month_count[month]:
            monthly.append(month_sum[month] / month_count[month])
    if len(monthly) < 10:
        raise RuntimeError(u"Недостаточно температурных данных для оценки промерзания.")
    mt = sum(abs(value) for value in monthly if value < 0.0)
    depth = frost_coefficient(texture_code) * math.sqrt(mt)
    return round(depth, 2), round(mt, 2), [round(value, 2) for value in monthly]


def seasonal_factor_from_frost(frost_depth):
    depth = float(frost_depth)
    # Если по климатической формуле СП Mt=0 и нормативная глубина равна 0,
    # сезонный коэффициент по замороженному верхнему слою не применяется.
    if depth <= 0.0:
        return 1.00
    if depth < 1.0:
        return 1.20
    if depth < 1.5:
        return 1.30
    if depth < 2.0:
        return 1.40
    return 1.50


def query_openmeteo_climate(latitude, longitude, texture_code):
    params = [
        ("latitude", "{:.6f}".format(float(latitude))),
        ("longitude", "{:.6f}".format(float(longitude))),
        ("start_date", "1991-01-01"),
        ("end_date", "2020-12-31"),
        ("daily", "temperature_2m_mean"),
        ("models", "era5_land"),
        ("timezone", "auto"),
    ]
    data = _request_json("https://archive-api.open-meteo.com/v1/archive?" + _urlencode(params),
                         timeout_ms=45000)
    daily = (data or {}).get("daily") or {}
    frost, mt, monthly = frost_depth_from_daily(
        daily.get("time"), daily.get("temperature_2m_mean"), texture_code)
    if mt <= 1e-9:
        frost_reason = (u"Mt = 0: по среднемесячным данным нет месяцев с отрицательной "
                        u"средней температурой; по формуле СП 22 dfn = d0×√Mt = 0 м")
        frost_zero_is_climatic = True
    else:
        frost_reason = (u"Расчет по СП 22: dfn = d0×√Mt; Mt = {:.2f}".format(mt))
        frost_zero_is_climatic = False
    return {
        "frost_depth": frost,
        "freezing_index_mt": mt,
        "monthly_temperature": monthly,
        "frost_depth_reason": frost_reason,
        "frost_zero_is_climatic": frost_zero_is_climatic,
        "seasonal_factor": seasonal_factor_from_frost(frost),
        "climate_station": (u"ERA5-Land 1991–2020, расчетная ячейка {:.4f}; {:.4f} "
                            u"(не станция СП)").format(float(latitude), float(longitude)),
    }


def _cache_key(address):
    raw = u"{}".format(address or u"").strip().lower().replace(u"ё", u"е").encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _utc_now_text():
    return datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


def _cache_age_days(item):
    try:
        timestamp = datetime.datetime.strptime(item.get("online_updated_utc"), "%Y-%m-%dT%H:%M:%SZ")
        return (datetime.datetime.utcnow() - timestamp).total_seconds() / 86400.0
    except Exception:
        return 10 ** 9


def get_cached_context(address, cache_days=None, allow_stale=False):
    item = _read_json(cache_path(), {}).get(_cache_key(address))
    if not isinstance(item, dict):
        return None
    if cache_days is None:
        cache_days = load_settings().get("cache_days", 30)
    if not allow_stale and _cache_age_days(item) > float(cache_days):
        return None
    return item


def _save_cached_context(address, result):
    cache = _read_json(cache_path(), {})
    cache[_cache_key(address)] = result
    _write_json(cache_path(), cache)


def fetch_online_context(address, settings=None):
    settings = settings or load_settings()
    if not credentials_configured(settings):
        raise RuntimeError(u"Онлайн-доступ не настроен. Откройте команду «Онлайн-данные».")
    if not address or not u"{}".format(address).strip():
        raise ValueError(u"В проекте не заполнен адрес объекта.")

    geo = geocode_dadata(address, settings["dadata_token"], settings["dadata_secret"])
    lookup_text = u"{} {} {}".format(
        geo.get("normalized_address") or u"", geo.get("region") or u"", geo.get("city") or u"")
    try:
        from project_context import infer_from_address
        profile = infer_from_address(lookup_text)
    except Exception:
        profile = None

    errors = []
    soil = None
    if settings.get("use_soilgrids", True):
        try:
            soil = query_soilgrids(geo["latitude"], geo["longitude"])
        except Exception as ex:
            errors.append(u"SoilGrids: {}".format(exception_text(ex)))

    if soil:
        texture_code = soil["texture_code"]
        soil_type = soil["soil_type"]
        rho = soil["rho"]
        soil_quality = "ONLINE_REFERENCE"
    elif profile:
        texture_code = "loam"
        soil_type = profile["soil_type"]
        rho = profile["rho"]
        soil_quality = "ADDRESS_REFERENCE"
    else:
        texture_code = "loam"
        soil_type = u"Суглинок (консервативное справочное допущение)"
        rho = 100.0
        soil_quality = "ADDRESS_REFERENCE"

    climate = None
    if settings.get("use_openmeteo", True):
        try:
            climate = query_openmeteo_climate(geo["latitude"], geo["longitude"], texture_code)
        except Exception as ex:
            errors.append(u"Open-Meteo: {}".format(exception_text(ex)))

    if climate:
        frost_depth = climate["frost_depth"]
        seasonal_factor = climate["seasonal_factor"]
        climate_station = climate["climate_station"]
        frost_depth_source = u"ERA5-Land 1991–2020, предварительный расчет по температурному индексу"
    elif profile:
        frost_depth = profile["frost_depth"]
        seasonal_factor = profile["seasonal_factor"]
        climate_station = profile["climate_station"]
        frost_depth_source = u"Справочный офлайн-профиль региона: {}".format(profile.get("name") or profile.get("id") or u"регион")
    else:
        frost_depth = None
        seasonal_factor = 1.30
        climate_station = u"Не определена; выбрать по СП 131.13330.2025"
        frost_depth_source = u"Не определена — требуется ввод проектировщиком"

    region_text = geo.get("region") or geo.get("city") or u"Регион по DaData"
    climate_region = (profile["climate_region"] if profile else
                      u"{}; климатический подрайон уточнить по СП 131.13330.2025".format(region_text))
    used = [u"DaData"]
    if soil:
        used.append(u"SoilGrids")
    if climate:
        used.append(u"Open-Meteo/ERA5-Land")
    if profile and (not soil or not climate):
        used.append(u"офлайн-профиль как резерв")

    result = {
        "normalized_address": geo.get("normalized_address"),
        "region": geo.get("region"),
        "city": geo.get("city"),
        "latitude": geo.get("latitude"),
        "longitude": geo.get("longitude"),
        "qc_geo": geo.get("qc_geo"),
        "fias_id": geo.get("fias_id"),
        "dadata_method": geo.get("dadata_method"),
        "climate_region": climate_region,
        "climate_station": climate_station,
        "soil_type": soil_type,
        "rho": u"{}".format(rho),
        "seasonal_factor": u"{:.2f}".format(float(seasonal_factor)),
        "frost_depth": u"{:.2f}".format(float(frost_depth)) if frost_depth is not None else None,
        "frost_depth_auto": u"{:.2f}".format(float(frost_depth)) if frost_depth is not None else None,
        "frost_depth_auto_source": frost_depth_source,
        "frost_depth_source": frost_depth_source,
        "frost_depth_reason": (climate.get("frost_depth_reason") if climate else u""),
        "frost_zero_is_climatic": bool(climate.get("frost_zero_is_climatic")) if climate else False,
        "location_profile_id": profile.get("id") if profile else "online_coordinates",
        "location_profile_name": profile.get("name") if profile else region_text,
        "location_data_source": u"Онлайн: {}".format(u" + ".join(used)),
        "soil_data_quality": soil_quality,
        "location_warning": (u"Онлайн-данные являются предварительными: тип грунта отражает верхний слой модели, "
                             u"ρ принято таблично; подтвердить ИГИ и измерением. Промерзание по ERA5-Land "
                             u"не заменяет выбор нормативной станции СП."),
        "online_errors": u"; ".join(errors),
        "online_updated_utc": _utc_now_text(),
        "data_provenance": u"; ".join(used),
    }
    if soil:
        result.update({
            "soil_clay_pct": soil.get("clay_pct"),
            "soil_sand_pct": soil.get("sand_pct"),
            "soil_silt_pct": soil.get("silt_pct"),
        })
    if climate:
        result["freezing_index_mt"] = climate.get("freezing_index_mt")
    _save_cached_context(address, result)
    return result


def resolve_online_context(address, force=False, allow_network=True):
    """Возвращает (данные, ошибка). Сеть используется только после явной настройки."""
    settings = load_settings()
    cached = get_cached_context(address, settings.get("cache_days", 30), allow_stale=False)
    if cached and not force:
        return cached, u""
    if not allow_network or not settings.get("auto_update", True):
        return cached, u""
    if not credentials_configured(settings):
        return cached, settings.get("credentials_error") or u""
    try:
        return fetch_online_context(address, settings), u""
    except Exception as ex:
        stale = get_cached_context(address, allow_stale=True)
        return stale, exception_text(ex)
