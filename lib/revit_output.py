# -*- coding: utf-8 -*-
from __future__ import division, print_function

import math
from perf_trace import mark as trace_mark, mark_exception as trace_exception


def _drafting_type(doc, DB):
    for item in DB.FilteredElementCollector(doc).OfClass(DB.ViewFamilyType):
        if item.ViewFamily == DB.ViewFamily.Drafting:
            return item
    return None


def _text_type_id(doc, DB):
    text_type_id = doc.GetDefaultElementTypeId(DB.ElementTypeGroup.TextNoteType)
    if text_type_id != DB.ElementId.InvalidElementId:
        return text_type_id
    text_types = list(DB.FilteredElementCollector(doc).OfClass(DB.TextNoteType))
    if not text_types:
        raise Exception(u"В проекте отсутствуют типы текста TextNoteType.")
    return text_types[0].Id


def _find_drafting_view(doc, DB, name):
    for view in DB.FilteredElementCollector(doc).OfClass(DB.ViewDrafting):
        try:
            if view.Name == name:
                return view
        except Exception:
            pass
    return None


def _mm(value):
    return float(value) / 304.8


def _configure_text_type(text_type, DB, size_mm, bold=False):
    """Force stable annotation text parameters every time the report is generated."""
    try:
        p = text_type.get_Parameter(DB.BuiltInParameter.TEXT_SIZE)
        if p and not p.IsReadOnly:
            p.Set(_mm(size_mm))
    except Exception:
        pass
    try:
        pb = text_type.get_Parameter(DB.BuiltInParameter.TEXT_STYLE_BOLD)
        if pb and not pb.IsReadOnly:
            pb.Set(1 if bold else 0)
    except Exception:
        pass
    # Prevent project-specific condensed/wide text types from changing the visual scale.
    try:
        pw = text_type.get_Parameter(DB.BuiltInParameter.TEXT_WIDTH_SCALE)
        if pw and not pw.IsReadOnly:
            pw.Set(1.0)
    except Exception:
        pass
    # IMPORTANT: an opaque TextNote background is always white in Revit and hides
    # any colored FilledRegion below the note. Force transparent backgrounds for
    # all report text types so the pastel table fill remains visible.
    try:
        pbg = text_type.get_Parameter(DB.BuiltInParameter.TEXT_BACKGROUND)
        if pbg and not pbg.IsReadOnly:
            pbg.Set(int(DB.TextElementBackground.TBGR_TRANSPARENT))
    except Exception:
        try:
            pbg = text_type.get_Parameter(DB.BuiltInParameter.TEXT_BACKGROUND)
            if pbg and not pbg.IsReadOnly:
                pbg.Set(1)
        except Exception:
            pass
    try:
        pborder = text_type.get_Parameter(DB.BuiltInParameter.TEXT_BOX_VISIBILITY)
        if pborder and not pborder.IsReadOnly:
            pborder.Set(0)
    except Exception:
        pass
    return text_type.Id


def _ensure_text_type(doc, DB, name, size_mm, bold=False):
    existing = None
    for t in DB.FilteredElementCollector(doc).OfClass(DB.TextNoteType):
        try:
            if t.Name == name:
                existing = t
                break
        except Exception:
            pass
    if existing is not None:
        return _configure_text_type(existing, DB, size_mm, bold)

    base = doc.GetElement(_text_type_id(doc, DB))
    try:
        new_type = base.Duplicate(name)
        return _configure_text_type(new_type, DB, size_mm, bold)
    except Exception:
        # Last-resort fallback: use the base type, but still enforce the requested size.
        return _configure_text_type(base, DB, size_mm, bold)


def _create_text(doc, view, DB, x, y, width, text, type_id):
    text = u"" if text is None else u"{}".format(text)
    point = DB.XYZ(x, y, 0)
    try:
        options = DB.TextNoteOptions(type_id)
        options.HorizontalAlignment = DB.HorizontalTextAlignment.Left
        return DB.TextNote.Create(doc, view.Id, point, max(_mm(8), width), text, options)
    except Exception:
        try:
            return DB.TextNote.Create(doc, view.Id, point, text, type_id)
        except Exception:
            return None


def _line(doc, view, DB, x1, y1, x2, y2):
    if abs(x1 - x2) < 1e-9 and abs(y1 - y2) < 1e-9:
        return None
    curve = DB.Line.CreateBound(DB.XYZ(x1, y1, 0), DB.XYZ(x2, y2, 0))
    return doc.Create.NewDetailCurve(view, curve)


def _solid_fill_pattern_id(doc, DB):
    """Prefer Revit's drafting Solid Fill; keep a solid fallback for unusual templates."""
    fallback = DB.ElementId.InvalidElementId
    try:
        for item in DB.FilteredElementCollector(doc).OfClass(DB.FillPatternElement):
            try:
                pattern = item.GetFillPattern()
                if pattern is None or not pattern.IsSolidFill:
                    continue
                if fallback == DB.ElementId.InvalidElementId:
                    fallback = item.Id
                try:
                    if pattern.Target == DB.FillPatternTarget.Drafting:
                        return item.Id
                except Exception:
                    pass
            except Exception:
                pass
    except Exception:
        pass
    return fallback


def _configure_filled_region_type(region_type, DB, solid_id, rgb):
    """Configure an ordinary colored FilledRegion (not a white masking region)."""
    color = DB.Color(int(rgb[0]), int(rgb[1]), int(rgb[2]))
    # In a project document a non-masking FilledRegion may use Solid Fill.
    # Keeping IsMasking=False avoids the white-mask behaviour that can make the
    # region look uncolored in drafting views.
    try:
        region_type.IsMasking = False
    except Exception:
        pass
    region_type.ForegroundPatternId = solid_id
    region_type.ForegroundPatternColor = color
    # The foreground Solid Fill is sufficient; clear the background layer so it
    # cannot override the intended pastel color in project templates.
    try:
        region_type.BackgroundPatternId = DB.ElementId.InvalidElementId
    except Exception:
        pass
    return region_type.Id


def _ensure_filled_region_type(doc, DB, name, rgb):
    """Return a reusable, explicitly reconfigured pastel FilledRegionType."""
    try:
        solid_id = _solid_fill_pattern_id(doc, DB)
        if solid_id == DB.ElementId.InvalidElementId:
            return None

        existing = None
        for item in DB.FilteredElementCollector(doc).OfClass(DB.FilledRegionType):
            try:
                if item.Name == name:
                    existing = item
                    break
            except Exception:
                pass
        if existing is not None:
            return _configure_filled_region_type(existing, DB, solid_id, rgb)

        base = None
        for item in DB.FilteredElementCollector(doc).OfClass(DB.FilledRegionType):
            base = item
            break
        if base is None:
            return None
        new_type = base.Duplicate(name)
        return _configure_filled_region_type(new_type, DB, solid_id, rgb)
    except Exception:
        return None


def _create_fill_rect(doc, view, DB, x0, y_top, width, height, type_id):
    if type_id is None or width <= 0 or height <= 0:
        return None
    try:
        loop = DB.CurveLoop()
        p1 = DB.XYZ(x0, y_top, 0)
        p2 = DB.XYZ(x0 + width, y_top, 0)
        p3 = DB.XYZ(x0 + width, y_top - height, 0)
        p4 = DB.XYZ(x0, y_top - height, 0)
        loop.Append(DB.Line.CreateBound(p1, p2))
        loop.Append(DB.Line.CreateBound(p2, p3))
        loop.Append(DB.Line.CreateBound(p3, p4))
        loop.Append(DB.Line.CreateBound(p4, p1))
        try:
            from System.Collections.Generic import List
            loops = List[DB.CurveLoop]()
            loops.Add(loop)
        except Exception:
            loops = [loop]
        # Fill is intentionally created BEFORE text and grid lines. This keeps
        # it visually behind later-created annotations without expensive
        # per-element draw-order operations.
        return DB.FilledRegion.Create(doc, type_id, view.Id, loops)
    except Exception:
        return None


def _row_fill_key(table, row, emphasis=False):
    """Choose a semantic pastel fill from explicit status/result cells only."""
    title = u"{}".format(table.get("title", u"")).upper()
    cells = [u"{}".format(x).strip().upper() for x in (row or [])]
    first = cells[0] if cells else u""
    second = cells[1] if len(cells) > 1 else u""
    last = cells[-1] if cells else u""

    # 4.2: the first column is the explicit check status.
    if u"4.2." in title:
        if first in (u"ОШИБКА", u"ERROR"):
            return "error"
        if first.startswith(u"ПРЕДУПР"):
            return "warning"
        if first == u"ОК":
            return "ok"

    # Summary/final rows contain an explicit semantic status in the value/status cells.
    status_text = u"{} | {}".format(second, last)
    if u"НЕ СООТВЕТСТВУЕТ" in status_text or u"ЕСТЬ ОШИБКИ" in status_text:
        return "error"
    if u"С ПРЕДУПРЕЖДЕНИЯМИ" in status_text:
        return "warning"
    if (u"СООТВЕТСТВУЕТ" in status_text or u"ПРОВЕРКИ ПРОЙДЕНЫ" in status_text or
            last == u"ПРИНЯТО"):
        return "ok"

    if table.get("result_table"):
        return "result"
    if emphasis:
        return "accent"
    if u"5. ПРИНЯТЫЕ РЕШЕНИЯ" in title:
        return "decision"
    return None


def _fallback_row_height_fast(row, col_widths, header=False, emphasis=False, text_size_mm=2.0):
    """Fast conservative row height for the requested report text size."""
    max_lines = 1
    for idx, cell in enumerate(row or []):
        if idx >= len(col_widths):
            continue
        text = u"" if cell is None else u"{}".format(cell)
        width_mm = max(12.0, col_widths[idx] * 304.8 - 5.0)
        # Estimate line capacity from the actual report text size.
        glyph_mm = max(1.15, float(text_size_mm) * 0.675)
        chars_per_line = max(7, int(width_mm / glyph_mm))
        lines = 0
        for part in text.split(u"\n"):
            part_len = max(1, len(part))
            lines += max(1, int(math.ceil(part_len / float(chars_per_line))))
        max_lines = max(max_lines, lines)
    line_pitch = max(4.4, float(text_size_mm) * 2.2)
    base = max(5.0, float(text_size_mm) * 2.0) + max_lines * line_pitch
    if header:
        base = max(base, 9.5)
    if emphasis:
        base = max(base, 10.5)
    return _mm(base)


def _is_emphasis_row(row):
    if not row:
        return False
    first = u"{}".format(row[0]).upper()
    keys = (
        u"ИТОГОВОЕ СОПРОТИВЛЕНИЕ",
        u"ПРИНЯТОЕ СЕЧЕНИЕ",
        u"ИТОГОВЫЙ ПРИМЕНИМЫЙ ПРЕДЕЛ",
        u"ИТОГ ПРОВЕРКИ",
    )
    return any(key in first for key in keys)


def _draw_data_row(doc, view, DB, x0, y_top, row, col_widths, type_id,
                   header=False, top_pad_mm=2.0, side_pad_mm=2.0, emphasis=False, fill_type_id=None,
                   text_size_mm=2.0):
    """Draw one table row without forcing a Revit document regeneration.

    Previous versions created TextNotes, called ``doc.Regenerate()`` for every
    row, measured their bounding boxes, and only then created the row geometry.
    On real projects this meant dozens of full document regenerations and could
    make Revit appear frozen when the calculation view was opened.

    v0.10.6 uses a deliberately conservative deterministic height estimate.
    The report text type has a fixed 2.0 mm size and width factor 1.0, so a
    slightly generous estimate is stable across templates and eliminates the
    expensive row-by-row regeneration completely.
    """
    total_w = sum(col_widths)

    # Calculate row geometry FIRST. The estimator intentionally over-allocates
    # vertical space rather than risking overlap. No Revit regeneration is
    # required to determine the row bounds.
    h = _fallback_row_height_fast(row, col_widths, header=header, emphasis=emphasis,
                                  text_size_mm=text_size_mm)
    y_bottom = y_top - h

    # Creation order matters: fill -> text -> grid. Later elements display over
    # earlier elements, so no DetailElementOrderUtils calls are needed.
    _create_fill_rect(doc, view, DB, x0, y_top, total_w, h, fill_type_id)

    x = x0
    for i, width in enumerate(col_widths):
        cell = row[i] if i < len(row) else u""
        _create_text(
            doc, view, DB,
            x + _mm(side_pad_mm),
            y_top - _mm(top_pad_mm),
            max(_mm(8), width - _mm(side_pad_mm * 2.0)),
            cell,
            type_id)
        x += width

    # Grid lines are created last and therefore remain crisp above fills.
    _line(doc, view, DB, x0, y_top, x0 + total_w, y_top)
    _line(doc, view, DB, x0, y_bottom, x0 + total_w, y_bottom)
    x = x0
    _line(doc, view, DB, x, y_top, x, y_bottom)
    for width in col_widths:
        x += width
        _line(doc, view, DB, x, y_top, x, y_bottom)
    if emphasis:
        inset = _mm(0.7)
        _line(doc, view, DB, x0, y_top - inset, x0 + total_w, y_top - inset)
        _line(doc, view, DB, x0, y_bottom + inset, x0 + total_w, y_bottom + inset)
    return y_bottom


def _draw_table(doc, view, DB, x0, y_top, table, type_body, type_header, type_section, type_result, fill_types,
                text_size_mm=2.0, section_height_mm=9.0):
    columns = table.get("columns") or []
    widths_mm = table.get("col_widths_mm") or []
    if not widths_mm and len(columns) == 4:
        widths_mm = [92.0, 92.0, 90.0, 106.0]
    elif not widths_mm or len(widths_mm) != len(columns):
        count = max(1, len(columns))
        widths_mm = [380.0 / float(count)] * count
    col_widths = [_mm(v) for v in widths_mm]
    total_w = sum(col_widths)

    h_title = _mm(section_height_mm)
    section_fill_key = table.get("section_fill_key") or "section"
    _create_fill_rect(doc, view, DB, x0, y_top, total_w, h_title, fill_types.get(section_fill_key))
    _line(doc, view, DB, x0, y_top, x0 + total_w, y_top)
    _line(doc, view, DB, x0, y_top - h_title, x0 + total_w, y_top - h_title)
    _line(doc, view, DB, x0, y_top, x0, y_top - h_title)
    _line(doc, view, DB, x0 + total_w, y_top, x0 + total_w, y_top - h_title)
    _create_text(doc, view, DB, x0 + _mm(2.5), y_top - _mm(2.1), total_w - _mm(5), table.get("title", u""), type_section)
    y = y_top - h_title

    y = _draw_data_row(doc, view, DB, x0, y, columns, col_widths, type_header, header=True,
                       fill_type_id=fill_types.get("header"), text_size_mm=text_size_mm)

    result_table = bool(table.get("result_table"))
    for row in table.get("rows") or []:
        emphasis = result_table or _is_emphasis_row(row)
        row_type = type_result if emphasis else type_body
        fill_key = _row_fill_key(table, row, emphasis)
        y = _draw_data_row(doc, view, DB, x0, y, row, col_widths, row_type, header=False, emphasis=emphasis,
                           fill_type_id=fill_types.get(fill_key) if fill_key else None,
                           text_size_mm=text_size_mm)
    return y, total_w

def _render_tables(doc, view, DB, report_tables):
    trace_mark("TABLE_RENDER_START")
    try:
        view.Scale = 1
    except Exception:
        pass

    readable = (report_tables.get("sheet_profile") == u"readable")
    if readable:
        body_mm = 2.5
        header_mm = 2.5
        section_mm = 2.8
        title_mm = 4.2
        sub_mm = 2.3
        result_mm = 2.6
        section_height_mm = 11.0
        table_gap_mm = 10.0
        title_gap_mm = 9.0
    else:
        body_mm = 2.0
        section_height_mm = 9.0
        table_gap_mm = 7.0
        title_gap_mm = 8.0

    if readable:
        type_body = _ensure_text_type(doc, DB, u"ЭОМ_Лист_Текст_2.5_r30", body_mm, False)
        type_header = _ensure_text_type(doc, DB, u"ЭОМ_Лист_Колонки_2.5_Ж_r30", header_mm, True)
        type_section = _ensure_text_type(doc, DB, u"ЭОМ_Лист_Раздел_2.8_Ж_r30", section_mm, True)
        type_title = _ensure_text_type(doc, DB, u"ЭОМ_Лист_Заголовок_4.2_Ж_r30", title_mm, True)
        type_sub = _ensure_text_type(doc, DB, u"ЭОМ_Лист_Подзаголовок_2.3_r30", sub_mm, False)
        type_result = _ensure_text_type(doc, DB, u"ЭОМ_Лист_Итог_2.6_Ж_r30", result_mm, True)
    else:
        # Preserve the established report text types for ordinary calculation views.
        type_body = _ensure_text_type(doc, DB, u"ЭОМ_Таблица_2.0_v106", 2.0, False)
        type_header = _ensure_text_type(doc, DB, u"ЭОМ_Таблица_2.0_Ж_v106", 2.0, True)
        type_section = _ensure_text_type(doc, DB, u"ЭОМ_Таблица_2.0_Раздел_v106", 2.0, True)
        type_title = _ensure_text_type(doc, DB, u"ЭОМ_Заголовок_3.5_Ж_v106", 3.5, True)
        type_sub = _ensure_text_type(doc, DB, u"ЭОМ_Подзаголовок_2.0_v106", 2.0, False)
        type_result = _ensure_text_type(doc, DB, u"ЭОМ_Итог_2.0_Ж_v106", 2.0, True)

    fill_types = {
        "section": _ensure_filled_region_type(doc, DB, u"ЭОМ_Заливка_Заголовок_v106", (214, 228, 244)),
        "section_input": _ensure_filled_region_type(doc, DB, u"ЭОМ_Лист_Исходные_r30", (221, 232, 244)),
        "section_calc": _ensure_filled_region_type(doc, DB, u"ЭОМ_Лист_Расчет_r30", (232, 236, 241)),
        "section_result": _ensure_filled_region_type(doc, DB, u"ЭОМ_Лист_Итоги_r30", (216, 235, 222)),
        "header": _ensure_filled_region_type(doc, DB, u"ЭОМ_Заливка_Колонки_v106", (232, 236, 241)),
        "result": _ensure_filled_region_type(doc, DB, u"ЭОМ_Заливка_Результат_v106", (211, 225, 248)),
        "accent": _ensure_filled_region_type(doc, DB, u"ЭОМ_Заливка_Итог_v106", (211, 225, 248)),
        "decision": _ensure_filled_region_type(doc, DB, u"ЭОМ_Заливка_Решение_v106", (222, 232, 246)),
        "ok": _ensure_filled_region_type(doc, DB, u"ЭОМ_Заливка_OK_v106", (211, 238, 218)),
        "warning": _ensure_filled_region_type(doc, DB, u"ЭОМ_Заливка_Предупреждение_v106", (250, 235, 190)),
        "error": _ensure_filled_region_type(doc, DB, u"ЭОМ_Заливка_Ошибка_v106", (247, 211, 207)),
    }

    x0 = 0.0
    y = 0.0
    table_widths = []
    for table in report_tables.get("tables") or []:
        try:
            table_widths.append(sum(float(x) for x in (table.get("col_widths_mm") or [])))
        except Exception:
            pass
    total_w = _mm(max(table_widths or [0.0]) or 380.0)
    _create_text(doc, view, DB, x0, y, total_w, report_tables.get("title", u"РАСЧЕТ ЗУ"), type_title)
    y -= _mm(title_gap_mm)
    if report_tables.get("subtitle"):
        _create_text(doc, view, DB, x0, y, total_w, report_tables.get("subtitle"), type_sub)
        y -= _mm(8.0)
    _line(doc, view, DB, x0, y, x0 + total_w, y)
    y -= _mm(6.0 if readable else 5.0)

    for table in report_tables.get("tables") or []:
        y, table_w = _draw_table(doc, view, DB, x0, y, table, type_body, type_header, type_section, type_result, fill_types,
                                 text_size_mm=body_mm, section_height_mm=section_height_mm)
        total_w = max(total_w, table_w)
        y -= _mm(table_gap_mm)

    note = (u"Примечание: расчетный вид является инженерным расчетом. Фактическое сопротивление ЗУ "
            u"подтверждается приемо-сдаточными измерениями после монтажа.")
    _create_text(doc, view, DB, x0, y, total_w, note, type_body)
    trace_mark("TABLE_RENDER_END")

def _clear_generated_view(doc, view, DB):
    ids = []
    try:
        for item in DB.FilteredElementCollector(doc, view.Id).OfClass(DB.TextNote):
            ids.append(item.Id)
    except Exception:
        pass
    try:
        for item in DB.FilteredElementCollector(doc, view.Id).OfClass(DB.CurveElement):
            ids.append(item.Id)
    except Exception:
        pass
    try:
        for item in DB.FilteredElementCollector(doc, view.Id).OfClass(DB.FilledRegion):
            ids.append(item.Id)
    except Exception:
        pass
    if ids:
        try:
            from System.Collections.Generic import List
            batch = List[DB.ElementId]()
            for eid in ids:
                batch.Add(eid)
            doc.Delete(batch)
            return
        except Exception as ex:
            trace_exception(
                "VIEW_CLEANUP_BATCH_FAIL", ex,
                u"view_id={}; elements={}".format(getattr(view, "Id", u""), len(ids)))
    # Fallback for unusual API/runtime combinations.
    failed = 0
    last_error = None
    for eid in ids:
        try:
            doc.Delete(eid)
        except Exception as ex:
            failed += 1
            last_error = ex

    if failed:
        trace_exception(
            "VIEW_CLEANUP_FALLBACK_FAIL", last_error,
            u"view_id={}; failed={}; total={}".format(
                getattr(view, "Id", u""), failed, len(ids)))

def _write_content(doc, view, DB, report_text, report_tables=None):
    if report_tables:
        _render_tables(doc, view, DB, report_tables)
    else:
        DB.TextNote.Create(doc, view.Id, DB.XYZ(0, 0, 0), report_text, _text_type_id(doc, DB))


def create_calculation_view(doc, uidoc, DB, report_text, base_name=u"ЭОМ_Расчет ЗУ", report_tables=None, activate=False):
    trace_mark("CREATE_VIEW_FUNC_START")
    drafting_type = _drafting_type(doc, DB)
    if drafting_type is None:
        raise Exception(u"В проекте отсутствует тип вида 'Чертежный вид / Drafting'.")

    existing_names = set()
    for view in DB.FilteredElementCollector(doc).OfClass(DB.ViewDrafting):
        try:
            existing_names.add(view.Name)
        except Exception:
            pass

    name = base_name
    idx = 2
    while name in existing_names:
        name = u"{} ({})".format(base_name, idx)
        idx += 1

    t = DB.Transaction(doc, u"ЭОМ: создать расчет ЗУ")
    t.Start()
    try:
        view = DB.ViewDrafting.Create(doc, drafting_type.Id)
        view.Name = name
        _write_content(doc, view, DB, report_text, report_tables)
        t.Commit()
    except Exception:
        t.RollBack()
        raise

    if activate:
        try:
            uidoc.ActiveView = view
        except Exception:
            pass
    return view

def create_or_update_named_calculation_view(doc, uidoc, DB, report_text, exact_name,
                                              report_tables=None,
                                              ownership_validator=None,
                                              ownership_writer=None,
                                              existing_view=None,
                                              activate=False):
    """Create/update an exact-name generated Drafting View.

    Optional ownership callbacks let the documentation workflow validate an
    existing view before clearing it and persist its canonical marker in the
    same transaction as the rendered content.
    """
    drafting_type = _drafting_type(doc, DB)
    if drafting_type is None:
        raise Exception(u"В проекте отсутствует тип вида 'Чертежный вид / Drafting'.")

    view = existing_view if existing_view is not None else _find_drafting_view(doc, DB, exact_name)

    t = DB.Transaction(doc, u"ЭОМ: расчетный вид для листа ЗУ")
    t.Start()
    try:
        if view is None:
            view = DB.ViewDrafting.Create(doc, drafting_type.Id)
            view.Name = exact_name
        else:
            if ownership_validator is not None:
                ownership_validator(view)
            view.Name = exact_name
            _clear_generated_view(doc, view, DB)
        try:
            view.ViewTemplateId = DB.ElementId.InvalidElementId
        except Exception as ex:
            raise Exception(
                u"Не удалось снять шаблон с расчётного вида «{}»: {}"
                .format(exact_name, ex))
        if ownership_writer is not None:
            ownership_writer(view)
        _write_content(doc, view, DB, report_text, report_tables)
        t.Commit()
    except Exception:
        t.RollBack()
        raise

    if activate:
        try:
            uidoc.ActiveView = view
        except Exception:
            pass
    return view

def create_or_update_group_view(doc, uidoc, DB, report_text, group_id, report_tables=None, activate=False):
    trace_mark("UPDATE_VIEW_FUNC_START")
    """Создает или обновляет выделенный расчетный вид конкретной группы ЗУ."""
    short_id = (group_id or "NOID")[:8]
    name = u"ЭОМ_Расчет ЗУ_{}".format(short_id)
    view = _find_drafting_view(doc, DB, name)
    drafting_type = _drafting_type(doc, DB)
    if drafting_type is None:
        raise Exception(u"В проекте отсутствует тип вида 'Чертежный вид / Drafting'.")

    t = DB.Transaction(doc, u"ЭОМ: обновить расчет ЗУ")
    t.Start()
    try:
        if view is None:
            view = DB.ViewDrafting.Create(doc, drafting_type.Id)
            view.Name = name
        else:
            _clear_generated_view(doc, view, DB)
        _write_content(doc, view, DB, report_text, report_tables)
        t.Commit()
    except Exception:
        t.RollBack()
        raise

    if activate:
        try:
            uidoc.ActiveView = view
        except Exception:
            pass
    return view


def activate_calculation_view(uidoc, view):
    """Activate a completed calculation view as the LAST UI action of a command.

    Keeping view activation separate prevents ownerless modal dialogs and the pyRevit
    output window from blocking Revit after the view appears. No document work should
    be done after this call.
    """
    if uidoc is None or view is None:
        return False
    try:
        uidoc.ActiveView = view
        return True
    except Exception:
        return False
