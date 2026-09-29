# -*- coding: utf-8 -*-
from __future__ import division, print_function

import clr
clr.AddReference("System.Windows.Forms")
clr.AddReference("System.Drawing")

from System.Drawing import Size, Point, Font, FontStyle, Color
from System.Windows.Forms import (Form, Label, TextBox, CheckBox, Button, Panel,
                                  FormBorderStyle, DialogResult, FormStartPosition,
                                  BorderStyle, DockStyle)


from plugin_version import display_version

class OnlineSettingsForm(Form):
    def __init__(self, settings=None):
        settings = settings or {}
        self.Text = u"ЭОМ — Онлайн-данные ({})".format(display_version())
        self.ClientSize = Size(900, 780)
        self.MinimumSize = Size(860, 690)
        self.FormBorderStyle = FormBorderStyle.Sizable
        self.StartPosition = FormStartPosition.CenterScreen
        self.BackColor = Color.White
        self.values = None
        self._y = 16
        self._section_index = 0
        self._left = 24
        self._label_x = 28
        self._input_x = 430
        self._label_w = 380
        self._input_w = 400

        panel = Panel()
        panel.AutoScroll = True
        panel.Dock = DockStyle.Fill
        panel.BackColor = Color.White
        self.Controls.Add(panel)
        self.panel = panel

        self._intro_note(u"Онлайн-источники используются только для предварительного заполнения справочных данных объекта. Проверяйте грунт и климатические параметры по ИГИ, измерениям и СП.")

        self._section(u"1. Онлайн-режим")
        self.enabled = self._checkbox_box(
            u"Подключение",
            u"Включить онлайн-источники",
            bool(settings.get("online_enabled", False)),
            u"Если опция выключена, плагин использует параметры Revit и локальный резервный профиль."
        )

        self._section(u"2. DaData")
        self.token = self._textbox(u"API-ключ DaData", settings.get("dadata_token") or u"", password=True)
        self.secret = self._textbox(u"Секретный ключ (резерв)", settings.get("dadata_secret") or u"", password=True)
        self._note(u"Адрес проекта передается DaData для нормализации и получения координат. Ключи хранятся локально и защищаются Windows DPAPI.")

        self._section(u"3. Автоматическое обновление")
        self.auto = self._checkbox_box(
            u"Обновление данных",
            u"Автоматически обновлять отсутствующие или устаревшие данные",
            bool(settings.get("auto_update", True)),
            u"Плагин сначала использует значения проекта, затем кэш и только после этого онлайн-запрос."
        )

        self._section(u"4. Источники справочных данных")
        self.soil, self.climate = self._checkbox_multi(
            u"Разрешенные источники",
            [(u"SoilGrids — предварительный гранулометрический тип верхнего слоя", bool(settings.get("use_soilgrids", True))),
             (u"Open-Meteo / ERA5-Land — справочная оценка климатических параметров", bool(settings.get("use_openmeteo", True)))],
            u"SoilGrids и ERA5-Land не заменяют инженерные изыскания и нормативную климатическую станцию СП."
        )

        self._section(u"5. Кэш")
        self.days = self._textbox(u"Срок хранения кэша, дней", u"{}".format(settings.get("cache_days", 30)), width=160)
        self._warning(u"ρ грунта, тип грунта и глубина промерзания, полученные онлайн, являются предварительными. В расчетной документации указывайте подтвержденный источник исходных данных.")

        save = Button(); save.Text = u"Сохранить и обновить"; save.Location = Point(470, self._y + 16); save.Size = Size(200, 36); save.Click += self._on_save
        self.panel.Controls.Add(save)
        cancel = Button(); cancel.Text = u"Отмена"; cancel.Location = Point(684, self._y + 16); cancel.Size = Size(140, 36); cancel.Click += self._on_cancel
        self.panel.Controls.Add(cancel)
        self.AcceptButton = save; self.CancelButton = cancel
        self.panel.AutoScrollMinSize = Size(840, self._y + 80)

    def _intro_note(self, text):
        box = Panel(); box.Location = Point(self._left, self._y); box.Size = Size(820, 62); box.BorderStyle = BorderStyle.FixedSingle; box.BackColor = Color.FromArgb(245, 249, 255)
        lbl = Label(); lbl.Text = text; lbl.Location = Point(12, 10); lbl.Size = Size(792, 42)
        box.Controls.Add(lbl); self.panel.Controls.Add(box); self._y += 78

    def _section(self, title):
        self._section_index += 1
        band = Panel(); band.Location = Point(self._left, self._y); band.Size = Size(820, 34); band.BorderStyle = BorderStyle.FixedSingle
        band.BackColor = Color.FromArgb(232, 239, 247) if self._section_index % 2 else Color.FromArgb(238, 244, 238)
        lbl = Label(); lbl.Text = title; lbl.Location = Point(10, 7); lbl.Size = Size(780, 20); lbl.Font = Font(lbl.Font, FontStyle.Bold)
        band.Controls.Add(lbl); self.panel.Controls.Add(band); self._y += 44

    def _textbox(self, label_text, value, width=None, password=False):
        lbl = Label(); lbl.Text = label_text; lbl.Location = Point(self._label_x, self._y + 3); lbl.Size = Size(self._label_w, 24)
        self.panel.Controls.Add(lbl)
        tb = TextBox(); tb.Text = value; tb.Location = Point(self._input_x, self._y); tb.Size = Size(width or self._input_w, 26); tb.UseSystemPasswordChar = password
        self.panel.Controls.Add(tb); self._y += 36
        return tb

    def _note(self, text):
        lbl = Label(); lbl.Text = text; lbl.Location = Point(self._label_x, self._y); lbl.Size = Size(810, 40); lbl.ForeColor = Color.DimGray
        self.panel.Controls.Add(lbl); self._y += 46

    def _warning(self, text):
        box = Panel(); box.Location = Point(self._left, self._y); box.Size = Size(820, 62); box.BorderStyle = BorderStyle.FixedSingle; box.BackColor = Color.FromArgb(255, 246, 242)
        lbl = Label(); lbl.Text = text; lbl.Location = Point(12, 9); lbl.Size = Size(792, 44); lbl.ForeColor = Color.DarkRed; lbl.Font = Font(lbl.Font, FontStyle.Bold)
        box.Controls.Add(lbl); self.panel.Controls.Add(box); self._y += 72

    def _checkbox_box(self, title, text, checked, note=None):
        height = 108 if note else 76
        box = Panel(); box.Location = Point(self._left, self._y); box.Size = Size(820, height); box.BorderStyle = BorderStyle.FixedSingle; box.BackColor = Color.FromArgb(252, 252, 244)
        t = Label(); t.Text = title; t.Location = Point(10, 8); t.Size = Size(780, 18); t.Font = Font(t.Font, FontStyle.Bold); box.Controls.Add(t)
        cb = CheckBox(); cb.Text = text; cb.Checked = checked; cb.Location = Point(14, 34); cb.Size = Size(780, 24); box.Controls.Add(cb)
        if note:
            n = Label(); n.Text = note; n.Location = Point(14, 64); n.Size = Size(786, 34); n.ForeColor = Color.DimGray; box.Controls.Add(n)
        self.panel.Controls.Add(box); self._y += height + 10
        return cb

    def _checkbox_multi(self, title, items, note=None):
        height = 48 + 30 * len(items) + (38 if note else 0)
        box = Panel(); box.Location = Point(self._left, self._y); box.Size = Size(820, height); box.BorderStyle = BorderStyle.FixedSingle; box.BackColor = Color.FromArgb(252, 252, 244)
        t = Label(); t.Text = title; t.Location = Point(10, 8); t.Size = Size(780, 18); t.Font = Font(t.Font, FontStyle.Bold); box.Controls.Add(t)
        controls = []; y = 34
        for text, checked in items:
            cb = CheckBox(); cb.Text = text; cb.Checked = checked; cb.Location = Point(14, y); cb.Size = Size(780, 24); box.Controls.Add(cb); controls.append(cb); y += 30
        if note:
            n = Label(); n.Text = note; n.Location = Point(14, y); n.Size = Size(786, 34); n.ForeColor = Color.DimGray; box.Controls.Add(n)
        self.panel.Controls.Add(box); self._y += height + 10
        return controls

    def _on_save(self, sender, args):
        self.values = {
            "online_enabled": bool(self.enabled.Checked),
            "dadata_token": self.token.Text.strip(),
            "dadata_secret": self.secret.Text.strip(),
            "auto_update": bool(self.auto.Checked),
            "use_soilgrids": bool(self.soil.Checked),
            "use_openmeteo": bool(self.climate.Checked),
            "cache_days": self.days.Text.strip() or "30",
        }
        self.DialogResult = DialogResult.OK; self.Close()

    def _on_cancel(self, sender, args):
        self.values = None; self.DialogResult = DialogResult.Cancel; self.Close()


def show_online_settings(settings=None):
    form = OnlineSettingsForm(settings)
    if form.ShowDialog() == DialogResult.OK: return form.values
    return None
