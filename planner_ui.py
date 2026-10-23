"""Tkinter-Oberfläche des Klinik-Dienstplans (dunkles HUD-Design mit Violett-Akzenten)."""
import math
import os
import re
import threading
import time
import tkinter as tk
from collections import defaultdict
from datetime import date, timedelta
from tkinter import filedialog, ttk
from tkinter import font as tkfont

import planner_core as core
from planner_core import (APP_NAME, KIND_LABELS, MONTHS, UNCHECKED_RULES, WEEKDAYS, WEEKDAYS_LONG, PlanContext,
                          StaleSwapError, add_months, fmt_day, fmt_hours, holiday_name, is_weekend, month_bounds,
                          month_grid)
from planner_theme import (C, DateField, F, RoundedCard, ScrollFrame, Segmented, Tooltip, apply_theme, ask,
                           attach_tooltip,
                           bind_mousewheel, blend, choose, enable_windows_polish, info, round_rect, safe_color,
                           set_dpi_awareness)

CHECKED_RULES = ['Schichteignung', 'Urlaub', 'Einzelne Abwesenheitstage', 'Doppelte Schicht am selben Tag',
                 'Max. Arbeitstage pro Woche', 'Aufeinanderfolgende Arbeitstage (Hausregel)',
                 'Ruhezeit zwischen Schichten (Hausregel)', 'Mindestbesetzung nach der Änderung']
STATUS_COLORS = {'ok': C.OK, 'warn': C.WARN, 'block': C.DANGER, 'info': C.MUTED}
STATUS_ICONS = {'ok': '✓', 'warn': '!', 'block': '✕', 'info': '•'}


def compact_time(shift):
    return shift.compact_time


def day_title(day):
    return f'{WEEKDAYS_LONG[day.weekday()]}, {day:%d.%m.%Y}'


# ---------------------------------------------------------------------------
# Monatskalender (Canvas)
# ---------------------------------------------------------------------------

class CalendarView(tk.Frame):
    """Monats- oder Wochenkalender auf einem Canvas mit Klick-, Hover- und Zieh-Interaktion."""
    CHIP_GAP = 4

    def __init__(self, parent, app):
        super().__init__(parent, bg=C.BG)
        self.app = app
        self.canvas = tk.Canvas(self, bg=C.BG, highlightthickness=0, bd=0)
        self.bar = ttk.Scrollbar(self, orient='vertical', command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self.bar.set)
        self.canvas.pack(side='left', fill='both', expand=True)
        self.bar.pack(side='right', fill='y')
        self.items, self.key_cell, self.chip_rects, self.chip_items = {}, {}, {}, {}
        self._measure = {}
        self.hover_key, self.hover_cell = None, None
        self._job = None
        self._width = 0
        self._press = None
        self.dragging = None
        self.drop_key = None
        self.f_small = tkfont.Font(font=F(7, 'bold'))
        self.f_title = tkfont.Font(font=F(8, 'bold'))
        self.f_name = tkfont.Font(font=F(9, 'bold'))
        self.canvas.bind('<Configure>', self._on_resize)
        self.canvas.bind('<ButtonPress-1>', self._on_press)
        self.canvas.bind('<B1-Motion>', self._on_drag)
        self.canvas.bind('<ButtonRelease-1>', self._on_release)
        self.canvas.bind('<Motion>', self._on_motion)
        self.canvas.bind('<Leave>', lambda _e: self.dragging or self._unhover())
        bind_mousewheel(self, self.canvas)

    def _on_resize(self, event):
        if abs(event.width - self._width) < 4:
            return
        self._width = event.width
        if self._job:
            self.after_cancel(self._job)
        self._job = self.after(60, self.render)

    def measure(self, font, text):
        """Textbreite mit Zwischenspeicher – font.measure ist in Tk vergleichsweise teuer."""
        key = (id(font), text)
        value = self._measure.get(key)
        if value is None:
            value = self._measure[key] = font.measure(text)
        return value

    def _fit(self, text, font, width):
        key = ('fit', id(font), text, int(width))
        if key in self._measure:
            return self._measure[key]
        result = text
        if self.measure(font, text) > width:
            cut = text
            while cut and self.measure(font, cut + '…') > width:
                cut = cut[:-1]
            result = cut + '…' if len(cut) > 1 else text[:2]
        self._measure[key] = result
        return result

    # -- Layout ------------------------------------------------------------
    @property
    def week_mode(self):
        return self.app.view_mode == 'week'

    def _active_day(self, day):
        return self.week_mode or day.month == self.app.month.month

    def _shift_height(self, n):
        rows = math.ceil(n / self.per_row) if n else 0
        return 22 + rows * (self.chip_h + 3) + (2 if rows else 0)

    def _absent_height(self, day):
        absent = self._absent_list(day)
        if not absent:
            return 0
        return 20 + len(absent) * 20 if self.week_mode else 22

    # -- Zeichnen ----------------------------------------------------------
    def render(self):
        self._job = None
        app, c = self.app, self.canvas
        ctx = app.ctx
        if ctx is None:
            return
        self.app.tip.hide()
        c.delete('all')
        self.items.clear(); self.key_cell.clear(); self.chip_rects.clear(); self.chip_items.clear()
        self.hover_key = self.hover_cell = None
        W = max(380, c.winfo_width())
        pad, gap = 8, 6
        cw = (W - 2 * pad - 6 * gap) / 7
        week = self.week_mode
        avail = cw - 23
        if week:
            self.chip_w, self.chip_h, self.per_row = max(40, avail), 24, 1
        else:
            self.chip_w, self.chip_h = 32, 18
            self.per_row = max(1, int((avail + self.CHIP_GAP) // (self.chip_w + self.CHIP_GAP)))
        weeks = [app.week_days()] if week else month_grid(app.month)
        for col in range(7):
            x = pad + col * (cw + gap)
            name = WEEKDAYS_LONG[col].upper() if cw > (170 if week else 118) else WEEKDAYS[col].upper()
            if week:
                name = f'{name}  {weeks[0][col]:%d.%m.}'
            c.create_text(x + 10, pad + 9, anchor='w', text=name, font=F(8, 'bold'),
                          fill=C.ACCENT_SOFT if col >= 5 else C.MUTED)
        y = pad + 24
        shifts = [s for s in ctx.shift_list if app.shift_filter in (None, s.id)]
        staff = defaultdict(list)
        for (eid, ds), sid in ctx.plan.items():
            if eid in ctx.employees:
                staff[(ds, sid)].append(eid)
        for lst in staff.values():
            lst.sort(key=lambda e: ctx.employees[e].name.lower())
        index = 0
        for days in weeks:
            row_h = 104
            for day in days:
                if not self._active_day(day):
                    continue
                h = 32 + sum(self._shift_height(max(len(staff[(day.isoformat(), s.id)]), s.coverage)) + 4
                             for s in shifts)
                row_h = max(row_h, h + self._absent_height(day) + 4)
            if week:
                row_h = max(row_h, c.winfo_height() - y - pad)
            for col, day in enumerate(days):
                x = pad + col * (cw + gap)
                self._draw_day(index, day, x, y, x + cw, y + row_h, shifts, staff, cw)
                index += 1
            y += row_h + gap
        c.configure(scrollregion=(0, 0, W, y + pad))

    def _absent_list(self, day):
        ctx = self.app.ctx
        out = [(eid, 'leave') for eid in ctx.leave_on(day) if eid in ctx.employees]
        ds = day.isoformat()
        out += [(e.id, 'absent') for e in ctx.active if ds in e.unavailable and (e.id, 'leave') not in out]
        return out

    def _draw_day(self, i, day, x1, y1, x2, y2, shifts, staff, cw):
        app, c, ctx = self.app, self.canvas, self.app.ctx
        active = self._active_day(day)
        hol, we = holiday_name(day), is_weekend(day)
        fill = C.HOLIDAY if hol else (C.WEEKEND if we else C.DAY)
        if not active:
            fill = C.OUTSIDE
        cell_tag = f'cell{i}'
        key = f'd{i}'
        rect = round_rect(c, x1, y1, x2, y2, 12, fill=fill, outline=C.BORDER_SOFT, width=1, tags=(key, cell_tag))
        self.items[key] = ('day', day, None, None)
        self.key_cell[cell_tag] = rect
        today = day == date.today()
        num_color = C.TEXT if active else C.DIM
        if today:
            c.create_oval(x1 + 6, y1 + 5, x1 + 28, y1 + 27, fill=C.ACCENT, outline=C.GLOW, width=1,
                          tags=(key, cell_tag))
            num_color = '#ffffff'
        c.create_text(x1 + 17, y1 + 16, text=str(day.day), font=F(10, 'bold'), fill=num_color, tags=(key, cell_tag))
        if hol:
            c.create_text(x2 - 9, y1 + 16, anchor='e', text=self._fit(hol, self.f_small, cw - 44), font=F(7, 'bold'),
                          fill=C.ACCENT_SOFT if active else C.DIM, tags=(key, cell_tag))
        elif we and active:
            c.create_text(x2 - 9, y1 + 16, anchor='e', text='WOCHENENDE' if cw > 120 else 'WE', font=F(6, 'bold'),
                          fill=C.DIM, tags=(key, cell_tag))
        if not active:
            return
        ds = day.isoformat()
        cy = y1 + 32
        cx1, cx2 = x1 + 6, x2 - 6
        for s in shifts:
            eids = staff[(ds, s.id)]
            have, need = len(eids), s.coverage
            ch = self._shift_height(max(have, need))
            color = safe_color(s.color)
            under = have < need
            skey = f's{i}_{s.id}'
            self.items[skey] = ('shift', day, s.id, None)
            self.key_cell[skey] = rect
            card_fill = blend(C.CARD, C.DANGER, .13) if under else C.CARD
            outline = blend(C.DANGER, C.CARD, .25) if under else C.BORDER_SOFT
            round_rect(c, cx1, cy, cx2, cy + ch, 9, fill=card_fill, outline=outline, width=1, tags=(skey, cell_tag))
            round_rect(c, cx1 + 2, cy + 5, cx1 + 6, cy + ch - 5, 2, fill=color, outline='', tags=(skey, cell_tag))
            count = f'{have}/{need}'
            if s.cap and self.week_mode and cw > 190:
                count += f' (max {s.cap})'
            count_color = C.DANGER if under else (C.ACCENT_SOFT if have > need else (C.OK if need else C.DIM))
            if under:
                count = '! ' + count
            count_w = self.measure(self.f_title, count)
            c.create_text(cx2 - 7, cy + 11, anchor='e', text=count, font=F(8, 'bold'), fill=count_color,
                          tags=(skey, cell_tag))
            title_space = (cx2 - 7 - count_w - 6) - (cx1 + 10)
            title = self._fit(s.short, self.f_title, title_space)
            c.create_text(cx1 + 10, cy + 11, anchor='w', text=title, font=F(8, 'bold'), fill=C.TEXT,
                          tags=(skey, cell_tag))
            time_x = cx1 + 10 + self.measure(self.f_title, title) + 5
            time_text = compact_time(s)
            if title == s.short and time_x + self.measure(self.f_small, time_text) < cx2 - 7 - count_w - 4:
                c.create_text(time_x, cy + 11, anchor='w', text=time_text, font=F(7), fill=C.DIM,
                              tags=(skey, cell_tag))
            for k, eid in enumerate(eids):
                r, col = divmod(k, self.per_row)
                self._draw_chip(eid, day, s, cx1 + 9 + col * (self.chip_w + self.CHIP_GAP),
                                cy + 21 + r * (self.chip_h + 3), color, cell_tag, rect)
            for k in range(have, need):
                r, col = divmod(k, self.per_row)
                gx = cx1 + 9 + col * (self.chip_w + self.CHIP_GAP)
                gy = cy + 21 + r * (self.chip_h + 3)
                gkey = f'g{i}_{s.id}_{k}'
                self.items[gkey] = ('ghost', day, s.id, None)
                self.key_cell[gkey] = rect
                round_rect(c, gx, gy, gx + self.chip_w, gy + self.chip_h, self.chip_h / 2,
                           fill=blend(C.DANGER, C.CARD, .9), outline=C.DANGER, dash=(3, 2), width=1,
                           tags=(gkey, cell_tag))
                c.create_text(gx + self.chip_w / 2, gy + self.chip_h / 2,
                              text='+  offen' if self.week_mode else '+', font=F(9 if not self.week_mode else 8, 'bold'),
                              fill=C.DANGER, tags=(gkey, cell_tag))
            cy += ch + 4
        absent = self._absent_list(day)
        if not absent:
            return
        if self.week_mode:
            c.create_text(cx1 + 2, cy + 9, anchor='w', text='ABWESEND', font=F(7, 'bold'), fill=C.MUTED,
                          tags=(key, cell_tag))
            ly = cy + 18
            for eid, kind in absent:
                lkey = f'l{i}_{eid}'
                self.items[lkey] = ('leave', day, None, eid)
                self.key_cell[lkey] = rect
                color = C.LEAVE if kind == 'leave' else C.WARN
                round_rect(c, cx1, ly, cx2, ly + 17, 8, fill=blend(color, C.DAY, .85), outline=color, width=1,
                           tags=(lkey, cell_tag))
                text = ('Urlaub · ' if kind == 'leave' else 'Abw. · ') + ctx.employees[eid].name
                c.create_text(cx1 + 8, ly + 8.5, anchor='w', text=self._fit(text, self.f_small, cx2 - cx1 - 14),
                              font=F(7, 'bold'), fill=color, tags=(lkey, cell_tag))
                ly += 20
            return
        label = 'Urlaub' if any(k == 'leave' for _e, k in absent) else 'Abw.'
        lw = self.measure(self.f_small, label) + 6
        c.create_text(cx1 + 2, cy + 10, anchor='w', text=label, font=F(7, 'bold'),
                      fill=C.LEAVE if label == 'Urlaub' else C.WARN, tags=(key, cell_tag))
        lx = cx1 + 2 + lw
        for n, (eid, kind) in enumerate(absent):
            if lx + 26 > cx2:
                c.create_text(lx, cy + 10, anchor='w', text=f'+{len(absent) - n}', font=F(7, 'bold'),
                              fill=C.MUTED, tags=(key, cell_tag))
                break
            lkey = f'l{i}_{eid}'
            self.items[lkey] = ('leave', day, None, eid)
            self.key_cell[lkey] = rect
            color = C.LEAVE if kind == 'leave' else C.WARN
            round_rect(c, lx, cy + 3, lx + 24, cy + 17, 7, fill=blend(color, C.DAY, .82), outline=color,
                       width=1, tags=(lkey, cell_tag))
            c.create_text(lx + 12, cy + 10, text=ctx.labels.get(eid, '?')[:3], font=F(6, 'bold'), fill=color,
                          tags=(lkey, cell_tag))
            lx += 27

    def _chip_style(self, eid, ds, color):
        """Farben eines Namenschips abhängig von Filter, Auswahl, Tauschpartnern und Vorschau."""
        app, emp = self.app, self.app.ctx.employees[eid]
        fill, text_color, outline, width, ring = blend(color, C.CARD, .62), C.TEXT, blend(color, C.CARD, .3), 1, None
        if (app.role_filter and emp.role != app.role_filter) or (
                app.selected and app.selected[0] != eid and (eid, ds) not in app.highlights):
            fill, text_color, outline = blend(C.CARD, C.BG, .35), C.DIM, C.BORDER_SOFT
        if (eid, ds) in app.highlights:
            fill, text_color, outline, width = blend(C.ACCENT, C.CARD, .45), '#ffffff', C.ACCENT_SOFT, 2
        if (eid, ds) in app.preview_marks:
            if app.preview is not None and not app.preview.valid:  # angeklickt, aber Tausch nicht zulässig
                fill, text_color, outline, width = blend(C.DANGER, C.CARD, .55), '#ffffff', C.DANGER, 2
            else:
                fill, text_color, outline, width = C.ACCENT_SOFT, '#160b33', C.GLOW, 2
            ring = 'preview'
        if app.selected == (eid, ds):
            fill, text_color, outline, width, ring = C.ACCENT, '#ffffff', C.GLOW, 2, 'selected'
        return fill, text_color, outline, width, ring

    def _draw_rings(self, key, ring, outline, x, y, w, h, cell_tag):
        c, r = self.canvas, h / 2
        if ring == 'preview':
            round_rect(c, x - 3, y - 3, x + w + 3, y + h + 3, r + 3, fill='', outline=blend(outline, C.CARD, .4),
                       width=2, tags=(key, cell_tag, 'ring'))
        elif ring == 'selected':
            for grow, t in ((4, .7), (2, .35)):
                round_rect(c, x - grow, y - grow, x + w + grow, y + h + grow, r + grow, fill='',
                           outline=blend(C.ACCENT, C.CARD, t), width=2, tags=(key, cell_tag, 'ring'))

    def _draw_chip(self, eid, day, shift, x, y, color, cell_tag, cell_rect):
        c, ctx = self.canvas, self.app.ctx
        emp = ctx.employees[eid]
        ds = day.isoformat()
        key = f'c{eid}_{ds}'
        self.items[key] = ('chip', day, shift.id, eid)
        self.key_cell[key] = cell_rect
        w, h = self.chip_w, self.chip_h
        fill, text_color, outline, width, ring = self._chip_style(eid, ds, color)
        self._draw_rings(key, ring, outline, x, y, w, h, cell_tag)
        rect = round_rect(c, x, y, x + w, y + h, h / 2, fill=fill, outline=outline, width=width,
                          tags=(key, 'chip', cell_tag))
        self.chip_rects[key] = (rect, outline, width)
        label_text = ctx.labels.get(eid, '?')
        texts, badge = [], None
        if self.week_mode:
            # Initialen-Badge + voller Name
            badge = c.create_oval(x + 3, y + 3, x + h - 3, y + h - 3, fill=blend(text_color, fill, .78), outline='',
                                  tags=(key, 'chip', cell_tag))
            texts.append(c.create_text(x + h / 2, y + h / 2, text=label_text[:2], font=F(6, 'bold'), fill=text_color,
                                       tags=(key, 'chip', cell_tag)))
            short_name = re.sub(r'^(Demo|Test)\s+', '', emp.name)  # Kennzeichnung steht im Tooltip
            name = self._fit(short_name, self.f_name, w - h - 10)
            texts.append(c.create_text(x + h + 3, y + h / 2, anchor='w', text=name, font=F(9, 'bold'),
                                       fill=text_color, tags=(key, 'chip', cell_tag)))
        else:
            texts.append(c.create_text(x + w / 2, y + h / 2, text=label_text, font=F(7, 'bold'), fill=text_color,
                                       tags=(key, 'chip', cell_tag)))
        self.chip_items[key] = (eid, ds, color, x, y, w, h, cell_tag, rect, badge, texts)

    def restyle(self):
        """Nur Farben/Leuchtrahmen der Namenschips aktualisieren – ohne den Kalender neu aufzubauen.

        Wird bei Auswahl, Vorschau und Tausch-Countdown genutzt und ist deshalb sofort sichtbar."""
        if not self.chip_items or self.app.ctx is None:
            self.render()
            return
        c = self.canvas
        self.app.tip.hide()
        c.delete('ring')
        self.hover_key = None
        for key, (eid, ds, color, x, y, w, h, cell_tag, rect, badge, texts) in self.chip_items.items():
            if eid not in self.app.ctx.employees:
                continue
            fill, text_color, outline, width, ring = self._chip_style(eid, ds, color)
            c.itemconfigure(rect, fill=fill, outline=outline, width=width)
            self.chip_rects[key] = (rect, outline, width)
            for t in texts:
                c.itemconfigure(t, fill=text_color)
            if badge:
                c.itemconfigure(badge, fill=blend(text_color, fill, .78))
            if ring:
                self._draw_rings(key, ring, outline, x, y, w, h, cell_tag)
                c.tag_raise(rect)
                for item in ([badge] if badge else []) + texts:
                    c.tag_raise(item)

    # -- Interaktion -------------------------------------------------------
    def _key_at(self):
        ids = self.canvas.find_withtag('current')
        if not ids:
            return None
        return self._key_of(ids[0])

    def _key_of(self, item):
        for tag in self.canvas.gettags(item):
            if tag in self.items:
                return tag
        return None

    def _key_at_point(self, x, y):
        for item in reversed(self.canvas.find_overlapping(x, y, x, y)):
            if 'drag' in self.canvas.gettags(item):
                continue
            key = self._key_of(item)
            if key:
                return key
        return None

    def _on_press(self, event):
        self.app.tip.hide()
        self._press = (self._key_at(), event.x, event.y)

    def _on_drag(self, event):
        if not self._press:
            return
        key, x0, y0 = self._press
        if not self.dragging:
            if not key or self.items[key][0] != 'chip' or abs(event.x - x0) + abs(event.y - y0) < 8:
                return
            _kind, day, _sid, eid = self.items[key]
            self.dragging = (eid, day)
            self.app.begin_drag(eid, day)  # Person auswählen, Tauschpartner leuchten auf
        c = self.canvas
        if event.y < 24:
            c.yview_scroll(-1, 'units')
        elif event.y > c.winfo_height() - 24:
            c.yview_scroll(1, 'units')
        x, y = c.canvasx(event.x), c.canvasy(event.y)
        c.delete('drag')
        eid, _day = self.dragging
        name = self.app.ctx.employees[eid].name
        width = self.measure(self.f_name, name) + 34
        round_rect(c, x + 10, y + 8, x + 10 + width, y + 34, 13, fill=C.ACCENT, outline=C.GLOW, width=2, tags='drag')
        c.create_text(x + 24, y + 21, anchor='w', text='⇄  ' + name, font=F(9, 'bold'), fill='#ffffff', tags='drag')
        target = self._key_at_point(x, y)
        if target != self.drop_key:
            self._clear_drop()
            if target and self.items[target][0] == 'chip' and target in self.chip_rects:
                rect, _o, _w = self.chip_rects[target]
                c.itemconfigure(rect, outline=C.GLOW, width=3)
                self.drop_key = target

    def _clear_drop(self):
        if self.drop_key in self.chip_rects:
            rect, outline, width = self.chip_rects[self.drop_key]
            self.canvas.itemconfigure(rect, outline=outline, width=width)
        self.drop_key = None

    def _on_release(self, event):
        press, self._press = self._press, None
        if self.dragging:
            target = self.drop_key
            self._clear_drop()
            self.canvas.delete('drag')
            self.dragging = None
            if target:
                _kind, day, _sid, eid = self.items[target]
                self.app.chip_clicked(eid, day)
            else:
                self.app.set_status('Zum Tauschen auf einen leuchtenden Namen ziehen – oder Namen anklicken.', 'warn')
            return
        if not press or not press[0]:
            return
        kind, day, sid, eid = self.items[press[0]]
        if kind == 'chip':
            self.app.chip_clicked(eid, day)
        elif self._active_day(day):
            self.app.open_day(day)
        else:
            self.app.goto_month(day.replace(day=1))

    def _unhover(self):
        self.app.tip.hide()
        if self.hover_cell is not None:
            try:
                self.canvas.itemconfigure(self.hover_cell, outline=C.BORDER_SOFT, width=1)
            except tk.TclError:
                pass
        if self.hover_key in self.chip_rects and self.hover_key != self.drop_key:
            rect, outline, width = self.chip_rects[self.hover_key]
            self.canvas.itemconfigure(rect, outline=outline, width=width)
        self.hover_key = self.hover_cell = None
        self.canvas.configure(cursor='')

    def _on_motion(self, event):
        if self.dragging:
            return
        key = self._key_at()
        if key == self.hover_key:
            return
        self._unhover()
        if not key:
            return
        self.hover_key = key
        cell = self.key_cell.get(key)
        if cell is not None:
            self.canvas.itemconfigure(cell, outline=blend(C.ACCENT, C.BORDER, .35), width=1)
            self.hover_cell = cell
        if key in self.chip_rects:
            rect, _o, _w = self.chip_rects[key]
            self.canvas.itemconfigure(rect, outline=C.GLOW, width=2)
        self.canvas.configure(cursor='hand2')
        self.app.tip.schedule(event.x_root, event.y_root, self._tooltip(key))

    def _tooltip(self, key):
        app, ctx = self.app, self.app.ctx
        kind, day, sid, eid = self.items[key]
        if kind == 'chip':
            e, s = ctx.employees[eid], ctx.shifts[sid]
            lines = [e.name + ('  ·  Testdaten' if e.is_demo else ''), e.role or 'ohne Bereich',
                     f'{s.name} · {s.time_label} · {fmt_day(day)}',
                     f'Stunden im Monat: {fmt_hours(ctx.month_hours(eid))} h von {fmt_hours(ctx.target_hours(eid))} h Soll']
            if e.preferences:
                lines.append(f'Wünsche: {e.preferences}')
            lines.append('Klicken oder auf einen anderen Namen ziehen: tauschen')
            return '\n'.join(lines)
        if kind in ('shift', 'ghost'):
            s = ctx.shifts[sid]
            names = [ctx.employees[x].name for x in ctx.staff_on(day, sid) if x in ctx.employees]
            cap = f', max. {s.cap}' if s.cap else ''
            lines = [f'{s.name} · {s.time_label}', f'{fmt_day(day)} · Besetzung {len(names)} (mind. {s.coverage}{cap})']
            lines += [f'  • {n}' for n in names]
            if len(names) < s.coverage:
                lines.append(f'Offen: {s.coverage - len(names)} Platz/Plätze')
            lines.append('Klicken: Tagesplanung öffnen')
            return '\n'.join(lines)
        if kind == 'leave':
            e = ctx.employees[eid]
            if ctx.on_leave(eid, day):
                period = next((f"{date.fromisoformat(lp['start_date']):%d.%m.}–{date.fromisoformat(lp['end_date']):%d.%m.%Y}"
                               + (f"\n{lp['note']}" if lp['note'] else '')
                               for lp in ctx.leave_periods if lp['employee_id'] == eid
                               and lp['start_date'] <= day.isoformat() <= lp['end_date']), '')
                return f'{e.name}\nUrlaub {period}'
            return f'{e.name}\nEinzelner Abwesenheitstag'
        text = day_title(day)
        hol = holiday_name(day)
        if hol:
            text += f'\nFeiertag: {hol} – regulärer Dienstbetrieb'
        elif is_weekend(day):
            text += '\nWochenende – regulärer Dienstbetrieb'
        if not self._active_day(day):
            return text + '\nKlicken: zu diesem Monat wechseln'
        return text + '\nKlicken: Tagesplanung öffnen'


# ---------------------------------------------------------------------------
# Kleine UI-Helfer
# ---------------------------------------------------------------------------

def label(parent, text, fg=C.TEXT, font=None, bg=None, **pack):
    lbl = tk.Label(parent, text=text, fg=fg, bg=bg or parent.cget('bg'), font=font or F(9), justify='left',
                   anchor='w', wraplength=pack.pop('wrap', 300))
    if pack.pop('nopack', False):
        return lbl
    lbl.pack(fill='x', **pack)
    return lbl


def section(parent, title, pady=(16, 6)):
    row = tk.Frame(parent, bg=parent.cget('bg'))
    row.pack(fill='x', pady=pady)
    tk.Label(row, text=title.upper(), bg=parent.cget('bg'), fg=C.ACCENT_SOFT, font=F(8, 'bold')).pack(side='left')
    tk.Frame(row, bg=C.BORDER, height=1).pack(side='left', fill='x', expand=True, padx=(8, 0), pady=(2, 0))
    return row


def clickable(widget, command, hover_bg=C.CARD_HOVER):
    normal = widget.cget('bg')
    widgets = [widget] + list(widget.winfo_children())

    def enter(_e):
        for w in widgets:
            try:
                w.configure(bg=hover_bg)
            except tk.TclError:
                pass

    def leave(_e):
        for w in widgets:
            try:
                w.configure(bg=normal)
            except tk.TclError:
                pass
    for w in widgets:
        w.bind('<Button-1>', lambda _e: command(), add='+')
        w.bind('<Enter>', enter, add='+')
        w.bind('<Leave>', leave, add='+')
        w.configure(cursor='hand2')


def card(parent, fill=C.CARD, border=None, padx=12, pady=9, radius=12, pack=True, **pack_kw):
    """Abgerundete Karte; Inhalt in .inner."""
    rc = RoundedCard(parent, fill=fill, border=border or blend(fill, '#ffffff', .06), radius=radius,
                     padx=padx, pady=pady)
    if pack:
        pack_kw.setdefault('fill', 'x')
        rc.pack(**pack_kw)
    return rc


def clickable_card(rc, command, hover_fill=C.CARD_HOVER):
    normal = rc.fill

    def all_widgets(w):
        yield w
        for child in w.winfo_children():
            yield from all_widgets(child)
    widgets = [rc] + list(all_widgets(rc.inner))
    for w in widgets:
        w.bind('<Button-1>', lambda _e: command(), add='+')
        w.bind('<Enter>', lambda _e: rc.set_colors(fill=hover_fill), add='+')
        w.bind('<Leave>', lambda _e: rc.set_colors(fill=normal), add='+')
        try:
            w.configure(cursor='hand2')
        except tk.TclError:
            pass


def chip_widget(parent, text, fill, fg='#ffffff', outline=None, glow=False):
    cv = tk.Canvas(parent, width=44 if glow else 36, height=30 if glow else 22, bg=parent.cget('bg'),
                   highlightthickness=0)
    off = 6 if glow else 2
    if glow:
        for grow, t in ((5, .75), (3, .5)):
            round_rect(cv, off - grow, off - grow + 0, off + 32 + grow, off + 18 + grow, 9 + grow, fill='',
                       outline=blend(C.ACCENT, parent.cget('bg'), t), width=2)
    round_rect(cv, off, off, off + 32, off + 18, 9, fill=fill, outline=outline or fill, width=2 if glow else 1)
    cv.create_text(off + 16, off + 9, text=text, font=F(7, 'bold'), fill=fg)
    return cv


# ---------------------------------------------------------------------------
# Hauptfenster
# ---------------------------------------------------------------------------

class App(tk.Tk):
    SIDE_W = 340

    def __init__(self, store=None):
        super().__init__()
        self.withdraw()
        self.store = store or core.Store()
        self.month = date.today().replace(day=1)
        self.last_generation = None
        if self.store.demo_seeded_now and not self.store.all('SELECT id FROM assignments LIMIT 1'):
            # Beispielmonat nur für eine frisch angelegte Demo-Datenbank – bestehende Pläne bleiben unberührt.
            self.last_generation = (self.month, core.generate_month(self.store, self.month))
        self.ctx = None
        self.role_filter, self.shift_filter = None, None
        self.selected = None
        self.swap_options, self.valid_options = [], []
        self.highlights, self.preview_marks = set(), set()
        self.preview = None
        self.show_invalid = False
        self.show_details = False
        self.show_scope = False
        self.pending = None
        self._pending_job = None
        self._pending_deadline = 0.0
        self.busy = False
        self.view_mode = 'week' if self.store.setting('ui_view_mode', 'month') == 'week' else 'month'
        today = date.today()
        self.week_start = today - timedelta(days=today.weekday())
        self.side_open = self.store.setting('ui_side_panel', '0') == '1'
        self.title(APP_NAME)
        self.geometry('1480x920')
        self.minsize(1120, 720)
        apply_theme(self)
        self.tip = Tooltip(self)
        self._build_shell()
        self.pages = {}
        self.page_builders = {'plan': self.build_plan, 'staff': self.build_staff, 'leave': self.build_leave,
                              'hours': self.build_hours, 'shifts': self.build_shifts}
        self.show_page('plan')
        self.bind('<Prior>', lambda _e: self._plan_key(lambda: self.step_period(-1)))
        self.bind('<Next>', lambda _e: self._plan_key(lambda: self.step_period(1)))
        self.bind('<Escape>', lambda _e: self.cancel_pending() if self.pending else (self.selected and self.cancel_swap()))
        self.bind('<Return>', lambda _e: self.pending and self._plan_key(self.confirm_swap))
        self.bind('<Control-z>', lambda _e: self._text_safe(self.undo_step))
        self.bind('<Control-y>', lambda _e: self._text_safe(self.redo_step))
        self.bind('<Control-Z>', lambda _e: self._text_safe(self.redo_step))  # Strg+Umschalt+Z
        self.protocol('WM_DELETE_WINDOW', self.on_close)
        self.deiconify()
        enable_windows_polish(self)
        if self.store.backup_path:
            self.set_status(f'Datenbank wurde aktualisiert. Sicherung vor der Migration: '
                            f'{os.path.basename(self.store.backup_path)} (Ordner data/backups)')
        elif self.last_generation:
            self.set_status('Demo-Datenbank angelegt: fiktive Testpersonen und ein Beispielmonat wurden erzeugt.')

    def on_close(self):
        self._stop_pending()  # ein noch laufender Klick-Tausch wird NICHT gespeichert
        self.tip.hide()
        self.store.close()
        self.destroy()

    def _text_safe(self, fn):
        # In Textfeldern bleibt Strg+Z das normale Rückgängig des Feldes
        if not isinstance(self.focus_get(), (ttk.Entry, ttk.Combobox, ttk.Spinbox, tk.Entry, tk.Text)):
            fn()

    def _plan_key(self, fn):
        if self.current_page == 'plan' and not isinstance(self.focus_get(), (ttk.Combobox, ttk.Entry)):
            fn()

    # -- Rahmen ------------------------------------------------------------
    def _build_shell(self):
        side = tk.Frame(self, bg=C.SIDEBAR, width=200)
        side.pack(side='left', fill='y')
        side.pack_propagate(False)
        logo = tk.Canvas(side, width=200, height=92, bg=C.SIDEBAR, highlightthickness=0)
        logo.pack(fill='x')
        cx, cy = 40, 46
        logo.create_oval(cx - 19, cy - 19, cx + 19, cy + 19, outline=blend(C.ACCENT, C.SIDEBAR, .55), width=1)
        logo.create_arc(cx - 15, cy - 15, cx + 15, cy + 15, start=20, extent=280, style='arc', outline=C.ACCENT,
                        width=3)
        logo.create_oval(cx - 8, cy - 8, cx + 8, cy + 8, outline=C.ACCENT_SOFT, width=2)
        logo.create_oval(cx - 3, cy - 3, cx + 3, cy + 3, fill=C.GLOW, outline='')
        logo.create_text(70, 36, anchor='w', text='KLINIK', font=F(8, 'bold'), fill=C.ACCENT_SOFT)
        logo.create_text(70, 56, anchor='w', text='Dienstplan', font=F(15, 'bold'), fill=C.TEXT)
        tk.Frame(side, bg=C.BORDER_SOFT, height=1).pack(fill='x', padx=16, pady=(0, 14))
        self.nav = {}
        for key, text in (('plan', '▦  Dienstplan'), ('staff', '◉  Mitarbeitende'), ('leave', '◷  Urlaub'),
                          ('hours', '◑  Stunden & Fairness'), ('shifts', '◔  Schichten & Regeln')):
            b = ttk.Button(side, text=text, style='Nav.TButton', command=lambda k=key: self.show_page(k))
            b.pack(fill='x', padx=10, pady=2)
            self.nav[key] = b
        foot = tk.Frame(side, bg=C.SIDEBAR)
        foot.pack(side='bottom', fill='x', padx=18, pady=16)
        tk.Label(foot, text='LOKAL · SQLITE', bg=C.SIDEBAR, fg=C.DIM, font=F(7, 'bold'), anchor='w').pack(fill='x')
        tk.Label(foot, text=os.path.basename(self.store.path), bg=C.SIDEBAR, fg=C.MUTED, font=F(8),
                 anchor='w').pack(fill='x')
        self.demo_badge = tk.Label(foot, text='', bg=C.SIDEBAR, fg=C.WARN, font=F(8, 'bold'), anchor='w',
                                   justify='left', wraplength=180)
        self.demo_badge.pack(fill='x', pady=(8, 0))
        self.main = tk.Frame(self, bg=C.BG)
        self.main.pack(side='left', fill='both', expand=True)
        status = tk.Frame(self.main, bg=C.SIDEBAR, height=32)
        status.pack(side='bottom', fill='x')
        self.status_dot = tk.Canvas(status, width=14, height=14, bg=C.SIDEBAR, highlightthickness=0)
        self.status_dot.pack(side='left', padx=(16, 4))
        self.status_dot.create_oval(3, 3, 11, 11, fill=C.OK, outline='')
        self.status_label = tk.Label(status, text='Bereit.', bg=C.SIDEBAR, fg=C.MUTED, font=F(9), anchor='w')
        self.status_label.pack(side='left', fill='x', expand=True, pady=6)
        self.status_undo = ttk.Button(status, text='↶ Rückgängig', command=self.undo_step)
        self.content = tk.Frame(self.main, bg=C.BG)
        self.content.pack(fill='both', expand=True)

    def update_demo_badge(self):
        n = self.store.db.execute('SELECT COUNT(*) FROM employees WHERE is_demo=1 AND active=1').fetchone()[0]
        self.demo_badge.configure(text=f'⚠ {n} Demo-Personen (Testdaten) aktiv' if n else '')

    def set_status(self, text, tone='ok', undo=False):
        self.status_label.configure(text=text)
        self.status_dot.itemconfigure(1, fill={'ok': C.OK, 'warn': C.WARN, 'danger': C.DANGER}.get(tone, C.OK))
        if undo:
            self.status_undo.pack(side='right', padx=12, pady=3)
        else:
            self.status_undo.pack_forget()

    def show_page(self, key):
        if self.busy:  # während „Monat planen“ rechnet, keine anderen Änderungen zulassen
            return
        self.tip.hide()
        if self.pending and key != 'plan':
            self.cancel_pending()
        if key not in self.pages:
            frame = tk.Frame(self.content, bg=C.BG)
            self.pages[key] = frame
            self.page_builders[key](frame)
        for k, frame in self.pages.items():
            frame.pack_forget()
            self.nav[k].configure(style='Nav.TButton')
        self.nav[key].configure(style='NavActive.TButton')
        self.pages[key].pack(fill='both', expand=True)
        self.current_page = key
        {'plan': self.refresh_plan, 'staff': self.refresh_staff, 'leave': self.refresh_leave,
         'hours': self.refresh_hours, 'shifts': self.refresh_shifts}[key]()
        self.update_demo_badge()

    def page_header(self, parent, eyebrow, title, subtitle):
        head = tk.Frame(parent, bg=C.BG)
        head.pack(fill='x', padx=26, pady=(20, 12))
        tk.Label(head, text=eyebrow, bg=C.BG, fg=C.ACCENT_SOFT, font=F(8, 'bold'), anchor='w').pack(fill='x')
        tk.Label(head, text=title, bg=C.BG, fg=C.TEXT, font=F(20, 'bold'), anchor='w').pack(fill='x')
        tk.Label(head, text=subtitle, bg=C.BG, fg=C.MUTED, font=F(10), anchor='w', justify='left').pack(fill='x')
        return head

    # =====================================================================
    # Dienstplan
    # =====================================================================
    def build_plan(self, p):
        top = tk.Frame(p, bg=C.BG)
        top.pack(fill='x', padx=22, pady=(18, 6))
        left = tk.Frame(top, bg=C.BG)
        left.pack(side='left')
        self.view_eyebrow = tk.Label(left, text='', bg=C.BG, fg=C.ACCENT_SOFT, font=F(8, 'bold'), anchor='w')
        self.view_eyebrow.pack(anchor='w')
        nav = tk.Frame(left, bg=C.BG)
        nav.pack(anchor='w', pady=(2, 0))
        ttk.Button(nav, text='‹', style='Icon.TButton', width=2, command=lambda: self.step_period(-1)).pack(side='left')
        self.month_label = tk.Label(nav, text='', bg=C.BG, fg=C.TEXT, font=F(19, 'bold'), anchor='w')
        self.month_label.pack(side='left', padx=12)
        ttk.Button(nav, text='›', style='Icon.TButton', width=2, command=lambda: self.step_period(1)).pack(side='left')
        ttk.Button(nav, text='Heute', command=self.go_today).pack(side='left', padx=(10, 10))
        self.view_switch = Segmented(nav, [('month', 'Monat'), ('week', 'Woche')], self.view_mode,
                                     on_change=self.set_view)
        self.view_switch.pack(side='left')
        right = tk.Frame(top, bg=C.BG)
        right.pack(side='right', anchor='s')
        ttk.Button(right, text='⚙  Monat planen', style='Accent.TButton', command=self.generate).pack(side='right')
        ttk.Button(right, text='CSV', command=self.export_csv).pack(side='right', padx=8)
        self.side_btn = ttk.Button(right, text='◧  Übersicht', command=self.toggle_side)
        self.side_btn.pack(side='right', padx=(8, 0))
        attach_tooltip(self.side_btn, self.tip, 'Seitenpanel mit Übersicht und Tauschassistent ein-/ausblenden')
        self._undo_tip = self._redo_tip = ''
        self.redo_btn = ttk.Button(right, text='↷', style='Icon.TButton', width=3, command=self.redo_step)
        self.redo_btn.pack(side='right', fill='y', padx=(4, 0))
        attach_tooltip(self.redo_btn, self.tip, lambda: self._redo_tip)
        self.undo_btn = ttk.Button(right, text='↶', style='Icon.TButton', width=3, command=self.undo_step)
        self.undo_btn.pack(side='right', fill='y')
        attach_tooltip(self.undo_btn, self.tip, lambda: self._undo_tip)
        bar = tk.Frame(p, bg=C.BG)
        bar.pack(fill='x', padx=22, pady=(6, 8))
        tk.Label(bar, text='Bereich', bg=C.BG, fg=C.MUTED, font=F(9)).pack(side='left')
        self.role_var = tk.StringVar(value='Alle Bereiche')
        self.role_box = ttk.Combobox(bar, textvariable=self.role_var, state='readonly', width=22)
        self.role_box.pack(side='left', padx=(6, 14))
        self.role_box.bind('<<ComboboxSelected>>', lambda _e: self._filters_changed())
        tk.Label(bar, text='Schicht', bg=C.BG, fg=C.MUTED, font=F(9)).pack(side='left')
        self.shift_var = tk.StringVar(value='Alle Schichten')
        self.shift_box = ttk.Combobox(bar, textvariable=self.shift_var, state='readonly', width=20)
        self.shift_box.pack(side='left', padx=(6, 14))
        self.shift_box.bind('<<ComboboxSelected>>', lambda _e: self._filters_changed())
        legend = tk.Canvas(bar, height=22, width=520, bg=C.BG, highlightthickness=0)
        legend.pack(side='right')
        x = 0
        for kind, text in (('sel', 'Ausgewählt'), ('partner', 'Tausch möglich'), ('open', 'Offen'),
                           ('leave', 'Urlaub'), ('we', 'Wochenende/Feiertag')):
            if kind == 'sel':
                round_rect(legend, x, 4, x + 22, 18, 7, fill=C.ACCENT, outline=C.GLOW, width=2)
            elif kind == 'partner':
                round_rect(legend, x, 4, x + 22, 18, 7, fill=blend(C.ACCENT, C.CARD, .45), outline=C.ACCENT_SOFT,
                           width=2)
            elif kind == 'open':
                round_rect(legend, x, 4, x + 22, 18, 7, fill=blend(C.DANGER, C.CARD, .9), outline=C.DANGER,
                           dash=(3, 2))
            elif kind == 'leave':
                round_rect(legend, x, 4, x + 22, 18, 7, fill=blend(C.LEAVE, C.DAY, .82), outline=C.LEAVE)
            else:
                legend.create_rectangle(x, 4, x + 10, 18, fill=C.WEEKEND, outline=C.BORDER)
                legend.create_rectangle(x + 12, 4, x + 22, 18, fill=C.HOLIDAY, outline=C.BORDER)
            legend.create_text(x + 28, 11, anchor='w', text=text, fill=C.MUTED, font=F(8))
            x += 28 + tkfont.Font(font=F(8)).measure(text) + 16
        legend.configure(width=x)
        body = tk.Frame(p, bg=C.BG)
        body.pack(fill='both', expand=True, padx=(16, 16), pady=(0, 10))
        body.columnconfigure(0, weight=1)
        body.rowconfigure(0, weight=1)
        self.cal = CalendarView(body, self)
        self.cal.grid(row=0, column=0, sticky='nsew')
        self.side_outer = RoundedCard(body, fill=C.SURFACE, border=C.BORDER, radius=16, padx=3, pady=10,
                                      fill_height=True)
        self.side_outer.configure(width=self.SIDE_W)
        self.side_outer.grid(row=0, column=1, sticky='ns', padx=(12, 0))
        self.side = self.side_outer.inner
        self.side_foot = tk.Frame(self.side, bg=C.SURFACE)
        self.side_foot.pack(side='bottom', fill='x')
        self.side_body = ScrollFrame(self.side, bg=C.SURFACE)
        self.side_body.pack(fill='both', expand=True)
        self._build_banner()
        self._build_selcard()
        self._apply_side()

    # -- Ansicht, Zeitraum, Seitenpanel ------------------------------------
    def week_days(self):
        return [self.week_start + timedelta(days=i) for i in range(7)]

    def set_view(self, mode):
        self.view_mode = mode
        self.store.set_setting('ui_view_mode', mode)
        if mode == 'week':
            today = date.today()
            anchor = today if (today.year, today.month) == (self.month.year, self.month.month) else self.month
            self.week_start = anchor - timedelta(days=anchor.weekday())
        self.cancel_swap(render=False)
        self.refresh_plan()

    def step_period(self, delta):
        if self.view_mode == 'week':
            self.goto_week(self.week_start + timedelta(days=7 * delta))
        else:
            self.goto_month(add_months(self.month, delta))

    def go_today(self):
        today = date.today()
        if self.view_mode == 'week':
            self.goto_week(today - timedelta(days=today.weekday()))
        else:
            self.goto_month(today.replace(day=1))

    def goto_week(self, monday):
        self.week_start = monday
        self.month = (monday + timedelta(days=3)).replace(day=1)  # Monat des Donnerstags (ISO-Woche)
        self.cancel_swap(render=False)
        self.refresh_plan()

    def toggle_side(self):
        self.side_open = not self.side_open
        self.store.set_setting('ui_side_panel', '1' if self.side_open else '0')
        self._apply_side()
        self.cal.after(30, self.cal.render)

    def _apply_side(self):
        if self.side_open:
            if getattr(self, '_side_dirty', True) and self.ctx is not None:
                self.render_side()
            self.side_outer.grid()
            self.side_btn.configure(style='ToggleOn.TButton')
        else:
            self.side_outer.grid_remove()
            self.side_btn.configure(style='Toggle.TButton')
        self.update_selcard()

    def begin_drag(self, eid, day):
        """Ziehen eines Namens beginnt: Person auswählen, damit die Tauschpartner leuchten."""
        if self.selected != (eid, day.isoformat()):
            self.select_chip(eid, day)

    def _build_selcard(self):
        self.selcard = RoundedCard(self.cal, fill=blend(C.ACCENT, C.SURFACE, .86), border=C.ACCENT_SOFT, radius=14,
                                   padx=16, pady=10, outer_bg=C.BG)
        inner = self.selcard.inner
        ttk.Button(inner, text='✕', style='Icon.TButton', width=2, command=self.cancel_swap).pack(side='right')
        self.selcard_btn = ttk.Button(inner, text='Vorschläge & Gründe', command=self.toggle_side)
        self.selcard_btn.pack(side='right', padx=(10, 8))
        self.selcard_title = tk.Label(inner, text='', bg=inner.cget('bg'), fg='#ffffff', font=F(10, 'bold'),
                                      anchor='w')
        self.selcard_title.pack(fill='x')
        self.selcard_sub = tk.Label(inner, text='', bg=inner.cget('bg'), fg=C.GLOW, font=F(9), anchor='w',
                                    justify='left', wraplength=700)
        self.selcard_sub.pack(fill='x')

    def update_selcard(self, message=None, tone='info'):
        """Schwebende Info-Karte, wenn das Seitenpanel eingeklappt ist."""
        if 'plan' not in self.pages:
            return
        if self.side_open or not self.selected or self.pending:
            self.selcard.place_forget()
            return
        eid, iso = self.selected
        day = date.fromisoformat(iso)
        sid = self.ctx.shift_of(eid, day)
        if not sid:
            self.selcard.place_forget()
            return
        name = self.ctx.employees[eid].name
        self.selcard_title.configure(text=f'{name} ausgewählt · {self.ctx.shifts[sid].short} {fmt_day(day)}')
        if message is None:
            n = len(self.highlights)
            message = (f'{n} Tauschpartner leuchten violett – Namen anklicken oder darauf ziehen.' if n else
                       core.explain_no_options(self.ctx, self.swap_options))
            tone = 'info' if n else 'warn'
        self.selcard_sub.configure(text=message, fg={'info': C.GLOW, 'warn': C.WARN, 'danger': C.DANGER}[tone])
        self.selcard.place(relx=.5, rely=1.0, anchor='s', y=-14, relwidth=.94)
        tk.Misc.tkraise(self.selcard)

    def goto_month(self, month):
        self.month = month
        self.cancel_swap(render=False)
        self.refresh_plan()

    def _filters_changed(self):
        role = self.role_var.get()
        self.role_filter = None if role == 'Alle Bereiche' else role
        name = self.shift_var.get()
        self.shift_filter = next((s.id for s in self.ctx.shift_list if s.name == name), None)
        self.cal.render()

    def refresh_plan(self):
        if 'plan' not in self.pages:
            return
        self.ctx = PlanContext.for_month(self.store, self.month)
        if self.view_mode == 'week':
            days = self.week_days()
            kw = days[0].isocalendar()[1]
            self.month_label.configure(text=f'KW {kw} · {days[0]:%d.%m.}–{days[-1]:%d.%m.%Y}')
            self.view_eyebrow.configure(text='DIENSTPLAN · WOCHENANSICHT')
        else:
            self.month_label.configure(text=f'{MONTHS[self.month.month - 1]} {self.month.year}')
            self.view_eyebrow.configure(text='DIENSTPLAN · MONATSANSICHT')
        self.view_switch.set(self.view_mode)
        roles = sorted({e.role for e in self.ctx.active if e.role})
        self.role_box.configure(values=['Alle Bereiche'] + roles)
        if self.role_filter and self.role_filter not in roles:
            self.role_filter = None
            self.role_var.set('Alle Bereiche')
        self.shift_box.configure(values=['Alle Schichten'] + [s.name for s in self.ctx.shift_list])
        if self.shift_filter not in self.ctx.shifts:
            self.shift_filter = None
            self.shift_var.set('Alle Schichten')
        self.update_history_buttons()
        if self.selected:
            self._compute_swap()
        self.cal.render()
        self.render_side()

    # -- Tauschassistent ---------------------------------------------------
    def chip_clicked(self, eid, day):
        """1. Klick wählt eine Person aus, 2. Klick auf eine andere Person startet den Tausch."""
        if self.busy:
            return
        iso = day.isoformat()
        if self.selected is None:
            self.select_chip(eid, day)
            return
        if self.selected == (eid, iso):
            if self.pending:
                self.cancel_pending()
            else:
                self.cancel_swap()
            return
        a_id, a_iso = self.selected
        if eid == a_id:
            self.select_chip(eid, day)  # andere Schicht derselben Person auswählen
            return
        try:
            opt = core.swap_between(self.ctx, a_id, date.fromisoformat(a_iso), eid, day)
        except ValueError as exc:
            self._stop_pending()
            self.set_status(str(exc), tone='warn')
            self.update_selcard(str(exc), 'warn')
            return
        if not opt.valid:
            self._stop_pending()
            self.preview_option(opt)
            self.set_status('Tausch nicht möglich: ' + opt.blockers[0].message, tone='danger')
            self.update_selcard('Tausch nicht möglich: ' + opt.blockers[0].message, 'danger')
            return
        self.start_pending(opt)

    def select_chip(self, eid, day):
        self._stop_pending()
        if self.selected == (eid, day.isoformat()):
            self.cancel_swap()
            return
        self.selected = (eid, day.isoformat())
        self.preview = None
        self.show_invalid = False
        self._compute_swap()
        self.cal.restyle()
        self.render_side()
        name = self.ctx.employees[eid].name
        if self.valid_options:
            self.set_status(f'{name} ausgewählt – jetzt einen leuchtenden Namen als Tauschpartner anklicken.')
        else:
            self.set_status(f'{name} ausgewählt – kein passender Tauschpartner gefunden (Begründung rechts).',
                            tone='warn')

    # -- Klick-Tausch mit Bedenkzeit ---------------------------------------
    def swap_delay(self):
        try:
            return max(0.0, min(60.0, float(self.store.setting('swap_delay_seconds') or 0)))
        except ValueError:
            return 5.0

    def _build_banner(self):
        self.banner = RoundedCard(self.cal, fill=blend(C.ACCENT, C.SURFACE, .78), border=C.ACCENT, radius=14,
                                  padx=16, pady=10, outer_bg=C.BG, border_width=2)
        inner = self.banner.inner
        top = tk.Frame(inner, bg=inner.cget('bg'))
        top.pack(fill='x')
        ttk.Button(top, text='Jetzt tauschen ⏎', style='Accent.TButton', command=self.confirm_swap).pack(
            side='right', padx=(8, 0))
        ttk.Button(top, text='Abbrechen (Esc)', command=self.cancel_pending).pack(side='right', padx=(16, 0))
        tk.Label(top, text='⇄', bg=inner.cget('bg'), fg=C.GLOW, font=F(16, 'bold')).pack(side='left', padx=(0, 10))
        texts = tk.Frame(top, bg=inner.cget('bg'))
        texts.pack(side='left', fill='x', expand=True)
        self.banner_title = tk.Label(texts, text='', bg=inner.cget('bg'), fg='#ffffff', font=F(10, 'bold'),
                                     anchor='w')
        self.banner_title.pack(fill='x')
        self.banner_sub = tk.Label(texts, text='', bg=inner.cget('bg'), fg=C.GLOW, font=F(9), anchor='w',
                                   justify='left')
        self.banner_sub.pack(fill='x')
        texts.bind('<Configure>', lambda e: self.banner_sub.configure(wraplength=max(200, e.width)))
        self.banner_bar = tk.Canvas(inner, height=4, bg=inner.cget('bg'), highlightthickness=0)
        self.banner_bar.pack(fill='x', pady=(8, 0))

    def start_pending(self, opt):
        self._stop_pending()
        self.preview_option(opt, render=False)
        delay = self.swap_delay()
        if delay <= 0:
            self.confirm_swap()
            return
        self.pending = opt
        self._pending_total = delay
        self._pending_deadline = time.monotonic() + delay
        self.cal.restyle()
        self.render_side()
        ctx = self.ctx
        a, b = ctx.employees[opt.a_id].name, ctx.employees[opt.b_id].name
        sa, sb = ctx.shifts[opt.a_shift], ctx.shifts[opt.b_shift]
        self.banner_title.configure(text=f'{a}: {sa.short} {fmt_day(opt.a_day)}   ⇄   {b}: {sb.short} {fmt_day(opt.b_day)}')
        self.selcard.place_forget()
        self.banner.place(relx=.5, rely=1.0, anchor='s', y=-14, relwidth=.94)
        tk.Misc.tkraise(self.banner)
        self.set_status(f'Tausch {a} ⇄ {b} läuft – Abbrechen mit Esc.', tone='warn')
        self._tick()

    def _tick(self):
        if not self.pending:
            return
        left = self._pending_deadline - time.monotonic()
        if left <= 0:
            self._pending_job = None
            self.confirm_swap()
            return
        warn = f' · {len(self.pending.warnings)} Hinweis(e) beachten' if self.pending.warnings else ''
        self.banner_sub.configure(text=f'Geprüfte Regeln erfüllt{warn}. Speichern in {math.ceil(left)} s – '
                                       'Abbrechen mit Esc oder Klick auf die Person.')
        width = self.banner_bar.winfo_width()
        self.banner_bar.delete('all')
        round_rect(self.banner_bar, 0, 0, width, 4, 2, fill=blend(C.ACCENT, C.SURFACE, .55), outline='')
        round_rect(self.banner_bar, 0, 0, max(4, width * left / self._pending_total), 4, 2, fill=C.GLOW, outline='')
        self._pending_job = self.after(100, self._tick)

    def _stop_pending(self):
        if self._pending_job:
            self.after_cancel(self._pending_job)
            self._pending_job = None
        was_pending = self.pending is not None
        self.pending = None
        if 'plan' in self.pages:
            self.banner.place_forget()
        return was_pending

    def cancel_pending(self):
        if self._stop_pending():
            self.preview, self.preview_marks = None, set()
            self.cal.restyle()
            self.render_side()
            self.set_status('Tausch abgebrochen – es wurde nichts gespeichert.', tone='warn')

    def _compute_swap(self):
        eid, iso = self.selected
        day = date.fromisoformat(iso)
        if not self.ctx.shift_of(eid, day):
            self.cancel_swap(render=False)
            return
        self.swap_options = core.find_swap_options(self.ctx, eid, day)
        self.valid_options = [o for o in self.swap_options if o.valid]
        self.highlights = core.partner_highlights(self.swap_options)
        if self.preview:
            old = self.preview
            self.preview = next((o for o in self.swap_options if (o.kind, o.b_id, o.b_day) ==
                                 (old.kind, old.b_id, old.b_day)), None)
        self.preview_marks = {(self.preview.b_id, self.preview.b_day.isoformat())} \
            if self.preview and self.preview.b_day else set()

    def preview_option(self, opt, render=True):
        self.preview = opt
        self.preview_marks = {(opt.b_id, opt.b_day.isoformat())} if opt.b_day else set()
        if render:
            self.cal.restyle()
            self.render_side(keep_scroll=True)

    def cancel_swap(self, render=True):
        self._stop_pending()
        self.selected, self.preview = None, None
        self.swap_options, self.valid_options = [], []
        self.highlights, self.preview_marks = set(), set()
        if render and 'plan' in self.pages:
            self.cal.restyle()
            self.render_side()

    def confirm_swap(self):
        self._stop_pending()
        opt = self.preview
        if not opt or not opt.valid:
            return
        description = opt.headline(self.ctx)
        try:
            core.apply_swap(self.store, opt)
        except StaleSwapError as exc:
            info(self, 'Tausch nicht gespeichert', 'Der Tausch wurde nicht gespeichert.', str(exc), tone='danger')
            self.refresh_plan()
            return
        self.cancel_swap(render=False)
        self.refresh_plan()
        self.set_status(f'Tausch gespeichert: {description}', undo=True)

    def undo_swap(self):  # Kompatibilität
        self.undo_step()

    def undo_step(self):
        """Einen Schritt zurück. Mehrfach klicken = mehrere Schritte. Ohne Rückfrage, weil „Wiederholen“
        jeden Schritt wiederherstellen kann."""
        self._history_step(undo=True)

    def redo_step(self):
        self._history_step(undo=False)

    def _history_step(self, undo):
        if self.busy:
            return
        if self.pending:
            self.cancel_pending()
            return
        try:
            description = core.undo_last_change(self.store) if undo else core.redo_next_change(self.store)
        except StaleSwapError as exc:
            info(self, 'Rückgängig' if undo else 'Wiederholen', 'Dieser Schritt ist nicht möglich.', str(exc),
                 tone='warn')
            return
        self.cancel_swap(render=False)
        self.refresh_current_page()
        next_undo, n_undo, next_redo, n_redo = core.undo_info(self.store)
        if undo:
            self.set_status(f'Rückgängig: {description} · noch {n_undo} Schritt(e) zurück möglich, '
                            'Wiederholen mit ↷ / Strg+Y', tone='warn')
        else:
            self.set_status(f'Wiederholt: {description}' + (f' · noch {n_redo} Schritt(e) vor' if n_redo else ''))

    def refresh_current_page(self):
        {'plan': self.refresh_plan, 'staff': self.refresh_staff, 'leave': self.refresh_leave,
         'hours': self.refresh_hours, 'shifts': self.refresh_shifts}[self.current_page]()
        self.update_history_buttons()

    def update_history_buttons(self):
        if 'plan' not in self.pages:
            return
        next_undo, n_undo, next_redo, n_redo = core.undo_info(self.store)
        self.undo_btn.state(['!disabled'] if n_undo else ['disabled'])
        self.redo_btn.state(['!disabled'] if n_redo else ['disabled'])
        self._undo_tip = (f'Rückgängig (Strg+Z): {next_undo}\n{n_undo} Schritt(e) gespeichert' if n_undo
                          else 'Nichts rückgängig zu machen')
        self._redo_tip = (f'Wiederholen (Strg+Y): {next_redo}\n{n_redo} Schritt(e)' if n_redo
                          else 'Nichts zu wiederholen')

    # -- Seitenpanel -------------------------------------------------------
    def render_side(self, keep_scroll=False):
        if not self.side_open:
            # Eingeklappt: nichts aufbauen (spart Zeit), nur die kleine Infokarte aktualisieren.
            self._side_dirty = True
            self.update_selcard()
            return
        # Kalender-Feedback zuerst zeigen, das Seitenpanel kurz danach aufbauen (fühlt sich sofort an).
        if getattr(self, '_side_job', None):
            self.after_cancel(self._side_job)
        self._side_keep = keep_scroll or getattr(self, '_side_keep', False)
        self._side_job = self.after(15, self._render_side_now)

    def _render_side_now(self):
        self._side_job = None
        keep_scroll, self._side_keep = self._side_keep, False
        if not self.side_open:
            return
        self._side_dirty = False
        pos = self.side_body.canvas.yview()[0] if keep_scroll else 0
        self.side_body.clear()
        for child in self.side_foot.winfo_children():
            child.destroy()
        inner = tk.Frame(self.side_body.inner, bg=C.SURFACE, padx=18, pady=16)
        inner.pack(fill='both', expand=True)
        if self.selected:
            self._render_swap(inner)
        else:
            self._render_overview(inner)
        if keep_scroll:
            self.side_body.update_idletasks()
            self.side_body.canvas.yview_moveto(pos)
        self.update_selcard()

    def _tile(self, parent, col, value, text, color):
        rc = card(parent, C.CARD, C.BORDER_SOFT, padx=10, pady=8, pack=False)
        rc.grid(row=0, column=col, sticky='nsew', padx=(0 if col == 0 else 6, 0))
        tk.Label(rc.inner, text=str(value), bg=C.CARD, fg=color, font=F(18, 'bold'), anchor='w').pack(fill='x')
        tk.Label(rc.inner, text=text, bg=C.CARD, fg=C.MUTED, font=F(8), anchor='w', justify='left',
                 wraplength=90).pack(fill='x')

    def _render_overview(self, inner):
        ctx = self.ctx
        ov = core.month_overview(ctx)
        label(inner, 'MONATSÜBERSICHT', C.ACCENT_SOFT, F(8, 'bold'))
        label(inner, f'{MONTHS[self.month.month - 1]} {self.month.year}', C.TEXT, F(14, 'bold'), pady=(0, 10))
        hint = card(inner, blend(C.ACCENT, C.SURFACE, .85), blend(C.ACCENT, C.SURFACE, .55), pady=9,
                    radius=14).inner
        hint.master.pack_configure(pady=(0, 12))
        label(hint, 'SO TAUSCHST DU', C.ACCENT_SOFT, F(7, 'bold'))
        delay = self.swap_delay()
        last = (f'3  Innerhalb von {fmt_hours(delay)} s mit Esc abbrechen – sonst wird gespeichert.' if delay
                else '3  Der Tausch wird sofort gespeichert (Rückgängig mit Strg+Z).')
        for text in ('1  Namen im Kalender anklicken – er leuchtet.',
                     '2  Leuchtenden Tauschpartner anklicken oder den Namen darauf ziehen.', last):
            label(hint, text, C.TEXT, F(9), pady=1, wrap=265)
        tiles = tk.Frame(inner, bg=C.SURFACE)
        tiles.pack(fill='x')
        for col in range(3):
            tiles.columnconfigure(col, weight=1, uniform='t')
        self._tile(tiles, 0, ov.open_total, 'offene\nSchichtplätze', C.DANGER if ov.open_total else C.OK)
        self._tile(tiles, 1, ov.leave_days_total, 'Urlaubstage\nim Monat', C.LEAVE)
        self._tile(tiles, 2, len(ov.warnings), 'Regel-\nhinweise', C.WARN if ov.warnings else C.OK)

        section(inner, 'Offene Schichtplätze')
        if not ov.open_slots:
            label(inner, '✓  Alle Mindestbesetzungen sind erfüllt.', C.OK)
        for slot in ov.open_slots[:10]:
            rc = card(inner, C.SURFACE2, padx=10, pady=6, radius=10)
            rc.pack_configure(pady=2)
            row = rc.inner
            tk.Label(row, text=f'{WEEKDAYS[slot.day.weekday()]} {fmt_day(slot.day)}', bg=C.SURFACE2, fg=C.TEXT,
                     font=F(9, 'bold'), width=10, anchor='w').pack(side='left')
            tk.Label(row, text=slot.shift_name, bg=C.SURFACE2, fg=C.MUTED, font=F(9), anchor='w').pack(side='left')
            tk.Label(row, text=f'fehlt {slot.missing}', bg=C.SURFACE2, fg=C.DANGER, font=F(9, 'bold')).pack(
                side='right')
            clickable_card(rc, lambda d=slot.day: self.open_day(d), C.CARD_HOVER)
        if len(ov.open_slots) > 10:
            label(inner, f'+ {len(ov.open_slots) - 10} weitere offene Schichten', C.MUTED, pady=(4, 0))

        section(inner, 'Urlaub im Monat')
        if not ov.leave_rows:
            label(inner, 'Kein Urlaub eingetragen.', C.MUTED)
        for emp, first, last, n, note in ov.leave_rows:
            row = tk.Frame(inner, bg=C.SURFACE)
            row.pack(fill='x', pady=2)
            chip_widget(row, ctx.labels.get(emp.id, '?'), blend(C.LEAVE, C.SURFACE, .8), C.LEAVE, C.LEAVE).pack(
                side='left')
            tk.Label(row, text=emp.name, bg=C.SURFACE, fg=C.TEXT, font=F(9, 'bold'), anchor='w').pack(
                side='left', padx=(6, 0))
            tk.Label(row, text=f'{first:%d.%m.}–{last:%d.%m.} · {n} T.', bg=C.SURFACE, fg=C.MUTED, font=F(9)).pack(
                side='right')

        section(inner, 'Besetzungs- und Regelhinweise')
        if not ov.warnings:
            label(inner, '✓  Keine Verstöße gegen die in der App hinterlegten Regeln gefunden.', C.OK)
        for check in ov.warnings[:8]:
            label(inner, f'!  {check.message}', C.WARN, pady=2)
        if len(ov.warnings) > 8:
            label(inner, f'+ {len(ov.warnings) - 8} weitere Hinweise', C.MUTED)

        stats = core.fairness_report(ctx)
        section(inner, 'Soll-Stunden & Fairness')
        ok = sum(1 for r in stats if r.status == 'ok')
        label(inner, f'{ok} von {len(stats)} Personen erfüllen ihr Soll (± {fmt_hours(ctx.hours_tolerance)} h).',
              C.OK if ok == len(stats) else C.WARN, F(9, 'bold'))
        for r in sorted(stats, key=lambda x: x.diff)[:6]:
            row = tk.Frame(inner, bg=C.SURFACE)
            row.pack(fill='x', pady=(4, 0))
            tk.Label(row, text=r.employee.name, bg=C.SURFACE, fg=C.TEXT, font=F(8, 'bold'), anchor='w').pack(side='left')
            color = {'ok': C.OK, 'under': C.WARN, 'over': C.ACCENT_SOFT}[r.status]
            tk.Label(row, text=f'{fmt_hours(r.actual)} / {fmt_hours(r.target)} h  ({r.diff:+.1f})'.replace('.', ','),
                     bg=C.SURFACE, fg=color, font=F(8)).pack(side='right')
            bar = tk.Canvas(inner, height=6, bg=C.SURFACE, highlightthickness=0)
            bar.pack(fill='x', pady=(2, 0))
            bar.bind('<Configure>', lambda e, b=bar, v=r.actual, t=r.target, c=color: (
                b.delete('all'),
                round_rect(b, 0, 0, e.width, 6, 3, fill=C.SURFACE2, outline=''),
                round_rect(b, 0, 0, max(6, e.width * min(1.0, v / t if t else 1)), 6, 3, fill=c, outline='')))
        ttk.Button(inner, text='Alle Stunden & Fairness →', style='Link.TButton',
                   command=lambda: self.show_page('hours')).pack(anchor='w', pady=(8, 0))

        if self.last_generation and self.last_generation[0] == self.month:
            res = self.last_generation[1]
            section(inner, 'Letzter Planlauf')
            label(inner, f'{res.filled} Dienste automatisch eingeplant.', C.TEXT)
            if res.open_slots:
                label(inner, f'{res.missing} Schichtplätze konnten nicht besetzt werden.', C.DANGER)
                reasons = defaultdict(int)
                for slot in res.open_slots:
                    for part in slot.reason.split(', '):
                        if part:
                            reasons[part.rsplit(' (', 1)[0]] += 1
                if reasons:
                    label(inner, 'Häufigste Gründe: ' + ', '.join(sorted(reasons, key=reasons.get, reverse=True)[:4]),
                          C.MUTED)

        section(inner, 'Mehr')
        ttk.Button(inner, text=('▾ ' if self.show_scope else '▸ ') + 'Was prüft die App – und was nicht?',
                   command=lambda: self._toggle('show_scope')).pack(anchor='w')
        if self.show_scope:
            label(inner, 'Geprüft: ' + ', '.join(CHECKED_RULES) + '.', C.MUTED, pady=(6, 2))
            for text in UNCHECKED_RULES:
                label(inner, '·  ' + text, C.DIM, pady=1)
            label(inner, 'Tag oder Schichtkarte anklicken → Tagesplanung. Bild↑/Bild↓ wechselt den Monat.',
                  C.MUTED, pady=(6, 0))

    def _render_swap(self, inner):
        ctx = self.ctx
        eid, iso = self.selected
        day = date.fromisoformat(iso)
        emp = ctx.employees[eid]
        shift = ctx.shifts[ctx.shift_of(eid, day)]
        head = tk.Frame(inner, bg=C.SURFACE)
        head.pack(fill='x')
        tk.Label(head, text='TAUSCHASSISTENT', bg=C.SURFACE, fg=C.ACCENT_SOFT, font=F(8, 'bold')).pack(side='left')
        close = ttk.Button(head, text='✕', style='Icon.TButton', width=2, command=self.cancel_swap)
        close.pack(side='right')
        who = tk.Frame(inner, bg=C.SURFACE)
        who.pack(fill='x', pady=(6, 0))
        chip_widget(who, ctx.labels.get(eid, '?'), C.ACCENT, '#ffffff', C.GLOW, glow=True).pack(side='left')
        box = tk.Frame(who, bg=C.SURFACE)
        box.pack(side='left', padx=(8, 0), fill='x')
        tk.Label(box, text=emp.name, bg=C.SURFACE, fg=C.TEXT, font=F(12, 'bold'), anchor='w').pack(fill='x')
        tk.Label(box, text=emp.role or 'ohne Bereich', bg=C.SURFACE, fg=C.MUTED, font=F(9), anchor='w').pack(fill='x')
        label(inner, f'{shift.name} · {shift.time_label} · {WEEKDAYS[day.weekday()]} {day:%d.%m.%Y}', C.TEXT,
              F(9, 'bold'), pady=(8, 0))
        n_valid = len(self.valid_options)
        if self.pending:
            step = 'Tausch läuft – Abbrechen mit Esc oder erneutem Klick auf die Person.'
        elif n_valid:
            step = (f'Jetzt einen der {len(self.highlights) or n_valid} leuchtenden Namen im Kalender anklicken '
                    '– oder unten einen Vorschlag wählen.')
        else:
            step = 'Kein Vorschlag erfüllt alle geprüften Regeln.'
        label(inner, step, C.ACCENT_SOFT if n_valid else C.WARN, F(9, 'bold' if n_valid else 'normal'), pady=(4, 0))
        if not n_valid:
            wc = card(inner, blend(C.WARN, C.SURFACE, .88), blend(C.WARN, C.SURFACE, .5), pady=10)
            wc.pack_configure(pady=(10, 0))
            warn = wc.inner
            label(warn, core.explain_no_options(ctx, self.swap_options), C.TEXT, wrap=300)

        if self.preview:
            self._render_preview(inner, self.preview)

        if n_valid:
            section(inner, f'Vorschläge ({n_valid})')
            for opt in self.valid_options[:12]:
                self._option_card(inner, opt)
            if n_valid > 12:
                label(inner, f'+ {n_valid - 12} weitere – im Kalender leuchten alle Partner', C.MUTED)

        invalid = [o for o in self.swap_options if not o.valid]
        if invalid:
            best = {}
            for opt in invalid:
                best.setdefault(opt.b_id, opt)
            section(inner, 'Nicht möglich')
            toggle = ttk.Button(inner, text=('▾ ' if self.show_invalid else '▸ ') +
                                f'Begründungen anzeigen ({len(best)} Personen)',
                                command=self._toggle_invalid)
            toggle.pack(anchor='w')
            if self.show_invalid:
                for b_id, opt in sorted(best.items(), key=lambda kv: ctx.employees[kv[0]].name.lower()):
                    rc = card(inner, C.SURFACE2, padx=10, pady=6, radius=10)
                    rc.pack_configure(pady=2)
                    row = rc.inner
                    tk.Label(row, text=ctx.employees[b_id].name, bg=C.SURFACE2, fg=C.TEXT, font=F(9, 'bold'),
                             anchor='w').pack(fill='x')
                    tk.Label(row, text=KIND_LABELS[opt.kind] + ' (bester Versuch)', bg=C.SURFACE2, fg=C.DIM,
                             font=F(8), anchor='w').pack(fill='x')
                    for check in opt.blockers[:2]:
                        tk.Label(row, text='✕  ' + check.message, bg=C.SURFACE2, fg=blend(C.DANGER, C.TEXT, .25),
                                 font=F(8), anchor='w', justify='left', wraplength=290).pack(fill='x')

        foot = tk.Frame(self.side_foot, bg=C.SURFACE2, padx=16, pady=12)
        foot.pack(fill='x')
        tk.Frame(self.side_foot, bg=C.BORDER, height=1).place(relx=0, rely=0, relwidth=1)
        tk.Label(foot, text='Vorschlag aus der Liste: gespeichert wird erst nach „Tausch bestätigen“.',
                 bg=C.SURFACE2, fg=C.DIM, font=F(8), anchor='w', justify='left', wraplength=300).pack(
            fill='x', pady=(0, 8))
        btns = tk.Frame(foot, bg=C.SURFACE2)
        btns.pack(fill='x')
        confirm = ttk.Button(btns, text='✓  Tausch bestätigen', style='Accent.TButton', command=self.confirm_swap)
        confirm.pack(side='right')
        ttk.Button(btns, text='Abbrechen', command=self.cancel_swap).pack(side='right', padx=(0, 8))
        if not (self.preview and self.preview.valid):
            confirm.state(['disabled'])

    def _toggle_invalid(self):
        self._toggle('show_invalid')

    def _toggle(self, attr):
        setattr(self, attr, not getattr(self, attr))
        self.render_side(keep_scroll=True)

    def _option_card(self, parent, opt):
        ctx = self.ctx
        active = opt is self.preview
        rc = RoundedCard(parent, fill=C.CARD, border=C.ACCENT if active else C.BORDER_SOFT, radius=12,
                         padx=12, pady=8, border_width=2 if active else 1)
        rc.pack(fill='x', pady=3)
        box = rc.inner
        top = tk.Frame(box, bg=C.CARD)
        top.pack(fill='x')
        tk.Label(top, text=KIND_LABELS[opt.kind].upper(), bg=C.CARD, fg=C.ACCENT_SOFT, font=F(7, 'bold')).pack(
            side='left')
        if opt.warnings:
            tk.Label(top, text=f'! {len(opt.warnings)} Hinweis', bg=C.CARD, fg=C.WARN, font=F(8, 'bold')).pack(
                side='right')
        tk.Label(box, text=ctx.employees[opt.b_id].name, bg=C.CARD, fg=C.TEXT, font=F(10, 'bold'),
                 anchor='w').pack(fill='x')
        for eid in (opt.a_id, opt.b_id):
            parts = [f'{fmt_day(d)} {old.split(" ")[0]} → {new.split(" ")[0]}'
                     for d, old, new in opt.before_after(ctx, eid) if old != new]
            tk.Label(box, text=f'{ctx.labels.get(eid)}:  ' + ' · '.join(parts), bg=C.CARD, fg=C.MUTED, font=F(8),
                     anchor='w', justify='left', wraplength=290).pack(fill='x')
        clickable_card(rc, lambda o=opt: self.preview_option(o))

    def _render_preview(self, inner, opt):
        ctx = self.ctx
        section(inner, 'Vorschau · Vorher / Nachher')
        label(inner, opt.headline(ctx), C.TEXT, F(9, 'bold'), pady=(0, 6))
        for eid in (opt.a_id, opt.b_id):
            pc_ = card(inner, C.SURFACE2, padx=10, pady=8, radius=12)
            pc_.pack_configure(pady=3)
            box = pc_.inner
            top = tk.Frame(box, bg=C.SURFACE2)
            top.pack(fill='x')
            tk.Label(top, text=ctx.employees[eid].name, bg=C.SURFACE2, fg=C.TEXT, font=F(9, 'bold')).pack(side='left')
            before = ctx.month_hours(eid)
            after = ctx.month_hours(eid, opt.overlay)
            if before != after:
                tk.Label(top, text=f'{fmt_hours(before)} → {fmt_hours(after)} h/Monat', bg=C.SURFACE2, fg=C.DIM,
                         font=F(8)).pack(side='right')
            grid = tk.Frame(box, bg=C.SURFACE2)
            grid.pack(fill='x', pady=(4, 0))
            for col, text in enumerate(('Datum', 'Vorher', '', 'Nachher')):
                tk.Label(grid, text=text, bg=C.SURFACE2, fg=C.DIM, font=F(7, 'bold'), anchor='w').grid(
                    row=0, column=col, sticky='w', padx=(0, 6))
            for r, (d, old, new) in enumerate(opt.before_after(ctx, eid), start=1):
                changed = old != new
                tk.Label(grid, text=f'{WEEKDAYS[d.weekday()]} {fmt_day(d)}', bg=C.SURFACE2, fg=C.MUTED,
                         font=F(8)).grid(row=r, column=0, sticky='w', padx=(0, 6))
                tk.Label(grid, text=old, bg=C.SURFACE2, fg=C.MUTED, font=F(8), anchor='w').grid(
                    row=r, column=1, sticky='w')
                tk.Label(grid, text='→', bg=C.SURFACE2, fg=C.DIM, font=F(8)).grid(row=r, column=2, padx=4)
                tk.Label(grid, text=new, bg=C.SURFACE2, fg=C.ACCENT_SOFT if changed else C.MUTED,
                         font=F(8, 'bold' if changed else 'normal'), anchor='w').grid(row=r, column=3, sticky='w')
        section(inner, 'Prüfung')
        verdict = ('✓  Tausch nach den geprüften Regeln möglich.' if opt.valid
                   else '✕  Tausch nicht möglich – siehe rot markierte Regeln.')
        label(inner, verdict, C.OK if opt.valid else C.DANGER, F(9, 'bold'), pady=(0, 4))
        groups = [(ctx.employees[opt.a_id].name, opt.checks_a), (ctx.employees[opt.b_id].name, opt.checks_b),
                  ('Besetzung', opt.checks_cov)]
        problems = [(t, c) for t, checks in groups for c in checks if c.status in ('block', 'warn')]
        seen = set()
        for title, check in problems:
            if check.message in seen:
                continue
            seen.add(check.message)
            label(inner, f'{STATUS_ICONS[check.status]}  {check.message}', STATUS_COLORS[check.status], F(8), pady=1)
        ttk.Button(inner, text=('▾ ' if self.show_details else '▸ ') + 'Prüfdetails',
                   command=lambda: self._toggle('show_details')).pack(anchor='w', pady=(6, 0))
        if not self.show_details:
            return
        for title, checks in groups:
            if not checks:
                continue
            label(inner, title, C.TEXT, F(8, 'bold'), pady=(6, 1))
            seen = set()
            ok_rules = []
            for check in checks:
                if (check.rule, check.message) in seen:
                    continue
                seen.add((check.rule, check.message))
                if check.status == 'ok' and title != 'Besetzung':
                    if check.rule not in ok_rules:
                        ok_rules.append(check.rule)
                    continue
                color = STATUS_COLORS.get(check.status, C.MUTED)
                text = f'{STATUS_ICONS.get(check.status, "•")}  {check.rule}: {check.message}'
                label(inner, text, color if check.status != 'ok' else blend(C.OK, C.MUTED, .45), F(8), pady=0)
            bad_rules = {c.rule for c in checks if c.status in ('block', 'warn')}
            ok_rules = [r for r in ok_rules if r not in bad_rules]
            if ok_rules:
                label(inner, '✓  eingehalten: ' + ', '.join(ok_rules), blend(C.OK, C.MUTED, .35), F(8), pady=0)
        label(inner, 'Nicht vollständig geprüft:', C.DIM, F(8, 'bold'), pady=(8, 1))
        for text in UNCHECKED_RULES:
            label(inner, '·  ' + text, C.DIM, F(8), pady=0)

    # -- Aktionen ----------------------------------------------------------
    def open_day(self, day):
        if self.busy:
            return
        self.tip.hide()
        if self.pending:
            self.cancel_pending()
        DayDialog(self, day)

    def generate(self):
        if self.busy:
            return
        ctx = self.ctx
        if not ctx.active:
            info(self, 'Monat planen', 'Es gibt noch keine aktiven Mitarbeitenden.', 'Lege zuerst Mitarbeitende an.')
            return
        start, end = month_bounds(self.month)
        existing = self.store.db.execute('SELECT COUNT(*) FROM assignments WHERE day BETWEEN ? AND ? '
                                         'AND shift_id IS NOT NULL', (start.isoformat(), end.isoformat())).fetchone()[0]
        month_name = f'{MONTHS[self.month.month - 1]} {self.month.year}'
        mode = choose(self, 'Monat planen', f'{month_name} automatisch planen',
                      'Der Generator berücksichtigt Urlaub, einzelne Abwesenheitstage, Schichteignung, maximale '
                      'Arbeitstage, Folgetage, Ruhezeit (Hausregel) und Wünsche. Samstage, Sonntage und Feiertage '
                      'werden ganz normal besetzt; Wochenend-, Feiertags- und Nachtdienste werden fair nach '
                      'Soll-Stunden verteilt.' + (
                          '\n\nZusätzlich werden Dienste über der Mindestbesetzung eingeplant (bis zur '
                          'Maximalbesetzung je Schicht), damit alle möglichst ihre Soll-Stunden erreichen.'
                          if self.fill_hours_enabled() else '') +
                      f'\n\nAktuell sind {existing} Dienste in diesem Monat eingetragen.',
                      [('Abbrechen', None, 'TButton'), ('Nur offene Plätze füllen', 'fill', 'TButton'),
                       ('Komplett neu planen', 'replace', 'Accent.TButton')])
        if not mode:
            return
        if mode == 'replace' and existing and not ask(
                self, 'Monat neu planen', f'{existing} bestehende Dienste ersetzen?',
                f'Alle Einträge im {month_name} werden durch einen neuen Entwurf ersetzt. Urlaube bleiben erhalten. '
                'Der ganze Planlauf lässt sich danach mit einem Klick auf ↶ (Rückgängig) zurücknehmen.',
                ok='Ja, neu planen', tone='warn', ok_style='Danger.TButton'):
            return
        self._run_generation(self.month, mode == 'replace')

    def _run_generation(self, month, clear):
        """Rechnet den Entwurf in einem Hintergrund-Thread (eigene Datenbankverbindung), damit das Fenster
        nicht einfriert. Gespeichert wird danach im Hauptthread als ein Rückgängig-Schritt."""
        self.cancel_swap(render=False)
        self.busy = True
        self.config(cursor='watch')
        self.set_status(f'Plane {MONTHS[month.month - 1]} {month.year} … mehrere Entwürfe werden verglichen.',
                        tone='warn')
        self._show_busy(f'Plane {MONTHS[month.month - 1]} {month.year} …',
                        'Mehrere Entwürfe werden berechnet und der fairste gespeichert.')
        box = {}
        fill = self.fill_hours_enabled()

        def work():
            reader = core.ReadStore(self.store.path)
            try:
                box['plan'] = core.compute_month_plan(reader, month, clear, fill_hours=fill)
            except Exception as exc:  # Fehler im Hauptthread anzeigen
                box['error'] = exc
            finally:
                reader.close()

        thread = threading.Thread(target=work, daemon=True)
        thread.start()

        def poll():
            if thread.is_alive():
                self.after(80, poll)
                return
            self.busy = False
            self.config(cursor='')
            self._hide_busy()
            if 'error' in box:
                info(self, 'Planung fehlgeschlagen', 'Der Entwurf konnte nicht berechnet werden.', str(box['error']),
                     tone='danger')
                return
            result = core.save_month_plan(self.store, month, box.get('plan'), clear)
            self._after_generation(month, result)
        self.after(80, poll)

    def _show_busy(self, title, text):
        self.busy_card = RoundedCard(self.cal, fill=blend(C.ACCENT, C.SURFACE, .8), border=C.ACCENT_SOFT, radius=16,
                                     padx=24, pady=16, outer_bg=C.BG)
        tk.Label(self.busy_card.inner, text=title, bg=self.busy_card.fill, fg='#ffffff', font=F(12, 'bold')).pack()
        tk.Label(self.busy_card.inner, text=text, bg=self.busy_card.fill, fg=C.GLOW, font=F(9)).pack(pady=(4, 8))
        bar = tk.Canvas(self.busy_card.inner, width=260, height=6, bg=self.busy_card.fill, highlightthickness=0)
        bar.pack()
        self.busy_card.place(relx=.5, rely=.4, anchor='center')
        tk.Misc.tkraise(self.busy_card)
        state = {'x': 0}

        def spin():
            if not self.busy or not bar.winfo_exists():
                return
            bar.delete('all')
            round_rect(bar, 0, 0, 260, 6, 3, fill=blend(C.ACCENT, C.SURFACE, .55), outline='')
            x = state['x'] = (state['x'] + 12) % 320
            round_rect(bar, max(0, x - 80), 0, min(260, x), 6, 3, fill=C.GLOW, outline='')
            self.after(40, spin)
        spin()

    def _hide_busy(self):
        card = getattr(self, 'busy_card', None)
        if card is not None:
            card.destroy()
            self.busy_card = None

    def _after_generation(self, month, result):
        self.month = month
        self.last_generation = (self.month, result)
        self.cancel_swap(render=False)
        self.refresh_plan()
        lines = [('ok', f'{result.filled} Dienste eingeplant (inkl. Wochenenden und Feiertage), davon '
                        f'{result.extra} Zusatzdienste zur Erfüllung der Soll-Stunden.')]
        stats = core.fairness_report(self.ctx)
        missed = [r for r in stats if r.status != 'ok']
        lines.append(('head', f'Soll-Stunden: {len(stats) - len(missed)} von {len(stats)} erfüllt '
                              f'(± {fmt_hours(self.ctx.hours_tolerance)} h)'))
        for r in missed:
            lines.append(('warn', f'{r.employee.name}: {fmt_hours(r.actual)} von {fmt_hours(r.target)} h '
                                  f'({r.diff:+.1f} h) – {r.status_text}'))
        if result.open_slots:
            lines.append(('head', f'Nicht besetzbare Schichtplätze: {result.missing}'))
            for slot in result.open_slots[:40]:
                lines.append(('block', f'{WEEKDAYS[slot.day.weekday()]} {fmt_day(slot.day)} · {slot.shift_name} · '
                                       f'fehlt {slot.missing}' + (f' – blockiert durch: {slot.reason}'
                                                                  if slot.reason else '')))
        lines.append(('head', 'Nicht vollständig geprüft'))
        lines += [('muted', text) for text in UNCHECKED_RULES]
        heading = (f'Entwurf erstellt – {result.missing} Schichtplätze offen' if result.missing
                   else 'Entwurf erstellt – alle Mindestbesetzungen erfüllt')
        info(self, 'Plan erstellt', heading, 'Der Plan ist ein Vorschlag. Bitte Besetzung, Qualifikationen sowie '
             'Arbeitszeit-, Tarif- und Hausregeln vor der Freigabe prüfen.', lines,
             tone='warn' if result.missing else 'ok')
        self.set_status(heading, tone='warn' if result.missing else 'ok')

    def fill_hours_enabled(self):
        return self.store.setting('fill_hours', '1') != '0'

    def export_csv(self):
        path = filedialog.asksaveasfilename(parent=self, defaultextension='.csv',
                                            initialfile=f'Dienstplan_{self.month:%Y_%m}.csv',
                                            filetypes=[('CSV-Datei', '*.csv')])
        if not path:
            return
        count = core.export_csv(self.store, self.month, path)
        self.set_status(f'{count} Dienste exportiert: {path}')

    # =====================================================================
    # Mitarbeitende
    # =====================================================================
    def build_staff(self, p):
        self.page_header(p, 'STAMMDATEN', 'Mitarbeitende',
                         'Bereich/Qualifikation, Vertragsstunden, Schichteignung und Wünsche. '
                         'Demo-Personen sind fiktive Testdaten.')
        bar = tk.Frame(p, bg=C.BG)
        bar.pack(fill='x', padx=26, pady=(0, 10))
        ttk.Button(bar, text='＋  Mitarbeiter hinzufügen', style='Accent.TButton',
                   command=lambda: EmployeeForm(self)).pack(side='left')
        ttk.Button(bar, text='Bearbeiten', command=self.edit_employee).pack(side='left', padx=8)
        ttk.Button(bar, text='Aktiv / Inaktiv', command=self.toggle_employee).pack(side='left')
        ttk.Button(bar, text='Löschen', style='Danger.TButton', command=self.delete_employee).pack(side='left', padx=8)
        self.staff_tree = self._tree(p, [('name', 'Name', 220), ('role', 'Bereich / Qualifikation', 190),
                                         ('hours', 'Std./Woche', 90), ('days', 'Max. Tage', 80),
                                         ('shifts', 'Einsetzbar für', 220), ('prefs', 'Wünsche', 230),
                                         ('status', 'Status', 150)])
        self.staff_tree.bind('<Double-1>', lambda _e: self.edit_employee())

    def _tree(self, parent, columns):
        rc = RoundedCard(parent, fill=C.SURFACE, border=C.BORDER, radius=14, padx=6, pady=8, fill_height=True)
        rc.pack(fill='both', expand=True, padx=26, pady=(0, 18))
        wrap = rc.inner
        tree = ttk.Treeview(wrap, columns=[c[0] for c in columns], show='headings', selectmode='browse')
        for key, title, width in columns:
            tree.heading(key, text=title, anchor='w')
            tree.column(key, width=width, anchor='w', stretch=True)
        bar = ttk.Scrollbar(wrap, orient='vertical', command=tree.yview)
        tree.configure(yscrollcommand=bar.set)
        tree.pack(side='left', fill='both', expand=True)
        bar.pack(side='right', fill='y')
        tree.tag_configure('odd', background=C.SURFACE)
        tree.tag_configure('even', background=blend(C.SURFACE, C.SURFACE2, .5))
        tree.tag_configure('inactive', foreground=C.DIM)
        tree.tag_configure('demo', foreground=C.ACCENT_SOFT)
        return tree

    def refresh_staff(self):
        tree = self.staff_tree
        tree.delete(*tree.get_children())
        shifts = {r['id']: r['name'] for r in self.store.all('SELECT id,name FROM shifts')}
        elig = defaultdict(list)
        for r in self.store.all('SELECT employee_id,shift_id FROM eligibility'):
            if r['shift_id'] in shifts:
                elig[r['employee_id']].append(shifts[r['shift_id']])
        for i, e in enumerate(self.store.all('SELECT * FROM employees ORDER BY active DESC, name')):
            status = 'Aktiv' if e['active'] else 'Inaktiv'
            if e['is_demo']:
                status += ' · Testdaten'
            tags = ['odd' if i % 2 else 'even']
            if not e['active']:
                tags.append('inactive')
            elif e['is_demo']:
                tags.append('demo')
            tree.insert('', 'end', iid=str(e['id']), tags=tags,
                        values=(e['name'], e['role'], fmt_hours(e['weekly_hours'] or 0), e['max_days'],
                                ', '.join(sorted(elig[e['id']])) or 'alle Schichten', e['preferences'], status))

    def edit_employee(self):
        sel = self.staff_tree.selection()
        if not sel:
            info(self, 'Mitarbeitende', 'Bitte zuerst eine Person auswählen.')
            return
        EmployeeForm(self, int(sel[0]))

    def toggle_employee(self):
        sel = self.staff_tree.selection()
        if not sel:
            return
        e = self.store.all('SELECT * FROM employees WHERE id=?', (int(sel[0]),))[0]
        if e['active']:
            future = self.store.db.execute('SELECT COUNT(*) FROM assignments WHERE employee_id=? AND day>=?',
                                           (e['id'], date.today().isoformat())).fetchone()[0]
            if not ask(self, 'Deaktivieren', f"{e['name']} deaktivieren?",
                       f'Plandaten bleiben erhalten. {future} zukünftige Dienste bleiben im Plan stehen und werden '
                       'als Regelhinweis markiert, bis sie umgeplant sind.', ok='Deaktivieren', tone='warn'):
                return
        self.store.run('UPDATE employees SET active=? WHERE id=?', (0 if e['active'] else 1, e['id']))
        self.refresh_staff()
        self.update_demo_badge()

    def delete_employee(self):
        sel = self.staff_tree.selection()
        if not sel:
            info(self, 'Mitarbeitende', 'Bitte zuerst eine Person auswählen.')
            return
        eid = int(sel[0])
        e = self.store.all('SELECT * FROM employees WHERE id=?', (eid,))[0]
        impact = core.employee_delete_impact(self.store, eid)
        lines = [('head', 'Wird endgültig entfernt'),
                 ('block', f"{impact['future']} zukünftige Dienste (ab heute) – diese Plätze werden danach offen"),
                 ('block', f"{impact['past']} vergangene Dienste – fehlen danach in Auswertung und CSV-Export"),
                 ('block', f"{impact['leave']} Urlaubseinträge sowie Schichteignungen und Wünsche"),
                 ('head', 'Sicherheit'),
                 ('ok', 'Vor dem Löschen wird automatisch eine Sicherung in data/backups angelegt.'),
                 ('info', 'Tipp: „Aktiv / Inaktiv“ blendet die Person aus, ohne Plandaten zu verlieren.')]
        tag = ' (Testdaten)' if e['is_demo'] else ''
        if not ask(self, 'Mitarbeiter löschen', f"{e['name']}{tag} endgültig löschen?",
                   'Das Löschen lässt sich in der App nicht rückgängig machen.', lines,
                   ok='Endgültig löschen', tone='danger', ok_style='Danger.TButton'):
            return
        if impact['assignments'] and not e['is_demo'] and not ask(
                self, 'Wirklich löschen?', f"Wirklich alle {impact['assignments']} Dienste von {e['name']} löschen?",
                'Letzte Rückfrage, weil echte Plandaten betroffen sind.', ok='Ja, löschen', tone='danger',
                ok_style='Danger.TButton'):
            return
        try:
            name, backup = core.delete_employee(self.store, eid)
        except ValueError as exc:
            info(self, 'Löschen nicht möglich', str(exc), tone='danger')
            return
        if self.selected and self.selected[0] == eid:
            self.cancel_swap(render=False)
        self.refresh_staff()
        self.update_demo_badge()
        self.set_status(f'„{name}“ gelöscht ({impact["future"]} zukünftige Dienste jetzt offen). '
                        f'Sicherung: {os.path.basename(backup)}', tone='warn')

    # =====================================================================
    # Urlaub
    # =====================================================================
    def build_leave(self, p):
        self.page_header(p, 'ABWESENHEITEN', 'Urlaubsplanung',
                         'Urlaub ist im Monatskalender sichtbar und wird vom automatischen Plan berücksichtigt.')
        rc = RoundedCard(p, fill=C.SURFACE, border=C.BORDER, radius=16, padx=18, pady=14)
        rc.pack(fill='x', padx=26, pady=(0, 12))
        card = rc.inner
        tk.Label(card, text='URLAUB EINTRAGEN', bg=C.SURFACE, fg=C.ACCENT_SOFT, font=F(8, 'bold')).grid(
            row=0, column=0, columnspan=6, sticky='w', pady=(0, 8))
        for col, text in enumerate(('Mitarbeiter', 'Von', 'Bis', 'Notiz')):
            tk.Label(card, text=text, bg=C.SURFACE, fg=C.MUTED, font=F(9)).grid(row=1, column=col, sticky='w',
                                                                               padx=(0, 12))
        self.leave_who = tk.StringVar()
        self.leave_box = ttk.Combobox(card, textvariable=self.leave_who, state='readonly', width=28)
        self.leave_box.grid(row=2, column=0, sticky='w', padx=(0, 12))
        self.leave_from = DateField(card, date.today(), on_change=self._leave_dates_changed)
        self.leave_from.grid(row=2, column=1, sticky='w', padx=(0, 12))
        self.leave_to = DateField(card, date.today(), on_change=self._leave_dates_changed)
        self.leave_to.grid(row=2, column=2, sticky='w', padx=(0, 12))
        self.leave_note = tk.StringVar()
        ttk.Entry(card, textvariable=self.leave_note, width=26).grid(row=2, column=3, sticky='w', padx=(0, 12))
        ttk.Button(card, text='＋  Urlaub eintragen', style='Accent.TButton', command=self.add_leave).grid(
            row=2, column=4, sticky='w')
        self.leave_days_label = tk.Label(card, text='', bg=C.SURFACE, fg=C.DIM, font=F(8))
        self.leave_days_label.grid(row=3, column=1, columnspan=3, sticky='w', pady=(6, 0))
        self._leave_dates_changed(None)
        bar = tk.Frame(p, bg=C.BG)
        bar.pack(fill='x', padx=26, pady=(0, 8))
        self.leave_month_only = tk.BooleanVar(value=False)
        ttk.Checkbutton(bar, text='Nur Urlaube im angezeigten Planmonat', variable=self.leave_month_only,
                        command=self.refresh_leave).pack(side='left')
        ttk.Button(bar, text='Ausgewählten Urlaub entfernen', style='Danger.TButton', command=self.remove_leave).pack(
            side='right')
        self.test_leave_remove = ttk.Button(bar, text='Testurlaube entfernen', command=self.remove_test_leaves)
        self.test_leave_remove.pack(side='right', padx=8)
        self.test_leave_add = ttk.Button(bar, text='＋  Testurlaube anlegen', command=self.add_test_leaves)
        self.test_leave_add.pack(side='right')
        attach_tooltip(self.test_leave_add, self.tip, 'Gestaffelte Testurlaube für Demo-Personen im angezeigten und '
                                                      'im folgenden Monat anlegen (nur Testdaten).')
        self.leave_tree = self._tree(p, [('name', 'Mitarbeiter', 240), ('start', 'Von', 150), ('end', 'Bis', 150),
                                         ('days', 'Kalendertage', 110), ('note', 'Notiz', 320)])

    def add_test_leaves(self):
        months = [self.month, add_months(self.month, 1)]
        plans = core.plan_test_leaves(self.store, months)
        if not plans:
            info(self, 'Testurlaube', 'Keine Testurlaube anzulegen.',
                 'Es gibt keine aktiven Demo-Personen ohne Urlaub in diesen Monaten. Testurlaube werden nur für '
                 'Demo-Personen (Testdaten) angelegt.')
            return
        lines, conflicts = [], 0
        for eid, name, begin, finish in plans:
            hits = core.assignments_in_range(self.store, eid, begin, finish)
            conflicts += len(hits)
            lines.append(('info', f'{name}: {begin:%d.%m.}–{finish:%d.%m.%Y}'
                                  + (f' · {len(hits)} Dienst(e) werden entfernt' if hits else '')))
        if not ask(self, 'Testurlaube anlegen', f'{len(plans)} Testurlaube anlegen?',
                   f'Nur für Demo-Personen, markiert als „{core.TEST_LEAVE_NOTE}“. '
                   + (f'{conflicts} geplante Dienste in diesen Zeiträumen werden entfernt und erscheinen danach als '
                      'offene Plätze.' if conflicts else 'Es werden keine geplanten Dienste berührt.'),
                   lines, ok='Testurlaube anlegen', tone='warn' if conflicts else 'info'):
            return
        added, removed = core.add_test_leaves(self.store, months)
        self.refresh_leave()
        self.set_status(f'{len(added)} Testurlaube angelegt' + (f', {removed} Dienste entfernt.' if removed else '.'),
                        tone='warn' if removed else 'ok')

    def remove_test_leaves(self):
        n = self.store.db.execute('SELECT COUNT(*) FROM leave_periods WHERE note=?',
                                  (core.TEST_LEAVE_NOTE,)).fetchone()[0]
        if not n:
            info(self, 'Testurlaube', 'Es sind keine Testurlaube eingetragen.')
            return
        if ask(self, 'Testurlaube entfernen', f'{n} Testurlaube entfernen?',
               'Nur Einträge mit der Notiz „Testurlaub (Testdaten)“. Bereits entfernte Dienste werden nicht '
               'automatisch wieder eingeplant – dafür „Monat planen → Nur offene Plätze füllen“ nutzen.',
               ok='Entfernen', tone='warn', ok_style='Danger.TButton'):
            removed = core.remove_test_leaves(self.store)
            self.refresh_leave()
            self.set_status(f'{removed} Testurlaube entfernt.')

    def _leave_dates_changed(self, _value):
        first, last = self.leave_from.get(), self.leave_to.get()
        if last < first:
            self.leave_to.set(first)
            return
        self.leave_days_label.configure(text=f'{(last - first).days + 1} Kalendertage · Wochenenden und Feiertage '
                                             'zählen mit (Krankenhausbetrieb)')

    def refresh_leave(self):
        employees = self.store.all('SELECT id,name FROM employees WHERE active=1 ORDER BY name')
        self.leave_names = {f"{e['name']}  (ID {e['id']})": e['id'] for e in employees}
        self.leave_box.configure(values=list(self.leave_names))
        if self.leave_who.get() not in self.leave_names:
            self.leave_who.set(next(iter(self.leave_names), ''))
        tree = self.leave_tree
        tree.delete(*tree.get_children())
        sql = 'SELECT l.*,e.name FROM leave_periods l JOIN employees e ON e.id=l.employee_id'
        args = ()
        if self.leave_month_only.get():
            first, last = month_bounds(self.month)
            sql += ' WHERE l.start_date<=? AND l.end_date>=?'
            args = (last.isoformat(), first.isoformat())
        for i, lp in enumerate(self.store.all(sql + ' ORDER BY l.start_date,e.name', args)):
            first, last = date.fromisoformat(lp['start_date']), date.fromisoformat(lp['end_date'])
            tree.insert('', 'end', iid=str(lp['id']), tags=('odd' if i % 2 else 'even',),
                        values=(lp['name'], f'{WEEKDAYS[first.weekday()]}, {first:%d.%m.%Y}',
                                f'{WEEKDAYS[last.weekday()]}, {last:%d.%m.%Y}', (last - first).days + 1, lp['note']))

    def add_leave(self):
        eid = self.leave_names.get(self.leave_who.get())
        if not eid:
            info(self, 'Urlaub', 'Bitte eine Person auswählen.', 'Lege bei Bedarf zuerst Mitarbeitende an.')
            return
        first, last = self.leave_from.get(), self.leave_to.get()
        name = self.leave_who.get().split('  (ID')[0]
        overlaps = core.overlapping_leave(self.store, eid, first, last)
        if overlaps and not ask(self, 'Überschneidung', 'Urlaub überschneidet sich mit einem bestehenden Eintrag.',
                                lines=[('warn', f"{date.fromisoformat(o['start_date']):%d.%m.%Y} – "
                                                f"{date.fromisoformat(o['end_date']):%d.%m.%Y} {o['note'] or ''}")
                                       for o in overlaps], ok='Trotzdem eintragen', tone='warn'):
            return
        conflicts = core.assignments_in_range(self.store, eid, first, last)
        if conflicts:
            ok = ask(self, 'Dienste im Urlaubszeitraum',
                     f'{name} ist in diesem Zeitraum für {len(conflicts)} Dienst(e) eingeplant.',
                     'Beim Speichern werden diese Dienste aus dem Plan entfernt; die Plätze erscheinen danach als offen.',
                     [('warn', f"{WEEKDAYS[date.fromisoformat(r['day']).weekday()]} "
                               f"{date.fromisoformat(r['day']):%d.%m.%Y} · {r['shift'] or 'ohne Schicht'}")
                      for r in conflicts], ok='Urlaub speichern & Dienste entfernen', tone='warn')
            if not ok:
                return
        removed = core.add_leave(self.store, eid, first, last, self.leave_note.get().strip())
        self.leave_note.set('')
        self.refresh_leave()
        self.set_status(f'Urlaub für {name} gespeichert ({first:%d.%m.}–{last:%d.%m.%Y})' +
                        (f', {removed} Dienst(e) entfernt.' if removed else '.'), tone='warn' if removed else 'ok')

    def remove_leave(self):
        sel = self.leave_tree.selection()
        if not sel:
            info(self, 'Urlaub', 'Bitte zuerst einen Urlaubszeitraum auswählen.')
            return
        values = self.leave_tree.item(sel[0], 'values')
        if ask(self, 'Urlaub entfernen', 'Urlaubszeitraum entfernen?', f'{values[0]}: {values[1]} – {values[2]}\n\n'
               'Bereits entfernte Dienste werden nicht automatisch wieder eingeplant.', ok='Entfernen',
               tone='warn', ok_style='Danger.TButton'):
            core.delete_leave(self.store, int(sel[0]))
            self.refresh_leave()
            self.set_status('Urlaubszeitraum entfernt.')

    # =====================================================================
    # Stunden & Fairness
    # =====================================================================
    def build_hours(self, p):
        self.page_header(p, 'AUSWERTUNG', 'Stunden & Fairness',
                         'Soll/Ist-Stunden je Person sowie die Verteilung von Wochenend-, Feiertags- und Nachtdiensten.')
        nav = tk.Frame(p, bg=C.BG)
        nav.pack(fill='x', padx=26, pady=(0, 10))
        ttk.Button(nav, text='‹', style='Icon.TButton', width=2,
                   command=lambda: self._hours_month(-1)).pack(side='left')
        self.hours_label = tk.Label(nav, text='', bg=C.BG, fg=C.TEXT, font=F(15, 'bold'))
        self.hours_label.pack(side='left', padx=12)
        ttk.Button(nav, text='›', style='Icon.TButton', width=2,
                   command=lambda: self._hours_month(1)).pack(side='left')
        self.hours_tiles = tk.Frame(p, bg=C.BG)
        self.hours_tiles.pack(fill='x', padx=26, pady=(0, 12))
        for col in range(3):
            self.hours_tiles.columnconfigure(col, weight=1, uniform='h')
        self.hours_tree = self._tree(p, [('name', 'Name', 190), ('role', 'Bereich', 140), ('target', 'Soll (h)', 80),
                                         ('actual', 'Ist (h)', 80), ('diff', 'Differenz', 90), ('status', 'Status', 100),
                                         ('shifts', 'Dienste', 70), ('special', 'WE/Feiertag (Ist / fair)', 170),
                                         ('nights', 'Nacht (Ist / fair)', 140), ('leave', 'Urlaubstage', 90)])
        for tag, color in (('ok', C.OK), ('under', C.WARN), ('over', C.ACCENT_SOFT)):
            self.hours_tree.tag_configure(tag, foreground=color)
        tk.Label(p, text='Soll = Wochenstunden ÷ 7 je Kalendertag; Urlaubstage werden angerechnet. „fair“ = Anteil '
                         'an allen Wochenend-/Feiertags- bzw. Nachtdiensten proportional zu den Soll-Stunden (Nacht nur '
                         'unter Personen, die für Nachtdienste eingetragen sind). Wünsche wie „Wochenende vermeiden“ '
                         'oder „Nachtdienst bevorzugt“ führen bewusst zu Abweichungen. Annahmen der App – keine '
                         'tarifliche Zeiterfassung.', bg=C.BG, fg=C.DIM, font=F(8), justify='left', anchor='w',
                 wraplength=1000).pack(fill='x', padx=26, pady=(0, 14))

    def _hours_month(self, delta):
        self.month = add_months(self.month, delta)
        if self.view_mode == 'week':
            self.week_start = self.month - timedelta(days=self.month.weekday())
        self.refresh_hours()

    def refresh_hours(self):
        ctx = PlanContext.for_month(self.store, self.month)
        self.hours_label.configure(text=f'{MONTHS[self.month.month - 1]} {self.month.year}')
        rows = core.fairness_report(ctx)
        for child in self.hours_tiles.winfo_children():
            child.destroy()
        ok = sum(1 for r in rows if r.status == 'ok')
        special_dev = max((abs(r.special - r.special_fair) for r in rows if not r.employee.avoids_weekend), default=0)
        night_dev = max((abs(r.nights - r.nights_fair) for r in rows if r.nights_fair), default=0)
        tiles = [(f'{ok}/{len(rows)}', f'erfüllen ihr Soll (± {fmt_hours(ctx.hours_tolerance)} h)',
                  C.OK if ok == len(rows) else C.WARN),
                 (f'± {fmt_hours(special_dev)}', 'größte Abweichung Wochenend-/Feiertagsdienste vom fairen Anteil',
                  C.OK if special_dev <= 2 else C.WARN),
                 (f'± {fmt_hours(night_dev)}', 'größte Abweichung Nachtdienste vom fairen Anteil',
                  C.OK if night_dev <= 2 else C.WARN)]
        for col, (value, text, color) in enumerate(tiles):
            rc = RoundedCard(self.hours_tiles, fill=C.SURFACE, border=C.BORDER, radius=14, padx=16, pady=12)
            rc.grid(row=0, column=col, sticky='nsew', padx=(0 if col == 0 else 10, 0))
            tk.Label(rc.inner, text=value, bg=C.SURFACE, fg=color, font=F(20, 'bold'), anchor='w').pack(fill='x')
            tk.Label(rc.inner, text=text, bg=C.SURFACE, fg=C.MUTED, font=F(9), anchor='w', justify='left',
                     wraplength=280).pack(fill='x')
        tree = self.hours_tree
        tree.delete(*tree.get_children())
        for r in rows:
            tree.insert('', 'end', tags=(r.status,), values=(
                r.employee.name, r.employee.role, fmt_hours(r.target), fmt_hours(r.actual),
                f'{r.diff:+.1f}'.replace('.', ','), r.status_text, r.shifts,
                f'{r.special} / {fmt_hours(r.special_fair)}', f'{r.nights} / {fmt_hours(r.nights_fair)}', r.leave_days))

    # =====================================================================
    # Schichten & Regeln
    # =====================================================================
    def build_shifts(self, p):
        self.page_header(p, 'KONFIGURATION', 'Schichtmodelle & Hausregeln',
                         'Uhrzeiten, Stunden, Farbe und tägliche Mindestbesetzung je Schicht. '
                         'Eigene Schichtmodelle erscheinen automatisch im Kalender.')
        rc = RoundedCard(p, fill=C.SURFACE, border=C.BORDER, radius=16, padx=18, pady=14)
        rc.pack(fill='x', padx=26, pady=(0, 12))
        rules = rc.inner
        tk.Label(rules, text='HAUSREGELN FÜR PRÜFUNG, TAUSCH & GENERATOR', bg=C.SURFACE, fg=C.ACCENT_SOFT,
                 font=F(8, 'bold')).grid(row=0, column=0, columnspan=6, sticky='w', pady=(0, 8))
        self.rest_var, self.consec_var = tk.StringVar(), tk.StringVar()
        self.delay_var, self.tol_var = tk.StringVar(), tk.StringVar()
        self.fill_var = tk.BooleanVar(value=True)
        fields = [('Mindestruhezeit zwischen Schichten (h)', self.rest_var, 0, 24, 0.5),
                  ('Max. Arbeitstage am Stück', self.consec_var, 0, 14, 1),
                  ('Bedenkzeit Klick-Tausch (s)', self.delay_var, 0, 30, 1),
                  ('Toleranz Soll-Stunden (± h)', self.tol_var, 0, 40, 0.5)]
        for i, (text, var, lo, hi, step) in enumerate(fields):
            r, c = divmod(i, 2)
            tk.Label(rules, text=text, bg=C.SURFACE, fg=C.TEXT, font=F(9)).grid(row=1 + r, column=c * 2, sticky='w',
                                                                                pady=4)
            ttk.Spinbox(rules, from_=lo, to=hi, increment=step, textvariable=var, width=6).grid(
                row=1 + r, column=c * 2 + 1, sticky='w', padx=(8, 28), pady=4)
        ttk.Checkbutton(rules, text='Generator plant Zusatzdienste, damit alle ihre Soll-Stunden erreichen',
                        variable=self.fill_var, style='Surface.TCheckbutton').grid(row=3, column=0, columnspan=4,
                                                                                   sticky='w', pady=(6, 0))
        ttk.Button(rules, text='Regeln speichern', style='Accent.TButton', command=self.save_rules).grid(
            row=1, column=4, rowspan=2, sticky='e', padx=(12, 0))
        rules.columnconfigure(4, weight=1)
        tk.Label(rules, text='Diese Werte sind App-Einstellungen, keine Rechtsauskunft. Bitte mit Arbeitszeitgesetz, '
                             'Tarifvertrag und Dienstvereinbarung abgleichen. 0 schaltet Ruhezeit/Folgetage ab; '
                             'Bedenkzeit 0 = Klick-Tausch sofort speichern (Rückgängig bleibt möglich). Soll-Stunden = '
                             'Wochenstunden ÷ 7 je Kalendertag, Urlaubstage werden angerechnet (Annahme der App).',
                 bg=C.SURFACE, fg=C.DIM, font=F(8), justify='left', wraplength=900).grid(
            row=4, column=0, columnspan=5, sticky='w', pady=(8, 0))
        bar = tk.Frame(p, bg=C.BG)
        bar.pack(fill='x', padx=26, pady=(0, 10))
        ttk.Button(bar, text='＋  Schicht hinzufügen', style='Accent.TButton', command=lambda: ShiftForm(self)).pack(
            side='left')
        ttk.Button(bar, text='Bearbeiten', command=self.edit_shift).pack(side='left', padx=8)
        ttk.Button(bar, text='Löschen', style='Danger.TButton', command=self.delete_shift).pack(side='left')
        self.shift_tree = self._tree(p, [('name', 'Schicht', 220), ('time', 'Arbeitszeit', 160),
                                         ('hours', 'Anrechenbare Stunden', 160), ('coverage', 'Mindestbesetzung', 150),
                                         ('max', 'Maximalbesetzung', 150), ('color', 'Farbe', 110)])
        self.shift_tree.configure(height=6)
        self.shift_tree.bind('<Double-1>', lambda _e: self.edit_shift())

        rule_card = RoundedCard(p, fill=C.SURFACE, border=C.BORDER, radius=14, padx=16, pady=12)
        rule_card.pack(fill='x', padx=26, pady=(0, 18))
        tk.Label(rule_card.inner, text='SCHICHTFOLGEN & SERIEN', bg=C.SURFACE, fg=C.ACCENT_SOFT,
                 font=F(8, 'bold')).pack(anchor='w')
        tk.Label(rule_card.inner, text='Zum Beispiel: Nach Nachtdienst kein Frühdienst am Folgetag; nach 4 Spätdiensten '
                 '2 volle Tage Pause. Diese Regeln gelten auch beim Tauschen und manuellen Ändern.',
                 bg=C.SURFACE, fg=C.MUTED, font=F(9), justify='left', wraplength=900).pack(anchor='w', pady=(4, 8))
        rule_buttons = tk.Frame(rule_card.inner, bg=C.SURFACE)
        rule_buttons.pack(fill='x', pady=(0, 6))
        ttk.Button(rule_buttons, text='＋ Schichtfolge-Regel', command=lambda: TransitionRuleForm(self)).pack(side='left')
        ttk.Button(rule_buttons, text='＋ Schichtserien-Regel', command=lambda: StreakRuleForm(self)).pack(
            side='left', padx=8)
        self.transition_rows = tk.Frame(rule_card.inner, bg=C.SURFACE)
        self.transition_rows.pack(fill='x', anchor='w')
        self.streak_rows = tk.Frame(rule_card.inner, bg=C.SURFACE)
        self.streak_rows.pack(fill='x', anchor='w', pady=(4, 0))

    def refresh_shifts(self):
        self.rest_var.set(self.store.setting('min_rest_hours'))
        self.consec_var.set(self.store.setting('max_consecutive_days'))
        self.delay_var.set(self.store.setting('swap_delay_seconds'))
        self.tol_var.set(self.store.setting('hours_tolerance'))
        self.fill_var.set(self.fill_hours_enabled())
        tree = self.shift_tree
        tree.delete(*tree.get_children())
        for s in self.store.all('SELECT * FROM shifts ORDER BY start,name'):
            tag = f"color{s['id']}"
            tree.tag_configure(tag, foreground=safe_color(s['color'], C.TEXT))
            tree.insert('', 'end', iid=str(s['id']), tags=(tag,),
                        values=(f"●  {s['name']}", f"{s['start']}–{s['end']}", fmt_hours(s['hours']), s['coverage'],
                                s['max_staff'] or 'unbegrenzt', s['color']))
        self._refresh_configured_rules()

    def _refresh_configured_rules(self):
        for frame in (self.transition_rows, self.streak_rows):
            for child in frame.winfo_children():
                child.destroy()
        transitions = self.store.all('''SELECT r.id,a.name AS after_name,b.name AS blocked_name,r.days_after
            FROM shift_transition_rules r JOIN shifts a ON a.id=r.after_shift_id
            JOIN shifts b ON b.id=r.blocked_shift_id ORDER BY a.start,b.start''')
        streaks = self.store.all('''SELECT r.shift_id,s.name,r.max_consecutive,r.rest_days
            FROM shift_streak_rules r JOIN shifts s ON s.id=r.shift_id ORDER BY s.start''')
        if not transitions and not streaks:
            tk.Label(self.transition_rows, text='Noch keine Schichtregeln hinterlegt.', bg=C.SURFACE,
                     fg=C.DIM, font=F(9)).pack(anchor='w', pady=3)
        for row in transitions:
            line = tk.Frame(self.transition_rows, bg=C.SURFACE)
            line.pack(fill='x', pady=2)
            tk.Label(line, text=f"Nach {row['after_name']} · {row['days_after']} Tag(e) gesperrt: "
                     f"{row['blocked_name']}", bg=C.SURFACE, fg=C.TEXT, font=F(9), anchor='w').pack(
                         side='left', fill='x', expand=True)
            ttk.Button(line, text='Entfernen', command=lambda rid=row['id']: self.delete_transition_rule(rid)).pack(
                side='right')
        for row in streaks:
            line = tk.Frame(self.streak_rows, bg=C.SURFACE)
            line.pack(fill='x', pady=2)
            tk.Label(line, text=f"{row['name']} · höchstens {row['max_consecutive']} hintereinander · danach "
                     f"{row['rest_days']} Ruhetage", bg=C.SURFACE, fg=C.TEXT, font=F(9), anchor='w').pack(
                         side='left', fill='x', expand=True)
            ttk.Button(line, text='Entfernen', command=lambda sid=row['shift_id']: self.delete_streak_rule(sid)).pack(
                side='right')

    def delete_transition_rule(self, rule_id):
        with self.store.db:
            self.store.db.execute('DELETE FROM shift_transition_rules WHERE id=?', (rule_id,))
        self.refresh_shifts()
        self.set_status('Schichtfolge-Regel entfernt.')

    def delete_streak_rule(self, shift_id):
        with self.store.db:
            self.store.db.execute('DELETE FROM shift_streak_rules WHERE shift_id=?', (shift_id,))
        self.refresh_shifts()
        self.set_status('Schichtserien-Regel entfernt.')

    def save_rules(self):
        try:
            rest = float(self.rest_var.get().replace(',', '.'))
            consec = int(float(self.consec_var.get().replace(',', '.')))
            delay = int(float(self.delay_var.get().replace(',', '.')))
            tol = float(self.tol_var.get().replace(',', '.'))
            if not (0 <= rest <= 24 and 0 <= consec <= 31 and 0 <= delay <= 30 and 0 <= tol <= 40):
                raise ValueError
        except ValueError:
            info(self, 'Eingabe prüfen', 'Bitte gültige Zahlen eingeben.', 'Ruhezeit 0–24 h, Folgetage 0–31, Bedenkzeit 0–30 s, Toleranz 0–40 h.',
                 tone='danger')
            return
        self.store.set_setting('min_rest_hours', fmt_hours(rest).replace(',', '.'))
        self.store.set_setting('max_consecutive_days', consec)
        self.store.set_setting('swap_delay_seconds', delay)
        self.store.set_setting('hours_tolerance', fmt_hours(tol).replace(',', '.'))
        self.store.set_setting('fill_hours', '1' if self.fill_var.get() else '0')
        self.refresh_shifts()
        self.set_status('Hausregeln gespeichert. Prüfung, Tauschassistent und Generator nutzen ab sofort die neuen Werte.')

    def edit_shift(self):
        sel = self.shift_tree.selection()
        if sel:
            ShiftForm(self, int(sel[0]))

    def delete_shift(self):
        sel = self.shift_tree.selection()
        if not sel:
            return
        sid = int(sel[0])
        s = self.store.all('SELECT * FROM shifts WHERE id=?', (sid,))[0]
        count = self.store.db.execute('SELECT COUNT(*) FROM assignments WHERE shift_id=?', (sid,)).fetchone()[0]
        if ask(self, 'Schichtmodell löschen', f"„{s['name']}“ löschen?",
               f'{count} geplante Dienste mit dieser Schicht werden dadurch zu „Frei“. Dies kann nicht rückgängig '
               'gemacht werden.', ok='Endgültig löschen', tone='danger', ok_style='Danger.TButton'):
            with self.store.db:
                self.store.db.execute('DELETE FROM assignments WHERE shift_id=?', (sid,))
                self.store.db.execute('DELETE FROM shifts WHERE id=?', (sid,))
            self.refresh_shifts()
            self.set_status(f"Schichtmodell „{s['name']}“ gelöscht.", tone='warn')


# ---------------------------------------------------------------------------
# Dialoge
# ---------------------------------------------------------------------------

class BaseWindow(tk.Toplevel):
    def __init__(self, app, title, width, height):
        super().__init__(app)
        self.withdraw()
        self.app = app
        self.title(title)
        self.configure(bg=C.SURFACE)
        self.transient(app)
        x = app.winfo_rootx() + max(0, (app.winfo_width() - width) // 2)
        y = app.winfo_rooty() + max(0, (app.winfo_height() - height) // 3)
        self.geometry(f'{width}x{height}+{x}+{y}')
        self.minsize(min(width, 560), min(height, 420))
        tk.Frame(self, bg=C.ACCENT, height=3).pack(fill='x')
        self.bind('<Escape>', lambda _e: self.destroy())

    def show(self):
        self.deiconify()
        enable_windows_polish(self)
        try:
            self.grab_set()
        except tk.TclError:
            pass
        self.focus_set()


class DayDialog(BaseWindow):
    def __init__(self, app, day):
        super().__init__(app, f'Tagesplanung · {day:%d.%m.%Y}', 820, 720)
        self.day = day
        self.ctx = PlanContext(app.store, day, day)
        ctx = self.ctx
        head = tk.Frame(self, bg=C.SURFACE, padx=22, pady=14)
        head.pack(fill='x')
        tk.Label(head, text='TAGESPLANUNG', bg=C.SURFACE, fg=C.ACCENT_SOFT, font=F(8, 'bold'), anchor='w').pack(fill='x')
        tk.Label(head, text=day_title(day), bg=C.SURFACE, fg=C.TEXT, font=F(17, 'bold'), anchor='w').pack(fill='x')
        hol = holiday_name(day)
        note = (f'Feiertag: {hol} · regulärer Dienstbetrieb' if hol else
                'Wochenende · regulärer Dienstbetrieb' if is_weekend(day) else
                'Jede Person auf „Frei“ oder eine Schicht setzen.')
        tk.Label(head, text=note, bg=C.SURFACE, fg=C.ACCENT_SOFT if hol else C.MUTED, font=F(9), anchor='w').pack(fill='x')
        self.pills = tk.Frame(head, bg=C.SURFACE)
        self.pills.pack(fill='x', pady=(10, 0))
        ds = day.isoformat()
        self.original = {}
        self.segments = {}
        people = [e for e in ctx.employees.values() if e.active or (e.id, ds) in ctx.plan]
        order = {s.id: i for i, s in enumerate(ctx.shift_list)}
        people.sort(key=lambda e: (order.get(ctx.plan.get((e.id, ds)), 99), e.name.lower()))
        foot = tk.Frame(self, bg=C.SURFACE2, padx=22, pady=12)
        foot.pack(side='bottom', fill='x')
        tk.Label(foot, text='Änderungen werden geprüft und erst nach Bestätigung gespeichert.', bg=C.SURFACE2,
                 fg=C.DIM, font=F(8)).pack(side='left')
        ttk.Button(foot, text='Prüfen & speichern', style='Accent.TButton', command=self.save).pack(side='right')
        ttk.Button(foot, text='Abbrechen', command=self.destroy).pack(side='right', padx=8)
        body = ScrollFrame(self, bg=C.SURFACE)
        body.pack(fill='both', expand=True, padx=(22, 8), pady=(0, 8))
        options = [(None, 'Frei')] + [(s.id, s.short) for s in ctx.shift_list]
        colors = {s.id: blend(safe_color(s.color), C.SURFACE, .35) for s in ctx.shift_list}
        for e in people:
            row = tk.Frame(body.inner, bg=C.SURFACE, pady=5)
            row.pack(fill='x')
            tk.Frame(body.inner, bg=C.BORDER_SOFT, height=1).pack(fill='x')
            chip_widget(row, ctx.labels.get(e.id, '?'), C.CARD, C.TEXT, C.BORDER).pack(side='left')
            names = tk.Frame(row, bg=C.SURFACE)
            names.pack(side='left', padx=(8, 0))
            tk.Label(names, text=e.name + ('' if e.active else '  (inaktiv)'), bg=C.SURFACE,
                     fg=C.TEXT if e.active else C.DIM, font=F(10, 'bold'), anchor='w').pack(fill='x')
            tk.Label(names, text=e.role or 'ohne Bereich', bg=C.SURFACE, fg=C.MUTED, font=F(8), anchor='w').pack(fill='x')
            current = ctx.plan.get((e.id, ds))
            if ctx.on_leave(e.id, day):
                badge = RoundedCard(row, fill=blend(C.LEAVE, C.SURFACE, .82), border=C.LEAVE, radius=11, padx=12,
                                    pady=4, fit_width=True)
                badge.pack(side='right', padx=(0, 8))
                tk.Label(badge.inner, text='Urlaub', bg=badge.fill, fg=C.LEAVE, font=F(9, 'bold')).pack()
                if not current:
                    continue
            self.original[e.id] = current
            seg = Segmented(row, options, current, on_change=lambda _v: self.update_pills(), colors=colors)
            seg.pack(side='right', padx=(0, 8))
            self.segments[e.id] = seg
            if ctx.absent(e.id, day):
                badge = RoundedCard(row, fill=blend(C.WARN, C.SURFACE, .85), border=C.WARN, radius=10, padx=8,
                                    pady=3, fit_width=True)
                badge.pack(side='right', padx=8)
                tk.Label(badge.inner, text='abwesend', bg=badge.fill, fg=C.WARN, font=F(8, 'bold')).pack()
        self.update_pills()
        self.show()

    def current_changes(self):
        return {eid: seg.value for eid, seg in self.segments.items() if seg.value != self.original.get(eid)}

    def update_pills(self):
        for child in self.pills.winfo_children():
            child.destroy()
        overlay = {(eid, self.day.isoformat()): sid for eid, sid in self.current_changes().items()}
        for s in self.ctx.shift_list:
            have = len(self.ctx.staff_on(self.day, s.id, overlay))
            under = have < s.coverage
            color = C.DANGER if under else (C.ACCENT_SOFT if s.cap and have > s.cap else C.OK)
            rc = RoundedCard(self.pills, fill=blend(color, C.SURFACE, .85), border=blend(color, C.SURFACE, .5),
                             radius=12, padx=10, pady=5, fit_width=True)
            rc.pack(side='left', padx=(0, 6))
            pill = rc.inner
            tk.Label(pill, text=f'{s.short} {compact_time(s)}', bg=pill.cget('bg'), fg=C.TEXT, font=F(8, 'bold')).pack(
                side='left')
            tk.Label(pill, text=f'  {have}/{s.coverage}', bg=pill.cget('bg'), fg=color, font=F(8, 'bold')).pack(
                side='left')

    def save(self):
        changes = self.current_changes()
        if not changes:
            self.destroy()
            return
        ctx = self.ctx
        issues = core.preview_day_changes(ctx, self.day, changes)
        lines = [('head', 'Änderungen')]
        for eid, sid in changes.items():
            old = self.original.get(eid)
            lines.append(('info', f'{ctx.employees[eid].name}: {ctx.shifts[old].name if old else "Frei"} → '
                                  f'{ctx.shifts[sid].name if sid else "Frei"}'))
        blocking = False
        if issues:
            lines.append(('head', 'Hinweise der Regelprüfung'))
            for key, checks in issues.items():
                for check in checks:
                    blocking |= check.blocking
                    lines.append((check.status, f'{check.rule}: {check.message}'))
        lines.append(('head', 'Nicht vollständig geprüft'))
        lines += [('muted', text) for text in UNCHECKED_RULES]
        if blocking:
            heading = 'Achtung: Die Änderungen verletzen geprüfte Regeln.'
            body = 'Du kannst bewusst trotzdem speichern – die Verstöße erscheinen dann als Regelhinweis in der Übersicht.'
        else:
            heading = f'{len(changes)} Änderung(en) speichern?'
            body = ''
        if ask(self, 'Tagesplanung speichern', heading, body, lines,
               ok='Trotzdem speichern' if blocking else 'Speichern', tone='warn' if blocking else 'info',
               ok_style='Danger.TButton' if blocking else 'Accent.TButton'):
            core.apply_day_changes(self.app.store, self.day, changes)
            self.destroy()
            self.app.refresh_plan()
            self.app.set_status(f'Tagesplanung {self.day:%d.%m.%Y} gespeichert ({len(changes)} Änderung(en)).',
                                tone='warn' if blocking else 'ok')


class TransitionRuleForm(BaseWindow):
    def __init__(self, app):
        super().__init__(app, 'Schichtfolge-Regel', 600, 380)
        f = tk.Frame(self, bg=C.SURFACE, padx=24, pady=18)
        f.pack(fill='both', expand=True)
        shifts = app.store.all('SELECT id,name,start,end FROM shifts ORDER BY start,name')
        self.ids = {f"{s['name']} ({s['start']}–{s['end']})": s['id'] for s in shifts}
        choices = list(self.ids)
        self.after_var = tk.StringVar(value=choices[0] if choices else '')
        self.blocked_var = tk.StringVar(value=choices[1] if len(choices) > 1 else (choices[0] if choices else ''))
        self.days_var = tk.StringVar(value='1')
        tk.Label(f, text='Nach welcher Schicht?', bg=C.SURFACE, fg=C.MUTED, font=F(9)).pack(anchor='w')
        ttk.Combobox(f, textvariable=self.after_var, values=choices, state='readonly').pack(fill='x', pady=(4, 12))
        tk.Label(f, text='Welche Schicht soll danach gesperrt sein?', bg=C.SURFACE, fg=C.MUTED,
                 font=F(9)).pack(anchor='w')
        ttk.Combobox(f, textvariable=self.blocked_var, values=choices, state='readonly').pack(fill='x', pady=(4, 12))
        row = tk.Frame(f, bg=C.SURFACE)
        row.pack(fill='x')
        tk.Label(row, text='Für wie viele Folgetage?', bg=C.SURFACE, fg=C.MUTED, font=F(9)).pack(side='left')
        ttk.Spinbox(row, from_=1, to=14, textvariable=self.days_var, width=5).pack(side='left', padx=10)
        tk.Label(f, text='Beispiel: Nach Nachtdienst ist Frühdienst am nächsten Tag gesperrt.',
                 bg=C.SURFACE, fg=C.DIM, font=F(8)).pack(anchor='w', pady=(10, 0))
        foot = tk.Frame(self, bg=C.SURFACE2, padx=24, pady=12)
        foot.pack(fill='x', side='bottom')
        ttk.Button(foot, text='Speichern', style='Accent.TButton', command=self.save).pack(side='right')
        ttk.Button(foot, text='Abbrechen', command=self.destroy).pack(side='right', padx=8)
        self.show()

    def save(self):
        after_id, blocked_id = self.ids.get(self.after_var.get()), self.ids.get(self.blocked_var.get())
        try:
            days = int(self.days_var.get())
            if not after_id or not blocked_id or after_id == blocked_id or not 1 <= days <= 14:
                raise ValueError
        except ValueError:
            info(self, 'Eingabe prüfen', 'Bitte zwei unterschiedliche Schichten und 1–14 Tage auswählen.',
                 tone='danger')
            return
        with self.app.store.db:
            self.app.store.db.execute('''INSERT INTO shift_transition_rules(after_shift_id,blocked_shift_id,days_after)
                VALUES(?,?,?) ON CONFLICT(after_shift_id,blocked_shift_id) DO UPDATE SET days_after=excluded.days_after''',
                (after_id, blocked_id, days))
        self.destroy()
        self.app.refresh_shifts()
        self.app.set_status('Schichtfolge-Regel gespeichert. Planer, Prüfung und Tauschassistent wenden sie an.')


class StreakRuleForm(BaseWindow):
    def __init__(self, app):
        super().__init__(app, 'Schichtserien-Regel', 600, 390)
        f = tk.Frame(self, bg=C.SURFACE, padx=24, pady=18)
        f.pack(fill='both', expand=True)
        shifts = app.store.all('SELECT id,name,start,end FROM shifts ORDER BY start,name')
        self.ids = {f"{s['name']} ({s['start']}–{s['end']})": s['id'] for s in shifts}
        choices = list(self.ids)
        self.shift_var = tk.StringVar(value=choices[0] if choices else '')
        self.max_var, self.rest_var = tk.StringVar(value='4'), tk.StringVar(value='2')
        tk.Label(f, text='Für welche Schicht gilt die Serienregel?', bg=C.SURFACE, fg=C.MUTED,
                 font=F(9)).pack(anchor='w')
        ttk.Combobox(f, textvariable=self.shift_var, values=choices, state='readonly').pack(fill='x', pady=(4, 14))
        row = tk.Frame(f, bg=C.SURFACE)
        row.pack(fill='x', pady=4)
        tk.Label(row, text='Maximale Anzahl direkt hintereinander:', bg=C.SURFACE, fg=C.MUTED,
                 font=F(9)).pack(side='left')
        ttk.Spinbox(row, from_=1, to=14, textvariable=self.max_var, width=5).pack(side='left', padx=10)
        row = tk.Frame(f, bg=C.SURFACE)
        row.pack(fill='x', pady=4)
        tk.Label(row, text='Ruhetage nach Erreichen der Grenze:', bg=C.SURFACE, fg=C.MUTED,
                 font=F(9)).pack(side='left')
        ttk.Spinbox(row, from_=0, to=14, textvariable=self.rest_var, width=5).pack(side='left', padx=10)
        tk.Label(f, text='Beispiel: höchstens 4 Spätdienste nacheinander, danach 2 volle Tage ohne Dienst.',
                 bg=C.SURFACE, fg=C.DIM, font=F(8)).pack(anchor='w', pady=(10, 0))
        foot = tk.Frame(self, bg=C.SURFACE2, padx=24, pady=12)
        foot.pack(fill='x', side='bottom')
        ttk.Button(foot, text='Speichern', style='Accent.TButton', command=self.save).pack(side='right')
        ttk.Button(foot, text='Abbrechen', command=self.destroy).pack(side='right', padx=8)
        self.show()

    def save(self):
        shift_id = self.ids.get(self.shift_var.get())
        try:
            maximum, rest = int(self.max_var.get()), int(self.rest_var.get())
            if not shift_id or not 1 <= maximum <= 14 or not 0 <= rest <= 14:
                raise ValueError
        except ValueError:
            info(self, 'Eingabe prüfen', 'Maximalfolge muss 1–14, Ruhetage 0–14 betragen.', tone='danger')
            return
        with self.app.store.db:
            self.app.store.db.execute('''INSERT INTO shift_streak_rules(shift_id,max_consecutive,rest_days)
                VALUES(?,?,?) ON CONFLICT(shift_id) DO UPDATE SET max_consecutive=excluded.max_consecutive,
                rest_days=excluded.rest_days''', (shift_id, maximum, rest))
        self.destroy()
        self.app.refresh_shifts()
        self.app.set_status('Schichtserien-Regel gespeichert. Planer, Prüfung und Tauschassistent wenden sie an.')


class EmployeeForm(BaseWindow):
    def __init__(self, app, employee_id=None):
        super().__init__(app, 'Mitarbeiter bearbeiten' if employee_id else 'Mitarbeiter hinzufügen', 760, 930)
        store = app.store
        self.e = store.all('SELECT * FROM employees WHERE id=?', (employee_id,))[0] if employee_id else None
        e = self.e
        f = tk.Frame(self, bg=C.SURFACE, padx=24, pady=16)
        f.pack(fill='both', expand=True)
        tk.Label(f, text='STAMMDATEN', bg=C.SURFACE, fg=C.ACCENT_SOFT, font=F(8, 'bold')).grid(row=0, column=0,
                                                                                             sticky='w')
        tk.Label(f, text=e['name'] if e else 'Neue Person', bg=C.SURFACE, fg=C.TEXT, font=F(16, 'bold')).grid(
            row=1, column=0, columnspan=2, sticky='w', pady=(0, 4))
        if e and e['is_demo']:
            tk.Label(f, text='Testdaten · fiktive Demo-Person', bg=C.SURFACE, fg=C.WARN, font=F(8, 'bold')).grid(
                row=2, column=0, columnspan=2, sticky='w')
        self.vars = {k: tk.StringVar(value=str(e[k] if e and e[k] is not None else v)) for k, v in
                     (('name', ''), ('role', ''), ('weekly_hours', '38.5'), ('max_days', '5'), ('preferences', ''),
                      ('unavailable', ''))}
        roles = sorted({r[0] for r in store.db.execute("SELECT DISTINCT role FROM employees WHERE role<>''")})
        row = 3

        def field(text, widget, hint=None):
            nonlocal row
            tk.Label(f, text=text, bg=C.SURFACE, fg=C.MUTED, font=F(9)).grid(row=row, column=0, sticky='nw', pady=7,
                                                                            padx=(0, 14))
            widget.grid(row=row, column=1, sticky='ew', pady=7)
            if hint:
                row += 1
                tk.Label(f, text=hint, bg=C.SURFACE, fg=C.DIM, font=F(8), justify='left').grid(
                    row=row, column=1, sticky='w', pady=(0, 4))
            row += 1

        field('Name', ttk.Entry(f, textvariable=self.vars['name']))
        field('Bereich / Qualifikation', ttk.Combobox(f, textvariable=self.vars['role'], values=roles))
        field('Vertragsstunden / Woche', ttk.Spinbox(f, from_=0, to=60, increment=0.5,
                                                      textvariable=self.vars['weekly_hours'], width=8))
        field('Max. Arbeitstage / Woche', ttk.Spinbox(f, from_=1, to=7, increment=1,
                                                       textvariable=self.vars['max_days'], width=8))
        field('Wünsche', ttk.Entry(f, textvariable=self.vars['preferences']),
              'kommagetrennt, z. B. „Frühdienst bevorzugt, Wochenende vermeiden“')
        tk.Label(f, text='Feste Wochenschichten', bg=C.SURFACE, fg=C.MUTED, font=F(9)).grid(
            row=row, column=0, sticky='nw', pady=7, padx=(0, 14))
        patterns_box = tk.Frame(f, bg=C.SURFACE)
        patterns_box.grid(row=row, column=1, sticky='ew', pady=7)
        row += 1
        shift_rows = store.all('SELECT id,name,start,end FROM shifts ORDER BY start,name')
        pattern_choices = {'– keine feste Schicht –': None}
        for shift in shift_rows:
            pattern_choices[f"{shift['name']} ({shift['start']}–{shift['end']})"] = shift['id']
        existing_patterns = {int(r['weekday']): r['shift_id'] for r in store.all(
            'SELECT weekday,shift_id FROM employee_shift_patterns WHERE employee_id=?',
            (e['id'],))} if e else {}
        self.pattern_vars = {}
        for weekday, weekday_name in enumerate(WEEKDAYS_LONG):
            tk.Label(patterns_box, text=weekday_name, bg=C.SURFACE, fg=C.MUTED, font=F(8), width=13,
                     anchor='w').grid(row=weekday, column=0, sticky='w', padx=(0, 8), pady=1)
            var = tk.StringVar(value=next((label for label, sid in pattern_choices.items()
                                           if sid == existing_patterns.get(weekday)), '– keine feste Schicht –'))
            ttk.Combobox(patterns_box, textvariable=var, values=list(pattern_choices), state='readonly',
                         width=32).grid(row=weekday, column=1, sticky='w', pady=1)
            self.pattern_vars[weekday] = (var, pattern_choices)
        tk.Label(patterns_box, text='Beispiel: Freitag → Nachtdienst (22:00–06:00). Die Schicht steht im Plan am Freitag.',
                 bg=C.SURFACE, fg=C.DIM, font=F(8), justify='left').grid(
                     row=7, column=0, columnspan=2, sticky='w', pady=(4, 0))
        row += 1
        absence = tk.Frame(f, bg=C.SURFACE)
        ttk.Entry(absence, textvariable=self.vars['unavailable']).pack(side='left', fill='x', expand=True)
        self.absence_pick = DateField(absence, date.today())
        self.absence_pick.pack(side='left', padx=(8, 4))
        ttk.Button(absence, text='＋ Tag', command=self.add_absence_day).pack(side='left')
        field('Einzelne Abwesenheitstage', absence, 'Datum wählen und „＋ Tag“ klicken (Format JJJJ-MM-TT, kommagetrennt)')
        shifts = store.all('SELECT * FROM shifts ORDER BY start,name')
        chosen = {r['shift_id'] for r in store.all('SELECT shift_id FROM eligibility WHERE employee_id=?',
                                                    (e['id'],))} if e else set()
        box = tk.Frame(f, bg=C.SURFACE)
        self.checks = []
        for s in shifts:
            var = tk.BooleanVar(value=(not chosen or s['id'] in chosen))
            ttk.Checkbutton(box, text=f"{s['name']}  ({s['start']}–{s['end']})", variable=var,
                            style='Surface.TCheckbutton').pack(anchor='w', pady=1)
            self.checks.append((s['id'], var))
        field('Einsetzbar für (Schichteignung)', box)
        f.columnconfigure(1, weight=1)
        foot = tk.Frame(self, bg=C.SURFACE2, padx=24, pady=12)
        foot.pack(fill='x', side='bottom')
        ttk.Button(foot, text='Speichern', style='Accent.TButton', command=self.save).pack(side='right')
        ttk.Button(foot, text='Abbrechen', command=self.destroy).pack(side='right', padx=8)
        self.show()

    def add_absence_day(self):
        days = [x.strip() for x in self.vars['unavailable'].get().split(',') if x.strip()]
        iso = self.absence_pick.get().isoformat()
        if iso not in days:
            days.append(iso)
        self.vars['unavailable'].set(', '.join(sorted(days)))

    def save(self):
        v = {k: var.get().strip() for k, var in self.vars.items()}
        if not v['name']:
            info(self, 'Eingabe prüfen', 'Bitte einen Namen eingeben.', tone='danger')
            return
        try:
            hours = float(v['weekly_hours'].replace(',', '.'))
            max_days = int(float(v['max_days']))
            if hours < 0 or not 1 <= max_days <= 7:
                raise ValueError
        except ValueError:
            info(self, 'Eingabe prüfen', 'Stunden und Arbeitstage prüfen.',
                 'Stunden ≥ 0, maximale Arbeitstage zwischen 1 und 7.', tone='danger')
            return
        days = [x.strip() for x in v['unavailable'].split(',') if x.strip()]
        bad = []
        for d in days:
            try:
                date.fromisoformat(d)
            except ValueError:
                bad.append(d)
        if bad:
            info(self, 'Eingabe prüfen', 'Ungültige Abwesenheitstage.', 'Bitte die Datumsauswahl nutzen: ' +
                 ', '.join(bad), tone='danger')
            return
        eligible_ids = {sid for sid, var in self.checks if var.get()}
        selected_patterns = [(weekday, choices.get(var.get())) for weekday, (var, choices) in self.pattern_vars.items()
                             if choices.get(var.get())]
        disallowed = [sid for _weekday, sid in selected_patterns if eligible_ids and sid not in eligible_ids]
        if disallowed:
            info(self, 'Eingabe prüfen', 'Feste Schicht ist nicht als einsetzbar markiert.',
                 'Markiere die Schicht zusätzlich unter „Einsetzbar für (Schichteignung)“.', tone='danger')
            return
        values = (v['name'], v['role'], hours, max_days, v['preferences'], ', '.join(days))
        store = self.app.store
        with store.db:
            if self.e:
                store.db.execute('UPDATE employees SET name=?,role=?,weekly_hours=?,max_days=?,preferences=?,'
                                 'unavailable=? WHERE id=?', (*values, self.e['id']))
                eid = self.e['id']
            else:
                eid = store.db.execute('INSERT INTO employees(name,role,weekly_hours,max_days,preferences,unavailable)'
                                       ' VALUES(?,?,?,?,?,?)', values).lastrowid
            store.db.execute('DELETE FROM eligibility WHERE employee_id=?', (eid,))
            store.db.executemany('INSERT INTO eligibility(employee_id,shift_id) VALUES(?,?)',
                                 [(eid, sid) for sid, var in self.checks if var.get()])
            store.db.execute('DELETE FROM employee_shift_patterns WHERE employee_id=?', (eid,))
            store.db.executemany('INSERT INTO employee_shift_patterns(employee_id,weekday,shift_id) VALUES(?,?,?)',
                                 [(eid, weekday, sid) for weekday, sid in selected_patterns])
        self.destroy()
        self.app.refresh_staff()
        self.app.update_demo_badge()
        self.app.set_status(f"Mitarbeiter „{v['name']}“ gespeichert.")


class ShiftForm(BaseWindow):
    PALETTE = ['#f5b544', '#8b7cf6', '#38bdf8', '#34d399', '#fb7185', '#f472b6', '#a3e635', '#94a3b8']

    def __init__(self, app, shift_id=None):
        super().__init__(app, 'Schichtmodell', 580, 620)
        self.s = app.store.all('SELECT * FROM shifts WHERE id=?', (shift_id,))[0] if shift_id else None
        s = self.s
        f = tk.Frame(self, bg=C.SURFACE, padx=24, pady=16)
        f.pack(fill='both', expand=True)
        tk.Label(f, text='SCHICHTMODELL', bg=C.SURFACE, fg=C.ACCENT_SOFT, font=F(8, 'bold')).grid(row=0, column=0,
                                                                                                sticky='w')
        tk.Label(f, text=s['name'] if s else 'Neue Schicht', bg=C.SURFACE, fg=C.TEXT, font=F(16, 'bold')).grid(
            row=1, column=0, columnspan=2, sticky='w', pady=(0, 8))
        defaults = {'name': '', 'start': '06:00', 'end': '14:00', 'hours': '8', 'coverage': '1', 'max_staff': '3',
                    'color': '#8b7cf6'}
        self.vars = {k: tk.StringVar(value=str(s[k] if s and s[k] is not None else v)) for k, v in defaults.items()}
        fields = [('name', 'Name', ttk.Entry), ('start', 'Beginn (HH:MM)', ttk.Entry), ('end', 'Ende (HH:MM)', ttk.Entry),
                  ('hours', 'Anrechenbare Stunden', ttk.Entry), ('coverage', 'Mindestbesetzung pro Tag', ttk.Spinbox),
                  ('max_staff', 'Maximalbesetzung (0 = unbegrenzt)', ttk.Spinbox)]
        for i, (key, text, cls) in enumerate(fields, start=2):
            tk.Label(f, text=text, bg=C.SURFACE, fg=C.MUTED, font=F(9)).grid(row=i, column=0, sticky='w', pady=7,
                                                                            padx=(0, 14))
            kw = {'from_': 0, 'to': 50, 'increment': 1} if cls is ttk.Spinbox else {}
            cls(f, textvariable=self.vars[key], **kw).grid(row=i, column=1, sticky='ew', pady=7)
        tk.Label(f, text='Farbe', bg=C.SURFACE, fg=C.MUTED, font=F(9)).grid(row=8, column=0, sticky='nw', pady=7)
        colors = tk.Frame(f, bg=C.SURFACE)
        colors.grid(row=8, column=1, sticky='w', pady=7)
        self.swatch = tk.Label(colors, text='   ', bg=safe_color(self.vars['color'].get()), width=3)
        self.swatch.pack(side='left')
        entry = ttk.Entry(colors, textvariable=self.vars['color'], width=10)
        entry.pack(side='left', padx=8)
        self.vars['color'].trace_add('write', lambda *_: self.swatch.configure(bg=safe_color(self.vars['color'].get())))
        for col in self.PALETTE:
            sw = tk.Label(colors, bg=col, width=2, cursor='hand2', highlightthickness=1, highlightbackground=C.BORDER)
            sw.pack(side='left', padx=2)
            sw.bind('<Button-1>', lambda _e, c=col: self.vars['color'].set(c))
        tk.Label(f, text='Endet die Schicht vor ihrem Beginn (z. B. 22:00–06:00), zählt sie bis in den Folgetag – '
                         'wichtig für die Ruhezeitprüfung. Die Maximalbesetzung begrenzt, wie viele Personen der '
                         'Generator für Zusatzdienste in diese Schicht legt.', bg=C.SURFACE, fg=C.DIM, font=F(8),
                 justify='left', wraplength=460).grid(row=9, column=0, columnspan=2, sticky='w', pady=(8, 0))
        f.columnconfigure(1, weight=1)
        foot = tk.Frame(self, bg=C.SURFACE2, padx=24, pady=12)
        foot.pack(fill='x', side='bottom')
        ttk.Button(foot, text='Speichern', style='Accent.TButton', command=self.save).pack(side='right')
        ttk.Button(foot, text='Abbrechen', command=self.destroy).pack(side='right', padx=8)
        self.show()

    def save(self):
        from datetime import datetime
        v = {k: var.get().strip() for k, var in self.vars.items()}
        try:
            if not v['name']:
                raise ValueError
            datetime.strptime(v['start'], '%H:%M')
            datetime.strptime(v['end'], '%H:%M')
            hours, coverage = float(v['hours'].replace(',', '.')), int(float(v['coverage']))
            max_staff = int(float(v['max_staff'] or 0))
            if coverage < 0 or hours <= 0 or max_staff < 0 or (max_staff and max_staff < coverage):
                raise ValueError
        except ValueError:
            info(self, 'Eingabe prüfen', 'Bitte Eingaben prüfen.',
                 'Name, gültige Zeiten (HH:MM), Stunden > 0, Mindestbesetzung ab 0 und eine Maximalbesetzung '
                 '(0 oder mindestens so groß wie die Mindestbesetzung) eingeben.', tone='danger')
            return
        values = (v['name'], v['start'], v['end'], hours, safe_color(v['color']), coverage, max_staff)
        store = self.app.store
        if self.s:
            store.run('UPDATE shifts SET name=?,start=?,end=?,hours=?,color=?,coverage=?,max_staff=? WHERE id=?',
                      (*values, self.s['id']))
        else:
            store.run('INSERT INTO shifts(name,start,end,hours,color,coverage,max_staff) VALUES(?,?,?,?,?,?,?)', values)
        self.destroy()
        self.app.refresh_shifts()
        self.app.set_status(f"Schichtmodell „{v['name']}“ gespeichert.")


def run():
    set_dpi_awareness()
    app = App()
    app.mainloop()


if __name__ == '__main__':
    run()
