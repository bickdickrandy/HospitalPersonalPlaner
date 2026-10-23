"""Dunkles HUD-Theme und wiederverwendbare Tkinter-Bausteine.

Technische Entscheidung: Die App bleibt bei Tkinter (Standardbibliothek, keine zusätzlichen
Abhängigkeiten, einfache PyInstaller-Paketierung). Die Standard-ttk-Optik wird über das
"clam"-Theme komplett umgefärbt; der Monatskalender wird auf einem Canvas gezeichnet, weil nur
so abgerundete Karten, Leuchteffekte und Hover-Zustände ohne Fremdbibliothek möglich sind.
"""
import base64
import calendar
import struct
import sys
import zlib
import tkinter as tk
from datetime import date
from tkinter import font as tkfont
from tkinter import ttk

from planner_core import MONTHS, WEEKDAYS, add_months, holiday_name, is_weekend


class C:
    BG = '#090b18'
    SIDEBAR = '#0c0e20'
    SURFACE = '#11132a'
    SURFACE2 = '#171a36'
    CARD = '#1c1f42'
    CARD_HOVER = '#24285a'
    BORDER = '#272b55'
    BORDER_SOFT = '#1e2148'
    DAY = '#151938'
    WEEKEND = '#0d0f24'
    HOLIDAY = '#1a1438'
    OUTSIDE = '#0c0e1f'
    ACCENT = '#8b5cf6'
    ACCENT_HOVER = '#9d74ff'
    ACCENT_DARK = '#6d3fe0'
    ACCENT_SOFT = '#a78bfa'
    GLOW = '#c4b5fd'
    TEXT = '#eceaff'
    MUTED = '#a3a6cc'
    DIM = '#62668f'
    OK = '#34d399'
    WARN = '#fbbf24'
    DANGER = '#fb7185'
    LEAVE = '#38bdf8'


def blend(a, b, t):
    """Mischt Hex-Farbe a mit b (t=0 -> a, t=1 -> b)."""
    try:
        a, b = a.lstrip('#'), b.lstrip('#')
        ra, ga, ba = (int(a[i:i + 2], 16) for i in (0, 2, 4))
        rb, gb, bb = (int(b[i:i + 2], 16) for i in (0, 2, 4))
    except (ValueError, IndexError):
        return '#' + b if not b.startswith('#') else b
    return '#%02x%02x%02x' % (round(ra + (rb - ra) * t), round(ga + (gb - ga) * t), round(ba + (bb - ba) * t))


def safe_color(value, fallback=C.ACCENT):
    value = (value or '').strip()
    if len(value) == 7 and value.startswith('#'):
        try:
            int(value[1:], 16)
            return value
        except ValueError:
            pass
    return fallback


_FONT_FAMILY = None


def family():
    global _FONT_FAMILY
    if _FONT_FAMILY is None:
        available = set(tkfont.families())
        for name in ('Segoe UI Variable Text', 'Segoe UI', 'Inter', 'Helvetica Neue', 'DejaVu Sans'):
            if name in available:
                _FONT_FAMILY = name
                break
        else:
            _FONT_FAMILY = 'TkDefaultFont'
    return _FONT_FAMILY


def F(size=10, weight='normal'):
    return (family(), size, weight)


def round_rect(canvas, x1, y1, x2, y2, r=8, **kw):
    r = max(0, min(r, (x2 - x1) / 2, (y2 - y1) / 2))
    pts = [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2, x2 - r, y2, x1 + r, y2,
           x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]
    return canvas.create_polygon(pts, smooth=True, splinesteps=6, **kw)


# ---------------------------------------------------------------------------
# Abgerundete Bedienelemente ohne Zusatzbibliothek: kantengeglättete PNG-Bilder werden
# zur Laufzeit erzeugt (zlib/struct aus der Standardbibliothek) und als 9-Slice-Bilder
# für ttk-Elemente verwendet. Dadurch skalieren Buttons und Felder mit ihrem Inhalt.
# ---------------------------------------------------------------------------

_IMAGES = []  # Referenzen halten, sonst verwirft Tk die Bilder
_PHOTO_CACHE = {}


def _rgb(color):
    color = color.lstrip('#')
    return tuple(int(color[i:i + 2], 16) for i in (0, 2, 4))


def _png_bytes(width, height, pixels):
    raw = b''.join(b'\x00' + bytes(row) for row in pixels)

    def chunk(kind, data):
        body = kind + data
        return struct.pack('>I', len(data)) + body + struct.pack('>I', zlib.crc32(body) & 0xffffffff)
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 6, 0, 0, 0)) +
            chunk(b'IDAT', zlib.compress(raw, 9)) + chunk(b'IEND', b''))


def _inside(x, y, x0, y0, x1, y1, r):
    if x < x0 or x > x1 or y < y0 or y > y1:
        return False
    cx = min(max(x, x0 + r), x1 - r)
    cy = min(max(y, y0 + r), y1 - r)
    return (x - cx) ** 2 + (y - cy) ** 2 <= r * r


def rounded_photo(fill, border=None, radius=10, border_width=1.0, size=None, ss=3):
    """Kantengeglättetes Rechteck mit runden Ecken als PhotoImage (transparente Ecken)."""
    key = (fill, border, radius, border_width, size, ss)
    if key in _PHOTO_CACHE:
        return _PHOTO_CACHE[key]
    size = size or (2 * radius + 4)
    w = h = size
    fr, fg_, fb = _rgb(fill)
    br, bg_, bb = _rgb(border or fill)
    bw = border_width if border else 0
    rows = []
    step = 1.0 / ss
    for py in range(h):
        row = []
        for px in range(w):
            outer = inner = 0
            for sy in range(ss):
                y = py + (sy + .5) * step
                for sx in range(ss):
                    x = px + (sx + .5) * step
                    if _inside(x, y, 0, 0, w, h, radius):
                        outer += 1
                        if not bw or _inside(x, y, bw, bw, w - bw, h - bw, max(0.0, radius - bw)):
                            inner += 1
            total = ss * ss
            if not outer:
                row += [0, 0, 0, 0]
                continue
            a_in, a_out = inner / total, outer / total
            edge = a_out - a_in
            r = (fr * a_in + br * edge) / a_out
            g = (fg_ * a_in + bg_ * edge) / a_out
            b = (fb * a_in + bb * edge) / a_out
            row += [round(r), round(g), round(b), round(a_out * 255)]
        rows.append(row)
    img = tk.PhotoImage(data=base64.b64encode(_png_bytes(w, h, rows)))
    _IMAGES.append(img)
    _PHOTO_CACHE[key] = img
    return img


def chevron_photo(color, size=14, ss=3):
    """Kleiner Pfeil nach unten für Auswahlfelder."""
    cr, cg, cb = _rgb(color)
    pts = ((size * .28, size * .40), (size * .5, size * .62), (size * .72, size * .40))
    rows = []

    def dist(px, py, a, b):
        ax, ay = a
        bx, by = b
        dx, dy = bx - ax, by - ay
        t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
        return ((px - ax - t * dx) ** 2 + (py - ay - t * dy) ** 2) ** .5
    for py in range(size):
        row = []
        for px in range(size):
            hits = 0
            for sy in range(ss):
                for sx in range(ss):
                    x, y = px + (sx + .5) / ss, py + (sy + .5) / ss
                    if min(dist(x, y, pts[0], pts[1]), dist(x, y, pts[1], pts[2])) <= 1.0:
                        hits += 1
            row += [cr, cg, cb, round(hits / (ss * ss) * 255)]
        rows.append(row)
    img = tk.PhotoImage(data=base64.b64encode(_png_bytes(size, size, rows)))
    _IMAGES.append(img)
    return img


def _round_button(style, name, bg, fg, hover, pressed, border, font, padding, radius=10, anchor=None):
    element = f'{name}.rounded'
    normal = rounded_photo(bg, border, radius)
    active = rounded_photo(hover, blend(hover, '#ffffff', .12) if border != bg else hover, radius)
    down = rounded_photo(pressed, pressed, radius)
    focus = rounded_photo(bg, C.ACCENT_SOFT, radius, 1.6)
    disabled = rounded_photo(blend(bg, C.BG, .55), None, radius)
    style.element_create(element, 'image', normal, ('disabled', disabled), ('pressed', '!disabled', down),
                         ('active', '!disabled', active), ('focus', '!disabled', focus),
                         border=radius + 1, padding=(4, 2), sticky='nsew')
    style.layout(name, [(element, {'sticky': 'nsew', 'children': [
        ('Button.padding', {'sticky': 'nsew', 'children': [('Button.label', {'sticky': 'nsew'})]})]})])
    style.configure(name, background=bg, foreground=fg, font=font, padding=padding, relief='flat')
    style.map(name, foreground=[('disabled', C.DIM)], background=[('active', hover), ('pressed', pressed)])
    if anchor:
        style.configure(name, anchor=anchor)


def apply_theme(root):
    _PHOTO_CACHE.clear()  # Bilder gehören zu genau einem Tk-Interpreter
    root.configure(bg=C.BG)
    style = ttk.Style(root)
    style.theme_use('clam')
    base = dict(background=C.BG, foreground=C.TEXT, font=F(10))
    style.configure('.', **base, bordercolor=C.BORDER, lightcolor=C.BORDER, darkcolor=C.BORDER,
                    troughcolor=C.SURFACE, focuscolor=C.ACCENT_SOFT, selectbackground=C.ACCENT,
                    selectforeground='#ffffff', insertcolor=C.TEXT, fieldbackground=C.SURFACE2)
    for name, bg in (('TFrame', C.BG), ('Surface.TFrame', C.SURFACE), ('Card.TFrame', C.CARD),
                     ('Sidebar.TFrame', C.SIDEBAR)):
        style.configure(name, background=bg)
    for name, bg, fg, font in (
            ('TLabel', C.BG, C.TEXT, F(10)), ('Muted.TLabel', C.BG, C.MUTED, F(9)),
            ('Title.TLabel', C.BG, C.TEXT, F(20, 'bold')), ('H2.TLabel', C.BG, C.TEXT, F(13, 'bold')),
            ('Eyebrow.TLabel', C.BG, C.ACCENT_SOFT, F(8, 'bold')),
            ('Surface.TLabel', C.SURFACE, C.TEXT, F(10)), ('SurfaceMuted.TLabel', C.SURFACE, C.MUTED, F(9)),
            ('SurfaceH.TLabel', C.SURFACE, C.TEXT, F(11, 'bold')),
            ('SurfaceEyebrow.TLabel', C.SURFACE, C.ACCENT_SOFT, F(8, 'bold')),
            ('Month.TLabel', C.BG, C.TEXT, F(18, 'bold'))):
        style.configure(name, background=bg, foreground=fg, font=font)

    def button(name, bg, fg, hover, pressed, border=None, font=F(10), padding=(12, 5), anchor=None):
        _round_button(style, name, bg, fg, hover, pressed, border or bg, font, padding, anchor=anchor)

    button('TButton', C.SURFACE2, C.TEXT, C.CARD_HOVER, C.CARD, C.BORDER)
    button('Accent.TButton', C.ACCENT, '#ffffff', C.ACCENT_HOVER, C.ACCENT_DARK, font=F(10, 'bold'))
    button('Danger.TButton', blend(C.DANGER, C.BG, .7), C.DANGER, blend(C.DANGER, C.BG, .55),
           blend(C.DANGER, C.BG, .45), blend(C.DANGER, C.BG, .4))
    button('Icon.TButton', C.SURFACE2, C.TEXT, C.CARD_HOVER, C.CARD, C.BORDER, F(12, 'bold'), (8, 2))
    button('Nav.TButton', C.SIDEBAR, C.MUTED, C.SURFACE2, C.CARD, C.SIDEBAR, F(10), (10, 7), anchor='w')
    button('NavActive.TButton', blend(C.ACCENT, C.SIDEBAR, .78), '#ffffff', blend(C.ACCENT, C.SIDEBAR, .7),
           blend(C.ACCENT, C.SIDEBAR, .6), blend(C.ACCENT, C.SIDEBAR, .35), F(10, 'bold'), (10, 7), anchor='w')
    button('Toggle.TButton', C.SURFACE2, C.MUTED, C.CARD_HOVER, C.CARD, C.BORDER, F(9, 'bold'), (10, 5))
    button('ToggleOn.TButton', blend(C.ACCENT, C.SURFACE2, .6), '#ffffff', blend(C.ACCENT, C.SURFACE2, .5),
           C.ACCENT_DARK, C.ACCENT, F(9, 'bold'), (10, 5))
    button('Link.TButton', C.SURFACE, C.ACCENT_SOFT, C.SURFACE2, C.CARD, C.SURFACE, F(9, 'bold'), (6, 2))

    for name in ('TEntry', 'TSpinbox', 'TCombobox'):
        style.configure(name, fieldbackground=C.SURFACE2, background=C.SURFACE2, foreground=C.TEXT,
                        bordercolor=C.BORDER, lightcolor=C.SURFACE2, darkcolor=C.SURFACE2, arrowcolor=C.MUTED,
                        insertcolor=C.TEXT, padding=6, selectbackground=C.ACCENT, selectforeground='#ffffff')
        style.map(name, bordercolor=[('focus', C.ACCENT), ('hover', C.ACCENT_SOFT)],
                  lightcolor=[('focus', C.ACCENT)], fieldbackground=[('readonly', C.SURFACE2), ('disabled', C.SURFACE)],
                  foreground=[('disabled', C.DIM), ('readonly', C.TEXT)],
                  selectbackground=[('readonly', C.SURFACE2)], selectforeground=[('readonly', C.TEXT)],
                  arrowcolor=[('hover', C.GLOW)], background=[('active', C.CARD_HOVER), ('readonly', C.SURFACE2)])
    # Abgerundete Eingabefelder (Entry, Combobox, Spinbox)
    field_n = rounded_photo(C.SURFACE2, C.BORDER, 9)
    field_h = rounded_photo(C.SURFACE2, C.ACCENT_SOFT, 9)
    field_f = rounded_photo(C.SURFACE2, C.ACCENT, 9, 1.6)
    field_d = rounded_photo(C.SURFACE, C.BORDER_SOFT, 9)
    style.element_create('Rounded.field', 'image', field_n, ('disabled', field_d), ('focus', field_f),
                         ('hover', field_h), border=10, padding=(4, 2), sticky='nsew')
    arrow_n, arrow_h = chevron_photo(C.MUTED), chevron_photo(C.GLOW)
    style.element_create('Rounded.downarrow', 'image', arrow_n, ('hover', arrow_h), ('active', arrow_h),
                         sticky='', padding=(0, 0, 8, 0))
    style.layout('TEntry', [('Rounded.field', {'sticky': 'nswe', 'children': [
        ('Entry.padding', {'sticky': 'nswe', 'children': [('Entry.textarea', {'sticky': 'nswe'})]})]})])
    style.layout('TCombobox', [('Rounded.field', {'sticky': 'nswe', 'children': [
        ('Rounded.downarrow', {'side': 'right', 'sticky': 'ns'}),
        ('Combobox.padding', {'sticky': 'nswe', 'children': [('Combobox.textarea', {'sticky': 'nswe'})]})]})])
    style.layout('TSpinbox', [('Rounded.field', {'side': 'top', 'sticky': 'we', 'children': [
        ('null', {'side': 'right', 'sticky': '', 'children': [('Spinbox.uparrow', {'side': 'top', 'sticky': 'e'}),
                                                              ('Spinbox.downarrow', {'side': 'bottom', 'sticky': 'e'})]}),
        ('Spinbox.padding', {'sticky': 'nswe', 'children': [('Spinbox.textarea', {'sticky': 'nswe'})]})]})])
    for name in ('TEntry', 'TCombobox', 'TSpinbox'):
        style.configure(name, padding=(8, 4, 4, 4))
    style.configure('TSpinbox', arrowsize=10, background=C.SURFACE2, bordercolor=C.SURFACE2,
                    lightcolor=C.SURFACE2, darkcolor=C.SURFACE2)
    root.option_add('*TCombobox*Listbox.background', C.SURFACE2)
    root.option_add('*TCombobox*Listbox.foreground', C.TEXT)
    root.option_add('*TCombobox*Listbox.selectBackground', C.ACCENT)
    root.option_add('*TCombobox*Listbox.selectForeground', '#ffffff')
    root.option_add('*TCombobox*Listbox.font', F(10))
    root.option_add('*TCombobox*Listbox.borderWidth', 0)

    for name, bg in (('TCheckbutton', C.BG), ('Surface.TCheckbutton', C.SURFACE)):
        style.configure(name, background=bg, foreground=C.TEXT, indicatorbackground=C.SURFACE2,
                        indicatorforeground='#ffffff', indicatormargin=(0, 0, 8, 0), focuscolor=C.ACCENT_SOFT)
        style.map(name, background=[('active', bg)], indicatorbackground=[('selected', C.ACCENT), ('active', C.CARD_HOVER)])

    for orient in ('Vertical', 'Horizontal'):
        style.configure(f'{orient}.TScrollbar', background=C.CARD, troughcolor=C.SURFACE, bordercolor=C.SURFACE,
                        lightcolor=C.CARD, darkcolor=C.CARD, arrowcolor=C.MUTED, gripcount=0, arrowsize=12)
        style.map(f'{orient}.TScrollbar', background=[('active', C.ACCENT_DARK), ('pressed', C.ACCENT)])

    style.configure('Treeview', background=C.SURFACE, fieldbackground=C.SURFACE, foreground=C.TEXT, rowheight=34,
                    bordercolor=C.BORDER, lightcolor=C.SURFACE, darkcolor=C.SURFACE, font=F(10))
    style.map('Treeview', background=[('selected', blend(C.ACCENT, C.SURFACE, .45))],
              foreground=[('selected', '#ffffff')])
    style.configure('Treeview.Heading', background=C.SURFACE2, foreground=C.MUTED, relief='flat', font=F(9, 'bold'),
                    bordercolor=C.BORDER, lightcolor=C.SURFACE2, darkcolor=C.SURFACE2, padding=(8, 7))
    style.map('Treeview.Heading', background=[('active', C.CARD)])
    style.layout('Treeview', [('Treeview.treearea', {'sticky': 'nswe'})])
    return style


def enable_windows_polish(root):
    """Scharfe Schrift (DPI) und dunkle Titelleiste unter Windows; anderswo ohne Wirkung."""
    if sys.platform != 'win32':
        return
    try:
        import ctypes
        root.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(root.winfo_id())
        value = ctypes.c_int(1)
        for attribute in (20, 19):  # DWMWA_USE_IMMERSIVE_DARK_MODE (neu/alt)
            if ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, attribute, ctypes.byref(value),
                                                          ctypes.sizeof(value)) == 0:
                break
    except Exception:
        pass


def set_dpi_awareness():
    if sys.platform == 'win32':
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Bausteine
# ---------------------------------------------------------------------------

class RoundedCard(tk.Canvas):
    """Karte mit abgerundeten Ecken; Inhalt kommt in `.inner` (ein normaler Frame)."""

    def __init__(self, parent, fill=C.CARD, border=None, radius=12, padx=12, pady=10, border_width=1,
                 outer_bg=None, fill_height=False, fit_width=False):
        super().__init__(parent, bg=outer_bg or parent.cget('bg'), highlightthickness=0, bd=0, height=pady * 2 + 4)
        self.fill, self.border, self.radius, self.border_width = fill, border, radius, border_width
        self.fill_height = fill_height
        self.fit_width = fit_width
        self.padx, self.pady = padx, pady
        self.inner = tk.Frame(self, bg=fill)
        self._win = self.create_window(padx, pady, window=self.inner, anchor='nw')
        self.bind('<Configure>', self._redraw)
        self.inner.bind('<Configure>', self._fit_height)

    def _fit_height(self, _event=None):
        if self.fit_width:
            width = self.inner.winfo_reqwidth() + 2 * self.padx
            if int(self.cget('width')) != width:
                self.configure(width=width)
        if self.fill_height:
            return
        height = self.inner.winfo_reqheight() + 2 * self.pady
        if int(self.cget('height')) != height:
            self.configure(height=height)

    def _redraw(self, event=None):
        w = event.width if event else self.winfo_width()
        h = event.height if event else self.winfo_height()
        self.delete('card')
        round_rect(self, 1, 1, w - 1, h - 1, self.radius, fill=self.fill, outline=self.border or self.fill,
                   width=self.border_width, tags='card')
        self.tag_lower('card')
        if not self.fit_width:
            self.itemconfigure(self._win, width=max(10, w - 2 * self.padx))
        if self.fill_height:
            self.itemconfigure(self._win, height=max(10, h - 2 * self.pady))

    def set_colors(self, fill=None, border=None):
        if fill:
            self.fill = fill
            self.inner.configure(bg=fill)
            for child in self.inner.winfo_children():
                _set_bg_recursive(child, fill)
        if border:
            self.border = border
        self._redraw()


def _set_bg_recursive(widget, color):
    try:
        if not isinstance(widget, (ttk.Widget,)) and not isinstance(widget, tk.Canvas):
            widget.configure(bg=color)
    except tk.TclError:
        pass
    for child in widget.winfo_children():
        _set_bg_recursive(child, color)


class Tooltip:
    """Ein wiederverwendbares, dunkles Tooltip-Fenster."""

    def __init__(self, root):
        self.root = root
        self.win = None
        self.job = None

    def schedule(self, x, y, text, delay=380):
        self.cancel()
        if text:
            self.job = self.root.after(delay, lambda: self.show(x, y, text))

    def cancel(self):
        if self.job:
            self.root.after_cancel(self.job)
            self.job = None

    def show(self, x, y, text):
        self.hide()
        self.win = tk.Toplevel(self.root)
        self.win.wm_overrideredirect(True)
        try:
            self.win.attributes('-topmost', True)
        except tk.TclError:
            pass
        outer = tk.Frame(self.win, bg=C.ACCENT_DARK, padx=1, pady=1)
        outer.pack()
        tk.Label(outer, text=text, bg=C.SURFACE2, fg=C.TEXT, font=F(9), justify='left', padx=10, pady=8,
                 wraplength=340).pack()
        self.win.update_idletasks()
        w, h = self.win.winfo_width(), self.win.winfo_height()
        sw, sh = self.win.winfo_screenwidth(), self.win.winfo_screenheight()
        px = min(x + 16, sw - w - 8)
        py = y + 18 if y + 18 + h < sh else y - h - 10
        self.win.geometry(f'+{px}+{py}')

    def hide(self):
        self.cancel()
        if self.win is not None:
            self.win.destroy()
            self.win = None


def attach_tooltip(widget, tip, text_fn):
    widget.bind('<Enter>', lambda e: tip.schedule(e.x_root, e.y_root, text_fn() if callable(text_fn) else text_fn),
                add='+')
    widget.bind('<Leave>', lambda _e: tip.hide(), add='+')
    widget.bind('<ButtonPress>', lambda _e: tip.hide(), add='+')


class ScrollFrame(ttk.Frame):
    """Vertikal scrollbarer Bereich; das Mausrad wirkt nur, solange der Zeiger darüber ist."""

    def __init__(self, parent, bg=C.BG, **kw):
        super().__init__(parent, **kw)
        self.canvas = tk.Canvas(self, bg=bg, highlightthickness=0, bd=0)
        self.bar = ttk.Scrollbar(self, orient='vertical', command=self.canvas.yview)
        self.inner = tk.Frame(self.canvas, bg=bg)
        self._win = self.canvas.create_window((0, 0), window=self.inner, anchor='nw')
        self.canvas.configure(yscrollcommand=self.bar.set)
        self.canvas.pack(side='left', fill='both', expand=True)
        self.bar.pack(side='right', fill='y')
        self.inner.bind('<Configure>', lambda _e: self.canvas.configure(scrollregion=self.canvas.bbox('all')))
        self.canvas.bind('<Configure>', lambda e: self.canvas.itemconfigure(self._win, width=e.width))
        bind_mousewheel(self, self.canvas)

    def clear(self):
        for child in self.inner.winfo_children():
            child.destroy()
        self.canvas.yview_moveto(0)


def bind_mousewheel(area, canvas):
    def on_wheel(event):
        if event.num == 4:
            delta = -1
        elif event.num == 5:
            delta = 1
        else:
            delta = -1 if event.delta > 0 else 1
            if abs(event.delta) >= 240:
                delta *= abs(event.delta) // 120
        first, last = canvas.yview()
        if first <= 0 and last >= 1:
            return
        canvas.yview_scroll(int(delta) * 2, 'units')

    def enter(_e):
        area.bind_all('<MouseWheel>', on_wheel)
        area.bind_all('<Button-4>', on_wheel)
        area.bind_all('<Button-5>', on_wheel)

    def leave(_e):
        area.unbind_all('<MouseWheel>')
        area.unbind_all('<Button-4>')
        area.unbind_all('<Button-5>')

    area.bind('<Enter>', enter, add='+')
    area.bind('<Leave>', leave, add='+')


_NOTHING = object()


class Segmented(tk.Canvas):
    """Abgerundete segmentierte Auswahl (z. B. Frei | Früh | Spät | Nacht).

    Maus: Klick wählt. Tastatur: Tab fokussiert, Pfeil links/rechts wechselt, Fokus mit Leuchtrand."""

    def __init__(self, parent, options, value, on_change=None, bg=None, colors=None, pad=3):
        super().__init__(parent, bg=bg or parent.cget('bg'), highlightthickness=0, bd=0, takefocus=1,
                         cursor='hand2')
        self.options, self.value, self.on_change = options, value, on_change
        self.colors = colors or {}
        self.pad = pad
        self.hover = _NOTHING  # eigener Marker, weil None ein gültiger Wert ist („Frei“)
        self.focused = False
        self.font = tkfont.Font(font=F(9, 'bold'))
        self.widths = [self.font.measure(text) + 22 for _v, text in options]
        self.height_px = self.font.metrics('linespace') + 12 + 2 * pad
        self.configure(width=sum(self.widths) + 2 * pad, height=self.height_px)
        self.bind('<Button-1>', self._click)
        self.bind('<Motion>', self._motion)
        self.bind('<Leave>', lambda _e: self._set_hover(_NOTHING))
        self.bind('<FocusIn>', lambda _e: self._focus(True))
        self.bind('<FocusOut>', lambda _e: self._focus(False))
        self.bind('<Left>', lambda _e: self._step(-1))
        self.bind('<Right>', lambda _e: self._step(1))
        self._paint()

    def _index_at(self, x):
        pos = self.pad
        for i, w in enumerate(self.widths):
            if pos <= x < pos + w:
                return i
            pos += w
        return None

    def _click(self, event):
        i = self._index_at(event.x)
        if i is not None:
            self.focus_set()
            self.set(self.options[i][0], True)

    def _motion(self, event):
        i = self._index_at(event.x)
        self._set_hover(self.options[i][0] if i is not None else _NOTHING)

    def _set_hover(self, value):
        if value != self.hover:
            self.hover = value
            self._paint()

    def _focus(self, state):
        self.focused = state
        self._paint()

    def _step(self, delta):
        values = [v for v, _t in self.options]
        i = values.index(self.value) if self.value in values else 0
        self.set(values[(i + delta) % len(values)], True)

    def set(self, value, notify=False):
        self.value = value
        self._paint()
        if notify and self.on_change:
            self.on_change(value)

    def _paint(self):
        self.delete('all')
        w, h = sum(self.widths) + 2 * self.pad, self.height_px
        round_rect(self, 1, 1, w - 1, h - 1, h / 2, fill=C.SURFACE2,
                   outline=C.ACCENT_SOFT if self.focused else C.BORDER, width=1)
        x = self.pad
        for (val, text), width in zip(self.options, self.widths):
            selected, hovered = val == self.value, val == self.hover
            if selected:
                color = self.colors.get(val, C.ACCENT) if val is not None else '#3b4070'
                round_rect(self, x + 1, self.pad + 1, x + width - 1, h - self.pad - 1, (h - 2 * self.pad) / 2,
                           fill=color, outline=blend(color, '#ffffff', .2))
            elif hovered:
                round_rect(self, x + 1, self.pad + 1, x + width - 1, h - self.pad - 1, (h - 2 * self.pad) / 2,
                           fill=C.CARD_HOVER, outline='')
            self.create_text(x + width / 2, h / 2, text=text, font=F(9, 'bold'),
                             fill='#ffffff' if selected else (C.TEXT if hovered else C.MUTED))
            x += width


class DatePicker(tk.Toplevel):
    """Dunkler Monatskalender als Popup – vermeidet fehleranfällige Freitext-Daten."""

    def __init__(self, parent, value, on_pick, anchor=None):
        super().__init__(parent)
        self.withdraw()
        self.overrideredirect(True)
        self.on_pick = on_pick
        self.value = value or date.today()
        self.month = self.value.replace(day=1)
        self.configure(bg=C.ACCENT_DARK)
        self.body = tk.Frame(self, bg=C.SURFACE, padx=10, pady=10)
        self.body.pack(padx=1, pady=1)
        self.render()
        self.update_idletasks()
        if anchor is not None:
            x, y = anchor.winfo_rootx(), anchor.winfo_rooty() + anchor.winfo_height() + 4
            sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
            x = min(x, sw - self.winfo_reqwidth() - 8)
            if y + self.winfo_reqheight() > sh:
                y = anchor.winfo_rooty() - self.winfo_reqheight() - 4
            self.geometry(f'+{x}+{y}')
        self.deiconify()
        self.lift()
        self.bind('<Escape>', lambda _e: self.destroy())
        self.focus_force()
        try:
            self.grab_set()
        except tk.TclError:
            pass
        self.bind('<Button-1>', self._click_outside, add='+')

    def _click_outside(self, event):
        x, y = event.x_root, event.y_root
        if not (self.winfo_rootx() <= x <= self.winfo_rootx() + self.winfo_width() and
                self.winfo_rooty() <= y <= self.winfo_rooty() + self.winfo_height()):
            self.destroy()

    def render(self):
        for child in self.body.winfo_children():
            child.destroy()
        head = tk.Frame(self.body, bg=C.SURFACE)
        head.grid(row=0, column=0, columnspan=7, sticky='ew', pady=(0, 6))
        for text, off, side in (('‹', -1, 'left'), ('›', 1, 'right')):
            b = tk.Label(head, text=text, bg=C.SURFACE2, fg=C.TEXT, font=F(12, 'bold'), padx=9, cursor='hand2')
            b.pack(side=side)
            b.bind('<Button-1>', lambda _e, o=off: self.step(o))
        tk.Label(head, text=f'{MONTHS[self.month.month - 1]} {self.month.year}', bg=C.SURFACE, fg=C.TEXT,
                 font=F(10, 'bold')).pack(side='left', expand=True)
        for col, name in enumerate(WEEKDAYS):
            tk.Label(self.body, text=name, bg=C.SURFACE, fg=C.DIM if col < 5 else C.ACCENT_SOFT,
                     font=F(8, 'bold'), width=4).grid(row=1, column=col)
        today = date.today()
        weeks = calendar.Calendar(0).monthdatescalendar(self.month.year, self.month.month)
        for r, week in enumerate(weeks, start=2):
            for col, d in enumerate(week):
                inside = d.month == self.month.month
                bg = C.WEEKEND if is_weekend(d) else C.SURFACE2
                if holiday_name(d):
                    bg = C.HOLIDAY
                fg = C.TEXT if inside else C.DIM
                if d == self.value:
                    bg, fg = C.ACCENT, '#ffffff'
                lbl = tk.Label(self.body, text=str(d.day), bg=bg, fg=fg, font=F(9, 'bold' if d == today else 'normal'),
                               width=4, pady=4, cursor='hand2', highlightthickness=1,
                               highlightbackground=C.ACCENT_SOFT if d == today else bg)
                lbl.grid(row=r, column=col, padx=1, pady=1)
                lbl.bind('<Button-1>', lambda _e, day=d: self.pick(day))
                lbl.bind('<Enter>', lambda _e, w=lbl, day=d: day != self.value and w.configure(bg=C.CARD_HOVER))
                lbl.bind('<Leave>', lambda _e, w=lbl, b=bg, day=d: day != self.value and w.configure(bg=b))
        foot = tk.Label(self.body, text='Heute', bg=C.SURFACE, fg=C.ACCENT_SOFT, font=F(9, 'bold'), cursor='hand2')
        foot.grid(row=9, column=0, columnspan=7, pady=(6, 0))
        foot.bind('<Button-1>', lambda _e: self.pick(today))

    def step(self, offset):
        self.month = add_months(self.month, offset)
        self.render()

    def pick(self, day):
        self.on_pick(day)
        self.destroy()


class DateField(tk.Canvas):
    """Abgerundetes Datumsfeld mit Kalender-Popup; Wert als `date` über .get()."""

    def __init__(self, parent, value=None, bg=None, on_change=None, allow_empty=False, width=200):
        super().__init__(parent, bg=bg or parent.cget('bg'), highlightthickness=0, bd=0, width=width, height=36,
                         takefocus=1, cursor='hand2')
        self.value = value if value or allow_empty else date.today()
        self.on_change = on_change
        self.hover = self.focused = False
        self.bind('<Configure>', lambda _e: self._paint())
        self.bind('<Button-1>', lambda _e: self.open())
        self.bind('<Return>', lambda _e: self.open())
        self.bind('<space>', lambda _e: self.open())
        self.bind('<Enter>', lambda _e: self._state(hover=True))
        self.bind('<Leave>', lambda _e: self._state(hover=False))
        self.bind('<FocusIn>', lambda _e: self._state(focused=True))
        self.bind('<FocusOut>', lambda _e: self._state(focused=False))
        self._paint()

    def _state(self, hover=None, focused=None):
        if hover is not None:
            self.hover = hover
        if focused is not None:
            self.focused = focused
        self._paint()

    def _paint(self):
        self.delete('all')
        w = max(int(self.cget('width')), self.winfo_width() if self.winfo_width() > 1 else 0)
        h = int(self.cget('height'))
        border = C.ACCENT if self.focused else (C.ACCENT_SOFT if self.hover else C.BORDER)
        round_rect(self, 1, 1, w - 1, h - 1, 9, fill=C.SURFACE2, outline=border, width=1.6 if self.focused else 1)
        round_rect(self, w - 38, 5, w - 6, h - 5, 7, fill=C.ACCENT_DARK if self.hover else C.CARD, outline='')
        self.create_text(w - 22, h / 2, text='▦', font=F(11), fill='#ffffff' if self.hover else C.ACCENT_SOFT)
        text = f'{WEEKDAYS[self.value.weekday()]}, {self.value:%d.%m.%Y}' if self.value else 'Datum wählen …'
        self.create_text(12, h / 2, anchor='w', text=text, font=F(10), fill=C.TEXT if self.value else C.DIM)

    def open(self):
        DatePicker(self.winfo_toplevel(), self.value, self.set, anchor=self)

    def set(self, value):
        self.value = value
        self._paint()
        if self.on_change:
            self.on_change(value)

    def get(self):
        return self.value


class Dialog(tk.Toplevel):
    """Dunkler, modaler Dialog mit optionaler farbiger Detailliste.

    lines: [(status, text)] mit status in ok|warn|block|info|muted|head."""

    ICONS = {'ok': '✓', 'warn': '!', 'block': '✕', 'info': '•', 'muted': '·', 'head': ''}

    def __init__(self, parent, title, heading, body='', lines=None, buttons=None, tone='info', width=560):
        super().__init__(parent)
        self.withdraw()
        self.title(title)
        self.configure(bg=C.SURFACE)
        self.transient(parent.winfo_toplevel())
        self.resizable(True, True)
        self.result = None
        tone_color = {'info': C.ACCENT, 'warn': C.WARN, 'danger': C.DANGER, 'ok': C.OK}.get(tone, C.ACCENT)
        tk.Frame(self, bg=tone_color, height=3).pack(fill='x')
        wrap = tk.Frame(self, bg=C.SURFACE, padx=22, pady=18)
        wrap.pack(fill='both', expand=True)
        tk.Label(wrap, text=heading, bg=C.SURFACE, fg=C.TEXT, font=F(13, 'bold'), justify='left', anchor='w',
                 wraplength=width - 50).pack(fill='x')
        if body:
            tk.Label(wrap, text=body, bg=C.SURFACE, fg=C.MUTED, font=F(10), justify='left', anchor='w',
                     wraplength=width - 50).pack(fill='x', pady=(6, 0))
        if lines:
            box = tk.Frame(wrap, bg=C.BORDER, padx=1, pady=1)
            box.pack(fill='both', expand=True, pady=(12, 0))
            text = tk.Text(box, bg=C.SURFACE2, fg=C.TEXT, font=F(9), wrap='word', relief='flat', padx=12, pady=10,
                           height=min(18, max(3, len(lines) + 3)), width=60, highlightthickness=0, cursor='arrow')
            bar = ttk.Scrollbar(box, orient='vertical', command=text.yview)
            text.configure(yscrollcommand=bar.set)
            for status, color in (('ok', C.OK), ('warn', C.WARN), ('block', C.DANGER), ('info', C.TEXT),
                                  ('muted', C.MUTED), ('head', C.ACCENT_SOFT)):
                text.tag_configure(status, foreground=color, lmargin2=18, spacing1=2, spacing3=2)
            text.tag_configure('head', font=F(9, 'bold'), spacing1=8)
            for status, line in lines:
                icon = self.ICONS.get(status, '•')
                text.insert('end', (f'{icon}  ' if icon else '') + line + '\n', status)
            text.configure(state='disabled')
            text.pack(side='left', fill='both', expand=True)
            if len(lines) > 15:
                bar.pack(side='right', fill='y')
        row = tk.Frame(wrap, bg=C.SURFACE)
        row.pack(fill='x', pady=(18, 0))
        buttons = buttons or [('OK', True, 'Accent.TButton')]
        first = None
        for text_, value, style in reversed(buttons):
            b = ttk.Button(row, text=text_, style=style, command=lambda v=value: self.close(v))
            b.pack(side='right', padx=(8, 0))
            first = first or b
        self.bind('<Escape>', lambda _e: self.close(None))
        self.protocol('WM_DELETE_WINDOW', lambda: self.close(None))
        self.update_idletasks()
        w = max(width, self.winfo_reqwidth())
        h = self.winfo_reqheight()
        p = parent.winfo_toplevel()
        x = p.winfo_rootx() + max(0, (p.winfo_width() - w) // 2)
        y = p.winfo_rooty() + max(0, (p.winfo_height() - h) // 3)
        self.geometry(f'{w}x{h}+{x}+{y}')
        self.deiconify()
        try:
            self.grab_set()
        except tk.TclError:
            pass
        if first:
            first.focus_set()
        enable_windows_polish(self)
        self.wait_window()

    def close(self, value):
        self.result = value
        self.destroy()


def ask(parent, title, heading, body='', lines=None, ok='Bestätigen', cancel='Abbrechen', tone='info',
        ok_style='Accent.TButton'):
    return bool(Dialog(parent, title, heading, body, lines, [(cancel, False, 'TButton'), (ok, True, ok_style)],
                       tone).result)


def info(parent, title, heading, body='', lines=None, tone='info'):
    Dialog(parent, title, heading, body, lines, [('OK', True, 'Accent.TButton')], tone)


def choose(parent, title, heading, body, buttons, lines=None, tone='info'):
    return Dialog(parent, title, heading, body, lines, buttons, tone).result
