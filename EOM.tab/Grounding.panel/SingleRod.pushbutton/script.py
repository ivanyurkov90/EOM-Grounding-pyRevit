# -*- coding: utf-8 -*-
from __future__ import print_function
import os
import sys

__title__ = u"Модульный\nэлектрод"
__author__ = u"ЭОМ / OpenAI"
__doc__ = u"Расчет одиночного модульного заземлителя для частного дома."
__min_revit_ver__ = 2023

THIS_DIR = os.path.dirname(__file__)
EXT_ROOT = os.path.abspath(os.path.join(THIS_DIR, os.pardir, os.pardir, os.pardir))
LIB_DIR = os.path.join(EXT_ROOT, "lib")
if LIB_DIR not in sys.path:
    sys.path.append(LIB_DIR)

from pyrevit import revit, DB, UI
from single_rod_ui import show_single_rod_form
from eom_ui_style import show_message
from grounding_core import (calculate_single_rod, format_single_rod_report,
                            optimize_single_rod_length, number_value)
from revit_grounding_model import pick_single_rod_placement, create_grounding_model
from revit_output import create_calculation_view, create_or_update_group_view
from calculation_tables import build_calculation_tables
from project_context import resolve_project_context
from panel_context import (list_gzsh_panels, show_gzsh_panel, refresh_panel_info,
                           link_panel_to_grounding)
from perf_trace import reset as trace_reset, mark as trace_mark, mark_exception as trace_exception


def main():
    if revit.doc is None:
        show_message(u"Нет открытого проекта Revit.", warn_icon=True)
        return

    defaults = resolve_project_context(revit.doc, DB)
    current_panel = refresh_panel_info(revit.doc, DB, defaults)
    if current_panel:
        defaults.update(current_panel)

    panel_options = list_gzsh_panels(revit.doc, DB)

    def _show_panel(panel_data):
        if show_gzsh_panel(revit.uidoc, revit.doc, DB, panel_data):
            return refresh_panel_info(revit.doc, DB, panel_data) or panel_data
        return None

    data = show_single_rod_form(defaults, panel_options=panel_options, panel_shower=_show_panel)
    if not data:
        return

    trace_reset("SingleRod")
    trace_mark("FORM_OK")

    try:
        trace_mark("CALC_START")
        optimize_note = u""
        if data.get("auto_single_length"):
            best, base_result = optimize_single_rod_length(data)
            if best is not None:
                data, result = best
                optimize_note = (u"\n\nАВТОПОДБОР ДЛИНЫ\nПринято {} секц. × {:.2f} м = {:.2f} м; "
                                 u"R={:.2f} Ом.".format(
                                     result.get("selected_modules", 0),
                                     result.get("module_step_m", 0.0),
                                     number_value(data.get("vertical_length")), result["total_r"]))
            else:
                result = base_result
                optimize_note = (u"\n\nАВТОПОДБОР ДЛИНЫ\nВариант не найден: критерий не определен "
                                 u"или требуемое R не достигнуто до заданной максимальной длины.")
        else:
            result = calculate_single_rod(data)
        report = format_single_rod_report(data, result) + optimize_note
        report_tables = build_calculation_tables(data, result, single=True)
        trace_mark("CALC_END")
    except Exception as ex:
        trace_exception("CALC_ERROR", ex)
        show_message(u"Ошибка исходных данных или расчета:\n\n{}".format(ex),
                    title=u"ЭОМ — Модульный электрод", warn_icon=True)
        return

    # Full text report is no longer printed to pyRevit Output by default.

    model_info = None
    if data.get("create_model"):
        trace_mark("PICK_POINT_START")
        placement = pick_single_rod_placement(revit.uidoc, revit.doc, DB, UI, data)
        trace_mark("PICK_POINT_END")
        if placement is not None:
            point = placement.get("point")
            direction = placement.get("direction")
            guide_ids = list(placement.get("guide_unique_ids") or [])
            if guide_ids:
                data["guide_source_unique_ids"] = u"|".join(guide_ids)
                data["guide_source_count"] = len(guide_ids)
                data["placement_source"] = placement.get("source") or data.get("placement_source")
            try:
                trace_mark("MODEL_START")
                model_info = create_grounding_model(revit.doc, DB, data, [point], False, electrode_directions=([direction] if direction is not None else None))
                trace_mark("MODEL_END")
            except Exception as ex:
                trace_exception("MODEL_ERROR", ex, u"single rod")
                show_message(u"Расчет выполнен, но 3D-штырь не создан:\n\n{}".format(ex),
                            title=u"ЭОМ — Модульный электрод", warn_icon=True)

    try:
        trace_mark("PANEL_LINK_START")
        link_panel_to_grounding(revit.doc, DB, data, model_info.get("group_id") if model_info else None)
        trace_mark("PANEL_LINK_END")
    except Exception as ex:
        trace_exception("PANEL_LINK_ERROR", ex)

    if data.get("create_view"):
        try:
            trace_mark("VIEW_START")
            if model_info and model_info.get("group_id"):
                view = create_or_update_group_view(revit.doc, revit.uidoc, DB, report, model_info["group_id"], report_tables=report_tables, activate=False)
            else:
                view = create_calculation_view(revit.doc, revit.uidoc, DB, report, report_tables=report_tables, activate=False)
            trace_mark("VIEW_END")
        except Exception as ex:
            trace_exception("VIEW_ERROR", ex)
            show_message(u"Расчет выполнен, но расчетный вид не создан:\n\n{}".format(ex),
                        title=u"ЭОМ — Модульный электрод", warn_icon=True)

    errors = [c for c in result.get("checks", []) if c.get("severity") == "ERROR" and not c.get("passed")]
    warnings = [c for c in result.get("checks", []) if c.get("severity") == "WARNING"]
    text = u"R одиночного модульного электрода = {:.2f} Ом\nОшибок: {}\nПредупреждений: {}".format(
        result["total_r"], len(errors), len(warnings))
    if model_info:
        mode = model_info.get("rod_model_mode")
        if mode in ("FAMILY", "EZETEK90136"):
            text += u"\n3D-штырь: секции EZETEK 90136 по 1,50 м + муфты 90227 + нижний наконечник 90326."
        else:
            text += u"\n3D-штырь создан как DirectShape (режим совместимости)."
        for warning in model_info.get("warnings", []):
            text += u"\nПРЕДУПР.: {}".format(warning)
    text += u"\nРасчетный вид создан. Откройте его вручную в Диспетчере проекта." if data.get("create_view") and 'view' in locals() else u""
    # Успешное завершение не должно блокировать Revit модальным окном.
    # Итог уже записан в расчетный вид; трассировка остается в TEMP для диагностики.
    trace_mark("DONE", u"R={:.2f}; errors={}; warnings={}".format(
        result["total_r"], len(errors), len(warnings)))


if __name__ == "__main__":
    try:
        main()
    finally:
        trace_mark("SCRIPT_RETURN")
