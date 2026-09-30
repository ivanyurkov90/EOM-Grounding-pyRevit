# -*- coding: utf-8 -*-
from __future__ import print_function
import os
import sys

__title__ = u"Онлайн\nданные"
__author__ = u"ЭОМ / OpenAI"
__doc__ = u"Настройка онлайн-источников и обновление справочных данных объекта."
__min_revit_ver__ = 2023

THIS_DIR = os.path.dirname(__file__)
EXT_ROOT = os.path.abspath(os.path.join(THIS_DIR, os.pardir, os.pardir, os.pardir))
LIB_DIR = os.path.join(EXT_ROOT, "lib")
if LIB_DIR not in sys.path:
    sys.path.append(LIB_DIR)

from pyrevit import revit, DB, forms, script
from eom_ui_style import show_message
from project_context import read_project_address_details
from online_context import load_settings, save_settings, fetch_online_context, exception_text
from online_settings_ui import show_online_settings


def _fmt(value, fallback=u"не определено"):
    return u"{}".format(value) if value not in (None, u"") else fallback


def main():
    if revit.doc is None:
        show_message(u"Нет открытого проекта Revit.", title=u"ЭОМ — Онлайн-данные", warn_icon=True)
        return

    current = load_settings()
    values = show_online_settings(current)
    if not values:
        return

    try:
        configured = save_settings(
            values.get("dadata_token"), values.get("dadata_secret"),
            online_enabled=values.get("online_enabled", False),
            auto_update=values.get("auto_update", True),
            use_soilgrids=values.get("use_soilgrids", True),
            use_openmeteo=values.get("use_openmeteo", True),
            cache_days=values.get("cache_days", 30),
        )
    except Exception as ex:
        show_message(u"Настройки не сохранены:\n\n{}".format(exception_text(ex)),
                    title=u"ЭОМ — Онлайн-данные", warn_icon=True)
        return

    if not configured.get("online_enabled"):
        show_message(u"Онлайн-источники отключены. Плагин продолжит использовать параметры проекта "
                    u"и локальную резервную базу.", title=u"ЭОМ — Онлайн-данные")
        return

    details = read_project_address_details(revit.doc, DB)
    address = details.get("value") or u""
    if not address:
        show_message(u"Настройки сохранены, но в проекте не найден адрес объекта.\n\n"
                    u"Заполните «Адрес проекта» и повторите команду.",
                    title=u"ЭОМ — Онлайн-данные", warn_icon=True)
        return

    try:
        with forms.ProgressBar(title=u"ЭОМ: получение онлайн-данных...", indeterminate=True) as progress:
            result = fetch_online_context(address, configured)
    except Exception as ex:
        error = exception_text(ex)
        diagnostic = (u"Адрес: {}\nИсточник параметра: {}\nОшибка: {}".format(
            address, details.get("source") or u"не определен", error))
        output = script.get_output()
        try:
            output.print_md("## ЭОМ — ошибка онлайн-запроса")
            output.print_code(diagnostic)
        except Exception:
            pass
        show_message(u"Настройки сохранены, но данные не получены:\n\n{}\n\n"
                    u"Полный текст также выведен в окно pyRevit. Проверьте интернет, "
                    u"API-ключ и разрешение доступа к домену suggestions.dadata.ru.".format(error),
                    title=u"ЭОМ — Онлайн-данные", warn_icon=True)
        return

    message = (
        u"Онлайн-данные сохранены в локальный кэш.\n\n"
        u"Адрес Revit: {address}\n"
        u"Нормализованный адрес: {normalized}\n"
        u"Координаты: {lat}; {lon}\n"
        u"Профиль: {profile}\n"
        u"Грунт: {soil}\n"
        u"ρ (справочно): {rho} Ом·м\n"
        u"Глубина промерзания (справочно): {frost} м\n"
        u"Сезонный коэффициент: {season}\n"
        u"Источник: {source}"
    ).format(
        address=address,
        normalized=_fmt(result.get("normalized_address")),
        lat=_fmt(result.get("latitude")),
        lon=_fmt(result.get("longitude")),
        profile=_fmt(result.get("climate_region")),
        soil=_fmt(result.get("soil_type")),
        rho=_fmt(result.get("rho")),
        frost=_fmt(result.get("frost_depth")),
        season=_fmt(result.get("seasonal_factor")),
        source=_fmt(result.get("location_data_source")),
    )
    if result.get("online_errors"):
        message += u"\n\nЧастичные ошибки (использован резервный профиль):\n{}".format(
            result.get("online_errors"))
    message += (u"\n\nТеперь откройте «Модульный электрод» или «Расчет ЗУ»: поля будут заполнены "
                u"автоматически. Данные заказчика в параметрах проекта имеют приоритет.")

    output = script.get_output()
    try:
        output.print_md("## ЭОМ — онлайн-контекст объекта")
        output.print_code(message)
    except Exception:
        pass
    show_message(message, title=u"ЭОМ — Онлайн-данные",
                warn_icon=bool(result.get("online_errors")))


if __name__ == "__main__":
    main()
