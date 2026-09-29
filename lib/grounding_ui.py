# -*- coding: utf-8 -*-
from __future__ import division, print_function

import clr
clr.AddReference("System.Windows.Forms")
clr.AddReference("System.Drawing")

from System.Drawing import Size, Point, Font, FontStyle, Color
from System.Windows.Forms import (Form, Label, TextBox, ComboBox, CheckBox, Button,
                                  FormBorderStyle, DialogResult, ComboBoxStyle, Panel,
                                  DockStyle, FormStartPosition, BorderStyle, MessageBox)

from grounding_core import (SYSTEM_TNCS, SYSTEM_TNS, SYSTEM_TT,
                            PURPOSE_PROTECTIVE, PURPOSE_REPEATED_PEN, PURPOSE_TNCS_INPUT,
                            PURPOSE_SOURCE_NEUTRAL, PURPOSE_GENERATOR_NEUTRAL,
                            PURPOSE_COMBINED_LPS, PURPOSE_FUNCTIONAL, SUPPLY_UNKNOWN, SUPPLY_OVERHEAD,
                            SUPPLY_CABLE, SUPPLY_GENERATOR, MAT_CU, MAT_AL, MAT_STEEL,
                            BREAKER_STD_60898, BREAKER_STD_OTHER, BREAKER_CURVES, number_value,
                            ETA_LAYOUT_AUTO, ETA_LAYOUT_ROW, ETA_LAYOUT_CONTOUR)


from panel_ui_common import (PANEL_FIELDS, panel_key, panel_choice_text, refresh_panel_selector,
                             update_panel_selector_state, on_panel_changed, collect_controls,
                             request_show_panel)
from plugin_version import display_version


def _text(value):
    return u"" if value is None else u"{}".format(value)


def _bool_value(value, fallback=False):
    if value is None:
        return bool(fallback)
    if isinstance(value, bool):
        return value
    text = _text(value).strip().lower()
    if text in (u"true", u"1", u"yes", u"да"):
        return True
    if text in (u"false", u"0", u"no", u"нет", u""):
        return False
    return bool(value)


class GroundingInputForm(Form):
    def __init__(self, defaults=None, panel_options=None, panel_shower=None):
        self.defaults = defaults or {}
        self.panel_options = list(panel_options or [])
        self._updating_panel_combo = False
        self.panel_shower = panel_shower
        self._panel_show_requested = None
        self._panel_data = {}
        for _key in PANEL_FIELDS:
            if _key in self.defaults:
                self._panel_data[_key] = self.defaults.get(_key)
        self.Text = u"ЭОМ — Расчет и моделирование ЗУ ({})".format(display_version())
        self.ClientSize = Size(900, 940)
        self.MinimumSize = Size(860, 720)
        self.FormBorderStyle = FormBorderStyle.Sizable
        self.StartPosition = FormStartPosition.CenterScreen
        self.ShowInTaskbar = True
        self.BackColor = Color.White
        self._controls = {}
        self._y = 16
        self._left = 24
        self._label_x = 28
        self._input_x = 430
        self._label_w = 380
        self._input_w = 400
        self._section_index = 0

        panel = Panel()
        panel.AutoScroll = True
        panel.Dock = DockStyle.Fill
        panel.BackColor = Color.White
        self.Controls.Add(panel)
        self.panel = panel

        self._required_input_header()
        self._intro_note(u"Расчет группового заземляющего устройства. Параметры сети, защиты, вводного кабеля и проводника ЗУ → ГЗШ объединены в верхней шапке; ниже задаются грунт, геометрия и дополнительные условия.")

        self._section(u"1. Объект и автоматические исходные данные")
        self._textbox("project_address", u"Адрес проекта", self._default("project_address", ""))
        self._textbox("climate_region", u"Климатический профиль/подрайон", self._default("climate_region", ""))
        self._textbox("climate_station", u"Расчетная климатическая станция", self._default("climate_station", ""))
        self._textbox("soil_type", u"Тип грунта", self._default("soil_type", ""))
        self._note(self._default("location_data_source", u"Источник данных: ручной ввод"))
        warning = self._default("location_warning", "")
        if warning:
            self._note(warning, warning=True)

        self._section(u"2. Грунт и расчетные ограничения")
        self._textbox("rho", u"ρ грунта, Ом·м", self._default("rho", "100"))
        self._textbox("seasonal_factor", u"Сезонный коэффициент", self._default("seasonal_factor", "1.3"))
        self._readonly_textbox("frost_depth_auto", u"Автоматическая оценка промерзания, м", self._default("frost_depth_auto", self._default("frost_depth", "")))
        self._note(u"Источник автоматической оценки: {}".format(self._default("frost_depth_auto_source", u"не определен")))
        frost_reason = self._default("frost_depth_reason", "")
        if frost_reason:
            self._note(u"Пояснение: {}".format(frost_reason), warning=bool(self.defaults.get("frost_zero_is_climatic", False)))
        mt_value = self.defaults.get("freezing_index_mt")
        if mt_value is not None:
            self._note(u"Коэффициент Mt по отрицательным среднемесячным температурам: {}".format(mt_value))
        self._textbox("frost_depth", u"Принятая глубина промерзания / сезонного слоя, м", self._default("frost_depth", ""))
        self._note(u"Источник принятого значения: {}".format(self._default("frost_depth_source", u"ручной ввод")))
        self._textbox("required_resistance", u"Доп. проектный предел R, Ом (не норма)", self._default("required_resistance", ""))
        self._textbox("design_margin", u"Коэффициент проектного запаса", self._default("design_margin", "0.8"))
        self._checkbox_box(
            u"Дополнительная опция",
            [("high_rho_relaxation", u"Учитывать допуск ПУЭ для ρ > 100 Ом·м, когда это применимо",
              bool(self._default("high_rho_relaxation", True)))],
            note=u"Применяйте только при наличии нормативных оснований для конкретного назначения ЗУ."
        )

        self._section(u"3. Дополнительное требование ТУ газоснабжения")
        self._checkbox_box(
            u"Опция по ТУ газоснабжающей организации",
            [("gas_tu_enabled", u"Учитывать требование ТУ газоснабжающей организации", False)],
            note=u"Требование ТУ является отдельным проектным ограничением и не назначается автоматически из-за наличия газового котла."
        )
        self._textbox("gas_tu_resistance", u"Rз по ТУ, Ом", self._default("gas_tu_resistance", ""))
        self._textbox("gas_tu_reference", u"Реквизиты ТУ / пункт", self._default("gas_tu_reference", ""))

        self._section(u"4. Вертикальные электроды")
        self._textbox("vertical_length", u"Длина одного электрода L, м", self._default("vertical_length", "3"))
        self._textbox("vertical_diameter_mm", u"Диаметр, мм", self._default("vertical_diameter_mm", "16"))
        self._textbox("vertical_top_depth", u"Глубина до верха, м", self._default("vertical_top_depth", "0.5"))
        self._textbox("vertical_count", u"Количество, шт.", self._default("vertical_count", "3"))
        self._textbox("vertical_spacing", u"Шаг между электродами, м", self._default("vertical_spacing", "3"))
        self._combo("vertical_eta_layout", u"Схема для коэффициента использования η",
                    [ETA_LAYOUT_AUTO, ETA_LAYOUT_ROW, ETA_LAYOUT_CONTOUR],
                    self._default("vertical_eta_layout", ETA_LAYOUT_AUTO))
        self._note(u"η выбирается по табличной методике для ряда или замкнутого контура; в режиме «Авто» схема определяется по размещению электродов.")

        self._section(u"5. Горизонтальный электрод")
        self._checkbox_box(
            u"Соединительная полоса",
            [("horizontal_enabled", u"Учитывать горизонтальную полосу", bool(self._default("horizontal_enabled", True)))],
            note=u"При создании модели фактическая длина полосы пересчитывается по выбранным точкам."
        )
        self._textbox("horizontal_length", u"Длина, м", self._default("horizontal_length", "10"))
        self._textbox("horizontal_width_mm", u"Ширина, мм", self._default("horizontal_width_mm", "40"))
        self._textbox("horizontal_thickness_mm", u"Толщина, мм", self._default("horizontal_thickness_mm", "4"))
        self._textbox("horizontal_depth", u"Глубина, м", self._default("horizontal_depth", "0.7"))

        self._section(u"6. Защита и TN-C-S")
        self._textbox("rcd_ma", u"IΔn УДТ, мА (TT)", self._default("rcd_ma", "30"))
        self._textbox("protective_conductor_r", u"R защитного проводника, Ом", self._default("protective_conductor_r", "0"))
        self._textbox("zs", u"Zs петли повреждения, Ом (TN)", self._default("zs", ""))
        self._textbox("ia", u"Ia защитного аппарата, А (TN)", self._default("ia", ""))
        self._checkbox_box(
            u"Параметры PEN",
            [("has_incoming_pen", u"На вводе имеется PEN", bool(self._default("has_incoming_pen", True))),
             ("reconnect_n_pe", u"N и PE повторно объединены после разделения (ошибка)", False)],
            note=u"Параметры используются при проверках TN-C-S и повторного заземления PEN."
        )
        self._combo("pen_material", u"Материал PEN", [MAT_AL, MAT_CU], self._default("pen_material", MAT_AL))
        self._textbox("pen_section", u"Сечение PEN, мм²", self._default("pen_section", "16"))

        self._section(u"7. Заземляющий проводник ЗУ → ГЗШ")
        self._checkbox_box(
            u"Расчет проводника",
            [("grounding_conductor_enabled", u"Рассчитать сечение проводника от ЗУ до ГЗШ", bool(self._default("grounding_conductor_enabled", True))),
             ("grounding_use_input_section", u"Учитывать проверку по сечению вводного фазного проводника", bool(self._default("grounding_use_input_section", True)))],
            note=u"Итог выбирается по наиболее строгому из применимых критериев: Iк√t/k, нормативный минимум и проверка по вводу."
        )
        self._textbox("incoming_phase_k", u"Коэффициент k1 вводного проводника", self._default("incoming_phase_k", "115"))
        self._note(u"Основные исходные данные ввода находятся в шапке формы. Здесь оставлен k1 как уточняющий параметр при разных материалах фазного и заземляющего проводников. Длина кабеля не используется отдельно, если Iк задан непосредственно в точке расчета.")

        self._section(u"8. Размещение и результат в Revit")
        self._combo("placement_source", u"Способ позиционирования",
                    [u"Вручную (PickPoint)", u"По направляющим Revit (Model Lines)"],
                    self._default("placement_source", u"Вручную (PickPoint)"))
        self._combo("layout_mode", u"Схема размещения", [u"Линия", u"Треугольник", u"Точки вручную", u"Контур по периметру", u"Незамкнутый контур"], self._default("layout_mode", u"Линия"))
        self._checkbox_box(
            u"Параметры построения",
            [("close_loop", u"Замкнуть горизонтальный контур (для ручных точек)", bool(self._default("close_loop", False))),
             ("open_contour_fixed_length", u"Фиксированная длина незамкнутого контура (по Model Lines)", _bool_value(self.defaults.get("open_contour_fixed_length"), False)),
             ("create_model", u"Создать 3D-модель ЗУ в Revit", bool(self._default("create_model", True))),
             ("use_rod_family", u"Использовать BIM-изделие вертикального электрода", bool(self._default("use_rod_family", True))),
             ("auto_optimize", u"Выполнить предварительный автоподбор конструкции", bool(self._default("auto_optimize", False))),
             ("create_view", u"Создать чертежный вид «ЭОМ_Расчет ЗУ»", bool(self._default("create_view", True)))],
            note=u"Для точной трассы выберите «По направляющим Revit» и одну любую линию цепочки. Для EZETEK 90136 используется облегченная BIM-модель без загрузки тяжелого RFA. Если фиксированная длина включена, используется вся незамкнутая трасса Model Lines; иначе линии задают направление и максимально доступную длину."
        )
        self._textbox("rod_family_name", u"Семейство вертикального электрода", self._default("rod_family_name", u"EZETEK 90136 — Ø16×1500 мм"))

        self._buttons()
        panel.AutoScrollMinSize = Size(840, self._y + 80)
        self.values = None

    def _default(self, key, fallback):
        value = self.defaults.get(key, fallback)
        return _text(fallback if value is None else value)


    def _required_input_header(self):
        """Единая шапка исходных данных сети и расчета ЗУ / проводника ЗУ -> ГЗШ."""
        box = Panel()
        box.Location = Point(self._left, self._y)
        box.Size = Size(820, 670)
        box.BorderStyle = BorderStyle.FixedSingle
        box.BackColor = Color.FromArgb(247, 250, 255)

        title = Label()
        title.Text = u"ИСХОДНЫЕ ДАННЫЕ СЕТИ И РАСЧЕТА"
        title.Location = Point(14, 10)
        title.Size = Size(790, 22)
        title.Font = Font(title.Font, FontStyle.Bold)
        title.ForeColor = Color.FromArgb(31, 78, 121)
        box.Controls.Add(title)

        hint = Label()
        hint.Text = (u"Сначала заполните параметры сети, затем защиту и вводной кабель. Поля со * необходимы "
                     u"для полного расчета проводника ЗУ → ГЗШ. Длина кабеля пока справочная, если Iк задан в точке расчета.")
        hint.Location = Point(14, 36)
        hint.Size = Size(790, 42)
        hint.ForeColor = Color.DimGray
        box.Controls.Add(hint)

        lx1, ix1 = 14, 190
        lx2, ix2 = 410, 584
        lw1, iw1 = 170, 194
        lw2, iw2 = 168, 216
        y0, dy = 96, 38
        required_bg = Color.FromArgb(255, 248, 214)
        optional_bg = Color.FromArgb(243, 247, 252)

        def add_group(text, y):
            lbl = Label(); lbl.Text = text; lbl.Location = Point(14, y); lbl.Size = Size(790, 22)
            lbl.Font = Font(lbl.Font, FontStyle.Bold); lbl.ForeColor = Color.FromArgb(70, 70, 70)
            box.Controls.Add(lbl)

        def add_label(text, x, y, w):
            lbl = Label(); lbl.Text = text; lbl.Location = Point(x, y + 4); lbl.Size = Size(w, 26)
            box.Controls.Add(lbl)

        def add_tb(key, text, x, y, w, required=True):
            tb = TextBox(); tb.Text = _text(text); tb.Location = Point(x, y); tb.Size = Size(w, 26)
            tb.BackColor = required_bg if required else optional_bg
            box.Controls.Add(tb); self._controls[key] = tb
            tb.TextChanged += self._update_input_header_status
            return tb

        def add_cb(key, items, default, x, y, w, required=True):
            cb = ComboBox(); cb.DropDownStyle = ComboBoxStyle.DropDownList
            cb.Location = Point(x, y); cb.Size = Size(w, 26)
            for item in items: cb.Items.Add(item)
            cb.SelectedIndex = items.index(default) if default in items else 0
            cb.BackColor = required_bg if required else optional_bg
            box.Controls.Add(cb); self._controls[key] = cb
            cb.SelectedIndexChanged += self._update_input_header_status
            return cb

        y = y0
        add_group(u"ПАНЕЛЬ С ГЗШ", y)
        y += 28
        self._panel_combo = ComboBox()
        self._panel_combo.DropDownStyle = ComboBoxStyle.DropDownList
        self._panel_combo.Location = Point(14, y)
        self._panel_combo.Size = Size(650, 27)
        self._panel_combo.DropDownWidth = 780
        self._panel_combo.MaxDropDownItems = 18
        self._panel_combo.SelectedIndexChanged += self._on_panel_changed
        box.Controls.Add(self._panel_combo)

        show_btn = Button()
        show_btn.Text = u"Показать в модели"
        show_btn.Location = Point(676, y - 1)
        show_btn.Size = Size(124, 29)
        show_btn.Click += self._on_show_panel
        box.Controls.Add(show_btn)
        self._panel_show_button = show_btn
        self._refresh_panel_selector()

        y += 46
        add_group(u"ПАРАМЕТРЫ СЕТИ", y)
        y += 28
        add_label(u"Система *", lx1, y, lw1)
        add_cb("system", [SYSTEM_TNCS, SYSTEM_TNS, SYSTEM_TT], self._default("system", SYSTEM_TNCS), ix1, y, iw1)
        add_label(u"Назначение ЗУ *", lx2, y, lw2)
        add_cb("purpose", [PURPOSE_PROTECTIVE, PURPOSE_REPEATED_PEN, PURPOSE_TNCS_INPUT, PURPOSE_SOURCE_NEUTRAL,
                           PURPOSE_GENERATOR_NEUTRAL, PURPOSE_COMBINED_LPS, PURPOSE_FUNCTIONAL],
               self._default("purpose", PURPOSE_TNCS_INPUT if self._default("system", SYSTEM_TNCS) == SYSTEM_TNCS and self._default("supply_type", SUPPLY_OVERHEAD) == SUPPLY_OVERHEAD else PURPOSE_PROTECTIVE), ix2, y, iw2)

        y += dy
        add_label(u"Тип ввода *", lx1, y, lw1)
        add_cb("supply_type", [SUPPLY_UNKNOWN, SUPPLY_OVERHEAD, SUPPLY_CABLE, SUPPLY_GENERATOR],
               self._default("supply_type", SUPPLY_OVERHEAD), ix1, y, iw1)
        add_label(u"U0 / Uл, В *", lx2, y, lw2)
        voltage_panel = Panel(); voltage_panel.Location = Point(ix2, y); voltage_panel.Size = Size(iw2, 28); box.Controls.Add(voltage_panel)
        u0 = TextBox(); u0.Text = _text(self._default("phase_voltage", "230")); u0.Location = Point(0, 0); u0.Size = Size(100, 26); u0.BackColor = required_bg
        ul = TextBox(); ul.Text = _text(self._default("line_voltage", "400")); ul.Location = Point(112, 0); ul.Size = Size(104, 26); ul.BackColor = required_bg
        voltage_panel.Controls.Add(u0); voltage_panel.Controls.Add(ul)
        self._controls["phase_voltage"] = u0; self._controls["line_voltage"] = ul
        u0.TextChanged += self._update_input_header_status; ul.TextChanged += self._update_input_header_status

        y += 46
        add_group(u"ЗАЩИТА И ВВОДНОЙ КАБЕЛЬ", y)
        y += 28
        add_label(u"Тип вводного АВ *", lx1, y, lw1)
        add_cb("incoming_breaker_standard", [BREAKER_STD_60898, BREAKER_STD_OTHER],
               self._default("incoming_breaker_standard", BREAKER_STD_60898), ix1, y, iw1)
        add_label(u"Номинал вводного АВ, А *", lx2, y, lw2)
        add_tb("incoming_breaker_rating_a", self._default("incoming_breaker_rating_a", ""), ix2, y, iw2)

        y += dy
        add_label(u"Характеристика / ВТХ *", lx1, y, lw1)
        add_cb("incoming_breaker_curve", list(BREAKER_CURVES),
               self._default("incoming_breaker_curve", u"C"), ix1, y, iw1)
        add_label(u"Материал кабеля *", lx2, y, lw2)
        add_cb("incoming_phase_material", [MAT_CU, MAT_AL],
               self._default("incoming_phase_material", MAT_CU), ix2, y, iw2)

        y += dy
        add_label(u"Сечение фазной жилы, мм² *", lx1, y, lw1)
        add_tb("incoming_phase_section", self._default("incoming_phase_section", ""), ix1, y, iw1)
        add_label(u"Длина вводного кабеля, м", lx2, y, lw2)
        add_tb("incoming_cable_length_m", self._default("incoming_cable_length_m", ""), ix2, y, iw2, required=False)

        y += 46
        add_group(u"ПРОВОДНИК ЗУ → ГЗШ", y)
        y += 28
        add_label(u"Iк в точке расчета, А *", lx1, y, lw1)
        add_tb("grounding_fault_current_a", self._default("grounding_fault_current_a", ""), ix1, y, iw1)
        add_label(u"Время отключения t, с *", lx2, y, lw2)
        add_tb("grounding_disconnection_time_s", self._default("grounding_disconnection_time_s", ""), ix2, y, iw2)

        y += dy
        add_label(u"Материал ЗУ → ГЗШ *", lx1, y, lw1)
        add_cb("grounding_conductor_material", [MAT_CU, MAT_STEEL],
               self._default("grounding_conductor_material", MAT_CU), ix1, y, iw1)
        add_label(u"k для S=Iк√t/k *", lx2, y, lw2)
        add_tb("grounding_k", self._default("grounding_k", "143"), ix2, y, iw2)

        self._input_header_status = Label()
        self._input_header_status.Location = Point(14, 612)
        self._input_header_status.Size = Size(790, 44)
        self._input_header_status.Font = Font(self._input_header_status.Font, FontStyle.Bold)
        box.Controls.Add(self._input_header_status)

        self.panel.Controls.Add(box)
        self._y += box.Height + 12
        self._update_input_header_status()

    def _panel_key(self, data):
        return panel_key(data)

    def _panel_choice_text(self, data):
        return panel_choice_text(data)

    def _refresh_panel_selector(self):
        refresh_panel_selector(self)

    def _update_panel_selector_state(self):
        update_panel_selector_state(self)

    def _on_panel_changed(self, sender, args):
        on_panel_changed(self)
        self._update_input_header_status()

    def _on_show_panel(self, sender, args):
        # Revit navigation is executed only after ShowDialog returns.
        # No nested message pumping and no Revit API call in a nested modal loop.
        request_show_panel(self)

    def _update_input_header_status(self, sender=None, args=None):
        if not hasattr(self, "_input_header_status"):
            return

        def text_of(key):
            ctl = self._controls.get(key)
            if ctl is None:
                return u""
            try:
                return _text(ctl.Text).strip()
            except Exception:
                return u""

        def positive(text):
            try:
                return number_value(text) > 0
            except Exception:
                return False

        missing = []
        if not (self._panel_data.get("gzsh_panel_unique_id") or self._panel_data.get("gzsh_panel_element_id")):
            missing.append(u"панель с ГЗШ")
        required_text = [
            ("phase_voltage", u"U0"),
            ("line_voltage", u"Uл"),
            ("incoming_breaker_rating_a", u"номинал вводного АВ"),
            ("incoming_phase_section", u"сечение вводного кабеля"),
            ("grounding_fault_current_a", u"Iк"),
            ("grounding_k", u"коэффициент k"),
        ]
        valid_bg = Color.FromArgb(236, 248, 239)
        missing_bg = Color.FromArgb(255, 239, 204)
        for key, caption in required_text:
            ctl = self._controls.get(key)
            ok = positive(text_of(key))
            if not ok: missing.append(caption)
            if ctl is not None: ctl.BackColor = valid_bg if ok else missing_bg

        # t может быть получено автоматически только для B/C/D по IEC 60898-1,
        # если Iк не ниже верхней границы зоны мгновенного расцепления.
        t_ctl = self._controls.get("grounding_disconnection_time_s")
        t_ok = positive(text_of("grounding_disconnection_time_s"))
        auto_t = False
        auto_note = u""
        if not t_ok:
            try:
                std = _text(self._controls["incoming_breaker_standard"].SelectedItem)
                curve = _text(self._controls["incoming_breaker_curve"].SelectedItem)
                rating = number_value(text_of("incoming_breaker_rating_a"))
                ik = number_value(text_of("grounding_fault_current_a"))
                multipliers = {u"B": 5.0, u"C": 10.0, u"D": 20.0}
                if std == BREAKER_STD_60898 and curve in multipliers and ik >= multipliers[curve] * rating:
                    auto_t = True
                    auto_note = u" t будет принято 0,1 с по гарантированной зоне мгновенного расцепления {}.".format(curve)
            except Exception:
                auto_t = False
        if not t_ok and not auto_t:
            missing.append(u"время отключения t")
        if t_ctl is not None:
            t_ctl.BackColor = valid_bg if (t_ok or auto_t) else missing_bg

        length_ctl = self._controls.get("incoming_cable_length_m")
        if length_ctl is not None:
            length_ctl.BackColor = Color.FromArgb(243, 247, 252)

        if missing:
            self._input_header_status.Text = u"НЕ ХВАТАЕТ ДАННЫХ: " + u", ".join(missing) + u"." + auto_note
            self._input_header_status.ForeColor = Color.FromArgb(183, 82, 0)
        else:
            self._input_header_status.Text = u"ИСХОДНЫЕ ДАННЫЕ ЗАПОЛНЕНЫ — доступен полный расчет проводника ЗУ → ГЗШ." + auto_note
            self._input_header_status.ForeColor = Color.FromArgb(28, 120, 65)

    def _intro_note(self, text):
        box = Panel(); box.Location = Point(self._left, self._y); box.Size = Size(820, 56)
        box.BorderStyle = BorderStyle.FixedSingle; box.BackColor = Color.FromArgb(245, 249, 255)
        lbl = Label(); lbl.Text = text; lbl.Location = Point(12, 10); lbl.Size = Size(792, 36)
        box.Controls.Add(lbl); self.panel.Controls.Add(box); self._y += 72

    def _section(self, title):
        self._section_index += 1
        band = Panel(); band.Location = Point(self._left, self._y); band.Size = Size(820, 34)
        band.BackColor = Color.FromArgb(232, 239, 247) if self._section_index % 2 else Color.FromArgb(238, 244, 238)
        band.BorderStyle = BorderStyle.FixedSingle
        lbl = Label(); lbl.Text = title; lbl.Location = Point(10, 7); lbl.Size = Size(780, 20); lbl.Font = Font(lbl.Font, FontStyle.Bold)
        band.Controls.Add(lbl); self.panel.Controls.Add(band); self._y += 44

    def _label(self, text, y):
        lbl = Label(); lbl.Text = text; lbl.AutoSize = False; lbl.Size = Size(self._label_w, 28); lbl.Location = Point(self._label_x, y + 2)
        self.panel.Controls.Add(lbl)

    def _note(self, text, warning=False):
        lbl = Label(); lbl.Text = _text(text); lbl.AutoSize = False; lbl.Size = Size(810, 38 if warning else 34); lbl.Location = Point(self._label_x, self._y)
        if warning:
            lbl.ForeColor = Color.DarkRed; lbl.Font = Font(lbl.Font, FontStyle.Bold)
        self.panel.Controls.Add(lbl); self._y += lbl.Height + 6

    def _textbox(self, key, label, default):
        self._label(label, self._y)
        tb = TextBox(); tb.Text = _text(default); tb.Location = Point(self._input_x, self._y); tb.Size = Size(self._input_w, 26)
        self.panel.Controls.Add(tb); self._controls[key] = tb; self._y += 34

    def _readonly_textbox(self, key, label, default):
        self._label(label, self._y)
        tb = TextBox()
        tb.Text = _text(default)
        tb.Location = Point(self._input_x, self._y)
        tb.Size = Size(self._input_w, 26)
        tb.ReadOnly = True
        tb.BackColor = Color.FromArgb(245, 247, 249)
        self.panel.Controls.Add(tb)
        self._controls[key] = tb
        self._y += 34

    def _combo(self, key, label, items, default):
        self._label(label, self._y)
        cb = ComboBox(); cb.DropDownStyle = ComboBoxStyle.DropDownList; cb.Location = Point(self._input_x, self._y); cb.Size = Size(self._input_w, 26)
        for item in items: cb.Items.Add(item)
        cb.SelectedIndex = items.index(default) if default in items else 0
        self.panel.Controls.Add(cb); self._controls[key] = cb; self._y += 34

    def _checkbox_box(self, title, items, note=None):
        height = 48 + (28 * len(items)) + (34 if note else 0)
        box = Panel(); box.Location = Point(self._left, self._y); box.Size = Size(820, height); box.BorderStyle = BorderStyle.FixedSingle
        box.BackColor = Color.FromArgb(252, 252, 244)
        title_lbl = Label(); title_lbl.Text = title; title_lbl.Location = Point(10, 8); title_lbl.Size = Size(780, 18); title_lbl.Font = Font(title_lbl.Font, FontStyle.Bold)
        box.Controls.Add(title_lbl); y = 30
        for key, label, default in items:
            cb = CheckBox(); cb.Text = label; cb.Checked = default; cb.AutoSize = False; cb.Size = Size(780, 24); cb.Location = Point(14, y)
            box.Controls.Add(cb); self._controls[key] = cb; y += 28
        if note:
            note_lbl = Label(); note_lbl.Text = note; note_lbl.Location = Point(14, y + 2); note_lbl.Size = Size(786, 32); note_lbl.ForeColor = Color.DimGray
            box.Controls.Add(note_lbl)
        self.panel.Controls.Add(box); self._y += height + 10

    def _buttons(self):
        calc = Button(); calc.Text = u"Рассчитать"; calc.Location = Point(290, self._y + 14); calc.Size = Size(170, 36); calc.Click += self._on_ok
        self.panel.Controls.Add(calc)
        cancel = Button(); cancel.Text = u"Отмена"; cancel.Location = Point(474, self._y + 14); cancel.Size = Size(120, 36); cancel.Click += self._on_cancel
        self.panel.Controls.Add(cancel); self.AcceptButton = calc; self.CancelButton = cancel

    def _collect_values(self):
        values = collect_controls(self._controls)
        values.update(self._panel_data)
        for key in ("location_profile_id", "location_profile_name", "location_data_source",
                    "soil_data_quality", "location_warning", "customer_project_fields",
                    "project_address_source", "project_address_parameter",
                    "normalized_address", "latitude", "longitude", "qc_geo", "fias_id", "dadata_method",
                    "online_updated_utc", "online_errors", "data_provenance",
                    "soil_clay_pct", "soil_sand_pct", "soil_silt_pct", "freezing_index_mt",
                    "frost_depth_auto_source", "frost_depth_source",
                    "frost_depth_project_confirmed", "frost_depth_project_ignored",
                    "frost_depth_reason", "frost_zero_is_climatic"):
            values[key] = self.defaults.get(key)
        return values

    def _on_ok(self, sender, args):
        self.values = self._collect_values()
        self.DialogResult = DialogResult.OK
        self.Close()

    def _on_cancel(self, sender, args):
        self.values = None
        self.DialogResult = DialogResult.Cancel
        self.Close()


def show_input_form(defaults=None, panel_options=None, panel_shower=None):
    working_defaults = dict(defaults or {})
    while True:
        form = GroundingInputForm(working_defaults, panel_options=panel_options, panel_shower=panel_shower)
        result = DialogResult.Cancel
        values = None
        panel_request = None
        try:
            result = form.ShowDialog()
            values = dict(form.values or {}) if getattr(form, "values", None) else None
            panel_request = dict(form._panel_show_requested or {}) if getattr(form, "_panel_show_requested", None) else None
        finally:
            try:
                form.Dispose()
            except Exception:
                pass
        if result == DialogResult.OK:
            return values or {}
        if result == DialogResult.Retry and panel_request and panel_shower is not None:
            try:
                refreshed = panel_shower(panel_request)
                if not isinstance(refreshed, dict):
                    MessageBox.Show(u"Не удалось показать выбранную панель в модели. Проверьте, что элемент существует и доступен на модельном виде.",
                                    u"ЭОМ — Панель с ГЗШ")
            except Exception as ex:
                from perf_trace import mark_exception as _trace_panel_exception
                _trace_panel_exception("PANEL_SHOW_ERROR", ex)
                MessageBox.Show(u"Не удалось показать выбранную панель в модели:\n\n{}".format(ex),
                                u"ЭОМ — Панель с ГЗШ")
            # Navigation is a terminal action for this modal workflow.
            # Do not reopen the plugin window after Revit has shown the panel.
            return None
        return None
