# -*- coding: utf-8 -*-
from __future__ import print_function

import clr
clr.AddReference("System.Windows.Forms")
clr.AddReference("System.Drawing")

from System.Drawing import Color
from System.Windows.Forms import DialogResult, CheckBox, ComboBox

PANEL_FIELDS = (
    "gzsh_panel_unique_id", "gzsh_panel_element_id", "gzsh_panel_name", "gzsh_panel_mark",
    "gzsh_panel_display_name", "gzsh_panel_level", "gzsh_panel_room", "gzsh_panel_family", "gzsh_panel_type"
)


def panel_key(data):
    data = data or {}
    uid = data.get("gzsh_panel_unique_id")
    if uid:
        return u"{}".format(uid)
    eid = data.get("gzsh_panel_element_id")
    return u"ID:{}".format(eid) if eid else u""


def panel_choice_text(data):
    try:
        from panel_context import format_panel_choice
        return format_panel_choice(data)
    except Exception:
        data = data or {}
        return (data.get("gzsh_panel_display_name") or data.get("gzsh_panel_mark") or
                data.get("gzsh_panel_name") or u"Панель")


def update_panel_selector_state(form):
    data = getattr(form, "_panel_data", {}) or {}
    has_panel = bool(data.get("gzsh_panel_unique_id") or data.get("gzsh_panel_element_id"))
    combo = getattr(form, "_panel_combo", None)
    if combo is not None:
        combo.BackColor = Color.FromArgb(236, 248, 239) if has_panel else Color.FromArgb(255, 239, 204)
    button = getattr(form, "_panel_show_button", None)
    if button is not None:
        button.Enabled = has_panel


def refresh_panel_selector(form):
    combo = getattr(form, "_panel_combo", None)
    if combo is None:
        return
    current_key = panel_key(getattr(form, "_panel_data", {}))
    options = list(getattr(form, "panel_options", []) or [])
    valid_keys = set(panel_key(x) for x in options)
    if current_key and current_key not in valid_keys:
        form._panel_data = {}
        current_key = u""
    form._updating_panel_combo = True
    try:
        combo.Items.Clear()
        combo.Items.Add(u"— выберите панель с ГЗШ —")
        selected_index = 0
        for idx, info in enumerate(options):
            combo.Items.Add(panel_choice_text(info))
            if current_key and panel_key(info) == current_key:
                selected_index = idx + 1
        combo.SelectedIndex = selected_index
    finally:
        form._updating_panel_combo = False
    update_panel_selector_state(form)


def on_panel_changed(form):
    if getattr(form, "_updating_panel_combo", False):
        return
    combo = getattr(form, "_panel_combo", None)
    if combo is None:
        return
    idx = combo.SelectedIndex
    options = list(getattr(form, "panel_options", []) or [])
    if idx <= 0:
        form._panel_data = {}
    elif idx - 1 < len(options):
        form._panel_data = dict(options[idx - 1])
    update_panel_selector_state(form)


def collect_controls(controls):
    values = {}
    for key, control in (controls or {}).items():
        if isinstance(control, CheckBox):
            values[key] = bool(control.Checked)
        elif isinstance(control, ComboBox):
            values[key] = control.SelectedItem
        else:
            text = control.Text.strip()
            values[key] = text if text else None
    return values


def request_show_panel(form):
    """Close the modal form and request Revit navigation after ShowDialog returns.

    This deliberately avoids nested message pumping and any Revit API call from a
    nested WinForms message loop.  The caller may reopen a fresh form afterwards.
    """
    data = getattr(form, "_panel_data", {}) or {}
    if not (data.get("gzsh_panel_unique_id") or data.get("gzsh_panel_element_id")):
        return False
    form._panel_show_requested = dict(data)
    form.values = form._collect_values()
    form.DialogResult = DialogResult.Retry
    form.Close()
    return True
