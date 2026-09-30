# -*- coding: utf-8 -*-
from __future__ import print_function
import os
import sys

__title__ = u"Контур\nЗУ"
__author__ = u"ЭОМ / OpenAI"
__doc__ = u"Двухэтапное редактирование замкнутого или незамкнутого контура через временные Model Lines."
__min_revit_ver__ = 2023

THIS_DIR = os.path.dirname(__file__)
EXT_ROOT = os.path.abspath(os.path.join(THIS_DIR, os.pardir, os.pardir, os.pardir))
LIB_DIR = os.path.join(EXT_ROOT, "lib")
if LIB_DIR not in sys.path:
    sys.path.append(LIB_DIR)

from System.Collections.Generic import List
from pyrevit import revit, DB, UI
from eom_ui_style import show_message
from grounding_core import calculate, format_report
from calculation_tables import build_calculation_tables
from revit_output import create_or_update_group_view
from revit_grounding_model import (group_from_selection, extract_group,
                                    contour_edit_is_active, contour_edit_guides_exist,
                                    begin_contour_edit,
                                    apply_contour_edit)
from perf_trace import reset as trace_reset, mark as trace_mark, mark_exception as trace_exception


def _select_and_show(uidoc, DB, element_ids):
    ids = List[DB.ElementId]()
    for element_id in element_ids or []:
        ids.Add(element_id)
    if not ids.Count:
        return
    uidoc.Selection.SetElementIds(ids)
    try:
        uidoc.ShowElements(ids)
    except Exception:
        pass


def _update_calculation_view(doc, uidoc, DB, group_id, data, edit_result):
    result = calculate(data)
    layout = edit_result.get("layout") or {}
    report = format_report(data, result, u"РАСЧЕТ ЗУ ПО ИЗМЕНЕННОМУ КОНТУРУ")
    report += (u"\n\nКОНТУР ОТРЕДАКТИРОВАН\n"
               u"Длина трассы: {:.2f} м. Вертикальных электродов: {}. "
               u"Шаг min/avg/max: {:.2f}/{:.2f}/{:.2f} м."
               .format(float(layout.get("perimeter_length") or 0.0),
                       len(edit_result.get("electrode_points") or []),
                       float(layout.get("spacing_min") or 0.0),
                       float(layout.get("spacing_avg") or 0.0),
                       float(layout.get("spacing_max") or 0.0)))
    open_design = edit_result.get("open_design") or {}
    if open_design:
        report += (u"\nАвтоподбор незамкнутого контура: доступно {:.2f} м, использовано {:.2f} м, "
                   u"установленный предел R ≤ {:.2f} Ом, получено R = {:.2f} Ом."
                   .format(float(open_design["available_length_m"]),
                           float(open_design["used_length_m"]),
                           float(open_design["target_r"]),
                           float(open_design["result"]["total_r"])))
        if not open_design.get("reserve_achieved"):
            report += u"\nПроектная цель с запасом R ≤ {:.2f} Ом не достигнута минимальной длиной.".format(
                float(open_design["reserve_target_r"]))
    elif not bool(data.get("close_loop")) and bool(data.get("open_contour_fixed_length")):
        report += u"\nДлина незамкнутого контура зафиксирована полной трассой Model Lines."
    tables = build_calculation_tables(data, result, single=False,
                                      title=u"РАСЧЕТ ЗУ ПО ИЗМЕНЕННОМУ КОНТУРУ")
    create_or_update_group_view(doc, uidoc, DB, report, group_id,
                                report_tables=tables, activate=False)
    return result


def main():
    doc = revit.doc
    uidoc = revit.uidoc
    trace_reset("EditContour")
    if doc is None or uidoc is None:
        show_message(u"Нет открытого проекта Revit.", warn_icon=True)
        return

    try:
        group_id, selected = group_from_selection(uidoc, doc, DB, UI)
        if not group_id:
            return
        group = extract_group(doc, DB, group_id)
    except Exception as ex:
        trace_exception("CONTOUR_GROUP_ERROR", ex)
        show_message(u"Не удалось прочитать выбранное ЗУ:\n\n{}".format(ex),
                    title=u"ЭОМ — Контур ЗУ", warn_icon=True)
        return

    if (not contour_edit_is_active(group) or
            not contour_edit_guides_exist(doc, DB, group)):
        try:
            trace_mark("CONTOUR_EDIT_BEGIN")
            edit_info = begin_contour_edit(doc, DB, group)
            _select_and_show(uidoc, DB, edit_info.get("element_ids"))
            trace_mark("CONTOUR_EDIT_READY", len(edit_info.get("guide_unique_ids") or []))
        except Exception as ex:
            trace_exception("CONTOUR_EDIT_BEGIN_ERROR", ex)
            show_message(u"Не удалось создать редактируемый контур:\n\n{}".format(ex),
                        title=u"ЭОМ — Контур ЗУ", warn_icon=True)
            return
        show_message(
            u"Временные Model Lines трассы созданы и выделены.\n\n"
            u"Измените вершины контура штатными инструментами Revit. Для новых сегментов "
            u"используйте стиль линии «{}».\n\n"
            u"После редактирования снова нажмите «Контур ЗУ» и выберите любой элемент этого ЗУ."
            .format(edit_info.get("style_name")),
            title=u"ЭОМ — Редактирование контура")
        return

    try:
        trace_mark("CONTOUR_EDIT_APPLY_START")
        edit_result = apply_contour_edit(doc, DB, group)
        trace_mark("CONTOUR_EDIT_APPLY_END")
    except Exception as ex:
        trace_exception("CONTOUR_EDIT_APPLY_ERROR", ex)
        show_message(
            u"Измененный контур не применен. Старая модель и временные линии сохранены.\n\n{}"
            .format(ex), title=u"ЭОМ — Контур ЗУ", warn_icon=True)
        return

    try:
        _update_calculation_view(doc, uidoc, DB, group_id, edit_result["data"], edit_result)
    except Exception as ex:
        trace_exception("CONTOUR_VIEW_ERROR", ex)

    model_info = edit_result.get("model_info") or {}
    new_ids = List[DB.ElementId]()
    for value in model_info.get("element_ids") or []:
        new_ids.Add(DB.ElementId(int(value)))
    if new_ids.Count:
        uidoc.Selection.SetElementIds(new_ids)
    layout = edit_result.get("layout") or {}
    show_message(
        u"Контур обновлен.\n\nДлина трассы: {:.2f} м\nЭлектродов: {}\n"
        u"Временные Model Lines удалены."
        .format(float(layout.get("perimeter_length") or 0.0),
                len(edit_result.get("electrode_points") or [])),
        title=u"ЭОМ — Контур ЗУ")
    trace_mark("DONE")


if __name__ == "__main__":
    main()
