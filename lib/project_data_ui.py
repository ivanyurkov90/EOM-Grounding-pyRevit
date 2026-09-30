# -*- coding: utf-8 -*-
from __future__ import division, print_function

import clr
clr.AddReference("System.Windows.Forms")
clr.AddReference("System.Drawing")

from System.Drawing import Color, Font, FontStyle, Point, Size
from System.Windows.Forms import (AnchorStyles, BorderStyle, Button, ComboBox,
                                  ComboBoxStyle, DialogResult, FlatStyle, Form,
                                  FormBorderStyle, FormStartPosition, Label,
                                  MessageBox, Panel, TextBox)

from project_profile import (GROUNDING_SYSTEMS, INCOMING_DEVICE_TYPES,
                             OBJECT_TYPES, normalize_profile)


ACCENT = Color.FromArgb(45, 112, 170)
PANEL_BG = Color.FromArgb(246, 248, 251)
FIELD_BG = Color.FromArgb(255, 255, 255)


class ProjectDataForm(Form):
    def __init__(self, defaults=None):
        self.defaults = normalize_profile(defaults)
        self.values = None
        self.Text = u"ЭОМ — Данные проекта"
        self.ClientSize = Size(760, 560)
        self.MinimumSize = Size(720, 540)
        self.FormBorderStyle = FormBorderStyle.Sizable
        self.StartPosition = FormStartPosition.CenterScreen
        self.ShowInTaskbar = True
        self.BackColor = Color.White
        self._controls = {}

        header = Panel()
        header.Location = Point(0, 0)
        header.Size = Size(760, 78)
        header.Anchor = AnchorStyles.Top | AnchorStyles.Left | AnchorStyles.Right
        header.BackColor = ACCENT
        self.Controls.Add(header)

        title = Label()
        title.Text = u"ДАННЫЕ ПРОЕКТА"
        title.Location = Point(24, 14)
        title.Size = Size(690, 28)
        title.Font = Font(title.Font.FontFamily, 13, FontStyle.Bold)
        title.ForeColor = Color.White
        header.Controls.Add(title)

        subtitle = Label()
        subtitle.Text = u"Единые исходные данные для расчёта ЗУ, модели и документации"
        subtitle.Location = Point(24, 45)
        subtitle.Size = Size(690, 22)
        subtitle.ForeColor = Color.FromArgb(226, 238, 248)
        header.Controls.Add(subtitle)

        self._card(u"ОБЪЕКТ", 96, 178)
        self._combo("project_object_type", u"Тип объекта", OBJECT_TYPES,
                    self.defaults.get("project_object_type"), 118)
        self._textbox("project_address", u"Адрес объекта",
                      self.defaults.get("project_address"), 160, multiline=True)
        self._textbox("allocated_power_kw", u"Выделенная мощность, кВт",
                      self.defaults.get("allocated_power_kw"), 224)

        self._card(u"ЭЛЕКТРОСНАБЖЕНИЕ", 290, 174)
        self._combo("system", u"Система заземления", GROUNDING_SYSTEMS,
                    self.defaults.get("system"), 312)
        self._combo("incoming_device_type", u"Аппарат на вводе", INCOMING_DEVICE_TYPES,
                    self.defaults.get("incoming_device_type"), 354)
        self._textbox("incoming_breaker_rating_a", u"Номинальный ток, А",
                      self.defaults.get("incoming_breaker_rating_a"), 396, width=150)
        self._textbox("incoming_breaker_curve", u"Характеристика / ВТХ",
                      self.defaults.get("incoming_breaker_curve"), 438, width=150)

        note = Label()
        note.Text = (u"Профиль сохраняется внутри RVT. Адрес также записывается в «Сведения о проекте». "
                     u"Расчётные команды автоматически используют эти значения как исходные.")
        note.Location = Point(28, 480)
        note.Size = Size(704, 34)
        note.ForeColor = Color.DimGray
        self.Controls.Add(note)

        save = Button()
        save.Text = u"Сохранить"
        save.Location = Point(452, 520)
        save.Size = Size(140, 32)
        save.BackColor = ACCENT
        save.ForeColor = Color.White
        save.FlatStyle = FlatStyle.Flat
        save.Click += self._on_save
        self.Controls.Add(save)

        cancel = Button()
        cancel.Text = u"Отмена"
        cancel.Location = Point(604, 520)
        cancel.Size = Size(120, 32)
        cancel.Click += self._on_cancel
        self.Controls.Add(cancel)
        self.AcceptButton = save
        self.CancelButton = cancel

    def _card(self, title_text, y, height):
        card = Panel()
        card.Location = Point(24, y)
        card.Size = Size(712, height)
        card.BackColor = PANEL_BG
        card.BorderStyle = BorderStyle.FixedSingle
        self.Controls.Add(card)
        title = Label()
        title.Text = title_text
        title.Location = Point(16, 8)
        title.Size = Size(670, 22)
        title.Font = Font(title.Font, FontStyle.Bold)
        title.ForeColor = ACCENT
        card.Controls.Add(title)

    def _label(self, text, y):
        label = Label()
        label.Text = text
        label.Location = Point(44, y + 4)
        label.Size = Size(250, 24)
        self.Controls.Add(label)

    def _textbox(self, key, label, value, y, width=410, multiline=False):
        self._label(label, y)
        control = TextBox()
        control.Text = u"{}".format(value or u"")
        control.Location = Point(300, y)
        control.Size = Size(width, 52 if multiline else 26)
        control.Multiline = multiline
        control.BackColor = FIELD_BG
        self.Controls.Add(control)
        self._controls[key] = control

    def _combo(self, key, label, items, value, y):
        self._label(label, y)
        control = ComboBox()
        control.DropDownStyle = ComboBoxStyle.DropDownList
        control.Location = Point(300, y)
        control.Size = Size(410, 26)
        for item in items:
            control.Items.Add(item)
        control.SelectedIndex = list(items).index(value) if value in items else 0
        self.Controls.Add(control)
        self._controls[key] = control

    def _collect(self):
        values = {}
        for key, control in self._controls.items():
            values[key] = u"{}".format(control.Text or u"").strip()
        return normalize_profile(values)

    def _positive_or_blank(self, value, label):
        if not value:
            return True
        try:
            if float(value.replace(",", ".")) > 0:
                return True
        except Exception:
            pass
        MessageBox.Show(u"Поле «{}» должно содержать положительное число.".format(label),
                        u"ЭОМ — Данные проекта")
        return False

    def _on_save(self, sender, args):
        values = self._collect()
        if not self._positive_or_blank(values.get("allocated_power_kw"), u"Выделенная мощность"):
            return
        if not self._positive_or_blank(values.get("incoming_breaker_rating_a"), u"Номинальный ток"):
            return
        self.values = values
        self.DialogResult = DialogResult.OK
        self.Close()

    def _on_cancel(self, sender, args):
        self.values = None
        self.DialogResult = DialogResult.Cancel
        self.Close()


def show_project_data_form(defaults=None):
    form = ProjectDataForm(defaults)
    try:
        result = form.ShowDialog()
        return dict(form.values or {}) if result == DialogResult.OK else None
    finally:
        try:
            form.Dispose()
        except Exception:
            pass
