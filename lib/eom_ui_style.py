# -*- coding: utf-8 -*-
from __future__ import division, print_function

import clr
clr.AddReference("System.Windows.Forms")
clr.AddReference("System.Drawing")

from System.Drawing import Size, Point, Font, FontStyle, Color
from System.Windows.Forms import (Form, Label, Button, Panel, TextBox, DialogResult,
                                  FormBorderStyle, FormStartPosition,
                                  BorderStyle, ScrollBars, AnchorStyles)

BG = Color.White
SECTION_A = Color.FromArgb(232, 239, 247)
SECTION_B = Color.FromArgb(238, 244, 238)
OPTION_BG = Color.FromArgb(252, 252, 244)
INFO_BG = Color.FromArgb(245, 249, 255)
WARN_BG = Color.FromArgb(255, 246, 242)
WARN_FG = Color.DarkRed
MUTED = Color.DimGray


def style_form(form, title, width=900, height=900, min_width=820, min_height=650):
    form.Text = title
    # ClientSize задает именно полезную область окна без учета рамки и заголовка.
    # Это предотвращает обрезание нижних кнопок в Revit/Windows.
    form.ClientSize = Size(width, height)
    form.MinimumSize = Size(min_width, min_height)
    form.FormBorderStyle = FormBorderStyle.Sizable
    form.StartPosition = FormStartPosition.CenterScreen
    form.BackColor = BG


def show_message(text, title=u"ЭОМ", warning=False, warn_icon=False, ok=True, **kwargs):
    warning = bool(warning or warn_icon)
    form = Form()
    # Размер задается по клиентской области: все элементы, включая кнопку OK,
    # гарантированно находятся внутри видимой части формы.
    style_form(form, title, 780, 580, 720, 500)
    form.MaximizeBox = False

    header = Panel()
    header.Location = Point(20, 18)
    header.Size = Size(740, 44)
    header.Anchor = AnchorStyles.Top | AnchorStyles.Left | AnchorStyles.Right
    header.BorderStyle = BorderStyle.FixedSingle
    header.BackColor = WARN_BG if warning else SECTION_A
    h = Label()
    h.Text = u"Предупреждение" if warning else u"Информация"
    h.Font = Font(h.Font, FontStyle.Bold)
    h.ForeColor = WARN_FG if warning else Color.Black
    h.Location = Point(12, 11)
    h.Size = Size(706, 22)
    h.Anchor = AnchorStyles.Top | AnchorStyles.Left | AnchorStyles.Right
    header.Controls.Add(h)
    form.Controls.Add(header)

    body = TextBox()
    body.Multiline = True
    body.ReadOnly = True
    body.ScrollBars = ScrollBars.Vertical
    body.BackColor = Color.White
    body.Text = text or u""
    body.Location = Point(20, 76)
    body.Size = Size(740, 430)
    body.Anchor = AnchorStyles.Top | AnchorStyles.Bottom | AnchorStyles.Left | AnchorStyles.Right
    body.TabStop = False
    form.Controls.Add(body)

    ok_button = Button()
    ok_button.Text = u"OK"
    ok_button.Location = Point(640, 524)
    ok_button.Size = Size(120, 36)
    ok_button.Anchor = AnchorStyles.Bottom | AnchorStyles.Right
    ok_button.DialogResult = DialogResult.OK
    form.Controls.Add(ok_button)
    form.AcceptButton = ok_button
    form.CancelButton = ok_button
    # Make Revit the explicit owner. Ownerless modal WinForms dialogs can open
    # behind the Revit/pyRevit output windows and make Revit look frozen.
    owner = None
    try:
        from System.Diagnostics import Process
        from System.Windows.Forms import NativeWindow
        handle = Process.GetCurrentProcess().MainWindowHandle
        if handle and handle.ToInt64() != 0:
            owner = NativeWindow()
            owner.AssignHandle(handle)
            form.ShowDialog(owner)
        else:
            form.ShowDialog()
    except Exception:
        form.ShowDialog()
    finally:
        try:
            if owner is not None:
                owner.ReleaseHandle()
        except Exception:
            pass
