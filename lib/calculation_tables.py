# -*- coding: utf-8 -*-
from __future__ import division, print_function

from plugin_version import display_version
from grounding_core import number_value

from grounding_topology import vertical_electrode_bom


def _s(value, default=u"—"):
    if value is None or value == "":
        return default
    return u"{}".format(value)


def _n(value, digits=2, default=u"—"):
    try:
        if value is None:
            return default
        return (u"{:.%df}" % digits).format(number_value(value))
    except Exception:
        return default


def _n1(value):
    return _n(value, 1)


def _n2(value):
    return _n(value, 2)


def _n3(value):
    return _n(value, 3)


def _row(a, b=u"", c=u"", d=u""):
    return [_s(a), _s(b), _s(c), _s(d)]


def _status(check):
    sev = check.get("severity")
    passed = check.get("passed")
    if sev == "ERROR" and not passed:
        return u"ОШИБКА"
    if sev == "WARNING":
        return u"ПРЕДУПР."
    return u"OK"


def _criteria_rows(result):
    rows = []
    criteria = result.get("criteria") or {}
    rcalc = result.get("total_r")
    for key, label in (("normative", u"Нормативный критерий"),
                       ("project", u"Проектный критерий"),
                       ("gas_tu", u"ТУ газоснабжения")):
        item = criteria.get(key) or {}
        if item.get("available"):
            max_r = item.get("max")
            status = u"OK" if rcalc is not None and rcalc <= max_r else u"НЕ ПРОХОДИТ"
            rows.append(_row(label,
                             u"Rрасч ≤ Rдоп",
                             u"{}; {:.2f} ≤ {:.2f} Ом".format(status, rcalc, max_r),
                             item.get("basis", u"")))
        else:
            rows.append(_row(label, u"—", u"Не применяется / не определен", item.get("basis", u"")))
    effective = criteria.get("effective") or result.get("criterion") or {}
    if effective.get("available"):
        rows.append(_row(u"ИТОГОВЫЙ ПРИМЕНИМЫЙ ПРЕДЕЛ",
                         u"Rрасч ≤ Rmax",
                         u"{:.2f} ≤ {:.2f} Ом".format(rcalc, effective.get("max")),
                         u"Проектная цель с запасом: {:.2f} Ом".format(effective.get("target"))))
    else:
        rows.append(_row(u"ИТОГОВЫЙ ПРИМЕНИМЫЙ ПРЕДЕЛ", u"—", u"Не определен", effective.get("basis", u"")))
    return rows


def _conductor_rows(data, result):
    c = result.get("grounding_conductor") or {}
    rows = []
    if not c.get("enabled"):
        return [_row(u"Расчет проводника", u"—", u"Отключен пользователем", u"")]

    ik = c.get("fault_current_a")
    t = c.get("disconnection_time_s")
    k = c.get("k_factor")
    if c.get("thermal_raw") is not None:
        rows.append(_row(
            u"Термическое сечение",
            u"S = Iк·√t / k",
            u"{:.1f}·√{:.3f}/{:.1f} = {:.2f} мм² → {:.1f} мм²".format(
                ik, t, k, c.get("thermal_raw"), c.get("thermal_standard")),
            c.get("disconnection_time_source", u"")))
    else:
        rows.append(_row(u"Термическое сечение", u"S = Iк·√t / k", u"Не рассчитано", u"Не хватает Iк, t или k"))

    rows.append(_row(
        u"Нормативный минимум",
        u"S ≥ Smin",
        u"Smin = {:.1f} мм² {}".format(c.get("norm_min", 0.0), c.get("material", u"")),
        c.get("norm_basis", u"")))

    if c.get("input_required_standard") is not None:
        same_mat = c.get("incoming_phase_material") == c.get("material")
        formula = u"табл. 54.2: S_PE = f(Sф)" if same_mat else u"S_PE = f(Sф)·k1/k2"
        rows.append(_row(
            u"Проверка по вводному проводнику",
            formula,
            u"{} {:.1f} мм² → ≥ {:.1f} мм² {}".format(
                c.get("incoming_phase_material", u""), c.get("incoming_phase_section", 0.0),
                c.get("input_required_standard", 0.0), c.get("material", u"")),
            c.get("input_basis", u"")))
    elif c.get("input_check_enabled"):
        rows.append(_row(u"Проверка по вводному проводнику", u"табл. 54.2", u"Не выполнена", u"Нет полного набора данных"))

    rows.append(_row(
        u"ПРИНЯТОЕ СЕЧЕНИЕ ЗУ–ГЗШ",
        u"Sпр = max(Sтерм; Smin; Sпо вводу)",
        u"{:.1f} мм² {}".format(c.get("final_section", 0.0), c.get("material", u"")),
        u"Определяющий критерий: {}".format(c.get("controlling_criterion", u""))))
    return rows


def _input_rows(data, result, single=False):
    c = result.get("grounding_conductor") or {}
    rows = []
    panel_name = data.get("gzsh_panel_display_name") or data.get("gzsh_panel_mark") or data.get("gzsh_panel_name")
    if panel_name:
        panel_note = []
        if data.get("gzsh_panel_level"):
            panel_note.append(u"Уровень: {}".format(data.get("gzsh_panel_level")))
        if data.get("gzsh_panel_room"):
            panel_note.append(u"Помещение: {}".format(data.get("gzsh_panel_room")))
        if data.get("gzsh_panel_element_id") is not None:
            panel_note.append(u"ElementId: {}".format(data.get("gzsh_panel_element_id")))
        rows.append(_row(u"Панель с ГЗШ", u"ГЗШ", panel_name, u"; ".join(panel_note)))
    else:
        rows.append(_row(u"Панель с ГЗШ", u"ГЗШ", u"Не выбрана", u"Требуется привязка к панели Revit"))
    rows.extend([
        _row(u"Тип объекта", u"—", data.get("project_object_type", u""), u"Профиль проекта"),
        _row(u"Система заземления", u"—", data.get("system", u""), u""),
        _row(u"Назначение ЗУ", u"—", data.get("purpose", u""), u""),
        _row(u"Тип ввода", u"—", data.get("supply_type", u""), u""),
    ])
    if data.get("project_address"):
        rows.append(_row(u"Адрес объекта", u"—", data.get("project_address"), data.get("location_data_source", u"")))
    if data.get("allocated_power_kw"):
        rows.append(_row(u"Выделенная мощность", u"Pвыд",
                         u"{} кВт".format(data.get("allocated_power_kw")), u"Профиль проекта"))
    if data.get("incoming_device_type"):
        rows.append(_row(u"Аппарат на вводе", u"—", data.get("incoming_device_type"),
                         u"Ограничивает вводную мощность"))
    if data.get("soil_type"):
        rows.append(_row(u"Тип грунта", u"—", data.get("soil_type"), u""))
    rows.extend([
        _row(u"Удельное сопротивление грунта", u"ρ", u"{} Ом·м".format(_n1(result.get("base_rho"))), u""),
        _row(u"Сезонный коэффициент", u"kсез", _n2(data.get("seasonal_factor", 1.0)), u""),
        _row(u"Автоматическая оценка промерзания", u"hпр,авт", u"{} м".format(_n2(data.get("frost_depth_auto"))), data.get("frost_depth_auto_source", u"")),
        _row(u"Температурный коэффициент", u"Mt", _n2(data.get("freezing_index_mt")), data.get("frost_depth_reason", u"")),
        _row(u"Принятая глубина промерзания", u"hпр", u"{} м".format(_n2(data.get("frost_depth"))), data.get("frost_depth_source", u"")),
        _row(u"Расчетное удельное сопротивление", u"ρрасч", u"{} Ом·м".format(_n1(result.get("design_rho"))), u""),
    ])

    if c:
        rows.append(_row(u"Вводной автомат", u"In / ВТХ",
                         u"{} А; {}".format(_n1(c.get("incoming_breaker_rating_a")), c.get("incoming_breaker_curve", u"—")),
                         c.get("incoming_breaker_standard", u"")))
        rows.append(_row(u"Вводной фазный проводник", u"Sф",
                         u"{} {:.1f} мм²".format(c.get("incoming_phase_material", u"—"), c.get("incoming_phase_section") or 0.0),
                         u"Длина: {} м".format(_n1(c.get("incoming_cable_length_m")))))
        rows.append(_row(u"Ток повреждения в точке расчета", u"Iк", u"{} А".format(_n1(c.get("fault_current_a"))), u""))
        rows.append(_row(u"Время отключения", u"t", u"{} с".format(_n3(c.get("disconnection_time_s"))), c.get("disconnection_time_source", u"")))
        rows.append(_row(u"Коэффициент материала проводника", u"k", _n1(c.get("k_factor")), u""))

    if single:
        rows.extend([
            _row(u"Материал электрода", u"—", data.get("electrode_material", u""), u""),
            _row(u"Длина стержня", u"L", u"{} м".format(_n2(data.get("vertical_length"))), u""),
            _row(u"Диаметр стержня", u"d", u"{} мм".format(_n1(data.get("vertical_diameter_mm"))), u""),
            _row(u"Глубина до верха", u"h", u"{} м".format(_n2(data.get("vertical_top_depth"))), u""),
            _row(u"Длина модульной секции", u"Lмод", u"{} м".format(_n2(data.get("rod_module_step"))), u""),
        ])
    else:
        rows.extend([
            _row(u"Вертикальные электроды", u"n / L / d",
                 u"{} шт.; {} м; {} мм".format(_s(data.get("vertical_count")), _n2(data.get("vertical_length")), _n1(data.get("vertical_diameter_mm"))),
                 u"Шаг: {} м; верх: {} м".format(_n2(data.get("vertical_spacing")), _n2(data.get("vertical_top_depth")))),
        ])
        if data.get("horizontal_enabled", True):
            rows.append(_row(u"Горизонтальный электрод", u"l / b / h",
                             u"{} м; {} мм; {} м".format(_n2(data.get("horizontal_length")), _n1(data.get("horizontal_width_mm")), _n2(data.get("horizontal_depth"))),
                             u"Толщина: {} мм".format(_n1(data.get("horizontal_thickness_mm", 4.0)))))
    return rows


def _zu_calc_rows(data, result, single=False):
    if single:
        L = number_value(data.get("vertical_length"))
        d = number_value(data.get("vertical_diameter_mm")) / 1000.0
        h = number_value(data.get("vertical_top_depth"))
        T = result.get("center_depth_m")
        rows = [
            _row(u"Расчетное сопротивление грунта", u"ρрасч = ρэкв·kсез,эфф",
                 u"{:.1f}·{:.3f} = {:.1f} Ом·м".format(result.get("natural_rho"), result.get("effective_seasonal_factor"), result.get("design_rho")),
                 u"Модель: {}".format(result.get("soil_model", u""))),
            _row(u"Глубина центра электрода", u"T = h + L/2",
                 u"{:.2f} + {:.2f}/2 = {:.2f} м".format(h, L, T), u""),
            _row(u"Сопротивление одиночного стержня",
                 u"Rв = ρ/(2πL)·[ln(2L/d)+0,5·ln((4T+L)/(4T−L))]",
                 u"Rв = {:.2f} Ом".format(result.get("single_vertical_r")),
                 u"ρрасч = {:.1f} Ом·м".format(result.get("design_rho"))),
            _row(u"Число модульных секций", u"n = L/Lмод",
                 u"{}".format(result.get("module_count") if result.get("module_count") is not None else u"L не кратна секции"),
                 u"Lмод = {:.2f} м".format(result.get("module_step_m", 0.0))),
            _row(u"ИТОГОВОЕ СОПРОТИВЛЕНИЕ ЗУ", u"RЗУ = Rв", u"{:.2f} Ом".format(result.get("total_r")), u"Одиночный стержень"),
        ]
        return rows

    L = number_value(data.get("vertical_length"))
    h = number_value(data.get("vertical_top_depth"))
    T = h + L / 2.0
    rows = [
        _row(u"Расчетное сопротивление грунта", u"ρрасч = ρ·kсез",
             u"{:.1f}·{:.2f} = {:.1f} Ом·м".format(result.get("base_rho"), number_value(data.get("seasonal_factor"), 1.0), result.get("design_rho")), u""),
        _row(u"Глубина центра вертикального электрода", u"T = h + L/2",
             u"{:.2f} + {:.2f}/2 = {:.2f} м".format(h, L, T), u""),
        _row(u"Сопротивление одного вертикального электрода",
             u"Rв = ρ/(2πL)·[ln(2L/d)+0,5·ln((4T+L)/(4T−L))]",
             u"{:.2f} Ом".format(result.get("single_vertical_r")), u""),
        _row(u"Коэффициент использования вертикальных электродов", u"ηв = f(n; a/L; схема)",
             u"ηв = {:.3f}".format(result.get("vertical_eta")),
             u"{}; {}".format(result.get("vertical_eta_layout", u"—"),
                              (result.get("vertical_eta_info") or {}).get("method", u"—"))),
        _row(u"Группа вертикальных электродов", u"Rв.гр = Rв/(n·ηв)",
             u"{:.2f} Ом".format(result.get("vertical_group_r")), u""),
    ]
    if result.get("horizontal_r") is not None:
        if result.get("horizontal_model") == "equivalent_ring":
            _hformula = u"Rг = ρ/(π²D)·ln(7D/√(b·hг)); D=l/π"
            _hnote = u"Эквивалентное кольцо по периметру"
        else:
            _hformula = u"Rг = ρ/(2πl)·ln(2l²/(b·hг))"
            _hnote = u"Прямая полоса"
        rows.append(_row(u"Горизонтальный электрод", _hformula,
                         u"{:.2f} Ом".format(result.get("horizontal_r")), _hnote))
        if result.get("horizontal_eta") is not None:
            rows.append(_row(u"Коэффициент использования соединительной полосы", u"ηг = f(n; a/L; схема)",
                             u"ηг = {:.3f}".format(result.get("horizontal_eta")),
                             (result.get("horizontal_eta_info") or {}).get("method", u"—")))
        rows.append(_row(u"ИТОГОВОЕ СОПРОТИВЛЕНИЕ ЗУ",
                         u"1/RЗУ = n·ηв/Rв + ηг/Rг",
                         u"{:.2f} Ом".format(result.get("total_r")), u"С учетом взаимного экранирования"))
    else:
        rows.append(_row(u"ИТОГОВОЕ СОПРОТИВЛЕНИЕ ЗУ", u"RЗУ = Rв.гр", u"{:.2f} Ом".format(result.get("total_r")), u""))
    return rows


def _formula_rows(single=False):
    rows = [
        _row(u"Расчетное сопротивление грунта", u"ρрасч = ρэкв·kсез,эфф", u"Для однородной модели: ρэкв=ρ; для послойной — эквивалент по принятой модели", u""),
        _row(u"Одиночный вертикальный электрод", u"Rв = ρ/(2πL)·[ln(2L/d)+0,5·ln((4T+L)/(4T−L))]", u"T = h + L/2", u""),
    ]
    if not single:
        rows.extend([
            _row(u"Группа вертикальных электродов", u"Rв.гр = Rв/(n·η)", u"n — число электродов; η — коэффициент использования", u""),
            _row(u"Горизонтальный электрод — ряд", u"Rг = ρ/(2πl)·ln(2l²/(b·hг))", u"l — длина; b — ширина полосы; hг — глубина", u""),
            _row(u"Горизонтальный электрод — замкнутый контур", u"Rг = ρ/(π²D)·ln(7D/√(b·hг)); D=l/π", u"Эквивалентная кольцевая модель по периметру", u""),
            _row(u"Общее сопротивление", u"1/RЗУ = n·ηв/Rв + ηг/Rг", u"ηв — вертикальные электроды; ηг — соединительная полоса", u""),
        ])
    rows.extend([
        _row(u"Заземляющий проводник ЗУ–ГЗШ", u"S = Iк·√t/k", u"Iк — ток повреждения; t — время отключения; k — коэффициент материала", u""),
        _row(u"Проверка TT", u"RA·IΔn ≤ 50 В", u"применяется для системы TT", u""),
        _row(u"Итоговое сечение проводника", u"Sпр = max(Sтерм; Smin; Sпо вводу)", u"принимается большее стандартное сечение", u""),
    ])
    return rows


def _summary_rows(data, result, single=False):
    """Compact result block shown first on the Revit calculation view."""
    rows = []
    criteria = result.get("criteria") or {}
    effective = criteria.get("effective") or result.get("criterion") or {}
    rcalc = result.get("total_r")

    if effective.get("available") and rcalc is not None:
        rmax = effective.get("max")
        status = u"СООТВЕТСТВУЕТ" if rmax is not None and rcalc <= rmax else u"НЕ СООТВЕТСТВУЕТ"
        rows.append(_row(
            u"Сопротивление ЗУ",
            u"RЗУ = {:.2f} Ом".format(rcalc),
            u"Rmax = {:.2f} Ом".format(rmax),
            status))
    elif rcalc is not None:
        rows.append(_row(
            u"Сопротивление ЗУ",
            u"RЗУ = {:.2f} Ом".format(rcalc),
            u"Нормативный предел не определен",
            u"ТРЕБУЕТ ПРОВЕРКИ"))

    c = result.get("grounding_conductor") or {}
    if c.get("enabled") and c.get("final_section") is not None:
        rows.append(_row(
            u"Проводник ЗУ → ГЗШ",
            u"Sпр = {:.1f} мм² {}".format(c.get("final_section"), c.get("material", u"")),
            u"Критерий: {}".format(c.get("controlling_criterion", u"—")),
            u"ПРИНЯТО"))

    if single:
        rows.append(_row(
            u"Принятая конструкция ЗУ",
            u"1 стержень; L = {} м".format(_n2(data.get("vertical_length"))),
            u"d = {} мм; h = {} м".format(_n1(data.get("vertical_diameter_mm")), _n2(data.get("vertical_top_depth"))),
            u"ОДИНОЧНЫЙ ЭЛЕКТРОД"))
        family_name = u"{}".format(data.get("rod_family_name") or u"")
        if data.get("use_rod_family") and u"90136" in family_name:
            try:
                modules = max(1, int(round(number_value(data.get("vertical_length")) / 1.5)))
            except Exception:
                modules = 1
            bom = vertical_electrode_bom(modules, 1)
            rows.append(_row(
                u"Комплектация электрода EZETEK",
                u"90136 — {} шт.; 90227 — {} шт.".format(bom.get("90136", 0), bom.get("90227", 0)),
                u"90326 — {} шт.; 90540 — {} шт.".format(bom.get("90326", 0), bom.get("90540", 0)),
                u"ПО ТОПОЛОГИИ УЗЛА"))
    else:
        geometry = u"{} шт. × {} м".format(_s(data.get("vertical_count")), _n2(data.get("vertical_length")))
        note = u"Шаг {} м".format(_n2(data.get("vertical_spacing")))
        if data.get("horizontal_enabled", True):
            note += u"; полоса {} м".format(_n2(data.get("horizontal_length")))
        rows.append(_row(u"Принятая конструкция ЗУ", geometry, note, u"ГРУППОВОЕ ЗУ"))
        family_name = u"{}".format(data.get("rod_family_name") or u"")
        if data.get("use_rod_family") and u"90136" in family_name:
            try:
                per_rod = max(1, int(round(number_value(data.get("vertical_length")) / 1.5)))
                rods_count = max(1, int(number_value(data.get("vertical_count"), 1)))
            except Exception:
                per_rod, rods_count = 1, 1
            bom = vertical_electrode_bom(per_rod, rods_count)
            rows.append(_row(
                u"Комплектация EZETEK",
                u"90136 — {} шт.; 90227 — {} шт.".format(bom.get("90136", 0), bom.get("90227", 0)),
                u"90326 — {} шт.; 90540 — {} шт.".format(bom.get("90326", 0), bom.get("90540", 0)),
                u"ПО ТОПОЛОГИИ УЗЛОВ"))

    errors = [x for x in result.get("checks", []) if x.get("severity") == "ERROR" and not x.get("passed")]
    warnings = [x for x in result.get("checks", []) if x.get("severity") == "WARNING"]
    if errors:
        overall = u"ЕСТЬ ОШИБКИ"
    elif warnings:
        overall = u"С ПРЕДУПРЕЖДЕНИЯМИ"
    else:
        overall = u"ПРОВЕРКИ ПРОЙДЕНЫ"
    rows.append(_row(
        u"Итог проверки",
        overall,
        u"Ошибок: {}; предупреждений: {}".format(len(errors), len(warnings)),
        u"См. таблицу 4.2"))
    return rows


def build_calculation_tables(data, result, single=False, title=None):
    if title is None:
        title = u"РАСЧЕТ ОДИНОЧНОГО МОДУЛЬНОГО ЗАЗЕМЛИТЕЛЯ" if single else u"РАСЧЕТ ЗАЗЕМЛЯЮЩЕГО УСТРОЙСТВА"

    tables = []
    tables.append({
        "title": u"0. КЛЮЧЕВЫЕ РЕЗУЛЬТАТЫ",
        "columns": [u"Показатель", u"Итоговое значение", u"Критерий / принято", u"Статус"],
        "rows": _summary_rows(data, result, single),
        "col_widths_mm": [92, 96, 116, 76],
        "result_table": True,
    })
    tables.append({
        "title": u"1. ИСХОДНЫЕ ДАННЫЕ",
        "columns": [u"Параметр", u"Обозначение", u"Значение", u"Примечание / источник"],
        "rows": _input_rows(data, result, single),
        "col_widths_mm": [92, 62, 102, 124],
    })
    tables.append({
        "title": u"2. РАСЧЕТ ЗАЗЕМЛЯЮЩЕГО УСТРОЙСТВА",
        "columns": [u"Расчетный этап", u"Формула", u"Подстановка / результат", u"Примечание"],
        "rows": _zu_calc_rows(data, result, single),
        "col_widths_mm": [88, 116, 96, 80],
    })
    tables.append({
        "title": u"3. РАСЧЕТ ПРОВОДНИКА ЗУ → ГЗШ",
        "columns": [u"Расчет / проверка", u"Формула / правило", u"Результат", u"Основание / примечание"],
        "rows": _conductor_rows(data, result),
        "col_widths_mm": [86, 104, 88, 102],
    })
    tables.append({
        "title": u"4.1. ПРОВЕРКА КРИТЕРИЕВ СОПРОТИВЛЕНИЯ",
        "columns": [u"Критерий", u"Условие", u"Результат", u"Основание / комментарий"],
        "rows": _criteria_rows(result),
        "col_widths_mm": [88, 72, 96, 124],
    })
    check_rows = []
    for check in result.get("checks", []):
        check_rows.append(_row(_status(check), check.get("title", u""), check.get("message", u""), check.get("reference", u"")))
    if not check_rows:
        check_rows.append(_row(u"—", u"Проверки отсутствуют", u"—", u""))
    tables.append({
        "title": u"4.2. ТАБЛИЦА ПРОВЕРОК",
        "columns": [u"Статус", u"Проверка", u"Результат", u"Норматив / основание"],
        "rows": check_rows,
        "col_widths_mm": [34, 104, 126, 116],
    })

    effective = (result.get("criteria") or {}).get("effective") or result.get("criterion") or {}
    c = result.get("grounding_conductor") or {}
    decision_rows = [
        _row(u"Расчетное сопротивление ЗУ", u"Rрасч", u"{:.2f} Ом".format(result.get("total_r")), u""),
    ]
    if effective.get("available"):
        decision_rows.append(_row(u"Применимый предел", u"Rmax", u"{:.2f} Ом".format(effective.get("max")), effective.get("basis", u"")))
        decision_rows.append(_row(u"Проектная цель с запасом", u"Rцель", u"{:.2f} Ом".format(effective.get("target")), u""))
    else:
        decision_rows.append(_row(u"Применимый предел", u"Rmax", u"Не определен", u"Проверяется по условиям защиты"))
    if c.get("enabled") and c.get("final_section") is not None:
        decision_rows.append(_row(u"Проводник ЗУ–ГЗШ", u"Sпр", u"{:.1f} мм² {}".format(c.get("final_section"), c.get("material", u"")), u"Критерий: {}".format(c.get("controlling_criterion", u""))))
    errors = [x for x in result.get("checks", []) if x.get("severity") == "ERROR" and not x.get("passed")]
    warnings = [x for x in result.get("checks", []) if x.get("severity") == "WARNING"]
    decision_rows.append(_row(u"Итог проверки", u"—", u"Ошибок: {}; предупреждений: {}".format(len(errors), len(warnings)), u"Перед выпуском РД устранить ошибки и проверить предупреждения"))
    tables.append({
        "title": u"5. ПРИНЯТЫЕ РЕШЕНИЯ",
        "columns": [u"Показатель", u"Обозначение", u"Принято", u"Комментарий"],
        "rows": decision_rows,
        "col_widths_mm": [92, 58, 92, 138],
    })
    tables.append({
        "title": u"6. РАСЧЕТНЫЕ ФОРМУЛЫ И ОБОЗНАЧЕНИЯ",
        "columns": [u"Расчет", u"Формула", u"Обозначения", u"Примечание"],
        "rows": _formula_rows(single),
        "col_widths_mm": [80, 128, 122, 50],
    })
    return {"title": title, "subtitle": u"Расчетное ядро pyRevit {}".format(display_version()), "tables": tables}
