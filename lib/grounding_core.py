# -*- coding: utf-8 -*-
from __future__ import division, print_function
import math
from plugin_version import display_version

try:
    from project_context import add_context_warnings
except Exception:
    def add_context_warnings(data, warnings):
        return warnings

SYSTEM_TNCS = "TN-C-S"
SYSTEM_TNS = "TN-S"
SYSTEM_TT = "TT"

PURPOSE_PROTECTIVE = u"Защитное заземление"
PURPOSE_REPEATED_PEN = u"Повторное заземление PEN"
PURPOSE_TNCS_INPUT = u"Вводное ЗУ TN-C-S (PEN + защитное)"
PURPOSE_SOURCE_NEUTRAL = u"Заземление нейтрали источника"
PURPOSE_GENERATOR_NEUTRAL = u"Нейтраль генератора"
PURPOSE_COMBINED_LPS = u"Совмещенное с молниезащитой"
PURPOSE_FUNCTIONAL = u"Рабочее (функциональное) заземление"

SUPPLY_UNKNOWN = u"Не задан"
SUPPLY_OVERHEAD = u"ВЛ"
SUPPLY_CABLE = u"Кабель"
SUPPLY_GENERATOR = u"Локальный генератор"

MAT_CU = "Cu"
MAT_AL = "Al"
MAT_STEEL = u"Сталь"

SEV_INFO = "INFO"
SEV_WARNING = "WARNING"
SEV_ERROR = "ERROR"

BREAKER_STD_60898 = u"ГОСТ IEC 60898-1 (B/C/D)"
BREAKER_STD_OTHER = u"ГОСТ IEC 60947-2 / данные изготовителя"
BREAKER_CURVES = (u"B", u"C", u"D", u"По данным изготовителя")
_BREAKER_INSTANT_MAX = {u"B": 5.0, u"C": 10.0, u"D": 20.0}


def number_value(value, default=None):
    """Parse a UI number written with either decimal comma or decimal point."""
    if value is None or value == "":
        return default
    if isinstance(value, (int, float)):
        return float(value)
    text = u"{}".format(value).strip().replace(u"\xa0", u"").replace(u" ", u"")
    text = text.replace(u",", u".")
    return float(text)


def _num(data, key, default=None):
    return number_value(data.get(key, default), default)


def _int(data, key, default=None):
    value = data.get(key, default)
    if value is None or value == "":
        return default
    parsed = number_value(value)
    rounded = int(round(parsed))
    if abs(parsed - rounded) > 1e-9:
        raise ValueError(u"Количество должно быть целым числом: {}".format(value))
    return rounded



STANDARD_CU_SECTIONS = [1.5, 2.5, 4.0, 6.0, 10.0, 16.0, 25.0, 35.0, 50.0, 70.0, 95.0, 120.0, 150.0, 185.0, 240.0]
STANDARD_STEEL_SECTIONS = [50.0, 70.0, 95.0, 120.0, 150.0, 185.0, 240.0]


def _round_standard_section(value, material):
    if value is None:
        return None
    series = STANDARD_CU_SECTIONS if material == MAT_CU else STANDARD_STEEL_SECTIONS
    for item in series:
        if item + 1e-9 >= value:
            return item
    return value


def _input_protective_base_section(phase_section):
    """Табличное правило 54.2 / ПУЭ 1.7.126 для проводника того же материала."""
    if phase_section <= 16.0:
        return phase_section
    if phase_section <= 35.0:
        return 16.0
    return phase_section / 2.0


def calculate_grounding_conductor(data):
    """Предварительный подбор заземляющего проводника ЗУ -> ГЗШ.

    Критерии разделены явно:
    1) адиабатический расчет S = I*sqrt(t)/k;
    2) минимальное нормативное сечение по назначению;
    3) проверка по сечению вводного фазного проводника (табл. 54.2 / ПУЭ 1.7.126)
       с приведением по проводимости для Cu/Al.
    Итог принимается по наиболее строгому применимому критерию.
    """
    enabled = bool(data.get("grounding_conductor_enabled", True))
    if not enabled:
        return {"enabled": False, "available": False, "warnings": []}

    material = data.get("grounding_conductor_material", MAT_CU)
    purpose = data.get("purpose", PURPOSE_PROTECTIVE)
    warnings = []

    # Паспорт ввода. Номинал/характеристика нужны для проверки возможности
    # гарантированного мгновенного расцепления у модульных АВ IEC 60898-1.
    breaker_standard = data.get("incoming_breaker_standard", BREAKER_STD_60898)
    breaker_rating = _num(data, "incoming_breaker_rating_a", None)
    breaker_curve = data.get("incoming_breaker_curve", u"C")
    cable_length = _num(data, "incoming_cable_length_m", None)

    # 1. Адиабатический расчет
    fault_current = _num(data, "grounding_fault_current_a", None)
    disconnection_time = _num(data, "grounding_disconnection_time_s", None)
    k_factor = _num(data, "grounding_k", None)
    time_source = u"Ручной ввод" if disconnection_time is not None else None
    instantaneous_threshold = None

    # Для бытового/аналогичного модульного АВ IEC 60898-1 допускаем
    # консервативно принять 0,1 с только если Iк не ниже ВЕРХНЕЙ границы
    # диапазона мгновенного расцепления: B=5In, C=10In, D=20In.
    if (disconnection_time is None and fault_current is not None and fault_current > 0
            and breaker_standard == BREAKER_STD_60898
            and breaker_curve in _BREAKER_INSTANT_MAX
            and breaker_rating is not None and breaker_rating > 0):
        instantaneous_threshold = _BREAKER_INSTANT_MAX[breaker_curve] * breaker_rating
        if fault_current >= instantaneous_threshold:
            disconnection_time = 0.1
            time_source = u"Принято 0,1 с по верхней границе мгновенного расцепления {}: Iк ≥ {:.0f}×In".format(
                breaker_curve, _BREAKER_INSTANT_MAX[breaker_curve])
            warnings.append(
                u"Время t=0,1 с принято консервативно для проверки сечения: Iк={:.0f} А ≥ {:.0f} А ({}×In, характеристика {}). "
                u"Фактическое время может быть меньше; для селективных/промышленных аппаратов используйте данные изготовителя.".format(
                    fault_current, instantaneous_threshold, _BREAKER_INSTANT_MAX[breaker_curve], breaker_curve))

    thermal_raw = None
    thermal_standard = None
    if fault_current is not None or disconnection_time is not None or k_factor is not None:
        if fault_current is None or fault_current <= 0:
            warnings.append(u"Не задан положительный ток повреждения Iк для термического расчета заземляющего проводника.")
        elif disconnection_time is None or disconnection_time <= 0:
            if breaker_standard == BREAKER_STD_60898 and breaker_curve in _BREAKER_INSTANT_MAX and breaker_rating:
                threshold = _BREAKER_INSTANT_MAX[breaker_curve] * breaker_rating
                warnings.append(u"Время t не определено автоматически: для характеристики {} гарантированная зона <0,1 с начинается при Iк ≥ {:.0f} А ({}×In). Задайте t по ВТХ/данным изготовителя.".format(
                    breaker_curve, threshold, _BREAKER_INSTANT_MAX[breaker_curve]))
            else:
                warnings.append(u"Не задано положительное время отключения t. Для АВ по IEC 60947-2 и иных аппаратов задайте t по ВТХ/уставкам изготовителя.")
        elif k_factor is None or k_factor <= 0:
            warnings.append(u"Не задан положительный коэффициент k для адиабатического расчета заземляющего проводника.")
        else:
            thermal_raw = fault_current * math.sqrt(disconnection_time) / k_factor
            thermal_standard = _round_standard_section(thermal_raw, material)
    else:
        warnings.append(u"Iк и время отключения не заданы: термический расчет S=I√t/k не выполнен.")


    if material == MAT_STEEL and k_factor is not None and abs(k_factor - 115.0) < 1e-9:
        warnings.append(u"Для стали оставлено значение k=115 по умолчанию Cu/PVC. Перед выпуском проекта задайте k по фактическому материалу и температурным условиям.")

    # 2. Нормативный минимум
    min_norm = 6.0 if material == MAT_CU else 50.0
    min_basis = u"ГОСТ Р 50571.5.54-2024, п. 542.3.1"
    if purpose == PURPOSE_COMBINED_LPS:
        min_norm = 16.0 if material == MAT_CU else 50.0
        min_basis = u"ГОСТ Р 50571.5.54-2024: заземляющий проводник при совмещении с молниезащитой"
    elif purpose == PURPOSE_FUNCTIONAL:
        min_norm = 10.0 if material == MAT_CU else 75.0
        min_basis = u"ПУЭ 7, п. 1.7.117 — рабочее (функциональное) заземление"

    # 3. Связь с вводным фазным проводником
    input_check_enabled = bool(data.get("grounding_use_input_section", True))
    input_required_raw = None
    input_required_standard = None
    input_basis = None
    phase_section = _num(data, "incoming_phase_section", None)
    phase_material = data.get("incoming_phase_material", MAT_CU)
    if input_check_enabled:
        if phase_section is None or phase_section <= 0:
            warnings.append(u"Не задано сечение вводного фазного проводника: табличная проверка по вводу не выполнена.")
        else:
            base_same_material = _input_protective_base_section(phase_section)
            if phase_material == material:
                input_required_raw = base_same_material
            else:
                phase_k = _num(data, "incoming_phase_k", None)
                target_k = k_factor
                if phase_k is None or phase_k <= 0 or target_k is None or target_k <= 0:
                    warnings.append(u"Для разных материалов линейного и заземляющего проводников задайте k1 и k2: табл. 54.2 требует приведения по коэффициентам k, а не только по удельной проводимости.")
                else:
                    input_required_raw = base_same_material * phase_k / target_k
            if input_required_raw is not None:
                input_required_standard = _round_standard_section(input_required_raw, material)
                input_basis = u"ГОСТ Р 50571.5.54-2024, табл. 54.2; ПУЭ 7, п. 1.7.113 и 1.7.126"

    candidates = [(u"Нормативный минимум", min_norm)]
    if thermal_standard is not None:
        candidates.append((u"Термический расчет", thermal_standard))
    if input_required_standard is not None:
        candidates.append((u"Проверка по вводу", input_required_standard))
    controlling_name, final_section = max(candidates, key=lambda x: x[1])

    missing_inputs = []
    if breaker_rating is None or breaker_rating <= 0:
        missing_inputs.append(u"номинал вводного АВ")
    if phase_section is None or phase_section <= 0:
        missing_inputs.append(u"сечение вводного фазного проводника")
    if fault_current is None or fault_current <= 0:
        missing_inputs.append(u"Iк в точке расчета")
    if disconnection_time is None or disconnection_time <= 0:
        missing_inputs.append(u"время отключения t")

    return {
        "enabled": True,
        "available": True,
        "material": material,
        "fault_current_a": fault_current,
        "disconnection_time_s": disconnection_time,
        "k_factor": k_factor,
        "thermal_raw": thermal_raw,
        "thermal_standard": thermal_standard,
        "norm_min": min_norm,
        "norm_basis": min_basis,
        "input_check_enabled": input_check_enabled,
        "incoming_phase_section": phase_section,
        "incoming_phase_material": phase_material,
        "input_required_raw": input_required_raw,
        "input_required_standard": input_required_standard,
        "input_basis": input_basis,
        "incoming_breaker_standard": breaker_standard,
        "incoming_breaker_rating_a": breaker_rating,
        "incoming_breaker_curve": breaker_curve,
        "incoming_cable_length_m": cable_length,
        "incoming_cable_length_used": False,
        "instantaneous_threshold_a": instantaneous_threshold,
        "disconnection_time_source": time_source,
        "input_data_complete": len(missing_inputs) == 0,
        "missing_inputs": missing_inputs,
        "final_section": final_section,
        "controlling_criterion": controlling_name,
        "warnings": warnings
    }


def _append_grounding_conductor_checks(checks, conductor):
    if not conductor or not conductor.get("enabled"):
        return
    if conductor.get("final_section") is not None:
        checks.append(_check(
            "GROUNDING_CONDUCTOR_SECTION", u"Сечение заземляющего проводника ЗУ–ГЗШ", True, SEV_INFO,
            u"Принято {:.1f} мм² {}; определяющий критерий: {}.".format(
                conductor["final_section"], conductor.get("material", ""), conductor.get("controlling_criterion", "")),
            conductor.get("norm_basis", u"")))
    for idx, warning in enumerate(conductor.get("warnings", [])):
        checks.append(_check(
            "GROUNDING_CONDUCTOR_WARNING_{}".format(idx + 1), u"Заземляющий проводник ЗУ–ГЗШ", True, SEV_WARNING,
            warning, u"Требуется уточнение исходных данных"))


def _append_grounding_conductor_report(lines, result):
    conductor = result.get("grounding_conductor") or {}
    lines.append(u"")
    lines.append(u"ЗАЗЕМЛЯЮЩИЙ ПРОВОДНИК ЗУ → ГЗШ")
    if not conductor.get("enabled"):
        lines.append(u"Расчет отключен пользователем.")
        return
    lines.append(u"Материал: {}".format(conductor.get("material", "")))
    if conductor.get("incoming_breaker_rating_a") is not None:
        lines.append(u"Вводной АВ: {:.0f} А; характеристика {}; {}".format(
            conductor.get("incoming_breaker_rating_a"), conductor.get("incoming_breaker_curve", ""),
            conductor.get("incoming_breaker_standard", "")))
    else:
        lines.append(u"Вводной АВ: номинал не задан.")
    if conductor.get("incoming_cable_length_m") is not None:
        lines.append(u"Длина вводного кабеля: {:.1f} м — справочно; при Iк, заданном в точке расчета, отдельно не используется.".format(
            conductor.get("incoming_cable_length_m")))
    if conductor.get("thermal_raw") is not None:
        lines.append(u"Термический расчет: S = Iк√t/k = {:.2f} мм²; ближайшее стандартное ≥ {:.1f} мм²".format(
            conductor["thermal_raw"], conductor["thermal_standard"]))
        lines.append(u"  Iк={:.1f} А; t={:.3f} с; k={:.1f}".format(
            conductor["fault_current_a"], conductor["disconnection_time_s"], conductor["k_factor"]))
        if conductor.get("disconnection_time_source"):
            lines.append(u"  Источник t: {}".format(conductor.get("disconnection_time_source")))
    else:
        lines.append(u"Термический расчет: не выполнен из-за отсутствия полного набора Iк, t, k.")
    lines.append(u"Нормативный минимум: ≥ {:.1f} мм². Основание: {}".format(
        conductor.get("norm_min", 0.0), conductor.get("norm_basis", "")))
    if conductor.get("input_required_standard") is not None:
        lines.append(u"Проверка по вводному проводнику: {} {:.1f} мм² → требуемое эквивалентное сечение ≥ {:.1f} мм² {}".format(
            conductor.get("incoming_phase_material", ""), conductor.get("incoming_phase_section", 0.0),
            conductor.get("input_required_standard", 0.0), conductor.get("material", "")))
        lines.append(u"  Основание: {}".format(conductor.get("input_basis", "")))
    elif conductor.get("input_check_enabled"):
        lines.append(u"Проверка по вводному проводнику: не выполнена.")
    lines.append(u"ИТОГ: принять не менее {:.1f} мм² {}; определяющий критерий — {}.".format(
        conductor.get("final_section", 0.0), conductor.get("material", ""), conductor.get("controlling_criterion", "")))
    if conductor.get("input_data_complete"):
        lines.append(u"Комплект исходных данных для полного расчета: заполнен.")
    else:
        lines.append(u"Комплект исходных данных неполный: {}.".format(u", ".join(conductor.get("missing_inputs", []))))
    for warning in conductor.get("warnings", []):
        lines.append(u"[ПРЕДУПРЕЖДЕНИЕ] {}".format(warning))


def validate(data):
    rho = _num(data, "rho", 0.0)
    kseason = _num(data, "seasonal_factor", 0.0)
    length = _num(data, "vertical_length", 0.0)
    diameter_mm = _num(data, "vertical_diameter_mm", 0.0)
    top_depth = _num(data, "vertical_top_depth", -1.0)
    count = _int(data, "vertical_count", 0)
    spacing = _num(data, "vertical_spacing", -1.0)

    if rho <= 0:
        raise ValueError(u"Удельное сопротивление грунта должно быть больше 0 Ом·м.")
    if kseason <= 0:
        raise ValueError(u"Сезонный коэффициент должен быть больше 0.")
    if length <= 0 or diameter_mm <= 0 or top_depth < 0:
        raise ValueError(u"Проверьте геометрию вертикального электрода.")
    if count < 1:
        raise ValueError(u"Количество вертикальных электродов должно быть не менее 1.")
    if spacing < 0:
        raise ValueError(u"Шаг электродов не может быть отрицательным.")
    if data.get("horizontal_enabled", True):
        if _num(data, "horizontal_length", 0) <= 0:
            raise ValueError(u"Длина горизонтального электрода должна быть больше 0.")
        if _num(data, "horizontal_width_mm", 0) <= 0:
            raise ValueError(u"Ширина горизонтального электрода должна быть больше 0.")
        if _num(data, "horizontal_depth", 0) <= 0:
            raise ValueError(u"Глубина горизонтального электрода должна быть больше 0.")
    project_limit = _num(data, "required_resistance", None)
    if project_limit is not None and project_limit <= 0:
        raise ValueError(u"Дополнительный проектный предел R должен быть больше 0 Ом.")
    if data.get("gas_tu_enabled", False):
        gas_tu_limit = _num(data, "gas_tu_resistance", None)
        if gas_tu_limit is None or gas_tu_limit <= 0:
            raise ValueError(u"При включенном требовании ТУ задайте Rз по ТУ больше 0 Ом.")


def single_vertical_resistance(rho, length_m, diameter_m, top_depth_m):
    """Приближенная формула одиночного вертикального цилиндрического электрода.
    t = глубина до середины электрода.
    """
    t = top_depth_m + length_m / 2.0
    denominator = 4.0 * t - length_m
    if denominator <= 0:
        raise ValueError(u"Некорректная глубина электрода: 4t-L должно быть > 0.")
    part1 = math.log((2.0 * length_m) / diameter_m)
    part2 = 0.5 * math.log((4.0 * t + length_m) / denominator)
    return rho / (2.0 * math.pi * length_m) * (part1 + part2)


def horizontal_strip_resistance(rho, length_m, width_m, burial_depth_m):
    argument = (2.0 * length_m * length_m) / (width_m * burial_depth_m)
    if argument <= 1.0:
        raise ValueError(u"Геометрия горизонтального электрода выходит за область применимости формулы.")
    return rho / (2.0 * math.pi * length_m) * math.log(argument)


def horizontal_ring_resistance(rho, perimeter_m, width_m, burial_depth_m):
    """Эквивалентное кольцо по длине замкнутого контура.

    Методическая формула для кольцевого ленточного заземлителя:
    R = rho/(pi^2*D) * ln(7D/sqrt(b*h)), где D = L/pi.
    Для произвольного многоугольного контура это эквивалентная круговая модель,
    а не расчет поля растекания фактической геометрии.
    """
    if perimeter_m <= 0 or width_m <= 0 or burial_depth_m <= 0:
        raise ValueError(u"Геометрия замкнутого горизонтального заземлителя должна быть положительной.")
    diameter = perimeter_m / math.pi
    argument = (7.0 * diameter) / math.sqrt(width_m * burial_depth_m)
    if argument <= 1.0:
        raise ValueError(u"Геометрия кольцевого заземлителя выходит за область применимости формулы.")
    return rho / (math.pi * math.pi * diameter) * math.log(argument)


ETA_LAYOUT_AUTO = u"Авто по схеме размещения"
ETA_LAYOUT_ROW = u"Ряд"
ETA_LAYOUT_CONTOUR = u"Замкнутый контур"

# Методический источник: «Руководство по проектированию, строительству и эксплуатации
# заземлений в установках проводной связи и радиотрансляционных узлов», 1971,
# табл. 2.4 (ряд), 2.5 (замкнутый контур), 2.7/2.8 (соединительная полоса).
# Значения вертикальных η приведены диапазонами. В расчете по умолчанию принимается
# нижняя граница диапазона как консервативное значение.
_VERTICAL_ETA_ROW = {
    1.0: {2:(0.84,0.87), 3:(0.76,0.80), 5:(0.67,0.72), 10:(0.56,0.62), 15:(0.51,0.56), 20:(0.47,0.52)},
    2.0: {2:(0.90,0.92), 3:(0.85,0.88), 5:(0.79,0.83), 10:(0.72,0.77), 15:(0.66,0.73), 20:(0.65,0.70)},
    3.0: {2:(0.93,0.95), 3:(0.90,0.92), 5:(0.85,0.88), 10:(0.79,0.83), 15:(0.76,0.80), 20:(0.74,0.79)},
}
_VERTICAL_ETA_CONTOUR = {
    1.0: {4:(0.66,0.72), 6:(0.58,0.65), 10:(0.52,0.58), 20:(0.44,0.50), 40:(0.38,0.44), 60:(0.36,0.42), 100:(0.33,0.39)},
    2.0: {4:(0.76,0.80), 6:(0.71,0.75), 10:(0.66,0.71), 20:(0.61,0.66), 40:(0.55,0.61), 60:(0.52,0.58), 100:(0.49,0.55)},
    3.0: {4:(0.84,0.86), 6:(0.78,0.82), 10:(0.74,0.78), 20:(0.68,0.73), 40:(0.64,0.69), 60:(0.62,0.67), 100:(0.59,0.65)},
}
_HORIZONTAL_ETA_ROW = {
    1.0: {4:0.77, 5:0.74, 8:0.67, 10:0.62, 20:0.42, 30:0.31, 50:0.21, 65:0.20},
    2.0: {4:0.89, 5:0.86, 8:0.79, 10:0.75, 20:0.56, 30:0.46, 50:0.36, 65:0.34},
    3.0: {4:0.92, 5:0.90, 8:0.85, 10:0.82, 20:0.68, 30:0.58, 50:0.49, 65:0.47},
}
_HORIZONTAL_ETA_CONTOUR = {
    1.0: {4:0.45, 5:0.40, 8:0.36, 10:0.34, 20:0.27, 30:0.24, 50:0.21, 70:0.20, 100:0.19},
    2.0: {4:0.55, 5:0.48, 8:0.43, 10:0.40, 20:0.32, 30:0.30, 50:0.28, 70:0.26, 100:0.24},
    3.0: {4:0.70, 5:0.64, 8:0.60, 10:0.56, 20:0.45, 30:0.41, 50:0.37, 70:0.35, 100:0.33},
}


def _legacy_vertical_utilization_factor(count, spacing_m, length_m):
    """Старый fallback только для области, не покрытой табличным методом."""
    if count <= 1:
        return 1.0
    if spacing_m <= 0 or length_m <= 0:
        return 0.55
    ratio = spacing_m / length_m
    if ratio >= 3.0:
        base_eta = 0.90
    elif ratio >= 2.0:
        base_eta = 0.84
    elif ratio >= 1.0:
        base_eta = 0.72
    elif ratio >= 0.5:
        base_eta = 0.60
    else:
        base_eta = 0.48
    penalty = min(0.18, max(0, count - 3) * 0.015)
    return max(0.35, base_eta - penalty)


def _bracket(keys, value):
    keys = sorted(keys)
    if value <= keys[0]:
        return keys[0], keys[0]
    if value >= keys[-1]:
        return keys[-1], keys[-1]
    for idx in range(1, len(keys)):
        if value <= keys[idx]:
            return keys[idx - 1], keys[idx]
    return keys[-1], keys[-1]


def _lerp(v0, v1, x0, x1, x):
    if x0 == x1:
        return float(v0)
    f = (float(x) - float(x0)) / (float(x1) - float(x0))
    return float(v0) + (float(v1) - float(v0)) * f


def _interp_table(table, ratio, count, pair=False):
    r0, r1 = _bracket(table.keys(), ratio)
    def at_ratio(r):
        row = table[r]
        n0, n1 = _bracket(row.keys(), count)
        a = row[n0]
        b = row[n1]
        if pair:
            lo = _lerp(a[0], b[0], n0, n1, count)
            hi = _lerp(a[1], b[1], n0, n1, count)
            return lo, hi
        return _lerp(a, b, n0, n1, count)
    v0 = at_ratio(r0)
    v1 = at_ratio(r1)
    if pair:
        return (_lerp(v0[0], v1[0], r0, r1, ratio),
                _lerp(v0[1], v1[1], r0, r1, ratio))
    return _lerp(v0, v1, r0, r1, ratio)


def resolve_vertical_utilization_layout(data):
    requested = data.get("vertical_eta_layout") or ETA_LAYOUT_AUTO
    if requested in (ETA_LAYOUT_ROW, ETA_LAYOUT_CONTOUR):
        return requested
    mode = u"{}".format(data.get("layout_mode") or u"").lower()
    if bool(data.get("close_loop", False)) or u"треуг" in mode:
        return ETA_LAYOUT_CONTOUR
    return ETA_LAYOUT_ROW


def vertical_utilization_factor_details(count, spacing_m, length_m, layout=ETA_LAYOUT_ROW):
    if count <= 1:
        return {
            "eta": 1.0, "eta_min": 1.0, "eta_max": 1.0, "ratio": None,
            "layout": layout, "method": "single", "table_supported": True,
            "source": u"Одиночный электрод: η = 1,00", "warning": None
        }
    ratio = (float(spacing_m) / float(length_m)) if spacing_m > 0 and length_m > 0 else None
    table = _VERTICAL_ETA_CONTOUR if layout == ETA_LAYOUT_CONTOUR else _VERTICAL_ETA_ROW
    min_n = min(next(iter(table.values())).keys())
    max_n = max(next(iter(table.values())).keys())
    if ratio is not None and ratio >= 1.0 and count >= min_n and count <= max_n:
        used_ratio = min(3.0, ratio)
        lo, hi = _interp_table(table, used_ratio, count, pair=True)
        warning = None
        if ratio > 3.0:
            warning = (u"a/L={:.2f} выше диапазона таблицы; принято значение при a/L=3 без экстраполяции, "
                       u"что является консервативным граничным допущением.").format(ratio)
        return {
            "eta": lo, "eta_min": lo, "eta_max": hi, "ratio": ratio, "ratio_used": used_ratio,
            "layout": layout, "method": "table_1971_lower_bound", "table_supported": True,
            "source": (u"Руководство по проектированию, строительству и эксплуатации заземлений в установках "
                       u"проводной связи и радиотрансляционных узлов, 1971, табл. {}"
                       .format(u"2.5" if layout == ETA_LAYOUT_CONTOUR else u"2.4")),
            "warning": warning
        }
    eta = _legacy_vertical_utilization_factor(count, spacing_m, length_m)
    reason = []
    if ratio is None:
        reason.append(u"не задано корректное a/L")
    elif ratio < 1.0:
        reason.append(u"a/L<1 вне диапазона таблицы")
    if count < min_n or count > max_n:
        reason.append(u"n={} вне диапазона таблицы {}…{}".format(count, min_n, max_n))
    return {
        "eta": eta, "eta_min": eta, "eta_max": eta, "ratio": ratio,
        "layout": layout, "method": "legacy_fallback", "table_supported": False,
        "source": u"Вне области табличного метода; использована прежняя инженерная аппроксимация",
        "warning": u"; ".join(reason) if reason else u"Вне области применимости табличного метода"
    }


def vertical_utilization_factor(count, spacing_m, length_m, layout=ETA_LAYOUT_ROW):
    return vertical_utilization_factor_details(count, spacing_m, length_m, layout).get("eta")


def horizontal_utilization_factor_details(count, spacing_m, length_m, layout=ETA_LAYOUT_ROW):
    """η соединительной полосы по табл. 2.7/2.8 того же методического источника."""
    if count <= 1:
        return {"eta": 1.0, "method": "single", "table_supported": True, "warning": None,
                "source": u"Одиночный электрод: коэффициент взаимного влияния полосы не применяется"}
    ratio = (float(spacing_m) / float(length_m)) if spacing_m > 0 and length_m > 0 else None
    table = _HORIZONTAL_ETA_CONTOUR if layout == ETA_LAYOUT_CONTOUR else _HORIZONTAL_ETA_ROW
    min_n = min(next(iter(table.values())).keys())
    max_n = max(next(iter(table.values())).keys())
    if ratio is not None and ratio >= 1.0 and count >= min_n and count <= max_n:
        used_ratio = min(3.0, ratio)
        eta = _interp_table(table, used_ratio, count, pair=False)
        warning = None
        if ratio > 3.0:
            warning = (u"Для η полосы a/L={:.2f} выше диапазона таблицы; принято значение при a/L=3 "
                       u"без экстраполяции.").format(ratio)
        return {
            "eta": eta, "ratio": ratio, "ratio_used": used_ratio, "layout": layout,
            "method": "table_1971", "table_supported": True,
            "source": (u"Руководство по проектированию, строительству и эксплуатации заземлений, 1971, табл. {}"
                       .format(u"2.8" if layout == ETA_LAYOUT_CONTOUR else u"2.7")),
            "warning": warning
        }
    return {
        "eta": 1.0, "ratio": ratio, "layout": layout,
        "method": "not_applied", "table_supported": False,
        "source": u"η соединительной полосы не применен вне табличного диапазона",
        "warning": (u"Коэффициент использования соединительной полосы не определен табличным методом "
                    u"для заданных n и a/L; вклад полосы рассчитан без η полосы и требует проверки.")
    }


def parallel(a, b):
    return 1.0 / (1.0 / a + 1.0 / b)


def repeated_pen_limit(line_voltage_v):
    if line_voltage_v <= 250.0:
        return 60.0
    if line_voltage_v <= 500.0:
        return 30.0
    return 15.0


def source_neutral_limit(line_voltage_v):
    if line_voltage_v <= 250.0:
        return 8.0
    if line_voltage_v <= 500.0:
        return 4.0
    return 2.0


def _criterion_unavailable(kind, title, basis, explanation, enabled=False):
    return {
        "kind": kind,
        "title": title,
        "available": False,
        "enabled": bool(enabled),
        "basis": basis,
        "explanation": explanation
    }


def _criterion_available(kind, title, max_r, margin, basis, explanation):
    return {
        "kind": kind,
        "title": title,
        "available": True,
        "enabled": True,
        "max": float(max_r),
        "target": float(max_r) * margin,
        "basis": basis,
        "explanation": explanation
    }


def resolve_normative_criterion(data, base_rho):
    """Возвращает только нормативный критерий сопротивления ЗУ.

    Наличие газового оборудования здесь намеренно не учитывается: само по себе оно
    не создает универсального федерального требования Rз <= 10 Ом.
    """
    margin = _num(data, "design_margin", 0.8)
    margin = max(0.2, min(1.0, margin))
    system = data.get("system", SYSTEM_TNCS)
    purpose = data.get("purpose", PURPOSE_PROTECTIVE)

    if system == SYSTEM_TT:
        rcd_ma = _num(data, "rcd_ma", None)
        if rcd_ma is None or rcd_ma <= 0:
            return _criterion_unavailable(
                "normative", u"Нормативный критерий", u"ГОСТ Р 50571.4.41-2022, п. 411.5.3",
                u"Для TT необходимо задать IΔn УДТ, чтобы определить предельное RA из условия RA×IΔn≤50 В.")
        protective_r = _num(data, "protective_conductor_r", 0.0)
        idn = rcd_ma / 1000.0
        max_r = 50.0 / idn - protective_r
        if max_r <= 0:
            return _criterion_unavailable(
                "normative", u"Нормативный критерий", u"ГОСТ Р 50571.4.41-2022, п. 411.5.3",
                u"Сопротивление защитного проводника исчерпывает допустимое RA.")
        return _criterion_available(
            "normative", u"Нормативный критерий", max_r, margin,
            u"ГОСТ Р 50571.4.41-2022, п. 411.5.3",
            u"Для TT предельное сопротивление определено из условия RA×IΔn≤50 В.")

    line_v = _num(data, "line_voltage", 400.0)
    if purpose in (PURPOSE_REPEATED_PEN, PURPOSE_TNCS_INPUT):
        if data.get("supply_type") != SUPPLY_OVERHEAD:
            return _criterion_unavailable(
                "normative", u"Нормативный критерий", u"ПУЭ 7, п. 1.7.103",
                u"Критерий повторного заземления PEN автоматически применяется только для ввода от ВЛ. Для кабельного ввода требуется анализ фактической схемы сети.")
        max_r = repeated_pen_limit(line_v)
        if data.get("high_rho_relaxation", True) and base_rho > 100.0:
            max_r *= min(10.0, 0.01 * base_rho)
        return _criterion_available(
            "normative", u"Нормативный критерий", max_r, margin,
            u"ПУЭ 7, п. 1.7.103 (для отдельного повторного заземлителя PEN)",
            u"Для вводного ЗУ TN-C-S один и тот же заземлитель выполняет функцию повторного заземления PEN и присоединяется к ГЗШ/PE. Предел относится к отдельному повторному заземлителю PEN ВЛ и не является требованием «10 Ом для газового котла».")

    if purpose in (PURPOSE_SOURCE_NEUTRAL, PURPOSE_GENERATOR_NEUTRAL):
        max_r = source_neutral_limit(line_v)
        if data.get("high_rho_relaxation", True) and base_rho > 100.0:
            max_r *= min(10.0, 0.01 * base_rho)
        return _criterion_available(
            "normative", u"Нормативный критерий", max_r, margin,
            u"ПУЭ 7, п. 1.7.101 (проверить применимость к фактической схеме источника)",
            u"Критерий относится к заземлению нейтрали источника и не переносится автоматически на защитный заземлитель здания.")

    return _criterion_unavailable(
        "normative", u"Нормативный критерий", u"Определяется системой защиты и назначением ЗУ",
        u"Для выбранного режима универсальный нормативный предел сопротивления местного ЗУ в омах не назначается. Необходимо отдельно проверить автоматическое отключение питания.")


def resolve_project_criterion(data):
    margin = _num(data, "design_margin", 0.8)
    margin = max(0.2, min(1.0, margin))
    manual = _num(data, "required_resistance", None)
    if manual is None:
        return _criterion_unavailable(
            "project", u"Дополнительный проектный критерий", u"Не задан",
            u"Дополнительный предел сопротивления проектировщиком не задан.")
    if manual <= 0:
        raise ValueError(u"Дополнительный проектный предел R должен быть больше 0 Ом.")
    return _criterion_available(
        "project", u"Дополнительный проектный критерий", manual, margin,
        u"Задано проектировщиком",
        u"Это дополнительное проектное ограничение; оно не подменяет нормативный критерий.")


def resolve_gas_tu_criterion(data):
    margin = _num(data, "design_margin", 0.8)
    margin = max(0.2, min(1.0, margin))
    enabled = bool(data.get("gas_tu_enabled", False))
    if not enabled:
        return _criterion_unavailable(
            "gas_tu", u"ТУ газоснабжающей организации", u"Не применяется",
            u"Дополнительное требование ТУ газоснабжающей организации не включено.", enabled=False)
    max_r = _num(data, "gas_tu_resistance", None)
    if max_r is None or max_r <= 0:
        raise ValueError(u"При включенном требовании ТУ задайте Rз по ТУ больше 0 Ом.")
    reference = data.get("gas_tu_reference") or u"ТУ газоснабжающей организации (реквизиты не указаны)"
    return _criterion_available(
        "gas_tu", u"ТУ газоснабжающей организации", max_r, margin,
        u"{}".format(reference),
        u"Дополнительное требование конкретных ТУ. Оно не трактуется программой как универсальная норма СП 402 или ПУЭ для газового котла.")


def resolve_resistance_criteria(data, base_rho):
    normative = resolve_normative_criterion(data, base_rho)
    project = resolve_project_criterion(data)
    gas_tu = resolve_gas_tu_criterion(data)
    applicable = [item for item in (normative, project, gas_tu) if item.get("available")]
    if applicable:
        strictest = min(applicable, key=lambda item: item.get("max", float("inf")))
        effective = {
            "kind": "effective",
            "title": u"Итоговый применимый предел",
            "available": True,
            "enabled": True,
            "max": min(item["max"] for item in applicable),
            "target": min(item["target"] for item in applicable),
            "basis": u"; ".join(item["title"] for item in applicable),
            "explanation": u"Для проверки соответствия применяется наиболее строгий из заданных пределов: {}.".format(strictest["title"])
        }
    else:
        effective = _criterion_unavailable(
            "effective", u"Итоговый применимый предел", u"Нет применимого предела R в омах",
            u"Автоподбор по сопротивлению недоступен, пока не появится нормативный или дополнительный проектный/ТУ критерий.")
    return {
        "normative": normative,
        "project": project,
        "gas_tu": gas_tu,
        "effective": effective
    }


def _check(code, title, passed, severity, message, reference):
    return {
        "code": code,
        "title": title,
        "passed": bool(passed),
        "severity": severity,
        "message": message,
        "reference": reference
    }


def evaluate_checks(data, result):
    checks = []
    criteria = result.get("criteria")
    if not criteria:
        # Совместимость с результатами старой структуры.
        criteria = {"normative": result.get("criterion", {})}

    normative = criteria.get("normative") or {}
    if normative.get("available"):
        max_r = normative["max"]
        ok = result["total_r"] <= max_r
        checks.append(_check(
            "R_NORMATIVE", u"Нормативный предел сопротивления ЗУ", ok,
            SEV_INFO if ok else SEV_ERROR,
            u"Rрасч={:.2f} Ом; нормативный предел ≤ {:.2f} Ом; проектная цель с запасом ≤ {:.2f} Ом.".format(
                result["total_r"], max_r, normative["target"]),
            normative.get("basis", u"")))
    else:
        checks.append(_check(
            "R_NORMATIVE_UNDEFINED", u"Нормативный предел сопротивления ЗУ", True, SEV_WARNING,
            normative.get("explanation", u"Универсальный нормативный предел R не определен."),
            normative.get("basis", u"")))

    project = criteria.get("project") or {}
    if project.get("available"):
        ok = result["total_r"] <= project["max"]
        checks.append(_check(
            "R_PROJECT", u"Дополнительный проектный предел", ok,
            SEV_INFO if ok else SEV_ERROR,
            u"Rрасч={:.2f} Ом; задано проектировщиком ≤ {:.2f} Ом.".format(result["total_r"], project["max"]),
            project.get("basis", u"")))

    gas_tu = criteria.get("gas_tu") or {}
    if gas_tu.get("available"):
        ok = result["total_r"] <= gas_tu["max"]
        checks.append(_check(
            "R_GAS_TU", u"Дополнительное требование ТУ газоснабжения", ok,
            SEV_INFO if ok else SEV_ERROR,
            u"Rрасч={:.2f} Ом; требование ТУ ≤ {:.2f} Ом.".format(result["total_r"], gas_tu["max"]),
            gas_tu.get("basis", u"")))

    system = data.get("system")
    if system == SYSTEM_TT:
        rcd_ma = _num(data, "rcd_ma", None)
        if rcd_ma is None:
            checks.append(_check(
                "TT_RCD_MISSING", u"Автоматическое отключение TT", True, SEV_WARNING,
                u"Не задан IΔn УДТ; условие RA×IΔn≤50 В не проверено.",
                u"ГОСТ Р 50571.4.41-2022, п. 411.5.3"))
        else:
            ra = result["total_r"] + _num(data, "protective_conductor_r", 0.0)
            touch_v = ra * (rcd_ma / 1000.0)
            ok = touch_v <= 50.0
            checks.append(_check(
                "TT_RA_IDN", u"Автоматическое отключение TT", ok,
                SEV_INFO if ok else SEV_ERROR,
                u"RA×IΔn={:.2f} В; RA={:.2f} Ом; IΔn={:.0f} мА.".format(touch_v, ra, rcd_ma),
                u"ГОСТ Р 50571.4.41-2022, п. 411.5.3"))
    else:
        zs = _num(data, "zs", None)
        ia = _num(data, "ia", None)
        if zs is None or ia is None:
            checks.append(_check(
                "TN_LOOP_DATA_MISSING", u"Автоматическое отключение TN", True, SEV_WARNING,
                u"Не заданы Zs и/или Ia. Местное ЗУ само по себе не подтверждает автоматическое отключение в TN.",
                u"ГОСТ Р 50571.4.41-2022, п. 411.4.4"))
        else:
            phase_v = _num(data, "phase_voltage", 230.0)
            lhs = zs * ia
            ok = lhs <= phase_v
            checks.append(_check(
                "TN_ZS_IA", u"Автоматическое отключение TN", ok,
                SEV_INFO if ok else SEV_ERROR,
                u"Zs×Ia={:.1f} В; U0={:.1f} В.".format(lhs, phase_v),
                u"ГОСТ Р 50571.4.41-2022, п. 411.4.4"))

    if system == SYSTEM_TNCS:
        if not data.get("has_incoming_pen", True):
            checks.append(_check(
                "TNCS_NO_PEN", u"Исходная система TN-C-S", True, SEV_WARNING,
                u"Выбрана TN-C-S, но наличие входящего PEN не подтверждено.",
                u"ГОСТ Р 50571.5.54-2024, раздел 543.4"))
        pen_section = _num(data, "pen_section", None)
        pen_material = data.get("pen_material", MAT_AL)
        if pen_section is not None:
            min_s = 10.0 if pen_material == MAT_CU else 16.0
            ok = pen_material != MAT_STEEL and pen_section >= min_s
            checks.append(_check(
                "PEN_MIN_SECTION", u"Минимальное сечение PEN", ok,
                SEV_INFO if ok else SEV_ERROR,
                u"Принято {:.1f} мм² {}; проверяемый минимум {:.0f} мм².".format(pen_section, pen_material, min_s),
                u"ГОСТ Р 50571.5.54-2024, п. 543.4.1"))
        if data.get("reconnect_n_pe", False):
            checks.append(_check(
                "NO_RECONNECT_N_PE", u"Разделение N и PE", False, SEV_ERROR,
                u"После разделения PEN указано повторное объединение N и PE.",
                u"ГОСТ Р 50571.5.54-2024, п. 543.4.3"))

    purpose = data.get("purpose", PURPOSE_PROTECTIVE)
    if purpose == PURPOSE_TNCS_INPUT:
        ok_mode = system == SYSTEM_TNCS and data.get("supply_type") == SUPPLY_OVERHEAD
        checks.append(_check(
            "TNCS_INPUT_PURPOSE", u"Назначение вводного ЗУ TN-C-S", ok_mode,
            SEV_INFO if ok_mode else SEV_ERROR,
            (u"Режим соответствует схеме TN-C-S с вводом от ВЛ: местный заземлитель соединен с ГЗШ/PE и используется для повторного заземления PEN."
             if ok_mode else
             u"Назначение «Вводное ЗУ TN-C-S» применяется для системы TN-C-S при вводе от ВЛ. Проверьте систему и тип ввода."),
            u"ПУЭ 7, п. 1.7.102, 1.7.103, 1.7.135"))

    return checks

def calculate(data):
    validate(data)
    base_rho = _num(data, "rho")
    kseason = _num(data, "seasonal_factor", 1.0)
    rho = base_rho * kseason

    l = _num(data, "vertical_length")
    d = _num(data, "vertical_diameter_mm") / 1000.0
    top = _num(data, "vertical_top_depth")
    count = _int(data, "vertical_count")
    spacing = _num(data, "vertical_spacing")

    rv = single_vertical_resistance(rho, l, d, top)
    eta_layout = resolve_vertical_utilization_layout(data)
    eta_info = vertical_utilization_factor_details(count, spacing, l, eta_layout)
    eta = eta_info["eta"]
    rvg = rv / (max(1, count) * eta)

    rh = None
    rh_effective = None
    horizontal_model = None
    eta_h_info = None
    total = rvg
    if data.get("horizontal_enabled", True):
        hlen = _num(data, "horizontal_length")
        hw = _num(data, "horizontal_width_mm") / 1000.0
        hdepth = _num(data, "horizontal_depth")
        if eta_layout == ETA_LAYOUT_CONTOUR:
            rh = horizontal_ring_resistance(rho, hlen, hw, hdepth)
            horizontal_model = "equivalent_ring"
        else:
            rh = horizontal_strip_resistance(rho, hlen, hw, hdepth)
            horizontal_model = "straight_strip"
        eta_h_info = horizontal_utilization_factor_details(count, spacing, l, eta_layout)
        eta_h = eta_h_info.get("eta", 1.0)
        rh_effective = rh / max(1e-9, eta_h)
        total = parallel(rvg, rh_effective)

    criteria = resolve_resistance_criteria(data, base_rho)
    criterion = criteria["effective"]
    warnings = [u"Расчет не заменяет приемо-сдаточные измерения фактического заземляющего устройства."]
    if eta_info.get("warning"):
        warnings.append(u"η вертикальных электродов: " + eta_info.get("warning"))
    if not eta_info.get("table_supported"):
        warnings.append(u"η вертикальных электродов вне области табличного метода — результат по группе является предварительным.")
    if eta_h_info and eta_h_info.get("warning"):
        warnings.append(eta_h_info.get("warning"))
    add_context_warnings(data, warnings)
    result = {
        "base_rho": base_rho,
        "design_rho": rho,
        "single_vertical_r": rv,
        "vertical_eta": eta,
        "vertical_eta_info": eta_info,
        "vertical_eta_layout": eta_layout,
        "vertical_group_r": rvg,
        "horizontal_r": rh,
        "horizontal_model": horizontal_model,
        "horizontal_eta": (eta_h_info.get("eta") if eta_h_info else None),
        "horizontal_eta_info": eta_h_info,
        "horizontal_effective_r": rh_effective,
        "total_r": total,
        "criterion": criterion,
        "criteria": criteria,
        "warnings": warnings
    }
    result["grounding_conductor"] = calculate_grounding_conductor(data)
    result["checks"] = evaluate_checks(data, result)
    _append_grounding_conductor_checks(result["checks"], result["grounding_conductor"])
    return result


def optimize(data):
    """Предварительный перебор геометрии. Возвращает вариант, проходящий проектную цель.
    Это не экономическая оптимизация и не численный расчет поля растекания.
    """
    base_result = calculate(data)
    criterion = base_result["criterion"]
    if not criterion.get("available"):
        return None, base_result
    limit_r = criterion.get("max")
    best = None
    best_score = None

    length_candidates = [3.0, 4.5, 6.0, 7.5, 9.0, 12.0, 15.0]
    for length_m in length_candidates:
        for count in range(1, 13):
            candidate = dict(data)
            candidate["vertical_length"] = length_m
            candidate["vertical_count"] = count
            candidate["vertical_spacing"] = max(length_m, _num(data, "vertical_spacing", length_m))
            if candidate.get("horizontal_enabled", True):
                candidate["horizontal_length"] = max(
                    _num(data, "horizontal_length", 1.0),
                    max(1, count - 1) * candidate["vertical_spacing"])
            try:
                result = calculate(candidate)
            except Exception:
                continue
            if result["total_r"] <= limit_r:
                hlen = _num(candidate, "horizontal_length", 0.0) if candidate.get("horizontal_enabled", True) else 0.0
                score = length_m * count + 0.15 * hlen
                if best is None or score < best_score:
                    best = (candidate, result)
                    best_score = score
    return best, base_result


def optimize_open_contour_length(data, available_length_m, max_count=20):
    """Find the shortest tabular-method row that satisfies the design target.

    The vertical-electrode utilization tables are used only in their supported
    range a/L=1..3.  For each integer electrode count, the smallest passing
    spacing is found and the globally shortest row length is returned.
    """
    available = float(available_length_m or 0.0)
    rod_length = _num(data, "vertical_length", 0.0)
    if available <= 0.0:
        raise ValueError(u"Длина направляющей незамкнутого контура должна быть больше 0 м.")
    if rod_length <= 0.0:
        raise ValueError(u"Длина вертикального электрода должна быть больше 0 м.")

    criterion_probe = dict(data)
    criterion_probe["layout_mode"] = u"Незамкнутый контур"
    criterion_probe["close_loop"] = False
    criterion_probe["vertical_eta_layout"] = ETA_LAYOUT_ROW
    criterion_probe["vertical_count"] = 2
    criterion_probe["vertical_spacing"] = rod_length
    criterion_probe["horizontal_length"] = max(rod_length, min(available, rod_length))
    probe_result = calculate(criterion_probe)
    criterion = probe_result.get("criterion") or {}
    if not criterion.get("available"):
        raise ValueError(
            u"Для автоматического подбора длины задайте применимый предел сопротивления ЗУ "
            u"(например, поле «Доп. проектный предел R»).")
    # The user requested the shortest length for the explicitly established
    # resistance. The design-margin target remains visible separately, but must
    # not silently replace that established limit during geometric minimization.
    target_r = float(criterion.get("max"))
    reserve_target_r = float(criterion.get("target"))

    def evaluate(count, spacing):
        candidate = dict(data)
        used_length = float(count - 1) * float(spacing)
        candidate["layout_mode"] = u"Незамкнутый контур"
        candidate["close_loop"] = False
        candidate["vertical_eta_layout"] = ETA_LAYOUT_ROW
        candidate["vertical_count"] = int(count)
        candidate["vertical_spacing"] = float(spacing)
        if candidate.get("horizontal_enabled", True):
            candidate["horizontal_length"] = used_length
        result = calculate(candidate)
        return candidate, result

    best = None
    max_count = max(2, min(20, int(max_count or 20)))
    for count in range(2, max_count + 1):
        minimum_spacing = rod_length
        maximum_spacing = min(3.0 * rod_length, available / float(count - 1))
        if maximum_spacing + 1e-9 < minimum_spacing:
            continue

        low = minimum_spacing
        high = maximum_spacing
        low_candidate, low_result = evaluate(count, low)
        if low_result["total_r"] <= target_r:
            selected_candidate, selected_result = low_candidate, low_result
        else:
            high_candidate, high_result = evaluate(count, high)
            if high_result["total_r"] > target_r:
                continue
            # In the supported row tables, increasing spacing improves eta and
            # increases the useful strip length. Locate the first passing value.
            for _ in range(45):
                mid = (low + high) / 2.0
                mid_candidate, mid_result = evaluate(count, mid)
                if mid_result["total_r"] <= target_r:
                    high = mid
                    high_candidate, high_result = mid_candidate, mid_result
                else:
                    low = mid
            selected_candidate, selected_result = high_candidate, high_result

        used_length = float(selected_candidate["horizontal_length"] if
                            selected_candidate.get("horizontal_enabled", True) else
                            (count - 1) * selected_candidate["vertical_spacing"])
        option = {
            "data": selected_candidate,
            "result": selected_result,
            "count": count,
            "spacing_m": float(selected_candidate["vertical_spacing"]),
            "used_length_m": used_length,
            "available_length_m": available,
            "target_r": target_r,
            "reserve_target_r": reserve_target_r,
            "reserve_achieved": bool(selected_result["total_r"] <= reserve_target_r),
            "limit_r": float(criterion.get("max")),
        }
        if best is None or (option["used_length_m"], option["count"]) < (best["used_length_m"], best["count"]):
            best = option

    if best is None:
        raise ValueError(
            u"Длины направляющей {:.2f} м недостаточно для достижения установленного предела "
            u"R ≤ {:.2f} Ом табличным методом ряда при a/L=1…3 и количестве электродов до {}."
            .format(available, target_r, max_count))
    return best


def _append_criteria_report(lines, result):
    criteria = result.get("criteria") or {}
    lines.append(u"")
    lines.append(u"КРИТЕРИИ ДОПУСТИМОСТИ")

    normative = criteria.get("normative") or {}
    lines.append(u"1. Нормативный критерий:")
    if normative.get("available"):
        lines.append(u"   R ≤ {:.2f} Ом; проектная цель с запасом ≤ {:.2f} Ом".format(
            normative["max"], normative["target"]))
    else:
        lines.append(u"   Универсальный нормативный предел R в омах для выбранного режима не назначен.")
    lines.append(u"   Основание: {}".format(normative.get("basis", u"")))
    if normative.get("explanation"):
        lines.append(u"   {}".format(normative.get("explanation")))

    project = criteria.get("project") or {}
    if project.get("available"):
        lines.append(u"2. Дополнительный проектный критерий: R ≤ {:.2f} Ом".format(project["max"]))
        lines.append(u"   Основание: {}".format(project.get("basis", u"")))
    else:
        lines.append(u"2. Дополнительный проектный критерий: не задан.")

    gas_tu = criteria.get("gas_tu") or {}
    if gas_tu.get("available"):
        lines.append(u"3. Дополнительное требование ТУ газоснабжения: R ≤ {:.2f} Ом".format(gas_tu["max"]))
        lines.append(u"   Реквизиты/основание: {}".format(gas_tu.get("basis", u"")))
        lines.append(u"   Важно: значение принято из ТУ и не выведено программой как универсальная норма для газового котла.")
    else:
        lines.append(u"3. Дополнительное требование ТУ газоснабжения: не применяется.")

    effective = criteria.get("effective") or result.get("criterion") or {}
    if effective.get("available"):
        lines.append(u"Итоговый применимый предел для проверки: R ≤ {:.2f} Ом".format(effective["max"]))
        lines.append(u"Проектная цель с коэффициентом запаса: R ≤ {:.2f} Ом".format(effective["target"]))
    else:
        lines.append(u"Итоговый предел R в омах не определен; автоподбор по сопротивлению отключен.")


def format_report(data, result, title=None):
    if title is None:
        title = u"РАСЧЕТ ЗАЗЕМЛЯЮЩЕГО УСТРОЙСТВА"
    lines = []
    lines.append(title)
    lines.append(u"Версия расчетного ядра: pyRevit {}".format(display_version()))
    lines.append(u"")
    lines.append(u"ИСХОДНЫЕ ДАННЫЕ")
    _panel_name = data.get("gzsh_panel_display_name") or data.get("gzsh_panel_mark") or data.get("gzsh_panel_name")
    if _panel_name:
        _panel_extra = []
        if data.get("gzsh_panel_level"):
            _panel_extra.append(u"уровень {}".format(data.get("gzsh_panel_level")))
        if data.get("gzsh_panel_element_id") is not None:
            _panel_extra.append(u"ElementId {}".format(data.get("gzsh_panel_element_id")))
        lines.append(u"Панель с ГЗШ: {}{}".format(_panel_name, u" (" + u"; ".join(_panel_extra) + u")" if _panel_extra else u""))
    else:
        lines.append(u"Панель с ГЗШ: не выбрана")
    lines.append(u"Система: {}".format(data.get("system", "")))
    if data.get("project_object_type"):
        lines.append(u"Тип объекта: {}".format(data.get("project_object_type")))
    lines.append(u"Назначение: {}".format(data.get("purpose", "")))
    lines.append(u"Ввод: {}".format(data.get("supply_type", "")))
    if data.get("project_address"):
        lines.append(u"Адрес проекта: {}".format(data.get("project_address")))
    if data.get("allocated_power_kw"):
        lines.append(u"Выделенная мощность: {} кВт".format(data.get("allocated_power_kw")))
    if data.get("incoming_device_type"):
        lines.append(u"Аппарат на вводе: {}".format(data.get("incoming_device_type")))
    if data.get("normalized_address") and data.get("normalized_address") != data.get("project_address"):
        lines.append(u"Нормализованный адрес: {}".format(data.get("normalized_address")))
    if data.get("latitude") is not None and data.get("longitude") is not None:
        lines.append(u"Координаты WGS84: {}; {}".format(data.get("latitude"), data.get("longitude")))
    if data.get("climate_region"):
        lines.append(u"Климатический профиль: {}".format(data.get("climate_region")))
    if data.get("climate_station"):
        lines.append(u"Климатическая станция: {}".format(data.get("climate_station")))
    if data.get("soil_type"):
        lines.append(u"Принятый тип грунта: {}".format(data.get("soil_type")))
    if data.get("location_data_source"):
        lines.append(u"Источник климатических/грунтовых данных: {}".format(data.get("location_data_source")))
    lines.append(u"ρ исходное = {:.1f} Ом·м; kсез = {:.2f}; ρрасч = {:.1f} Ом·м".format(
        result["base_rho"], _num(data, "seasonal_factor", 1.0), result["design_rho"]))
    lines.append(u"Вертикальные электроды: {} шт.; L={:.2f} м; Ø={:.1f} мм; шаг={:.2f} м; верх={:.2f} м".format(
        _int(data, "vertical_count"), _num(data, "vertical_length"), _num(data, "vertical_diameter_mm"),
        _num(data, "vertical_spacing"), _num(data, "vertical_top_depth")))
    if data.get("horizontal_enabled", True):
        lines.append(u"Горизонтальный электрод: L={:.2f} м; {}×{} мм; глубина={:.2f} м".format(
            _num(data, "horizontal_length"), _num(data, "horizontal_width_mm"),
            _num(data, "horizontal_thickness_mm", 4.0), _num(data, "horizontal_depth")))
    else:
        lines.append(u"Горизонтальный электрод: не учитывается")

    lines.append(u"")
    lines.append(u"РЕЗУЛЬТАТ")
    lines.append(u"R одного вертикального электрода = {:.2f} Ом".format(result["single_vertical_r"]))
    lines.append(u"η вертикальных электродов = {:.3f}".format(result["vertical_eta"]))
    eta_info = result.get("vertical_eta_info") or {}
    if eta_info.get("eta_max") is not None and eta_info.get("eta_max") != eta_info.get("eta_min"):
        lines.append(u"Диапазон η по таблице = {:.3f}…{:.3f}; в расчет принято нижнее значение.".format(eta_info.get("eta_min"), eta_info.get("eta_max")))
    lines.append(u"Схема η: {}; метод: {}; источник: {}".format(
        result.get("vertical_eta_layout", u"—"), eta_info.get("method", u"—"), eta_info.get("source", u"—")))
    lines.append(u"R группы вертикальных электродов = {:.2f} Ом".format(result["vertical_group_r"]))
    if result["horizontal_r"] is not None:
        _hm = result.get("horizontal_model")
        _hlabel = u"эквивалентное кольцо" if _hm == "equivalent_ring" else u"прямая полоса"
        lines.append(u"R горизонтального электрода ({}) = {:.2f} Ом".format(_hlabel, result["horizontal_r"]))
        if result.get("horizontal_eta") is not None:
            lines.append(u"η соединительной полосы = {:.3f}; Rг/ηг = {:.2f} Ом".format(
                result.get("horizontal_eta"), result.get("horizontal_effective_r")))
    lines.append(u"R итоговое = {:.2f} Ом".format(result["total_r"]))

    _append_grounding_conductor_report(lines, result)
    _append_criteria_report(lines, result)

    lines.append(u"ПРОВЕРКИ")
    for check in result.get("checks", []):
        if check["severity"] == SEV_ERROR and not check["passed"]:
            mark = u"[ОШИБКА]"
        elif check["severity"] == SEV_WARNING:
            mark = u"[ПРЕДУПРЕЖДЕНИЕ]"
        else:
            mark = u"[OK]"
        lines.append(u"{} {}: {}".format(mark, check["title"], check["message"]))
        if check.get("reference"):
            lines.append(u"  Норма: {}".format(check["reference"]))

    lines.append(u"")
    lines.append(u"ОГРАНИЧЕНИЯ")
    for warning in result.get("warnings", []):
        lines.append(u"- {}".format(warning))
    return u"\n".join(lines)

# --- v0.4: отдельный расчет одиночного штыревого заземлителя ---
ELECTRODE_STEEL_GALV = u"Сталь горячего цинкования"
ELECTRODE_STAINLESS = u"Нержавеющая сталь"
ELECTRODE_COPPER_BONDED = u"Сталь с гальваническим медным покрытием"
ELECTRODE_COPPER = u"Медь"

SOIL_MODEL_HOMOGENEOUS = u"Однородный грунт"
SOIL_MODEL_THREE_LAYER = u"До 3 слоев (эквивалентное rho)"

SEASON_RHO_ALREADY_DESIGN = u"rho уже расчетное (k не применять)"
SEASON_UNIFORM = u"kсез по всей длине"
SEASON_SHALLOW = u"kсез только до глубины промерзания"


def single_rod_min_diameter_mm(material, combined_lps=False):
    """Минимальный диаметр вертикального стержня по табл. 54.1 ГОСТ Р 50571.5.54-2024.

    Для меди значение 12 мм в скобках допускается только для защиты от поражения
    электрическим током; при молниезащите принимается 15 мм.
    """
    if material == ELECTRODE_STEEL_GALV:
        return 16.0
    if material == ELECTRODE_STAINLESS:
        return 16.0
    if material == ELECTRODE_COPPER_BONDED:
        return 14.0
    if material == ELECTRODE_COPPER:
        return 15.0 if combined_lps else 12.0
    return None


def validate_single_rod(data):
    rho = _num(data, "rho", 0.0)
    kseason = _num(data, "seasonal_factor", 0.0)
    length = _num(data, "vertical_length", 0.0)
    diameter_mm = _num(data, "vertical_diameter_mm", 0.0)
    top_depth = _num(data, "vertical_top_depth", -1.0)
    if rho <= 0:
        raise ValueError(u"Удельное сопротивление грунта должно быть больше 0 Ом·м.")
    if kseason <= 0:
        raise ValueError(u"Сезонный коэффициент должен быть больше 0.")
    if length <= 0:
        raise ValueError(u"Длина штыревого заземлителя должна быть больше 0 м.")
    if diameter_mm <= 0:
        raise ValueError(u"Диаметр штыревого заземлителя должен быть больше 0 мм.")
    if top_depth < 0:
        raise ValueError(u"Глубина до верха штыря не может быть отрицательной.")
    module_step = _num(data, "rod_module_step", 1.5)
    if module_step <= 0:
        raise ValueError(u"Длина одной модульной секции должна быть больше 0 м.")
    # В используемой формуле T = h + L/2; условие 4T-L > 0.
    t = top_depth + length / 2.0
    if 4.0 * t - length <= 0:
        raise ValueError(u"Некорректная геометрия: для расчетной формулы должно выполняться 4T-L>0.")

    soil_model = data.get("soil_model", SOIL_MODEL_HOMOGENEOUS)
    if soil_model == SOIL_MODEL_THREE_LAYER:
        layer1_bottom = _num(data, "soil_layer1_bottom", 0.0)
        layer2_bottom = _num(data, "soil_layer2_bottom", 0.0)
        if layer1_bottom <= 0 or layer2_bottom <= layer1_bottom:
            raise ValueError(
                u"Для послойной оценки должны выполняться условия: H1>0 и H2>H1.")
        for key, label in (("soil_layer1_rho", u"rho слоя 1"),
                           ("soil_layer2_rho", u"rho слоя 2"),
                           ("soil_layer3_rho", u"rho слоя 3")):
            if _num(data, key, 0.0) <= 0:
                raise ValueError(u"{} должно быть больше 0 Ом·м.".format(label))

    season_model = data.get("seasonal_model", SEASON_UNIFORM)
    if season_model == SEASON_SHALLOW:
        frost_depth = _num(data, "frost_depth", None)
        if frost_depth is None or frost_depth < 0:
            raise ValueError(
                u"Для режима сезонности по верхнему слою задайте неотрицательную глубину промерзания.")
        if frost_depth == 0:
            mt = _num(data, "freezing_index_mt", None)
            climatic_zero = bool(data.get("frost_zero_is_climatic")) or (mt is not None and mt <= 0)
            if not climatic_zero:
                raise ValueError(
                    u"Глубина промерзания 0 м допустима только когда автоматический климатический расчет дает Mt=0.")


def _interval_overlap(start_a, end_a, start_b, end_b):
    start = max(start_a, start_b)
    end = min(end_a, end_b)
    return max(0.0, end - start), start, end


def _single_rod_base_layers(data):
    """Возвращает расчетные слои от поверхности вниз.

    Послойный режим использует линейно-взвешенное эквивалентное удельное
    сопротивление. Это прозрачная предварительная оценка, а не точное решение
    поля токов на границах слоев.
    """
    base_rho = _num(data, "rho")
    if data.get("soil_model", SOIL_MODEL_HOMOGENEOUS) != SOIL_MODEL_THREE_LAYER:
        return [(0.0, float("inf"), base_rho, u"Однородный грунт")]

    h1 = _num(data, "soil_layer1_bottom")
    h2 = _num(data, "soil_layer2_bottom")
    return [
        (0.0, h1, _num(data, "soil_layer1_rho"), u"Слой 1"),
        (h1, h2, _num(data, "soil_layer2_rho"), u"Слой 2"),
        (h2, float("inf"), _num(data, "soil_layer3_rho"), u"Слой 3")
    ]


def single_rod_effective_resistivity(data):
    """Эквивалентное rho вдоль фактически погруженной части электрода.

    Режимы сезонности разделены явно:
    - расчетное rho: пользователь уже учел неблагоприятный сезон;
    - единый kсез: совместимость с традиционным однородным расчетом;
    - верхний слой: kсез действует только на часть стержня выше заданной
      глубины промерзания. Полученное rho является длино-взвешенной инженерной
      оценкой для предварительного подбора.
    """
    length_m = _num(data, "vertical_length")
    top_depth_m = _num(data, "vertical_top_depth")
    bottom_depth_m = top_depth_m + length_m
    kseason = _num(data, "seasonal_factor", 1.0)
    season_model = data.get("seasonal_model", SEASON_UNIFORM)
    frost_depth = _num(data, "frost_depth", None)

    segments = []

    def add_segment(start, end, rho_value, layer_name, factor):
        if end <= start:
            return
        segments.append({
            "top": start,
            "bottom": end,
            "length": end - start,
            "layer": layer_name,
            "natural_rho": rho_value,
            "seasonal_factor": factor,
            "design_rho": rho_value * factor
        })

    for layer_top, layer_bottom, rho_value, layer_name in _single_rod_base_layers(data):
        overlap, start, end = _interval_overlap(
            top_depth_m, bottom_depth_m, layer_top, layer_bottom)
        if overlap <= 0:
            continue
        if season_model == SEASON_RHO_ALREADY_DESIGN:
            add_segment(start, end, rho_value, layer_name, 1.0)
        elif season_model == SEASON_SHALLOW:
            boundary = frost_depth
            shallow_end = min(end, boundary)
            add_segment(start, shallow_end, rho_value, layer_name, kseason)
            add_segment(max(start, boundary), end, rho_value, layer_name, 1.0)
        else:
            add_segment(start, end, rho_value, layer_name, kseason)

    covered = sum(item["length"] for item in segments)
    if abs(covered - length_m) > 1e-7:
        raise ValueError(u"Не удалось покрыть всю длину электрода расчетными слоями грунта.")

    natural_rho = sum(
        item["natural_rho"] * item["length"] for item in segments) / length_m
    design_rho = sum(
        item["design_rho"] * item["length"] for item in segments) / length_m
    effective_factor = design_rho / natural_rho if natural_rho > 0 else 1.0
    return {
        "natural_rho": natural_rho,
        "design_rho": design_rho,
        "effective_seasonal_factor": effective_factor,
        "segments": segments,
        "seasonal_model": season_model,
        "soil_model": data.get("soil_model", SOIL_MODEL_HOMOGENEOUS)
    }


def calculate_single_rod(data):
    """Расчет отдельного вертикального цилиндрического (штыревого) заземлителя.

    Горизонтальный соединительный проводник в сопротивление не включается: это
    позволяет получить самостоятельную характеристику штыря и не смешивать ее
    с сопротивлением заземляющего проводника до ГЗШ.
    """
    validate_single_rod(data)
    base_rho = _num(data, "rho")
    length_m = _num(data, "vertical_length")
    diameter_mm = _num(data, "vertical_diameter_mm")
    top_depth_m = _num(data, "vertical_top_depth")
    diameter_m = diameter_mm / 1000.0
    center_depth_m = top_depth_m + length_m / 2.0
    bottom_depth_m = top_depth_m + length_m

    rho_model = single_rod_effective_resistivity(data)
    natural_rho = rho_model["natural_rho"]
    design_rho = rho_model["design_rho"]

    denominator = 4.0 * center_depth_m - length_m
    logarithm_length = math.log((2.0 * length_m) / diameter_m)
    logarithm_depth = 0.5 * math.log(
        (4.0 * center_depth_m + length_m) / denominator)
    geometric_factor = (
        logarithm_length + logarithm_depth) / (2.0 * math.pi * length_m)

    resistance = single_vertical_resistance(
        design_rho, length_m, diameter_m, top_depth_m)

    criteria = resolve_resistance_criteria(data, natural_rho)
    criterion = criteria["effective"]
    warnings = [
        u"Рассчитано сопротивление только одиночного вертикального штыря; вклад заземляющего проводника/полосы не учитывается.",
        u"Фактическое сопротивление ЗУ должно быть подтверждено измерением после монтажа.",
        u"Утверждение о типичном снижении фактического R в 2–3 раза не использовано как расчетный коэффициент: оно должно подтверждаться измерением на конкретной площадке."
    ]
    if rho_model["soil_model"] == SOIL_MODEL_THREE_LAYER:
        warnings.append(
            u"Послойное rho приведено к длино-взвешенному эквиваленту. Это предварительная оценка; точное влияние границ слоев требует полевого/численного расчета по данным ИГИ.")
    else:
        warnings.append(
            u"Однородный грунт является расчетным упрощением; для глубокого электрода предпочтительны данные ИГИ по слоям.")
    if rho_model["seasonal_model"] == SEASON_SHALLOW:
        _frost = _num(data, "frost_depth", None)
        if _frost == 0:
            warnings.append(
                u"Автоматический климатический расчет дал Mt=0 и глубину промерзания 0 м; в режиме верхнего слоя сезонный коэффициент к замороженному слою не применяется.")
        else:
            warnings.append(
                u"Сезонный коэффициент применен только к части электрода в пределах заданной глубины промерзания; это осознанная предварительная поправка для глубокого стержня.")
    add_context_warnings(data, warnings)

    module_step = _num(data, "rod_module_step", 1.5)
    module_ratio = length_m / module_step
    rounded_modules = int(round(module_ratio))
    module_count = rounded_modules if abs(module_ratio - rounded_modules) <= 1e-7 else None
    if module_count is None:
        warnings.append(
            u"Длина L не кратна заданной длине модульной секции; проверьте фактическую комплектацию стержней и муфт.")

    result = {
        "base_rho": base_rho,
        "natural_rho": natural_rho,
        "design_rho": design_rho,
        "effective_seasonal_factor": rho_model["effective_seasonal_factor"],
        "rho_segments": rho_model["segments"],
        "seasonal_model": rho_model["seasonal_model"],
        "soil_model": rho_model["soil_model"],
        "single_vertical_r": resistance,
        "vertical_eta": 1.0,
        "vertical_group_r": resistance,
        "horizontal_r": None,
        "total_r": resistance,
        "criterion": criterion,
        "criteria": criteria,
        "center_depth_m": center_depth_m,
        "bottom_depth_m": bottom_depth_m,
        "logarithm_length": logarithm_length,
        "logarithm_depth": logarithm_depth,
        "geometric_factor": geometric_factor,
        "module_count": module_count,
        "module_step_m": module_step,
        "warnings": warnings
    }
    checks = evaluate_checks(data, result)

    material = data.get("electrode_material", ELECTRODE_STEEL_GALV)
    combined_lps = data.get("purpose") == PURPOSE_COMBINED_LPS
    min_d = single_rod_min_diameter_mm(material, combined_lps)
    if min_d is not None:
        ok = diameter_mm >= min_d
        checks.append(_check(
            "SINGLE_ROD_DIAMETER", u"Минимальный диаметр вертикального электрода", ok,
            SEV_INFO if ok else SEV_ERROR,
            u"Принято Ø{:.1f} мм; для материала «{}» проверяемый минимум Ø{:.1f} мм{} .".format(
                diameter_mm, material, min_d,
                u" при совмещении с молниезащитой" if combined_lps else u""),
            u"ГОСТ Р 50571.5.54-2024, табл. 54.1"))

    frost_depth = _num(data, "frost_depth", None)
    if frost_depth is not None and frost_depth > 0:
        below = bottom_depth_m - frost_depth
        ok = below > 0
        checks.append(_check(
            "SINGLE_ROD_FROST", u"Положение электрода относительно глубины промерзания", ok,
            SEV_INFO if ok else SEV_WARNING,
            u"Низ электрода {:.2f} м; заданная глубина промерзания {:.2f} м; запас ниже границы {:.2f} м.".format(
                bottom_depth_m, frost_depth, below),
            u"ГОСТ Р 50571.5.54-2024, п. 542.2.4 и приложение с расчетными рекомендациями"))

    slenderness = length_m / diameter_m
    checks.append(_check(
        "SINGLE_ROD_SLENDERNESS", u"Область применимости стержневой формулы", slenderness >= 20.0,
        SEV_INFO if slenderness >= 20.0 else SEV_WARNING,
        u"Отношение L/d={:.0f}; для формулы принят тонкий цилиндрический электрод.".format(slenderness),
        u"Инженерная проверка геометрической применимости расчетной модели"))

    result["grounding_conductor"] = calculate_grounding_conductor(data)
    _append_grounding_conductor_checks(checks, result["grounding_conductor"])
    result["checks"] = checks
    return result


def format_single_rod_report(data, result):
    lines = []
    lines.append(u"РАСЧЕТ ОДИНОЧНОГО МОДУЛЬНОГО ЗАЗЕМЛИТЕЛЯ")
    lines.append(u"Версия расчетного ядра: pyRevit {}".format(display_version()))
    lines.append(u"")
    lines.append(u"ИСХОДНЫЕ ДАННЫЕ")
    _panel_name = data.get("gzsh_panel_display_name") or data.get("gzsh_panel_mark") or data.get("gzsh_panel_name")
    if _panel_name:
        _panel_extra = []
        if data.get("gzsh_panel_level"):
            _panel_extra.append(u"уровень {}".format(data.get("gzsh_panel_level")))
        if data.get("gzsh_panel_element_id") is not None:
            _panel_extra.append(u"ElementId {}".format(data.get("gzsh_panel_element_id")))
        lines.append(u"Панель с ГЗШ: {}{}".format(_panel_name, u" (" + u"; ".join(_panel_extra) + u")" if _panel_extra else u""))
    else:
        lines.append(u"Панель с ГЗШ: не выбрана")
    lines.append(u"Система: {}".format(data.get("system", "")))
    if data.get("project_object_type"):
        lines.append(u"Тип объекта: {}".format(data.get("project_object_type")))
    lines.append(u"Назначение: {}".format(data.get("purpose", "")))
    lines.append(u"Ввод: {}".format(data.get("supply_type", "")))
    if data.get("project_address"):
        lines.append(u"Адрес проекта: {}".format(data.get("project_address")))
    if data.get("allocated_power_kw"):
        lines.append(u"Выделенная мощность: {} кВт".format(data.get("allocated_power_kw")))
    if data.get("incoming_device_type"):
        lines.append(u"Аппарат на вводе: {}".format(data.get("incoming_device_type")))
    if data.get("normalized_address") and data.get("normalized_address") != data.get("project_address"):
        lines.append(u"Нормализованный адрес: {}".format(data.get("normalized_address")))
    if data.get("latitude") is not None and data.get("longitude") is not None:
        lines.append(u"Координаты WGS84: {}; {}".format(data.get("latitude"), data.get("longitude")))
    if data.get("climate_region"):
        lines.append(u"Климатический профиль: {}".format(data.get("climate_region")))
    if data.get("climate_station"):
        lines.append(u"Климатическая станция: {}".format(data.get("climate_station")))
    if data.get("soil_type"):
        lines.append(u"Принятый тип грунта: {}".format(data.get("soil_type")))
    if data.get("location_data_source"):
        lines.append(u"Источник климатических/грунтовых данных: {}".format(data.get("location_data_source")))
    lines.append(u"Материал электрода: {}".format(data.get("electrode_material", ELECTRODE_COPPER_BONDED)))
    lines.append(u"Модель грунта: {}".format(result.get("soil_model", SOIL_MODEL_HOMOGENEOUS)))
    lines.append(u"Модель сезонности: {}".format(result.get("seasonal_model", SEASON_UNIFORM)))
    lines.append(u"rho поля/справочника = {:.1f} Ом·м; rho экв. без сезонности = {:.1f} Ом·м".format(
        result["base_rho"], result["natural_rho"]))
    lines.append(u"kсез заданный = {:.2f}; kсез эффективный = {:.3f}; rho расч. экв. = {:.1f} Ом·м".format(
        _num(data, "seasonal_factor", 1.0), result["effective_seasonal_factor"], result["design_rho"]))
    lines.append(u"Штырь: L={:.2f} м; Ø={:.1f} мм; глубина верха h={:.2f} м".format(
        _num(data, "vertical_length"), _num(data, "vertical_diameter_mm"), _num(data, "vertical_top_depth")))
    lines.append(u"Глубина до середины T={:.2f} м; глубина низа={:.2f} м".format(
        result["center_depth_m"], result["bottom_depth_m"]))
    if result.get("module_count") is not None:
        lines.append(u"Модульная сборка: {} секц. × {:.2f} м = {:.2f} м".format(
            result["module_count"], result["module_step_m"], _num(data, "vertical_length")))
    else:
        lines.append(u"Модульная сборка: L не кратна секции {:.2f} м".format(result["module_step_m"]))
    frost = _num(data, "frost_depth", None)
    if frost is not None:
        lines.append(u"Заданная глубина промерзания = {:.2f} м".format(frost))

    lines.append(u"")
    lines.append(u"РАСЧЕТНЫЙ ПРОФИЛЬ ПО ГЛУБИНЕ")
    for segment in result.get("rho_segments", []):
        lines.append(
            u"- {}: {:.2f}–{:.2f} м (L={:.2f} м), rho={:.1f}, k={:.2f}, rhoрасч={:.1f} Ом·м".format(
                segment["layer"], segment["top"], segment["bottom"], segment["length"],
                segment["natural_rho"], segment["seasonal_factor"], segment["design_rho"]))

    lines.append(u"")
    lines.append(u"РАСЧЕТ")
    lines.append(u"R = ρ/(2πL) × [ln(2L/d) + 0,5×ln((4T+L)/(4T-L))]")
    lines.append(u"ln(2L/d) = {:.5f}; 0,5×ln((4T+L)/(4T-L)) = {:.5f}".format(
        result["logarithm_length"], result["logarithm_depth"]))
    lines.append(u"Геометрический коэффициент K = {:.6f} 1/м; R = rhoрасч.экв × K".format(
        result["geometric_factor"]))
    lines.append(u"R одиночного модульного электрода = {:.2f} Ом".format(result["single_vertical_r"]))
    lines.append(u"Горизонтальный заземляющий проводник в данном режиме не учитывается.")
    lines.append(u"Формула сверена со статьей ZANDZ «Расчет заземления»; статья является инженерным источником, а не нормативным критерием допустимости.")

    if result.get("selection_table"):
        lines.append(u"")
        lines.append(u"АВТОПОДБОР ПО МОДУЛЬНЫМ СЕКЦИЯМ")
        for item in result["selection_table"]:
            lines.append(u"- {:>2} секц.; L={:>5.2f} м; R={:>7.2f} Ом; {}".format(
                item["modules"], item["length"], item["resistance"],
                u"проходит" if item["passes"] else u"не проходит"))

    _append_grounding_conductor_report(lines, result)
    _append_criteria_report(lines, result)

    lines.append(u"ПРОВЕРКИ")
    for check in result.get("checks", []):
        if check["severity"] == SEV_ERROR and not check["passed"]:
            mark = u"[ОШИБКА]"
        elif check["severity"] == SEV_WARNING:
            mark = u"[ПРЕДУПРЕЖДЕНИЕ]"
        else:
            mark = u"[OK]"
        lines.append(u"{} {}: {}".format(mark, check["title"], check["message"]))
        if check.get("reference"):
            lines.append(u"  Норма: {}".format(check["reference"]))

    lines.append(u"")
    lines.append(u"ПРИМЕЧАНИЯ")
    for warning in result.get("warnings", []):
        lines.append(u"- {}".format(warning))
    return u"\n".join(lines)


def optimize_single_rod_length(data):
    """Подбор длины одного глубинного штыря дискретными секциями.

    Возвращает (candidate_data, result) либо (None, base_result), если критерий
    сопротивления автоматически не определен или в заданном диапазоне не достигнут.
    """
    base_result = calculate_single_rod(data)
    criterion = base_result.get("criterion") or {}
    if not criterion.get("available"):
        return None, base_result
    limit_r = criterion.get("max")
    step = _num(data, "rod_module_step", 1.5)
    max_length = _num(data, "rod_max_length", 30.0)
    start = _num(data, "vertical_length", step)
    if step <= 0 or max_length <= 0:
        raise ValueError(u"Шаг секции и максимальная длина должны быть больше 0.")
    # Начинаем не ниже одного модуля и не ниже заданной пользователем исходной длины.
    n = max(1, int(math.ceil(start / step)))
    selection_table = []
    while n * step <= max_length + 1e-9:
        candidate = dict(data)
        candidate["vertical_length"] = n * step
        result = calculate_single_rod(candidate)
        selection_table.append({
            "modules": n,
            "length": n * step,
            "resistance": result["total_r"],
            "passes": result["total_r"] <= limit_r
        })
        if result["total_r"] <= limit_r:
            result["optimized_single_rod"] = True
            result["selected_modules"] = n
            result["module_step_m"] = step
            result["selection_table"] = selection_table
            return (candidate, result), base_result
        n += 1
    base_result["selection_table"] = selection_table
    return None, base_result
