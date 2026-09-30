# -*- coding: utf-8 -*-
from __future__ import print_function

import os
import sys

__title__ = u"Данные\nпроекта"
__author__ = u"ЭОМ / OpenAI"
__doc__ = u"Единые исходные данные объекта для расчёта ЗУ и документации."
__min_revit_ver__ = 2023

THIS_DIR = os.path.dirname(__file__)
EXT_ROOT = os.path.abspath(os.path.join(THIS_DIR, os.pardir, os.pardir, os.pardir))
LIB_DIR = os.path.join(EXT_ROOT, "lib")
if LIB_DIR not in sys.path:
    sys.path.append(LIB_DIR)

from pyrevit import revit, DB
from eom_ui_style import show_message
from project_context import resolve_project_context
from project_data_ui import show_project_data_form
from project_profile import write_project_profile


def main():
    doc = revit.doc
    if doc is None:
        show_message(u"Нет открытого проекта Revit.", warn_icon=True)
        return
    values = show_project_data_form(resolve_project_context(doc, DB))
    if values is None:
        return
    try:
        saved = write_project_profile(doc, DB, values)
    except Exception as ex:
        show_message(u"Не удалось сохранить данные проекта:\n\n{}".format(ex),
                     title=u"ЭОМ — Данные проекта", warn_icon=True)
        return
    show_message(
        u"Данные проекта сохранены.\n\nОбъект: {}\nАдрес: {}\nВыделенная мощность: {} кВт\nСистема: {}"
        .format(saved.get("project_object_type") or u"—",
                saved.get("project_address") or u"—",
                saved.get("allocated_power_kw") or u"—",
                saved.get("system") or u"—"),
        title=u"ЭОМ — Данные проекта")


if __name__ == "__main__":
    main()
