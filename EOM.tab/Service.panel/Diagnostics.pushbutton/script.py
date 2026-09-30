# -*- coding: utf-8 -*-
from __future__ import print_function

__title__ = u"Диагностика"
__author__ = u"ЭОМ / OpenAI"
__doc__ = u"Проверяет, что расширение загружено и Revit API доступен."
__min_revit_ver__ = 2023

from plugin_version import display_version
from pyrevit import HOST_APP, revit, DB

import os
import sys
THIS_DIR = os.path.dirname(__file__)
EXT_ROOT = os.path.abspath(os.path.join(THIS_DIR, os.pardir, os.pardir, os.pardir))
LIB_DIR = os.path.join(EXT_ROOT, "lib")
if LIB_DIR not in sys.path:
    sys.path.append(LIB_DIR)
from eom_ui_style import show_message
from project_context import resolve_project_context, address_debug_candidates


def main():
    doc_title = u"Нет открытого проекта"
    try:
        if revit.doc:
            doc_title = revit.doc.Title
    except Exception:
        pass
    context_text = u"Автоподбор недоступен без открытого проекта."
    if revit.doc:
        context = resolve_project_context(revit.doc, DB)
        context_text = (u"Адрес проекта: {}\n"
                        u"Параметр: {}\n"
                        u"Прочитан из: {}\n"
                        u"Профиль: {}\n"
                        u"Грунт: {}; ρ={} Ом·м; промерзание={} м\n"
                        u"Координаты: {}; {}\n"
                        u"Источник: {}\n"
                        u"Ошибки онлайн-источников: {}").format(
                            context.get("project_address") or u"не задан",
                            context.get("project_address_parameter") or u"не определен",
                            context.get("project_address_source") or u"не определено",
                            context.get("location_profile_name") or u"не определен",
                            context.get("soil_type") or u"не задан",
                            context.get("rho") or u"не задан",
                            context.get("frost_depth") or u"не задано",
                            context.get("latitude") if context.get("latitude") is not None else u"не заданы",
                            context.get("longitude") if context.get("longitude") is not None else u"не заданы",
                            context.get("location_data_source") or u"не задан",
                            context.get("online_errors") or u"нет")
        if not context.get("project_address"):
            candidates = address_debug_candidates(revit.doc, DB)
            if candidates:
                context_text += u"\n\nНайдены похожие параметры:\n- " + u"\n- ".join(candidates)
            else:
                context_text += u"\n\nПараметры, содержащие слово «адрес», не найдены."
    msg = (u"Расширение EOM Grounding {} загружено.\nРасчетное ядро: {}.\n\n"
           u"Revit: {}\n"
           u"Документ: {}\n\n"
           u"{}\n\n"
           u"Если вы видите это окно, структура pyRevit и запуск Python-команд работают.").format(
               display_version(), display_version(), HOST_APP.version, doc_title, context_text)
    show_message(msg, title=u"ЭОМ — Диагностика")


if __name__ == "__main__":
    main()
