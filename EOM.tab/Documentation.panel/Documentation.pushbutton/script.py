# -*- coding: utf-8 -*-
from __future__ import division, print_function

import os
import sys

__title__ = u"Лист\nЗУ"
__author__ = u"ЭОМ / OpenAI"
__doc__ = u"Создает или обновляет лист ЗУ с расчетом, планом, двумя разрезами и аксонометрией."
__min_revit_ver__ = 2023

THIS_DIR = os.path.dirname(__file__)
EXT_ROOT = os.path.abspath(os.path.join(THIS_DIR, os.pardir, os.pardir, os.pardir))
LIB_DIR = os.path.join(EXT_ROOT, "lib")
if LIB_DIR not in sys.path:
    sys.path.append(LIB_DIR)

from pyrevit import revit, DB, UI

from calculation_tables import build_calculation_tables
from eom_ui_style import show_message
from grounding_core import (calculate, format_report, calculate_single_rod,
                            format_single_rod_report)
from panel_context import refresh_panel_info
from perf_trace import reset as trace_reset, mark as trace_mark, mark_exception as trace_exception
from project_context import resolve_project_context
from revit_documentation_sheet import create_or_update_documentation_sheet
from revit_grounding_model import (group_from_selection, extract_group, spacing_metrics_m,
                                    route_length_m, contour_edit_is_active)



def _parse_kv(text):
    result = {}
    for part in (text or u"").split(u";"):
        if u"=" in part:
            key, value = part.split(u"=", 1)
            result[key.strip()] = value.strip()
    return result


def _actual_strip_length_m(DB, group, fallback_points, close_loop):
    total = 0.0
    count = 0
    for strip in group.get("strips") or []:
        try:
            p = strip.get_Parameter(DB.BuiltInParameter.ALL_MODEL_INSTANCE_COMMENTS)
            kv = _parse_kv(p.AsString() if p else u"")
            if kv.get("L_M"):
                total += float(kv.get("L_M"))
                count += 1
        except Exception:
            pass
    if count:
        return total
    return route_length_m(fallback_points or [], close_loop) if len(fallback_points or []) > 1 else 0.0

def _recalculate_from_group(doc, DB, group):
    stored = dict(group.get("data") or {})
    single = ("auto_single_length" in stored or "rod_module_step" in stored)

    defaults = resolve_project_context(doc, DB)
    defaults.update(stored)
    panel = refresh_panel_info(doc, DB, defaults)
    if panel:
        defaults.update(panel)

    data = defaults
    data["vertical_count"] = len(group.get("points") or [])
    metrics = spacing_metrics_m(group.get("points") or [])
    data["vertical_spacing"] = metrics["pair_min"] if len(group.get("points") or []) > 1 else 0.0
    close_loop = bool(data.get("close_loop", group.get("close_loop"))) and len(group.get("points") or []) > 2
    data["close_loop"] = close_loop
    data["auto_optimize"] = False
    data["create_model"] = True

    route = group.get("route_points") or group.get("points") or []
    if data.get("horizontal_enabled", True) and (group.get("strips") or len(route) > 1):
        data["horizontal_length"] = _actual_strip_length_m(DB, group, route, close_loop)
    else:
        data["horizontal_enabled"] = False
        data["horizontal_length"] = 0.0

    if single:
        data["horizontal_enabled"] = False
        data["horizontal_length"] = 0.0
        result = calculate_single_rod(data)
        title = u"РАСЧЕТ ОДИНОЧНОГО МОДУЛЬНОГО ЗАЗЕМЛИТЕЛЯ"
        report = format_single_rod_report(data, result)
    else:
        result = calculate(data)
        title = u"РАСЧЕТ ЗУ ПО ФАКТИЧЕСКОЙ BIM-ГЕОМЕТРИИ"
        report = format_report(data, result, title)
    report_tables = build_calculation_tables(data, result, single=single, title=title)
    rows = [
        [u"Группа ЗУ", u"ID", group.get("group_id") or u"—", u""],
        [u"Вертикальные электроды", u"n", u"{} шт.".format(len(group.get("points") or [])), u"Фактическая BIM-геометрия"],
    ]
    if len(group.get("points") or []) > 1:
        rows.extend([
            [u"Шаг соседних электродов", u"a min/avg/max",
             u"{:.2f} / {:.2f} / {:.2f} м".format(metrics["min"], metrics["avg"], metrics["max"]), u""],
            [u"Минимум между любой парой", u"amin", u"{:.2f} м".format(metrics["pair_min"]),
             u"Консервативное значение для предварительной оценки η"],
        ])
    if data.get("horizontal_enabled"):
        rows.append([
            u"Соединительный электрод", u"lфакт",
            u"{:.2f} м".format(float(data.get("horizontal_length") or 0.0)),
            u"Замкнутый" if close_loop else u"Разомкнутый"
        ])
    report_tables["tables"].append({
        "title": u"7. BIM-ГЕОМЕТРИЯ",
        "columns": [u"Параметр", u"Обозначение", u"Значение", u"Примечание"],
        "rows": rows,
    })
    return data, result, report, report_tables


def main():
    doc = revit.doc
    uidoc = revit.uidoc
    trace_reset("Documentation")
    if doc is None or uidoc is None:
        show_message(u"Нет открытого проекта Revit.", title=u"ЭОМ — Лист ЗУ", warn_icon=True)
        return

    try:
        group_id, selected = group_from_selection(uidoc, doc, DB, UI)
        if not group_id:
            return
        group = extract_group(doc, DB, group_id)
    except Exception as ex:
        trace_exception("DOC_GROUP_READ_ERROR", ex)
        show_message(u"Не удалось прочитать выбранное ЗУ:\n\n{}".format(ex),
                     title=u"ЭОМ — Лист ЗУ", warn_icon=True)
        return

    if contour_edit_is_active(group):
        show_message(
            u"Для выбранного ЗУ активен режим редактирования контура.\n\n"
            u"Сначала завершите команду «Контур ЗУ», затем сформируйте документационный лист.",
            title=u"ЭОМ — Лист ЗУ", warn_icon=True)
        return

    try:
        trace_mark("DOC_RECALC_START")
        data, result, report, report_tables = _recalculate_from_group(doc, DB, group)
        trace_mark("DOC_RECALC_END")
    except Exception as ex:
        trace_exception("DOC_RECALC_ERROR", ex)
        show_message(u"Не удалось пересчитать ЗУ для документационного листа:\n\n{}".format(ex),
                     title=u"ЭОМ — Лист ЗУ", warn_icon=True)
        return

    try:
        trace_mark("DOC_SHEET_START")
        created = create_or_update_documentation_sheet(
            doc, uidoc, DB, group, report, report_tables)
        trace_mark("DOC_SHEET_END")
    except Exception as ex:
        trace_exception("DOC_SHEET_ERROR", ex)
        show_message(u"Лист ЗУ не сформирован. Изменения документации отменены.\n\n{}".format(ex),
                     title=u"ЭОМ — Лист ЗУ", warn_icon=True)
        return

    sheet = created["sheet"]
    scales = created.get("scales") or {}
    warnings = created.get("warnings") or []
    text = (u"Лист ЗУ сформирован.\n\n"
            u"Лист: {} — {}\n"
            u"План: 1:{}\n"
            u"Разрез 1-1: 1:{}\n"
            u"Разрез 2-2: 1:{}\n"
            u"3D: 1:{}"
            .format(sheet.SheetNumber, sheet.Name,
                    scales.get("PLAN", u"—"),
                    scales.get("SECTION_1", u"—"),
                    scales.get("SECTION_2", u"—"),
                    scales.get("AXON", u"—")))
    if warnings:
        text += u"\n\nПредупреждения:\n- " + u"\n- ".join(warnings)
    show_message(text, title=u"ЭОМ — Лист ЗУ", warning=bool(warnings))

    # Last UI action only: show the generated sheet after all transactions and dialogs.
    try:
        uidoc.ActiveView = sheet
    except Exception:
        pass
    trace_mark("DONE", u"sheet={}".format(sheet.SheetNumber))


if __name__ == "__main__":
    main()
