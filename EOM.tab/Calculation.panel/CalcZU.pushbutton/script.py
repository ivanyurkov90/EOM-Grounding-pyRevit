# -*- coding: utf-8 -*-
from __future__ import print_function
import os
import sys

__title__ = u"Расчет\nЗУ"
__author__ = u"ЭОМ / OpenAI"
__doc__ = u"Расчет, нормативные проверки и создание 3D-модели заземляющего устройства."
__min_revit_ver__ = 2023

THIS_DIR = os.path.dirname(__file__)
EXT_ROOT = os.path.abspath(os.path.join(THIS_DIR, os.pardir, os.pardir, os.pardir))
LIB_DIR = os.path.join(EXT_ROOT, "lib")
if LIB_DIR not in sys.path:
    sys.path.append(LIB_DIR)

from pyrevit import revit, DB, UI
from eom_ui_style import show_message
from grounding_ui import show_input_form
from grounding_core import calculate, optimize, format_report
from revit_output import create_calculation_view, create_or_update_group_view
from calculation_tables import build_calculation_tables
from revit_grounding_model import pick_grounding_layout, route_length_m, create_grounding_model
from project_context import resolve_project_context
from panel_context import (list_gzsh_panels, show_gzsh_panel, refresh_panel_info,
                           link_panel_to_grounding)
from perf_trace import reset as trace_reset, mark as trace_mark, mark_exception as trace_exception



def _run_calculation(data):
    if data.get("auto_optimize"):
        best, base_result = optimize(data)
        if best is None:
            report_suffix = (u"\n\nАВТОПОДБОР\nПодбор не выполнен: автоматический проектный критерий "
                             u"не определен или вариант не найден в заданном диапазоне.")
            return data, base_result, report_suffix, False
        optimized_data, result = best
        report_suffix = (u"\n\nАВТОПОДБОР\nВыбран предварительный вариант по внутреннему эвристическому "
                         u"показателю v0.5. Перед выпуском РД требуется инженерная проверка.")
        return optimized_data, result, report_suffix, True
    return data, calculate(data), u"", False


def main():
    doc = revit.doc
    uidoc = revit.uidoc
    if doc is None or uidoc is None:
        show_message(u"Нет открытого проекта Revit.", warn_icon=True)
        return

    defaults = resolve_project_context(doc, DB)
    current_panel = refresh_panel_info(doc, DB, defaults)
    if current_panel:
        defaults.update(current_panel)

    panel_options = list_gzsh_panels(doc, DB)

    def _show_panel(panel_data):
        if show_gzsh_panel(uidoc, doc, DB, panel_data):
            return refresh_panel_info(doc, DB, panel_data) or panel_data
        return None

    data = show_input_form(defaults, panel_options=panel_options, panel_shower=_show_panel)
    if not data:
        return

    trace_reset("CalcZU")
    trace_mark("FORM_OK")

    try:
        trace_mark("CALC_START")
        data, result, suffix, optimized = _run_calculation(data)
        trace_mark("CALC_END")
    except Exception as ex:
        trace_exception("CALC_ERROR", ex)
        show_message(u"Ошибка исходных данных или расчета:\n\n{}".format(ex),
                    title=u"ЭОМ — Заземление", warn_icon=True)
        return

    model_info = None
    points = None
    strip_points = None
    electrode_directions = None
    route_nodes = []
    layout_info = None
    close_loop = False

    if data.get("create_model"):
        try:
            trace_mark("PICK_LAYOUT_START")
            layout_info = pick_grounding_layout(uidoc, doc, DB, UI, data)
            trace_mark("PICK_LAYOUT_END")
            if not layout_info:
                show_message(u"Размещение ЗУ отменено пользователем. Расчет не изменен.",
                            title=u"ЭОМ — Заземление")
            else:
                points = layout_info.get("electrode_points") or []
                strip_points = layout_info.get("strip_points") or points
                electrode_directions = layout_info.get("electrode_directions")
                route_nodes = layout_info.get("route_nodes") or []
                close_loop = bool(layout_info.get("close_loop"))
                if layout_info.get("mode") in (u"Контур по периметру", u"Незамкнутый контур"):
                    data["perimeter_max_spacing_m"] = float(
                        layout_info.get("spacing_avg_m") or data.get("vertical_spacing") or 0.0)
                open_design = layout_info.get("open_design") or {}
                data["open_contour_fixed_length"] = bool(
                    layout_info.get("open_contour_fixed_length", data.get("open_contour_fixed_length", False)))
                if open_design:
                    data["open_contour_available_length_m"] = float(open_design["available_length_m"])
                    data["open_contour_used_length_m"] = float(open_design["used_length_m"])
                    data["open_contour_target_r"] = float(open_design["target_r"])
                    data["open_contour_reserve_target_r"] = float(open_design["reserve_target_r"])
                    data["open_contour_design_r"] = float(open_design["result"]["total_r"])
                guide_ids = list(layout_info.get("guide_unique_ids") or [])
                if guide_ids:
                    data["guide_source_unique_ids"] = u"|".join(guide_ids)
                    data["guide_source_count"] = len(guide_ids)
                    data["placement_source"] = layout_info.get("placement_source") or data.get("placement_source")
                # После выбора фактической геометрии расчет использует реальные n,
                # признак замыкания, длину полосы и фактический средний шаг.
                data["vertical_count"] = len(points)
                data["close_loop"] = bool(close_loop)
                if len(points) > 1:
                    actual_spacing = float(layout_info.get("spacing_avg_m") or 0.0)
                    if actual_spacing <= 0.0:
                        electrode_route_length = route_length_m(points, close_loop)
                        segment_count = len(points) if close_loop else (len(points) - 1)
                        actual_spacing = electrode_route_length / float(segment_count) if segment_count > 0 else 0.0
                    if actual_spacing > 0.0:
                        data["vertical_spacing"] = actual_spacing
                if data.get("horizontal_enabled", True):
                    data["horizontal_length"] = route_length_m(strip_points, close_loop)
                result = calculate(data)
        except Exception as ex:
            show_message(u"Не удалось получить геометрию размещения:\n\n{}".format(ex),
                        title=u"ЭОМ — Заземление", warn_icon=True)
            return

    title = u"РАСЧЕТ ЗУ — ПРЕДВАРИТЕЛЬНО ПОДОБРАННЫЙ ВАРИАНТ" if optimized else None
    report = format_report(data, result, title)
    report += suffix

    if points:
        report += u"\n\nBIM-ГЕОМЕТРИЯ"
        report += u"\nВертикальных электродов в модели: {} шт.".format(len(points))
        if data.get("horizontal_enabled", True):
            report += u"\nФактическая длина соединительного электрода по трассе: {:.2f} м.".format(
                route_length_m(strip_points or points, close_loop))
        if layout_info and layout_info.get("mode") in (u"Контур по периметру", u"Незамкнутый контур"):
            report += u"\n{}: вершин трассы {}; фактический шаг электродов min/avg/max = {:.2f}/{:.2f}/{:.2f} м.".format(
                u"Замкнутый контур" if close_loop else u"Незамкнутый контур",
                len(strip_points or []), float(layout_info.get("spacing_min_m") or 0.0),
                float(layout_info.get("spacing_avg_m") or 0.0), float(layout_info.get("spacing_max_m") or 0.0))
            report += u"\nВертикальные электроды автоматически расставлены по трассе; вершины контура сохранены как топологические узлы."
            if layout_info.get("guide_unique_ids"):
                report += u"\nИсточник трассы: {} Model Lines Revit; после успешного построения направляющие удаляются, их идентификаторы сохраняются в метаданных ЗУ.".format(len(layout_info.get("guide_unique_ids") or []))
            open_design = layout_info.get("open_design") or {}
            if open_design:
                report += (u"\nАвтоподбор незамкнутого контура: из доступных {:.2f} м использовано {:.2f} м; "
                           u"электродов {}; расчетный шаг {:.2f} м; установленный предел R ≤ {:.2f} Ом; "
                           u"получено R = {:.2f} Ом."
                           .format(float(open_design["available_length_m"]),
                                   float(open_design["used_length_m"]),
                                   int(open_design["count"]),
                                   float(open_design["spacing_m"]),
                                   float(open_design["target_r"]),
                                   float(open_design["result"]["total_r"])))
                if not open_design.get("reserve_achieved"):
                    report += (u"\nПроектная цель с коэффициентом запаса R ≤ {:.2f} Ом этой минимальной длиной "
                               u"не обеспечивается. Если подбор должен выполняться именно по этому значению, "
                               u"задайте его как дополнительный проектный предел R."
                               .format(float(open_design["reserve_target_r"])))
            elif (layout_info.get("mode") == u"Незамкнутый контур" and
                  layout_info.get("open_contour_fixed_length")):
                report += (u"\nФиксированная длина незамкнутого контура: использована вся трасса "
                           u"Model Lines длиной {:.2f} м."
                           .format(route_length_m(strip_points or points, False)))
        report += u"\nДля η используется фактическое число электродов, тип контура и средний фактический шаг."

    report_tables = build_calculation_tables(data, result, single=False, title=(title or u"РАСЧЕТ ЗАЗЕМЛЯЮЩЕГО УСТРОЙСТВА"))
    if points:
        report_tables["tables"].append({
            "title": u"7. BIM-ГЕОМЕТРИЯ",
            "columns": [u"Параметр", u"Обозначение", u"Значение", u"Примечание"],
            "rows": [
                [u"Вертикальные электроды в модели", u"n", u"{} шт.".format(len(points)), u"Фактическая BIM-геометрия"],
                [u"Соединительный электрод", u"lфакт", u"{:.2f} м".format(route_length_m(strip_points or points, close_loop)) if data.get("horizontal_enabled", True) else u"Не используется", u"По выбранным точкам Revit"],
                [u"Тип контура", u"—", u"Замкнутый" if close_loop else u"Разомкнутый", u""],
                [u"Вершины трассы полосы", u"Nтр", u"{} шт.".format(len(strip_points or points)),
                 u"Топологические вершины трассы" if layout_info and layout_info.get("mode") in (u"Контур по периметру", u"Незамкнутый контур") else u""],
                [u"Источник размещения", u"—", u"{}".format((layout_info or {}).get("placement_source") or data.get("placement_source") or u"Вручную"),
                 u"{} направляющих Model Lines".format(len((layout_info or {}).get("guide_unique_ids") or [])) if (layout_info or {}).get("guide_unique_ids") else u""],
                [u"Фактический шаг электродов", u"aф",
                 u"{:.2f} / {:.2f} / {:.2f} м".format(float((layout_info or {}).get("spacing_min_m") or 0.0), float((layout_info or {}).get("spacing_avg_m") or data.get("vertical_spacing") or 0.0), float((layout_info or {}).get("spacing_max_m") or 0.0)),
                 u"min / avg / max"],
                [u"Использованная длина незамкнутой трассы", u"lподб",
                 (u"{:.2f} из {:.2f} м".format(float(((layout_info or {}).get("open_design") or {}).get("used_length_m") or 0.0),
                                                float(((layout_info or {}).get("open_design") or {}).get("available_length_m") or 0.0))
                  if (layout_info or {}).get("open_design") else u"—"),
                 u"Минимальная длина по установленному пределу R и табличному η ряда" if (layout_info or {}).get("open_design") else u""],
                [u"Фиксация длины незамкнутого контура", u"—",
                 (u"Да — вся длина Model Lines" if (layout_info or {}).get("open_contour_fixed_length") else
                  (u"Нет — минимальная расчетная длина" if (layout_info or {}).get("mode") == u"Незамкнутый контур" else u"—")),
                 u""],
            ]
        })


    errors = [c for c in result.get("checks", []) if c.get("severity") == "ERROR" and not c.get("passed")]
    warnings = [c for c in result.get("checks", []) if c.get("severity") == "WARNING"]
    summary = u"Rрасч = {:.2f} Ом\nОшибок: {}\nПредупреждений: {}".format(
        result["total_r"], len(errors), len(warnings))

    if points:
        try:
            trace_mark("MODEL_START")
            model_info = create_grounding_model(doc, DB, data, points, close_loop, route_points=strip_points, electrode_directions=electrode_directions, route_nodes=route_nodes)
            trace_mark("MODEL_END")
            summary += u"\nСоздано элементов модели: {}".format(len(model_info.get("element_ids") or []))
            summary += u"\nГруппа ЗУ: {}".format(model_info["group_id"][:8])
            if layout_info and layout_info.get("mode") in (u"Контур по периметру", u"Незамкнутый контур"):
                summary += u"\nТрасса: {:.2f} м; вертикальных электродов: {}; узлов трассы: {}.".format(
                    model_info.get("strip_length_m", 0.0), len(points), len(route_nodes))
                if model_info.get("deleted_guide_count"):
                    summary += u"\nУдалено направляющих Model Lines: {}.".format(model_info.get("deleted_guide_count"))
            mode = model_info.get("rod_model_mode")
            if mode in ("FAMILY", "EZETEK90136"):
                summary += u"\nВертикальные электроды: EZETEK 90136 + муфты 90227 + нижние наконечники 90326."
            elif mode == "DIRECTSHAPE":
                summary += u"\nВертикальные электроды: DirectShape (режим совместимости)."
            for warning in model_info.get("warnings", []):
                summary += u"\nПРЕДУПР.: {}".format(warning)
            bom = model_info.get("bom") or {}
            if bom:
                bom_rows = []
                for article in sorted(bom.keys()):
                    bom_rows.append([u"Соединительная/монтажная деталь", article, u"{} шт.".format(bom[article]), u"По топологии ЗУ"])
                if data.get("horizontal_enabled", True):
                    bom_rows.append([u"Полоса {}×{} мм".format(data.get("horizontal_width_mm"), data.get("horizontal_thickness_mm")),
                                     u"—", u"{:.2f} м".format(model_info.get("strip_length_m", 0.0)),
                                     u"Непрерывная трасса; повороты выполняются гибом без отдельного зажима"])
                report_tables["tables"].append({
                    "title": u"8. СПЕЦИФИКАЦИЯ BIM-КОНТУРА",
                    "columns": [u"Наименование", u"Артикул", u"Количество", u"Основание"],
                    "rows": bom_rows
                })
        except Exception as ex:
            trace_exception("MODEL_ERROR", ex, u"CalcZU")
            summary += u"\n\n3D-модель не создана: {}".format(ex)
            show_message(u"Расчет выполнен, но 3D-модель не создана:\n\n{}".format(ex),
                        title=u"ЭОМ — Расчет ЗУ", warn_icon=True)

    try:
        trace_mark("PANEL_LINK_START")
        link_panel_to_grounding(doc, DB, data, model_info.get("group_id") if model_info else None)
        trace_mark("PANEL_LINK_END")
    except Exception as ex:
        trace_exception("PANEL_LINK_ERROR", ex)

    if data.get("create_view"):
        try:
            trace_mark("VIEW_START")
            if model_info and model_info.get("group_id"):
                view = create_or_update_group_view(doc, uidoc, DB, report, model_info["group_id"], report_tables=report_tables, activate=False)
            else:
                view = create_calculation_view(doc, uidoc, DB, report, report_tables=report_tables, activate=False)
            summary += u"\nСоздан/обновлен вид: {}\nОткройте расчетный вид вручную в Диспетчере проекта.".format(view.Name)
            trace_mark("VIEW_END")
        except Exception as ex:
            trace_exception("VIEW_ERROR", ex)
            summary += u"\n\nВид не создан: {}".format(ex)
            show_message(u"Расчет выполнен, но расчетный вид не создан:\n\n{}".format(ex),
                        title=u"ЭОМ — Расчет ЗУ", warn_icon=True)

    # Успешное завершение — без модального окна. Итог находится в расчетном виде.
    trace_mark("DONE", u"R={:.2f}; errors={}; warnings={}".format(
        result["total_r"], len(errors), len(warnings)))


if __name__ == "__main__":
    main()
