# -*- coding: utf-8 -*-
from __future__ import print_function
import os
import sys

__title__ = u"Обновить\nЗУ"
__author__ = u"ЭОМ / OpenAI"
__doc__ = u"Пересчет существующего ЗУ по фактическим координатам электродов в Revit."
__min_revit_ver__ = 2023

THIS_DIR = os.path.dirname(__file__)
EXT_ROOT = os.path.abspath(os.path.join(THIS_DIR, os.pardir, os.pardir, os.pardir))
LIB_DIR = os.path.join(EXT_ROOT, "lib")
if LIB_DIR not in sys.path:
    sys.path.append(LIB_DIR)

from pyrevit import revit, DB, UI
from eom_ui_style import show_message
from grounding_ui import show_input_form
from grounding_core import calculate, format_report, number_value
from revit_grounding_model import (group_from_selection, extract_group,
                                    spacing_metrics_m, route_length_m,
                                    sync_group_after_rod_moves,
                                    contour_edit_is_active, apply_contour_edit)
from revit_output import create_or_update_group_view
from calculation_tables import build_calculation_tables
from project_context import resolve_project_context
from panel_context import (list_gzsh_panels, show_gzsh_panel, refresh_panel_info,
                           link_panel_to_grounding)
from perf_trace import reset as trace_reset, mark as trace_mark, mark_exception as trace_exception


def _num(value, default=0.0):
    try:
        if value is None or value == "":
            return default
        return number_value(value, default)
    except Exception:
        return default



def main():
    doc = revit.doc
    uidoc = revit.uidoc
    trace_reset("UpdateZU")
    if doc is None or uidoc is None:
        show_message(u"Нет открытого проекта Revit.", warn_icon=True)
        return

    try:
        group_id, selected = group_from_selection(uidoc, doc, DB, UI)
        if not group_id:
            return
        group = extract_group(doc, DB, group_id)
    except Exception as ex:
        trace_exception("GROUP_READ_ERROR", ex)
        show_message(u"Не удалось прочитать существующее ЗУ:\n\n{}".format(ex),
                    title=u"ЭОМ — Обновить ЗУ", warn_icon=True)
        return

    defaults = resolve_project_context(doc, DB)
    defaults.update(group["data"])
    current_panel = refresh_panel_info(doc, DB, defaults)
    if current_panel:
        defaults.update(current_panel)
    defaults["create_model"] = True
    defaults["create_view"] = True
    defaults["auto_optimize"] = False

    if not group.get("metadata_found"):
        show_message(
            u"Это ЗУ создано версией v0.2 без полного хранения исходных данных.\n\n"
            u"Геометрия считана из модели. Проверьте систему, назначение, грунт и защиту в форме. "
            u"После сохранения группа будет переведена на актуальный формат v0.5.",
            title=u"ЭОМ — Обновление старой группы")

    panel_options = list_gzsh_panels(doc, DB)

    def _show_panel(panel_data):
        if show_gzsh_panel(uidoc, doc, DB, panel_data):
            return refresh_panel_info(doc, DB, panel_data) or panel_data
        return None

    data = show_input_form(defaults, panel_options=panel_options, panel_shower=_show_panel)
    if not data:
        return

    if contour_edit_is_active(group):
        try:
            trace_mark("CONTOUR_EDIT_APPLY_START")
            group["data"].update(data)
            edit_result = apply_contour_edit(doc, DB, group)
            group = extract_group(doc, DB, group_id)
            data = dict(edit_result.get("data") or data)
            data["create_model"] = True
            data["create_view"] = True
            data["auto_optimize"] = False
            trace_mark("CONTOUR_EDIT_APPLY_END")
        except Exception as ex:
            trace_exception("CONTOUR_EDIT_APPLY_ERROR", ex)
            show_message(
                u"Измененный контур не применен. Старая модель и временные линии сохранены.\n\n{}"
                .format(ex), title=u"ЭОМ — Обновить ЗУ", warn_icon=True)
            return

    # Координаты и размеры существующих стержней задаются самой BIM-моделью.
    locked = group["data"]
    changed_rod_geometry = (
        abs(_num(data.get("vertical_length")) - _num(locked.get("vertical_length"))) > 1e-6 or
        abs(_num(data.get("vertical_diameter_mm")) - _num(locked.get("vertical_diameter_mm"))) > 1e-6 or
        abs(_num(data.get("vertical_top_depth")) - _num(locked.get("vertical_top_depth"))) > 1e-6 or
        int(_num(data.get("vertical_count"), 1)) != len(group["points"])
    )
    if changed_rod_geometry:
        show_message(
            u"Команда «Обновить ЗУ» пока не меняет длину/диаметр/количество уже созданных стержней. "
            u"Для расчета сохранена фактическая геометрия модели. Изменение размеров стержней добавим отдельной командой.",
            title=u"ЭОМ — Геометрия стержней")

    data["vertical_length"] = locked.get("vertical_length")
    data["vertical_diameter_mm"] = locked.get("vertical_diameter_mm")
    data["vertical_top_depth"] = locked.get("vertical_top_depth")
    data["vertical_count"] = len(group["points"])
    data["auto_optimize"] = False

    metrics = spacing_metrics_m(group["points"])
    data["vertical_spacing"] = metrics["pair_min"] if len(group["points"]) > 1 else 0.0
    close_loop = bool(data.get("close_loop", group["close_loop"])) and len(group["points"]) > 2
    group["close_loop"] = close_loop

    actual_strip_points = group.get("route_points") or group["points"]
    if data.get("horizontal_enabled", True) and len(actual_strip_points) > 1:
        data["horizontal_length"] = route_length_m(actual_strip_points, close_loop)
    else:
        data["horizontal_enabled"] = False
        data["horizontal_length"] = 0.0

    try:
        result = calculate(data)
    except Exception as ex:
        trace_exception("CALC_ERROR", ex)
        show_message(u"Ошибка пересчета:\n\n{}".format(ex),
                    title=u"ЭОМ — Обновить ЗУ", warn_icon=True)
        return

    report = format_report(data, result, u"РАСЧЕТ ЗУ ПО ФАКТИЧЕСКОЙ BIM-ГЕОМЕТРИИ")
    report += u"\n\nBIM-ГЕОМЕТРИЯ v0.5"
    report += u"\nГруппа ЗУ: {}".format(group_id)
    report += u"\nВертикальных электродов: {} шт.".format(len(group["points"]))
    if len(group["points"]) > 1:
        report += u"\nШаг соседних электродов: min={:.2f} м; avg={:.2f} м; max={:.2f} м.".format(
            metrics["min"], metrics["avg"], metrics["max"])
        report += u"\nМинимальное расстояние между любой парой: {:.2f} м.".format(metrics["pair_min"])
        report += u"\nДля предварительного коэффициента использования принят консервативный шаг {:.2f} м.".format(
            data["vertical_spacing"])
    if data.get("horizontal_enabled", False):
        report += u"\nФактическая длина соединительного электрода: {:.2f} м.".format(data["horizontal_length"])
        report += u"\nКонтур: {}.".format(u"замкнутый" if close_loop else u"разомкнутый")
    report += (u"\nПосле перемещения стержней соединительная полоса перестраивается по их текущим координатам. "
               u"Точный полевой расчет взаимного влияния электродов остается следующим этапом разработки.")

    report_tables = build_calculation_tables(data, result, single=False, title=u"РАСЧЕТ ЗУ ПО ФАКТИЧЕСКОЙ BIM-ГЕОМЕТРИИ")
    bim_rows = [
        [u"Группа ЗУ", u"ID", group_id, u""],
        [u"Вертикальные электроды", u"n", u"{} шт.".format(len(group["points"])), u"Фактическая BIM-геометрия"],
    ]
    if len(group["points"]) > 1:
        bim_rows.extend([
            [u"Шаг соседних электродов", u"a min/avg/max", u"{:.2f} / {:.2f} / {:.2f} м".format(metrics["min"], metrics["avg"], metrics["max"]), u""],
            [u"Минимум между любой парой", u"amin", u"{:.2f} м".format(metrics["pair_min"]), u"Принят для предварительной оценки η"],
        ])
    if data.get("horizontal_enabled", False):
        bim_rows.append([u"Соединительный электрод", u"lфакт", u"{:.2f} м".format(data["horizontal_length"]), u"Замкнутый" if close_loop else u"Разомкнутый"])
    report_tables["tables"].append({
        "title": u"7. BIM-ГЕОМЕТРИЯ",
        "columns": [u"Параметр", u"Обозначение", u"Значение", u"Примечание"],
        "rows": bim_rows,
    })

    try:
        trace_mark("MODEL_SYNC_START")
        sync_group_after_rod_moves(doc, DB, group, data)
        trace_mark("MODEL_SYNC_END")
    except Exception as ex:
        trace_exception("MODEL_SYNC_ERROR", ex)
        show_message(u"Расчет выполнен, но не удалось перестроить полосу/метаданные:\n\n{}".format(ex),
                    title=u"ЭОМ — Обновить ЗУ", warn_icon=True)
        return

    try:
        trace_mark("PANEL_LINK_START")
        link_panel_to_grounding(doc, DB, data, group_id)
        trace_mark("PANEL_LINK_END")
    except Exception as ex:
        trace_exception("PANEL_LINK_ERROR", ex)

    try:
        trace_mark("VIEW_START")
        view = create_or_update_group_view(doc, uidoc, DB, report, group_id, report_tables=report_tables, activate=False)
        view_name = view.Name
        trace_mark("VIEW_END")
    except Exception as ex:
        trace_exception("VIEW_ERROR", ex)
        view_name = u"не обновлен: {}".format(ex)


    errors = [c for c in result.get("checks", []) if c.get("severity") == "ERROR" and not c.get("passed")]
    warnings = [c for c in result.get("checks", []) if c.get("severity") == "WARNING"]
    summary = (u"Группа: {}\nRрасч = {:.2f} Ом\nЭлектродов: {}\n"
               u"Полоса: {:.2f} м\nОшибок: {}\nПредупреждений: {}\nРасчетный вид: {}"
               .format(group_id[:8], result["total_r"], len(group["points"]),
                       data.get("horizontal_length", 0.0), len(errors), len(warnings), view_name))
    trace_mark("DONE", u"R={:.2f}; errors={}; warnings={}".format(result["total_r"], len(errors), len(warnings)))
    # Успешное обновление завершается без модального окна.
    # Итоговые значения находятся в обновленном расчетном виде.


if __name__ == "__main__":
    try:
        main()
    finally:
        trace_mark("SCRIPT_RETURN")
