"""Straty Kuchnia — приложение менеджера (Windows, Flet).

База лежит рядом с программой и является единственным источником правды.
Сервер на дроплете — почтовый ящик: повара кладут зашлушения через QR,
приложение их забирает, менеджер подтверждает, и страты попадают в журнал.
Ровно та же схема, что в Beer Count, только без беччек и рецептур.

Четыре раздела:
    Podsumowanie — сколько чего ушло за месяц (главный ответ приложения)
    Straty       — журнал: ручной ввод, правка, фильтры
    Zgłoszenia   — очередь с сервера, подтверждение
    Ustawienia   — позиции, причины, связь с сервером

Запуск:  python main_kuchnia.py
"""

import os
import sys
import traceback
from datetime import date, datetime, timedelta

import flet as ft

import db
import export_xlsx
import sync

APP_VERSION = "1.0.0"
ACCENT = "#D9952E"                       # то же золото, что в Beer Count
OK_COLOR = "#34D399"
BAD_COLOR = "#F87171"                    # цвета состояний — как у формы повара
WARN_COLOR = "#FBBF24"

UNIT_OPTIONS = ["szt", "kg", "g", "l", "ml"]


def resource_path(name: str) -> str:
    """Путь к файлу рядом с программой — работает и из .exe (PyInstaller)."""
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, name)


def data_dir() -> str:
    """Где лежит база. Рядом с exe, а не во временной папке PyInstaller —
    иначе после обновления программы данные бы «исчезали»."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


class KuchniaApp:
    # ─────────────────────────── старт ───────────────────────────
    def __init__(self, page: ft.Page):
        self.page = page
        if not os.environ.get("KUCHNIA_DB"):
            db.DB_PATH = os.path.join(data_dir(), "kuchnia.db")
        self.c = db.init_db()
        self.cfg = db.get_config(self.c)
        self.first_run = not db.list_items(self.c, active_only=False)
        if self.first_run:
            try:
                db.seed(self.c)
            except (OSError, ValueError):
                pass        # без стартового каталога тоже можно жить

        self.month = db.current_month()
        self.summary_tab = "items"
        self.summary_sort = "qty"
        self.journal_filters = {"category": "", "reason": "", "person": "",
                                "search": ""}
        self.reports = []                          # очередь с сервера
        self.reports_status = "Nie sprawdzono"
        self.server_ok = False                     # была ли связь удачной
        self.selected = None
        self.nav_items = {}

        page.title = "Straty Kuchnia — HTS"
        page.padding = 0
        page.window.min_width = 940
        page.window.min_height = 620
        page.window.width = 1360
        page.window.height = 880
        icon = resource_path("icon.ico")
        if os.path.exists(icon):
            page.window.icon = icon

        # Inter вшит в программу (assets/fonts, лицензия OFL рядом): Segoe UI
        # на мелких размерах выглядел тонким. Шрифт переменный — Flet сам
        # выбирает из него нужную жирность.
        page.fonts = {"Inter": "fonts/Inter.ttf"}
        # Все поверхности — оттенки синего, золото только на кнопках и
        # выбранных «таблетках». Фон окна (scaffold/canvas) задан явно:
        # иначе Flet выводит его из золотого seed и окно становится
        # коричневым (тёмная тема) или персиковым (светлая).
        page.theme = ft.Theme(
            color_scheme_seed=ACCENT, font_family="Inter",
            scaffold_bgcolor="#EEF2F8", canvas_color="#EEF2F8",
            color_scheme=ft.ColorScheme(
                surface="#EEF2F8", on_surface="#141A26",
                surface_tint="#EEF2F8", surface_dim="#E2E8F2",
                surface_bright="#FFFFFF",
                surface_container_lowest="#F7F9FC",
                surface_container_low="#FFFFFF",
                surface_container="#E6ECF5",
                surface_container_high="#E0E7F2",
                surface_container_highest="#D3DCEA",
                on_surface_variant="#56637A",
                outline="#D3DCEA", outline_variant="#D3DCEA",
                primary="#B27C20", on_primary="#FFFFFF",
                primary_container="#E0E7F2", on_primary_container="#141A26",
                secondary_container="#E0E7F2",
                on_secondary_container="#141A26"))
        # Тёмная тема — та же палитра, что у формы повара.
        # surface_tint приравнен к фону: Material 3 иначе подмешивает золото
        # в каждую «поднятую» поверхность и вся программа желтеет.
        page.dark_theme = ft.Theme(
            color_scheme_seed=ACCENT, font_family="Inter",
            scaffold_bgcolor="#12151C", canvas_color="#12151C",
            color_scheme=ft.ColorScheme(
                surface="#12151C", on_surface="#EEF2F8",
                surface_tint="#12151C", surface_dim="#12151C",
                surface_bright="#222836",
                surface_container_lowest="#12151C",
                surface_container_low="#191D27",
                surface_container="#191D27",
                surface_container_high="#222836",
                surface_container_highest="#2C3444",
                on_surface_variant="#8E9AB0",
                outline="#2C3444", outline_variant="#2C3444",
                primary=ACCENT, on_primary="#231604",
                primary_container="#222836", on_primary_container="#EEF2F8",
                secondary_container="#222836",
                on_secondary_container="#EEF2F8",
                error=BAD_COLOR, on_error="#2B0A0A",
                inverse_surface="#EDEFF2", on_inverse_surface="#171B20"))
        self.mode = self.cfg.get("theme", "dark")
        page.theme_mode = (ft.ThemeMode.DARK if self.mode == "dark"
                           else ft.ThemeMode.LIGHT)

        self.fp = ft.FilePicker()
        page.services.append(self.fp)

        self.content = ft.AnimatedSwitcher(
            content=ft.Container(), expand=True,
            transition=ft.AnimatedSwitcherTransition.FADE,
            duration=220, reverse_duration=160)

        page.add(ft.Row([self._build_sidebar(),
                         ft.Container(content=self.content, expand=True,
                                      padding=ft.Padding(22, 18, 22, 18))],
                        spacing=0, expand=True))
        self.select("summary")
        if self.cfg.get("server_token"):
            page.run_task(self.load_reports)
        if self.first_run:
            self.dialog("info", "Witaj!",
                        "Wgraliśmy startowy katalog kuchni — poprawisz go w "
                        "Ustawieniach.\n\nŻeby kucharze mogli zgłaszać straty "
                        "z telefonu, wpisz adres serwera i token w "
                        "Ustawieniach → Serwer.")

    # ─────────────────────────── каркас ───────────────────────────
    def _build_sidebar(self):
        logo = ft.Container(
            content=ft.Row([
                ft.Icon(ft.Icons.RESTAURANT_MENU, size=30, color=ACCENT),
                ft.Column([
                    ft.Text("Straty Kuchnia", size=16,
                            weight=ft.FontWeight.W_800),
                    ft.Text("Kontrola strat", size=11.5,
                            color=ft.Colors.ON_SURFACE_VARIANT),
                ], spacing=0),
            ], spacing=10),
            padding=ft.Padding(18, 22, 12, 18))

        # Три пункта, не четыре: отдельная «Straty» дублировала
        # «Podsumowanie» — журнал стал его вкладкой «Wpisy».
        items = [
            ("summary", ft.Icons.INSIGHTS, "Straty"),
            ("reports", ft.Icons.INBOX, "Zgłoszenia"),
            ("settings", ft.Icons.TUNE, "Ustawienia"),
        ]
        nav_col = ft.Column(spacing=4)
        for key, icon, label in items:
            ic = ft.Icon(icon, size=20, color=ft.Colors.ON_SURFACE_VARIANT)
            tx = ft.Text(label, size=15, weight=ft.FontWeight.W_600,
                         color=ft.Colors.ON_SURFACE_VARIANT)
            badge = ft.Container(
                visible=False, bgcolor=ACCENT, border_radius=999,
                padding=ft.Padding(7, 1, 7, 1),
                content=ft.Text("", size=12, weight=ft.FontWeight.W_700,
                                color="#231604"))
            cont = ft.Container(
                content=ft.Row([ic, tx, ft.Container(expand=True), badge],
                               spacing=12),
                padding=ft.Padding(14, 11, 14, 11), border_radius=12, ink=True,
                on_click=lambda e, k=key: self.select(k))
            self.nav_items[key] = (cont, ic, tx, badge)
            nav_col.controls.append(cont)

        self.theme_icon = ft.Icon(
            ft.Icons.LIGHT_MODE if self.mode == "dark" else ft.Icons.DARK_MODE,
            size=20, color=ft.Colors.ON_SURFACE_VARIANT)
        self.theme_label = ft.Text(
            "Tryb jasny" if self.mode == "dark" else "Tryb ciemny",
            size=15, weight=ft.FontWeight.W_600,
            color=ft.Colors.ON_SURFACE_VARIANT)
        theme_btn = ft.Container(
            content=ft.Row([self.theme_icon, self.theme_label], spacing=12),
            padding=ft.Padding(14, 11, 14, 11), border_radius=12, ink=True,
            on_click=self.toggle_theme)

        return ft.Container(
            width=232, bgcolor=ft.Colors.SURFACE_CONTAINER_LOW,
            border=ft.Border(right=ft.BorderSide(1, ft.Colors.OUTLINE_VARIANT)),
            content=ft.Column([
                logo,
                ft.Container(content=nav_col, padding=ft.Padding(10, 0, 10, 0),
                             expand=True),
                ft.Container(content=theme_btn, padding=ft.Padding(10, 0, 10, 4)),
                ft.Container(
                    content=ft.Text(f"Powered by HTS  •  v{APP_VERSION}",
                                    size=11.5,
                                    color=ft.Colors.ON_SURFACE_VARIANT),
                    padding=ft.Padding(24, 4, 14, 16)),
            ], spacing=0, expand=True))

    def select(self, key):
        self.selected = key
        for k, (cont, ic, tx, _badge) in self.nav_items.items():
            on = k == key
            cont.bgcolor = (ft.Colors.SURFACE_CONTAINER_HIGH if on else None)
            ic.color = ACCENT if on else ft.Colors.ON_SURFACE_VARIANT
            tx.color = (ft.Colors.ON_SURFACE if on
                        else ft.Colors.ON_SURFACE_VARIANT)
        builder = {"summary": self.view_summary, "reports": self.view_reports,
                   "settings": self.view_settings}[key]
        self.content.content = ft.Container(content=builder(), expand=True)
        self.page.update()

    def refresh(self):
        """Перерисовать текущий раздел — после любой правки данных."""
        if self.selected:
            self.select(self.selected)

    def toggle_theme(self, e=None):
        self.mode = "light" if self.mode == "dark" else "dark"
        db.set_config(self.c, "theme", self.mode)
        self.page.theme_mode = (ft.ThemeMode.DARK if self.mode == "dark"
                                else ft.ThemeMode.LIGHT)
        self.theme_icon.name = (ft.Icons.LIGHT_MODE if self.mode == "dark"
                                else ft.Icons.DARK_MODE)
        self.theme_label.value = ("Tryb jasny" if self.mode == "dark"
                                  else "Tryb ciemny")
        self.page.update()

    # ─────────────────────────── мелкие детали ───────────────────────────
    def card(self, title, icon, body, subtitle=None, trailing=None,
             expand=None):
        hdr = [ft.Icon(icon, size=18, color=ACCENT),
               ft.Text(title, size=16, weight=ft.FontWeight.W_700)]
        if subtitle:
            hdr.append(ft.Text(subtitle, size=12,
                               color=ft.Colors.ON_SURFACE_VARIANT))
        top = ft.Row([ft.Row(hdr, spacing=8, expand=True)]
                     + ([trailing] if trailing else []),
                     alignment=ft.MainAxisAlignment.SPACE_BETWEEN)
        return ft.Container(
            content=ft.Column([top, body], spacing=12, expand=expand),
            bgcolor=ft.Colors.SURFACE_CONTAINER_LOW,
            border=ft.Border.all(1, ft.Colors.OUTLINE_VARIANT),
            border_radius=18, padding=ft.Padding(18, 16, 18, 16), expand=expand)

    def kpi(self, label, value, color=None):
        return ft.Container(
            content=ft.Column([
                ft.Text(str(value), size=26, weight=ft.FontWeight.W_800,
                        color=color),
                ft.Text(label, size=12.5, color=ft.Colors.ON_SURFACE_VARIANT),
            ], spacing=2),
            bgcolor=ft.Colors.SURFACE_CONTAINER_LOW,
            border=ft.Border.all(1, ft.Colors.OUTLINE_VARIANT),
            border_radius=16, padding=ft.Padding(18, 14, 18, 14), expand=True)

    def dialog(self, kind, title, message):
        icons = {"info": (ft.Icons.INFO, ACCENT),
                 "success": (ft.Icons.CHECK_CIRCLE, OK_COLOR),
                 "warning": (ft.Icons.WARNING_AMBER, WARN_COLOR),
                 "error": (ft.Icons.ERROR, BAD_COLOR)}
        ic, color = icons.get(kind, icons["info"])
        dlg = ft.AlertDialog(
            icon=ft.Icon(ic, color=color, size=34),
            title=ft.Text(title, text_align=ft.TextAlign.CENTER,
                          weight=ft.FontWeight.W_700, size=16),
            content=ft.Text(message, size=15),
            actions=[ft.FilledButton(
                "OK", on_click=lambda e: self.page.pop_dialog())],
            actions_alignment=ft.MainAxisAlignment.CENTER,
            shape=ft.RoundedRectangleBorder(radius=18))
        self.page.show_dialog(dlg)

    def confirm(self, title, message, on_yes, yes_text="Tak"):
        def yes(e):
            self.page.pop_dialog()
            on_yes()

        dlg = ft.AlertDialog(
            icon=ft.Icon(ft.Icons.HELP_OUTLINE, color=ACCENT, size=34),
            title=ft.Text(title, text_align=ft.TextAlign.CENTER,
                          weight=ft.FontWeight.W_700, size=16),
            content=ft.Text(message, size=15),
            actions=[ft.TextButton("Anuluj",
                                   on_click=lambda e: self.page.pop_dialog()),
                     ft.FilledButton(yes_text, on_click=yes)],
            actions_alignment=ft.MainAxisAlignment.CENTER,
            shape=ft.RoundedRectangleBorder(radius=18))
        self.page.show_dialog(dlg)

    def toast(self, text, good=True):
        self.page.show_dialog(ft.SnackBar(
            content=ft.Text(text, size=15),
            bgcolor=(ft.Colors.SURFACE_CONTAINER_HIGHEST if good
                     else BAD_COLOR)))

    def field(self, label, value="", width=None, on_change=None, hint=None,
              multiline=False, password=False):
        return ft.TextField(
            label=label, value=value, width=width, dense=True, text_size=15,
            hint_text=hint, multiline=multiline, password=password,
            content_padding=ft.Padding(12, 12, 12, 12), border_radius=14,
            border_color=ft.Colors.OUTLINE_VARIANT,
            focused_border_color=ACCENT, on_change=on_change,
            bgcolor=ft.Colors.SURFACE_CONTAINER_LOWEST)

    def chips(self, options, selected, on_pick, small=False):
        """Ряд «таблеток» — выбор одного значения. Дешевле выпадашки в
        кликах, а вариантов здесь всегда мало."""
        row = ft.Row(spacing=6, wrap=True, run_spacing=6)
        for opt in options:
            key, label = (opt if isinstance(opt, tuple) else (opt, opt))
            on = key == selected
            row.controls.append(ft.Container(
                content=ft.Text(label, size=13.5 if small else 13,
                                weight=ft.FontWeight.W_600 if on else None,
                                color=("#231604" if on
                                       else ft.Colors.ON_SURFACE_VARIANT)),
                bgcolor=(ACCENT if on else ft.Colors.SURFACE_CONTAINER_LOWEST),
                border=ft.Border.all(1, ACCENT if on
                                     else ft.Colors.OUTLINE_VARIANT),
                border_radius=999, ink=True,
                padding=ft.Padding(12, 7, 12, 7),
                on_click=lambda e, k=key: on_pick(k)))
        return row

    def table(self, headers, rows, widths=None, empty="Brak danych",
              on_row_click=None, expand=True, on_sort=None, sort_active=None):
        """Простая таблица: шапка + прокручиваемые строки.

        Своя, а не DataTable: нужен контроль над шириной колонок и
        подсветкой строк, а данных тут максимум пара сотен строк.

        Заголовок вида («Ilość», "r", "qty") — колонка выравнена вправо и
        кликабельна: сортировка живёт в шапке таблицы, как везде, а не в
        отдельном ряду кнопок сбоку.
        """
        widths = widths or [None] * len(headers)

        def cell(value, width, bold=False, color=None, right=False, size=14.5):
            return ft.Container(
                width=width, expand=(width is None),
                content=ft.Text(str(value), size=size, no_wrap=True,
                                color=color,
                                weight=ft.FontWeight.W_700 if bold else None,
                                text_align=(ft.TextAlign.RIGHT if right
                                            else ft.TextAlign.LEFT)),
                # отступ справа: иначе «0.5 szt» прилипает к следующей колонке
                padding=ft.Padding(0, 0, 14 if right else 0, 0),
                alignment=(ft.Alignment.CENTER_RIGHT if right
                           else ft.Alignment.CENTER_LEFT))

        def is_right(h):
            """Заголовок вида («Ilość», "r") — колонка выравнивается вправо."""
            return isinstance(h, tuple) and len(h) > 1 and h[1] == "r"

        def sort_key(h):
            return h[2] if isinstance(h, tuple) and len(h) > 2 else None

        head_cells = []
        for h, w in zip(headers, widths):
            label = h[0] if isinstance(h, tuple) else h
            key = sort_key(h)
            if key and key == sort_active:
                label = f"{label} ▼"
            c = cell(label, w, bold=True, color=ACCENT, size=12,
                     right=is_right(h))
            if key and on_sort:
                c.ink = True
                c.border_radius = 8
                c.padding = ft.Padding(6, 4, 6, 4)
                c.tooltip = "Kliknij, żeby posortować"
                c.on_click = lambda e, k=key: on_sort(k)
            head_cells.append(c)

        head = ft.Container(
            content=ft.Row(head_cells, spacing=8),
            bgcolor=ft.Colors.SURFACE_CONTAINER_HIGH,
            border_radius=ft.BorderRadius(14, 14, 0, 0),
            padding=ft.Padding(14, 9, 14, 9))

        body = ft.ListView(spacing=0, expand=expand, padding=0)
        for n, row in enumerate(rows):
            cells = []
            for value, w, h in zip(row, widths, headers):
                right = is_right(h)
                if isinstance(value, ft.Control):
                    cells.append(ft.Container(width=w, expand=(w is None),
                                              content=value,
                                              alignment=ft.Alignment.CENTER_LEFT))
                else:
                    cells.append(cell(value, w, right=right))
            body.controls.append(ft.Container(
                content=ft.Row(cells, spacing=8),
                padding=ft.Padding(14, 10, 14, 10), ink=bool(on_row_click),
                bgcolor=(ft.Colors.SURFACE_CONTAINER_LOWEST if n % 2 else None),
                on_click=((lambda e, r=n: on_row_click(r)) if on_row_click
                          else None)))
        if not rows:
            body.controls.append(ft.Container(
                content=ft.Text(empty, size=14.5,
                                color=ft.Colors.ON_SURFACE_VARIANT),
                padding=ft.Padding(14, 22, 14, 22),
                alignment=ft.Alignment.CENTER))
        return ft.Container(
            content=ft.Column([head, body], spacing=0, expand=expand),
            border=ft.Border.all(1, ft.Colors.OUTLINE_VARIANT),
            border_radius=14, expand=expand, clip_behavior=ft.ClipBehavior.HARD_EDGE)

    def month_picker(self, value, on_change, allow_all=False):
        """Выбор месяца: ‹ [месяц] ›, плюс «cały okres» где уместно."""
        months = db.months_with_data(self.c)
        cur = db.current_month()
        if cur not in months:
            months.append(cur)
        if value and value not in months:
            months.append(value)
        months = sorted(set(months), reverse=True)
        opts = ([ft.DropdownOption(key="", text="Cały okres")] if allow_all
                else [])
        opts += [ft.DropdownOption(key=m, text=db.month_label_pl(m))
                 for m in months]
        dd = ft.Dropdown(
            value=value, options=opts, width=220, dense=True, text_size=15,
            border_color=ft.Colors.OUTLINE_VARIANT,
            border_radius=14, bgcolor=ft.Colors.SURFACE_CONTAINER_LOWEST,
            on_select=lambda e: on_change(e.control.value))

        def step(delta):
            base = value or cur
            on_change(db.shift_month(base, delta))

        return ft.Row([
            ft.IconButton(ft.Icons.CHEVRON_LEFT, tooltip="Poprzedni miesiąc",
                          on_click=lambda e: step(-1)),
            dd,
            ft.IconButton(ft.Icons.CHEVRON_RIGHT, tooltip="Następny miesiąc",
                          on_click=lambda e: step(1)),
        ], spacing=2)

    # ═══════════════════════ Podsumowanie ═══════════════════════
    def view_summary(self):
        stats = db.stats(self.c, self.month)
        waiting = len(self.reports)

        head = ft.Row([
            ft.Text("Straty", size=22, weight=ft.FontWeight.W_800),
            ft.Container(expand=True),
            self.month_picker(self.month, self._set_month),
            ft.OutlinedButton("Excel — miesiąc", icon=ft.Icons.FILE_DOWNLOAD,
                              on_click=lambda e: self.page.run_task(
                                  self.export_excel, self.month)),
            ft.OutlinedButton("Excel — cały okres", icon=ft.Icons.FILE_DOWNLOAD,
                              on_click=lambda e: self.page.run_task(
                                  self.export_excel, "")),
            ft.FilledButton("Nowa strata", icon=ft.Icons.ADD,
                            on_click=lambda e: self.loss_dialog()),
        ], spacing=10, vertical_alignment=ft.CrossAxisAlignment.CENTER)

        kpis = ft.Row([
            self.kpi("wpisów w miesiącu", stats["entries"]),
            self.kpi("pozycji ze stratami", stats["items"]),
            self.kpi("wpisów dziś", stats["today"]),
            self.kpi("zgłoszeń czeka", waiting,
                     color=ACCENT if waiting else None),
        ], spacing=12)

        tabs = self.chips([("items", "Pozycje"), ("entries", "Wpisy"),
                           ("months", "Wg miesięcy"), ("reasons", "Powody"),
                           ("categories", "Kategorie")],
                          self.summary_tab, self._set_summary_tab)

        if self.summary_tab == "items":
            body = self._summary_items()
        elif self.summary_tab == "entries":
            body = self._summary_entries()
        elif self.summary_tab == "months":
            body = self._summary_months()
        elif self.summary_tab == "reasons":
            body = self._summary_simple(
                db.by_reason(self.c, month=self.month), "Powód", "reason")
        else:
            body = self._summary_simple(
                db.by_category(self.c, month=self.month), "Kategoria",
                "category")

        return ft.Column([head, kpis, tabs, body], spacing=14, expand=True)

    def _set_month(self, m):
        self.month = m or db.current_month()
        self.refresh()

    def _set_summary_tab(self, key):
        self.summary_tab = key
        self.refresh()

    def _set_summary_sort(self, key):
        self.summary_sort = key
        self.refresh()

    def _summary_items(self):
        """Итог по позициям. Сортировка — кликом по шапке, как в любой
        таблице; отдельный ряд кнопок «Sortuj:» только путал."""
        rows = db.sort_summary(db.by_item(self.c, month=self.month),
                               self.summary_sort)
        table = self.table(
            [("Pozycja", "l", "name"), ("Kategoria", "l", "category"),
             ("Ilość", "r", "qty"), ("Wpisów", "r", "entries")],
            [(r["name"], r["category"] or "—", r["qty_text"], r["entries"])
             for r in rows],
            widths=[None, 200, 130, 90],
            empty="W tym miesiącu nie ma jeszcze strat.",
            on_row_click=lambda n: self.show_item_history(rows[n]),
            on_sort=self._set_summary_sort, sort_active=self.summary_sort)
        hint = ft.Text(
            "Kliknij nagłówek, żeby posortować · kliknij wiersz, żeby zobaczyć "
            "pojedyncze wpisy. „Ilość” porównuje pozycje wewnątrz grup "
            "jednostek — kilogramy z kilogramami, litry z litrami.",
            size=12.5, color=ft.Colors.ON_SURFACE_VARIANT)
        return ft.Column([table, hint], spacing=10, expand=True)

    def _summary_entries(self):
        """Журнал отдельных записей — то, что раньше было разделом «Straty».

        Фильтры лежат в Row(wrap=True) БЕЗ распорок с `expand`: Flutter
        разрешает Expanded только внутри Flex, а при wrap строит Wrap — и
        вместо таблицы рисует серый прямоугольник ошибки. Проверено.
        """
        f = self.journal_filters
        entries = db.journal(self.c, month=self.month,
                             category=f["category"] or None,
                             reason=f["reason"] or None,
                             person=f["person"] or None,
                             search=f["search"] or None)

        def dd(label, key, values):
            return ft.Dropdown(
                label=label, value=f[key], width=190, dense=True, text_size=14.5,
                border_color=ft.Colors.OUTLINE_VARIANT, border_radius=14,
                bgcolor=ft.Colors.SURFACE_CONTAINER_LOWEST,
                options=[ft.DropdownOption(key="", text="wszystko")]
                + [ft.DropdownOption(key=v, text=v) for v in values],
                on_select=lambda e, k=key: self._set_filter(k, e.control.value))

        search = self.field("Szukaj", f["search"], width=200,
                            on_change=lambda e: self._set_filter(
                                "search", e.control.value, redraw=False))
        search.on_submit = lambda e: self.refresh()

        filters = ft.Row([
            search,
            dd("Kategoria", "category", db.item_categories(self.c)),
            dd("Powód", "reason", db.reasons_used(self.c)),
            dd("Osoba", "person", db.persons_used(self.c)),
            ft.OutlinedButton("Wyczyść filtry", icon=ft.Icons.FILTER_ALT_OFF,
                              on_click=lambda e: self._clear_filters()),
            ft.Container(
                padding=ft.Padding(6, 0, 0, 0),
                content=ft.Text(f"{len(entries)} {self._wpisy_word(len(entries))}",
                                size=14, color=ft.Colors.ON_SURFACE_VARIANT)),
        ], spacing=10, wrap=True, run_spacing=8,
            vertical_alignment=ft.CrossAxisAlignment.CENTER)

        rows = []
        for e in entries:
            rows.append((
                e["loss_date"], e["item_name"], e["qty_text"],
                e["reason"] or "—", e["person"] or "—",
                ft.Row([
                    ft.Icon(ft.Icons.SMARTPHONE if e["source"] == "qr"
                            else ft.Icons.EDIT_NOTE, size=15,
                            color=ft.Colors.ON_SURFACE_VARIANT,
                            tooltip=("z telefonu kucharza" if e["source"] == "qr"
                                     else "wpisane ręcznie")),
                    ft.IconButton(ft.Icons.EDIT, icon_size=16, tooltip="Popraw",
                                  on_click=lambda ev, x=e: self.loss_dialog(x)),
                    ft.IconButton(ft.Icons.DELETE_OUTLINE, icon_size=16,
                                  tooltip="Usuń", icon_color=BAD_COLOR,
                                  on_click=lambda ev, x=e: self.delete_loss(x)),
                ], spacing=0)))

        table = self.table(
            [("Data",), ("Pozycja",), ("Ilość", "r"), ("Powód",), ("Osoba",),
             ("",)],
            rows, widths=[100, None, 110, 170, 130, 120],
            empty="Brak wpisów w tym miesiącu.")
        return ft.Column([filters, table], spacing=12, expand=True)

    @staticmethod
    def _wpisy_word(n: int) -> str:
        n10, n100 = n % 10, n % 100
        if n == 1:
            return "wpis"
        if 2 <= n10 <= 4 and not 12 <= n100 <= 14:
            return "wpisy"
        return "wpisów"

    def _summary_months(self):
        data = db.by_month(self.c)
        months = data["months"]
        if not months:
            return self.table([("Pozycja",)], [], empty="Brak danych.")
        headers = [("Pozycja",), ("Jedn.",)] + \
            [(db.month_short_pl(m), "r") for m in months] + [("Razem", "r")]
        widths = [None, 70] + [96] * len(months) + [110]
        rows = []
        for it in data["items"]:
            cells = [it["name"], it["unit"]]
            for m in months:
                v = it["months_qty"].get(m)
                cells.append("" if v is None else db.fmt_num(v))
            cells.append(it["total_text"])
            rows.append(tuple(cells))
        return self.table(headers, rows, widths=widths)

    def _summary_simple(self, data, title, key):
        return self.table(
            [(title,), ("Wpisów", "r")],
            [(r[key], r["entries"]) for r in data],
            widths=[None, 110], empty="Brak wpisów w tym miesiącu.")

    def show_item_history(self, row):
        entries = db.journal(self.c, month=self.month, item_id=row["item_id"])
        lst = ft.ListView(spacing=6, height=320, width=560)
        for e in entries:
            lst.controls.append(ft.Container(
                content=ft.Row([
                    ft.Text(e["loss_date"], size=14, width=94,
                            color=ft.Colors.ON_SURFACE_VARIANT),
                    ft.Text(e["qty_text"], size=14.5, width=90,
                            weight=ft.FontWeight.W_700),
                    ft.Text(" · ".join(x for x in
                                       [e["reason"], e["person"], e["note"]]
                                       if x) or "—",
                            size=14, expand=True, no_wrap=True,
                            color=ft.Colors.ON_SURFACE_VARIANT),
                    ft.Icon(ft.Icons.QR_CODE if e["source"] == "qr"
                            else ft.Icons.EDIT, size=14,
                            color=ft.Colors.ON_SURFACE_VARIANT),
                ], spacing=8),
                bgcolor=ft.Colors.SURFACE_CONTAINER_LOWEST, border_radius=14,
                padding=ft.Padding(12, 9, 12, 9)))
        dlg = ft.AlertDialog(
            title=ft.Text(f"{row['name']} — {row['qty_text']} "
                          f"({db.month_label_pl(self.month)})",
                          size=16, weight=ft.FontWeight.W_700),
            content=lst,
            actions=[ft.FilledButton(
                "Zamknij", on_click=lambda e: self.page.pop_dialog())],
            shape=ft.RoundedRectangleBorder(radius=18))
        self.page.show_dialog(dlg)

    # ─── журнал и правка отдельных страт ───
    def _set_filter(self, key, value, redraw=True):
        self.journal_filters[key] = value or ""
        if redraw:
            self.refresh()

    def _clear_filters(self):
        self.journal_filters = {"category": "", "reason": "", "person": "",
                                "search": ""}
        self.refresh()

    def delete_loss(self, entry):
        self.confirm(
            "Usunąć wpis?",
            f"{entry['item_name']} — {entry['qty_text']} "
            f"({entry['loss_date']})",
            lambda: (db.delete_loss(self.c, entry["id"]), self.refresh(),
                     self.toast("Wpis usunięty")),
            yes_text="Usuń")

    # ─── диалог страты (ручной ввод и правка) ───
    def loss_dialog(self, entry=None):
        items = db.list_items(self.c)
        if not items:
            self.dialog("warning", "Pusty katalog",
                        "Najpierw dodaj pozycje w Ustawieniach.")
            return

        state = {
            "item": (db.get_item(self.c, entry["item_id"]) if entry
                     else None),
            "unit": entry["unit"] if entry else None,
            "reason": (entry["reason"] or "") if entry else "",
            "search": "",
        }

        picked = ft.Text("", size=15.5, weight=ft.FontWeight.W_700)
        item_list = ft.ListView(spacing=4, height=170)
        qty = self.field("Ilość", db.fmt_num(entry["qty"]) if entry else "1",
                         width=140)
        unit_row = ft.Row(spacing=6)
        reason_row = ft.Row(spacing=6, wrap=True, run_spacing=6)
        date_f = self.field("Data (RRRR-MM-DD)",
                            entry["loss_date"] if entry
                            else db.current_shift_date(), width=190)
        person = self.field("Osoba", (entry["person"] or "") if entry else "",
                            width=190)
        note = self.field("Uwagi", (entry["note"] or "") if entry else "",
                          width=400)
        error = ft.Text("", size=14, color=BAD_COLOR, visible=False)

        def paint_units():
            unit_row.controls = []
            if not state["item"]:
                return
            units = db.units_for(state["item"]["unit"])
            if state["unit"] not in units:
                state["unit"] = units[0]
            for u in units:
                on = u == state["unit"]
                unit_row.controls.append(ft.Container(
                    content=ft.Text(u, size=14.5,
                                    color=("#231604" if on
                                           else ft.Colors.ON_SURFACE_VARIANT),
                                    weight=ft.FontWeight.W_700 if on else None),
                    bgcolor=(ACCENT if on
                             else ft.Colors.SURFACE_CONTAINER_LOWEST),
                    border=ft.Border.all(1, ACCENT if on
                                         else ft.Colors.OUTLINE_VARIANT),
                    border_radius=999, ink=True, width=54,
                    alignment=ft.Alignment.CENTER,
                    padding=ft.Padding(0, 8, 0, 8),
                    on_click=lambda e, x=u: pick_unit(x)))

        def pick_unit(u):
            state["unit"] = u
            paint_units()
            self.page.update()

        def paint_reasons():
            reason_row.controls = []
            names = db.reason_names(self.c)
            if not names:
                reason_row.controls.append(ft.Text(
                    "Brak powodów — dodasz je w Ustawieniach.", size=13.5,
                    color=ft.Colors.ON_SURFACE_VARIANT))
            for r in names:
                on = r == state["reason"]
                reason_row.controls.append(ft.Container(
                    content=ft.Text(r, size=14,
                                    color=("#231604" if on
                                           else ft.Colors.ON_SURFACE_VARIANT),
                                    weight=ft.FontWeight.W_700 if on else None),
                    bgcolor=(ACCENT if on
                             else ft.Colors.SURFACE_CONTAINER_LOWEST),
                    border=ft.Border.all(1, ACCENT if on
                                         else ft.Colors.OUTLINE_VARIANT),
                    border_radius=999, ink=True,
                    padding=ft.Padding(12, 7, 12, 7),
                    on_click=lambda e, x=r: pick_reason(x)))

        def pick_reason(r):
            state["reason"] = "" if state["reason"] == r else r
            paint_reasons()
            self.page.update()

        def paint_items():
            q = (state["search"] or "").strip().lower()
            item_list.controls = []
            for it in items:
                if q and q not in it["name"].lower() \
                        and q not in (it["category"] or "").lower():
                    continue
                on = state["item"] and it["id"] == state["item"]["id"]
                item_list.controls.append(ft.Container(
                    content=ft.Row([
                        ft.Text(it["name"], size=14.5, expand=True,
                                weight=ft.FontWeight.W_700 if on else None),
                        ft.Text(it["category"] or "", size=12.5,
                                color=ft.Colors.ON_SURFACE_VARIANT),
                        ft.Text(it["unit"], size=13.5, width=36,
                                color=ft.Colors.ON_SURFACE_VARIANT),
                    ], spacing=8),
                    bgcolor=(ft.Colors.SURFACE_CONTAINER_HIGH if on
                             else ft.Colors.SURFACE_CONTAINER_LOWEST),
                    border=ft.Border.all(1, ACCENT if on
                                         else ft.Colors.OUTLINE_VARIANT),
                    border_radius=14, ink=True,
                    padding=ft.Padding(12, 9, 12, 9),
                    on_click=lambda e, x=it: pick_item(x)))
            if not item_list.controls:
                item_list.controls.append(ft.Container(
                    content=ft.Text("Nic nie znaleziono", size=14,
                                    color=ft.Colors.ON_SURFACE_VARIANT),
                    padding=ft.Padding(12, 12, 12, 12)))

        def pick_item(it):
            state["item"] = it
            state["unit"] = it["unit"]
            picked.value = f"{it['name']}  ({it['unit']})"
            paint_items()
            paint_units()
            self.page.update()

        search = self.field("Szukaj pozycji", width=400)

        def on_search(e):
            state["search"] = e.control.value
            paint_items()
            self.page.update()

        search.on_change = on_search

        def save(e):
            if not state["item"]:
                error.value = "Wybierz pozycję"
                error.visible = True
                self.page.update()
                return
            payload = {"item_id": state["item"]["id"], "qty": qty.value,
                       "unit": state["unit"], "reason": state["reason"],
                       "person": person.value, "note": note.value,
                       "loss_date": (date_f.value or "").strip(),
                       "source": entry["source"] if entry else "manual"}
            try:
                if entry:
                    db.update_loss(self.c, entry["id"], payload)
                else:
                    db.add_loss(self.c, payload)
            except ValueError as err:
                error.value = str(err)
                error.visible = True
                self.page.update()
                return
            self.page.pop_dialog()
            self.refresh()
            self.toast("Zapisano" if entry else "Strata zapisana")

        if state["item"]:
            picked.value = f"{state['item']['name']}  ({state['item']['unit']})"
        paint_items()
        paint_units()
        paint_reasons()

        quick_dates = ft.Row([
            ft.TextButton("Dziś", on_click=lambda e: set_date(0)),
            ft.TextButton("Wczoraj", on_click=lambda e: set_date(-1)),
        ], spacing=0)

        def set_date(delta):
            d = date.fromisoformat(db.current_shift_date()) + timedelta(days=delta)
            date_f.value = d.isoformat()
            self.page.update()

        body = ft.Column([
            search, item_list, picked,
            ft.Row([qty, unit_row], spacing=12,
                   vertical_alignment=ft.CrossAxisAlignment.CENTER),
            ft.Text("Powód", size=13.5, color=ft.Colors.ON_SURFACE_VARIANT),
            reason_row,
            ft.Row([date_f, quick_dates, person], spacing=10,
                   vertical_alignment=ft.CrossAxisAlignment.CENTER),
            note, error,
        ], spacing=12, width=620, scroll=ft.ScrollMode.AUTO, height=560)

        dlg = ft.AlertDialog(
            title=ft.Text("Popraw stratę" if entry else "Nowa strata",
                          size=16, weight=ft.FontWeight.W_700),
            content=body,
            actions=[ft.TextButton("Anuluj",
                                   on_click=lambda e: self.page.pop_dialog()),
                     ft.FilledButton("Zapisz", on_click=save)],
            shape=ft.RoundedRectangleBorder(radius=18))
        self.page.show_dialog(dlg)

    # ═══════════════════════ Zgłoszenia ═══════════════════════
    def view_reports(self):
        head = ft.Row([
            ft.Text("Zgłoszenia z telefonów", size=22,
                    weight=ft.FontWeight.W_800),
            ft.Container(expand=True),
            ft.Text(self.reports_status, size=14,
                    color=ft.Colors.ON_SURFACE_VARIANT),
            ft.FilledTonalButton("Odśwież", icon=ft.Icons.REFRESH,
                                 on_click=lambda e: self.page.run_task(
                                     self.load_reports, True)),
        ], spacing=10, vertical_alignment=ft.CrossAxisAlignment.CENTER)

        if not self.cfg.get("server_token"):
            body = self.card(
                "Serwer nie jest podłączony", ft.Icons.CLOUD_OFF,
                ft.Column([
                    ft.Text("Żeby kucharze mogli zgłaszać straty z telefonu, "
                            "wpisz adres serwera i token w Ustawieniach.",
                            size=14.5),
                    ft.FilledButton("Otwórz Ustawienia",
                                    on_click=lambda e: self.select("settings")),
                ], spacing=12))
            return ft.Column([head, body], spacing=14, expand=True)

        cards = [self._report_card(r) for r in self.reports]
        if not cards:
            cards = [self.card(
                "Brak nowych zgłoszeń", ft.Icons.INBOX,
                ft.Text("Wszystko zatwierdzone. Nowe zgłoszenia pojawią się "
                        "tutaj po odświeżeniu.", size=14.5,
                        color=ft.Colors.ON_SURFACE_VARIANT))]

        history = db.confirmed_reports(self.c, limit=10)
        hist_rows = []
        for h in history:
            lines = db.losses_for_report(self.c, h["report_id"])
            # состав прямо в строке: «2 pozycji» ни о чём не говорит, а
            # менеджеру нужно узнать своё зашлушение, не открывая ничего
            summary = ", ".join(
                f"{l['item_name']} {db.fmt_num(l['qty'])} {l['unit']}"
                for l in lines[:3])
            if len(lines) > 3:
                summary += f" … +{len(lines) - 3}"
            hist_rows.append((
                h["loss_date"] or "—", h["person"] or "—",
                summary or f"{h['lines']} pozycji",
                (h["confirmed_at"] or "")[:16].replace("T", " "),
                ft.TextButton("Cofnij",
                              on_click=lambda e, x=h: self.undo_report(x))))
        hist = self.card(
            "Ostatnio zatwierdzone", ft.Icons.HISTORY,
            self.table([("Data",), ("Osoba",), ("Co zatwierdzono",),
                        ("Kiedy",), ("",)],
                       hist_rows, widths=[100, 130, None, 140, 100],
                       empty="Jeszcze nic nie zatwierdzono.", expand=False,
                       on_row_click=lambda n: self.show_report_details(
                           history[n])),
            subtitle="kliknij wiersz, żeby zobaczyć wszystkie pozycje")

        return ft.Column([head] + cards + [hist], spacing=14, expand=True,
                         scroll=ft.ScrollMode.AUTO)

    def _report_card(self, rep):
        data = rep.get("data") or {}
        lines = db.resolve_report_lines(self.c, data.get("lines") or [])
        problems = [l for l in lines if l["problem"]]
        rows = []
        for l in lines:
            rows.append((
                l["item_name"],
                f"{db.fmt_num(l['qty'])} {l['unit']}",
                l["reason"] or "—",
                l["note"] or "",
                ft.Text(l["problem"] or "", size=13.5, color=BAD_COLOR)))
        table = self.table(
            [("Pozycja",), ("Ilość", "r"), ("Powód",), ("Uwagi",), ("",)],
            rows, widths=[None, 110, 150, 200, 190], expand=False)

        warn = None
        if problems:
            warn = ft.Container(
                content=ft.Row([
                    ft.Icon(ft.Icons.WARNING_AMBER, size=18, color=WARN_COLOR),
                    ft.Text("Część pozycji nie pasuje do katalogu — popraw je "
                            "przed zatwierdzeniem (klikając „Popraw”).",
                            size=14, expand=True)], spacing=8),
                bgcolor=ft.Colors.with_opacity(0.12, WARN_COLOR),
                border_radius=14, padding=ft.Padding(12, 9, 12, 9))

        actions = ft.Row([
            ft.OutlinedButton("Popraw i zatwierdź", icon=ft.Icons.EDIT,
                              on_click=lambda e: self.fix_report_dialog(rep)),
            ft.FilledButton("Zatwierdź", icon=ft.Icons.CHECK,
                            disabled=bool(problems),
                            on_click=lambda e: self.confirm_report(rep)),
            ft.TextButton("Odrzuć",
                          on_click=lambda e: self.reject_report(rep)),
        ], spacing=8, alignment=ft.MainAxisAlignment.END)

        body = ft.Column([x for x in [warn, table, actions] if x], spacing=12)
        return self.card(
            f"{rep.get('name') or '—'} · {rep.get('shift_date') or '—'}",
            ft.Icons.SMARTPHONE, body,
            subtitle=f"{len(lines)} pozycji · wysłano "
                     f"{(rep.get('created_at') or '')[:16].replace('T', ' ')}")

    def confirm_report(self, rep, lines=None):
        try:
            res = db.confirm_report(self.c, rep, lines)
        except ValueError as err:
            self.dialog("error", "Nie można zatwierdzić", str(err))
            return
        self.page.run_task(self._drop_report_on_server, rep["id"])
        self.reports = [r for r in self.reports if r["id"] != rep["id"]]
        self._paint_badge()
        self.refresh()
        self.toast(f"Zatwierdzono {res['lines']} pozycji")

    def reject_report(self, rep):
        self.confirm(
            "Odrzucić zgłoszenie?",
            f"{rep.get('name') or '—'} · {rep.get('shift_date') or '—'}\n"
            "Zgłoszenie zniknie z serwera i nie trafi do strat.",
            lambda: self._do_reject(rep), yes_text="Odrzuć")

    def _do_reject(self, rep):
        self.page.run_task(self._drop_report_on_server, rep["id"])
        self.reports = [r for r in self.reports if r["id"] != rep["id"]]
        self._paint_badge()
        self.refresh()
        self.toast("Zgłoszenie odrzucone")

    def undo_report(self, hist):
        self.confirm(
            "Cofnąć zatwierdzenie?",
            f"Usuniemy {hist['lines']} pozycji z dnia {hist['loss_date']}.",
            lambda: (db.unconfirm_report(self.c, hist["report_id"]),
                     self.refresh(), self.toast("Cofnięto")),
            yes_text="Cofnij")

    def fix_report_dialog(self, rep):
        """Правка строк перед подтверждением.

        Нужна ровно для двух случаев: повар промахнулся с количеством и
        позиция не нашлась в каталоге (её переименовали или удалили).
        """
        data = rep.get("data") or {}
        lines = db.resolve_report_lines(self.c, data.get("lines") or [])
        items = db.list_items(self.c)
        rows = ft.Column(spacing=8, scroll=ft.ScrollMode.AUTO, height=380,
                         width=760)
        state = {"lines": lines}

        def redraw():
            rows.controls = []
            for line in state["lines"]:
                qty_f = ft.TextField(
                    value=db.fmt_num(line["qty"]), width=90, dense=True,
                    text_size=14.5, border_radius=8,
                    border_color=ft.Colors.OUTLINE_VARIANT,
                    content_padding=ft.Padding(8, 8, 8, 8),
                    on_change=lambda e, l=line: set_qty(l, e.control.value))
                item_dd = ft.Dropdown(
                    value=line["item_id"], width=250, dense=True, text_size=14.5,
                    border_color=ft.Colors.OUTLINE_VARIANT, border_radius=8,
                    # с сотней позиций выпадашка без поиска бесполезна
                    editable=True, enable_filter=True,
                    options=[ft.DropdownOption(key=i["id"],
                                               text=f"{i['name']} ({i['unit']})")
                             for i in items],
                    on_select=lambda e, l=line: set_item(l, e.control.value))
                unit_dd = ft.Dropdown(
                    value=line["unit"], width=90, dense=True, text_size=14.5,
                    border_color=ft.Colors.OUTLINE_VARIANT, border_radius=8,
                    options=[ft.DropdownOption(key=u, text=u)
                             for u in UNIT_OPTIONS],
                    on_select=lambda e, l=line: set_unit(l, e.control.value))
                rows.controls.append(ft.Container(
                    content=ft.Row([
                        item_dd, qty_f, unit_dd,
                        ft.Text(line["reason"] or "—", size=14, width=120,
                                color=ft.Colors.ON_SURFACE_VARIANT),
                        ft.Text(line["problem"] or "", size=13.5, expand=True,
                                color=BAD_COLOR),
                        ft.IconButton(ft.Icons.DELETE_OUTLINE, icon_size=17,
                                      icon_color=BAD_COLOR,
                                      on_click=lambda e, l=line: drop(l)),
                    ], spacing=8),
                    bgcolor=ft.Colors.SURFACE_CONTAINER_LOWEST,
                    border_radius=14, padding=ft.Padding(10, 8, 10, 8)))
            self.page.update()

        def revalidate(line):
            item = db.get_item(self.c, line["item_id"]) if line["item_id"] else None
            if not item:
                line["problem"] = "nieznana pozycja"
            elif line["unit"] not in db.UNITS:
                line["problem"] = "nieznana jednostka"
            elif not db.compatible(line["unit"], item["unit"]):
                line["problem"] = f"jednostka nie pasuje ({item['unit']})"
            elif line["qty"] <= 0:
                line["problem"] = "zła ilość"
            else:
                line["problem"] = None
                line["item_name"] = item["name"]

        def set_qty(line, value):
            try:
                line["qty"] = db.parse_qty(value)
            except ValueError:
                line["qty"] = 0
            revalidate(line)

        def set_item(line, item_id):
            line["item_id"] = item_id
            item = db.get_item(self.c, item_id)
            if item and not db.compatible(line["unit"], item["unit"]):
                line["unit"] = item["unit"]
            revalidate(line)
            redraw()

        def set_unit(line, unit):
            line["unit"] = unit
            revalidate(line)
            redraw()

        def drop(line):
            state["lines"] = [l for l in state["lines"] if l is not line]
            redraw()

        def save(e):
            if not state["lines"]:
                self.page.pop_dialog()
                self.reject_report(rep)
                return
            bad = [l for l in state["lines"] if l["problem"]]
            if bad:
                self.toast("Popraw podświetlone linie", good=False)
                return
            self.page.pop_dialog()
            self.confirm_report(rep, state["lines"])

        redraw()
        dlg = ft.AlertDialog(
            title=ft.Text(f"Popraw zgłoszenie — {rep.get('name') or '—'}",
                          size=16, weight=ft.FontWeight.W_700),
            content=rows,
            actions=[ft.TextButton("Anuluj",
                                   on_click=lambda e: self.page.pop_dialog()),
                     ft.FilledButton("Zatwierdź", on_click=save)],
            shape=ft.RoundedRectangleBorder(radius=18))
        self.page.show_dialog(dlg)

    def show_report_details(self, hist):
        """Что именно приняли этим зашлушением — читаем из журнала по
        report_id, а не из очереди: на сервере его уже нет."""
        lines = db.losses_for_report(self.c, hist["report_id"])
        rows = [(l["item_name"], f"{db.fmt_num(l['qty'])} {l['unit']}",
                 l["reason"] or "—", l["note"] or "") for l in lines]
        dlg = ft.AlertDialog(
            title=ft.Text(f"Zgłoszenie — {hist['person'] or '—'} · "
                          f"{hist['loss_date'] or '—'}",
                          size=16, weight=ft.FontWeight.W_700),
            content=ft.Container(
                width=640, height=340,
                content=self.table(
                    [("Pozycja",), ("Ilość", "r"), ("Powód",), ("Uwagi",)],
                    rows, widths=[None, 110, 150, 190],
                    empty="Wpisy tego zgłoszenia zostały już usunięte.")),
            actions=[ft.TextButton(
                "Cofnij zatwierdzenie",
                on_click=lambda e: (self.page.pop_dialog(),
                                    self.undo_report(hist))),
                ft.FilledButton("Zamknij",
                                on_click=lambda e: self.page.pop_dialog())],
            shape=ft.RoundedRectangleBorder(radius=18))
        self.page.show_dialog(dlg)

    def _paint_badge(self):
        cont, ic, tx, badge = self.nav_items["reports"]
        n = len(self.reports)
        badge.visible = bool(n)
        badge.content.value = str(n)
        # Текст статуса пересчитываем здесь же: иначе после подтверждения
        # в шапке остаётся «Oczekujące: 3» рядом с «Brak nowych zgłoszeń».
        # Сообщение об ошибке связи не трогаем — оно важнее счётчика.
        if self.server_ok:
            self.reports_status = (f"Oczekujące: {n}" if n
                                   else "Brak nowych zgłoszeń")

    # ═══════════════════════ Ustawienia ═══════════════════════
    def view_settings(self):
        head = ft.Text("Ustawienia", size=22, weight=ft.FontWeight.W_800)
        tabs = ft.Tabs(length=4, selected_index=0, expand=True, content=ft.Column([
            ft.TabBar(tabs=[ft.Tab(label="Pozycje"), ft.Tab(label="Powody"),
                            ft.Tab(label="Serwer"), ft.Tab(label="O programie")]),
            ft.TabBarView(controls=[
                ft.Container(content=self._settings_items(),
                             padding=ft.Padding(0, 14, 0, 0)),
                ft.Container(content=self._settings_reasons(),
                             padding=ft.Padding(0, 14, 0, 0)),
                ft.Container(content=self._settings_server(),
                             padding=ft.Padding(0, 14, 0, 0)),
                ft.Container(content=self._settings_about(),
                             padding=ft.Padding(0, 14, 0, 0)),
            ], expand=True),
        ], spacing=0, expand=True))
        return ft.Column([head, tabs], spacing=14, expand=True)

    def _settings_items(self):
        items = db.list_items(self.c, active_only=False)
        rows = []
        for it in items:
            used = db.item_usage(self.c, it["id"])
            rows.append((
                ft.Row([
                    ft.Text(it["name"], size=14.5,
                            color=(None if it["active"]
                                   else ft.Colors.ON_SURFACE_VARIANT)),
                    ft.Container(
                        visible=not it["active"],
                        bgcolor=ft.Colors.SURFACE_CONTAINER_HIGHEST,
                        border_radius=999, padding=ft.Padding(8, 2, 8, 2),
                        content=ft.Text("ukryta", size=11.5,
                                        color=ft.Colors.ON_SURFACE_VARIANT)),
                ], spacing=8),
                it["category"] or "—", it["unit"],
                f"{used}" if used else "—",
                ft.Row([
                    ft.IconButton(
                        ft.Icons.VISIBILITY if it["active"]
                        else ft.Icons.VISIBILITY_OFF, icon_size=16,
                        tooltip=(
                            "Schowaj przed kucharzami — zniknie z telefonu, "
                            "ale dawne straty zostaną w raportach"
                            if it["active"] else
                            "Przywróć — pozycja znów pojawi się na telefonach"),
                        on_click=lambda e, x=it: self.toggle_item(x)),
                    ft.IconButton(ft.Icons.EDIT, icon_size=16, tooltip="Popraw",
                                  on_click=lambda e, x=it: self.item_dialog(x)),
                    ft.IconButton(ft.Icons.DELETE_OUTLINE, icon_size=16,
                                  icon_color=BAD_COLOR, tooltip="Usuń",
                                  on_click=lambda e, x=it: self.delete_item(x)),
                ], spacing=0)))
        table = self.table(
            [("Pozycja",), ("Kategoria",), ("Jedn.",), ("Wpisów", "r"), ("",)],
            rows, widths=[None, 200, 80, 90, 140],
            empty="Katalog jest pusty.")
        head = ft.Row([
            ft.FilledButton("Nowa pozycja", icon=ft.Icons.ADD,
                            on_click=lambda e: self.item_dialog()),
            ft.OutlinedButton("Wgraj katalog startowy", icon=ft.Icons.DOWNLOAD,
                              on_click=lambda e: self.seed_catalog()),
            ft.Container(expand=True),
            ft.Text(f"{len(items)} pozycji", size=14,
                    color=ft.Colors.ON_SURFACE_VARIANT),
        ], spacing=10)
        hint = ft.Text(
            "Ukryta pozycja znika z formularza kucharzy, ale jej dawne straty "
            "zostają w raportach. Usuwaj tylko to, czego nigdy nie było.",
            size=12.5, color=ft.Colors.ON_SURFACE_VARIANT)
        return ft.Column([head, table, hint], spacing=12, expand=True)

    def toggle_item(self, item):
        db.set_item_active(self.c, item["id"], not item["active"])
        self.refresh()

    def delete_item(self, item):
        used = db.item_usage(self.c, item["id"])
        if used:
            self.confirm(
                "Pozycja ma historię",
                f"„{item['name']}” ma {used} zapisanych strat.\n\n"
                "Usunięcie skasuje też te straty i zmieni dawne raporty. "
                "Zwykle lepiej ją ukryć.",
                lambda: (db.delete_item(self.c, item["id"], force=True),
                         self.refresh(), self.toast("Pozycja usunięta")),
                yes_text="Usuń mimo to")
        else:
            self.confirm(
                "Usunąć pozycję?", f"„{item['name']}”",
                lambda: (db.delete_item(self.c, item["id"]), self.refresh(),
                         self.toast("Pozycja usunięta")),
                yes_text="Usuń")

    def item_dialog(self, item=None):
        state = {"unit": item["unit"] if item else "szt"}
        name = self.field("Nazwa", item["name"] if item else "", width=380)
        cat = self.field("Kategoria", (item["category"] or "") if item else "",
                         width=380, hint="np. Mięso")
        unit_row = ft.Row(spacing=6)
        error = ft.Text("", size=14, color=BAD_COLOR, visible=False)

        def paint():
            unit_row.controls = []
            for u in UNIT_OPTIONS:
                on = u == state["unit"]
                unit_row.controls.append(ft.Container(
                    content=ft.Text(u, size=14.5,
                                    color=("#231604" if on
                                           else ft.Colors.ON_SURFACE_VARIANT),
                                    weight=ft.FontWeight.W_700 if on else None),
                    bgcolor=(ACCENT if on
                             else ft.Colors.SURFACE_CONTAINER_LOWEST),
                    border=ft.Border.all(1, ACCENT if on
                                         else ft.Colors.OUTLINE_VARIANT),
                    border_radius=999, ink=True, width=56,
                    alignment=ft.Alignment.CENTER,
                    padding=ft.Padding(0, 8, 0, 8),
                    on_click=lambda e, x=u: pick(x)))

        def pick(u):
            state["unit"] = u
            paint()
            self.page.update()

        def save(e):
            try:
                db.save_item(self.c, {
                    "id": item["id"] if item else None,
                    "name": name.value, "category": cat.value,
                    "unit": state["unit"],
                    "active": item["active"] if item else 1})
            except ValueError as err:
                error.value = str(err)
                error.visible = True
                self.page.update()
                return
            self.page.pop_dialog()
            self.refresh()
            self.toast("Zapisano")

        paint()
        used = db.item_usage(self.c, item["id"]) if item else 0
        hint = ft.Text(
            (f"Pozycja ma {used} zapisanych strat — jednostkę można zmienić "
             "tylko w tej samej grupie (kg↔g, l↔ml)." if used
             else "Jednostkę zmienisz później tylko w tej samej grupie."),
            size=12.5, color=ft.Colors.ON_SURFACE_VARIANT)

        dlg = ft.AlertDialog(
            title=ft.Text("Popraw pozycję" if item else "Nowa pozycja",
                          size=16, weight=ft.FontWeight.W_700),
            content=ft.Column([
                name, cat,
                ft.Text("Jednostka", size=13.5,
                        color=ft.Colors.ON_SURFACE_VARIANT),
                unit_row, hint, error,
            ], spacing=12, width=420, height=330),
            actions=[ft.TextButton("Anuluj",
                                   on_click=lambda e: self.page.pop_dialog()),
                     ft.FilledButton("Zapisz", on_click=save)],
            shape=ft.RoundedRectangleBorder(radius=18))
        self.page.show_dialog(dlg)

    def seed_catalog(self):
        self.confirm(
            "Wgrać katalog startowy?",
            "Dodamy brakujące pozycje i powody z pliku startowego. "
            "Istniejące zostaną nietknięte.",
            lambda: self._do_seed(), yes_text="Wgraj")

    def _do_seed(self):
        try:
            res = db.seed(self.c)
        except (OSError, ValueError) as err:
            self.dialog("error", "Nie udało się", str(err))
            return
        self.refresh()
        self.toast(f"Dodano {res['items']} pozycji i {res['reasons']} powodów")

    def _settings_reasons(self):
        reasons = db.list_reasons(self.c, active_only=False)
        new = self.field("Nowy powód", width=260)

        def add(e):
            try:
                db.save_reason(self.c, {"name": new.value})
            except ValueError as err:
                self.toast(str(err), good=False)
                return
            self.refresh()

        new.on_submit = add
        rows = [(r["name"],
                 ft.IconButton(ft.Icons.DELETE_OUTLINE, icon_size=16,
                               icon_color=BAD_COLOR,
                               on_click=lambda e, x=r: self.delete_reason(x)))
                for r in reasons]
        return ft.Column([
            ft.Row([new, ft.FilledButton("Dodaj", icon=ft.Icons.ADD,
                                         on_click=add)],
                   spacing=10, vertical_alignment=ft.CrossAxisAlignment.CENTER),
            self.table([("Powód",), ("",)], rows, widths=[None, 60],
                       empty="Nie ma jeszcze powodów."),
            ft.Text("Powody widzi kucharz w formularzu. W zapisanych stratach "
                    "powód zostaje tekstem, więc usunięcie niczego nie psuje.",
                    size=12.5, color=ft.Colors.ON_SURFACE_VARIANT),
        ], spacing=12, expand=True)

    def delete_reason(self, reason):
        db.delete_reason(self.c, reason["id"])
        self.refresh()

    def _settings_server(self):
        cfg = db.get_config(self.c)
        kitchen = self.field("Nazwa kuchni", cfg.get("kitchen_name", ""),
                             width=380)
        url = self.field("Adres serwera", cfg.get("server_url", ""), width=380,
                         hint="https://kuchnia.htsautomation.com")
        token = self.field("Token kuchni", cfg.get("server_token", ""),
                           width=380, hint="z panelu /admin serwera")
        status = ft.Text("", size=14, color=ft.Colors.ON_SURFACE_VARIANT)

        def save(e=None):
            db.set_config_many(self.c, {
                "kitchen_name": (kitchen.value or "Kuchnia").strip(),
                "server_url": (url.value or "").strip(),
                "server_token": (token.value or "").strip()})
            self.cfg = db.get_config(self.c)
            self.toast("Zapisano ustawienia serwera")

        def check(e):
            save()
            self.page.run_task(self._check_server, status)

        def push(e):
            save()
            self.page.run_task(self._push_catalog, status)

        return ft.Column([
            self.card(
                "Połączenie z serwerem zgłoszeń", ft.Icons.CLOUD_SYNC,
                ft.Column([
                    kitchen, url, token,
                    ft.Row([
                        ft.FilledButton("Zapisz", icon=ft.Icons.SAVE,
                                        on_click=save),
                        ft.OutlinedButton("Sprawdź połączenie",
                                          icon=ft.Icons.WIFI_TETHERING,
                                          on_click=check),
                        ft.OutlinedButton("Wyślij katalog na serwer",
                                          icon=ft.Icons.UPLOAD,
                                          on_click=push),
                    ], spacing=10),
                    status,
                ], spacing=12)),
            self.card(
                "Jak to działa", ft.Icons.HELP_OUTLINE,
                ft.Text(
                    "1. W panelu /admin serwera dodajesz kuchnię i kopiujesz "
                    "jej token — wklejasz go powyżej.\n"
                    "2. „Wyślij katalog na serwer” pokazuje kucharzom aktualne "
                    "pozycje i powody. Rób to po każdej zmianie katalogu.\n"
                    "3. Kucharz skanuje QR z panelu, wpisuje straty i wysyła "
                    "zgłoszenie.\n"
                    "4. Ty odbierasz je w zakładce „Zgłoszenia” i zatwierdzasz "
                    "— dopiero wtedy trafiają do bazy na tym komputerze.",
                    size=14.5)),
        ], spacing=14, expand=True, scroll=ft.ScrollMode.AUTO)

    def _settings_about(self):
        return self.card(
            "Straty Kuchnia", ft.Icons.INFO,
            ft.Column([
                ft.Text(f"Wersja {APP_VERSION}", size=15),
                ft.Text(f"Baza danych: {db.DB_PATH}", size=14,
                        color=ft.Colors.ON_SURFACE_VARIANT, selectable=True),
                ft.Text("Baza leży obok programu. Kopia zapasowa = skopiowanie "
                        "tego pliku, np. raz w miesiącu na dysk sieciowy.",
                        size=14, color=ft.Colors.ON_SURFACE_VARIANT),
                ft.Row([
                    ft.OutlinedButton("Kopia zapasowa bazy",
                                      icon=ft.Icons.BACKUP,
                                      on_click=lambda e: self.page.run_task(
                                          self.backup_db)),
                ], spacing=10),
            ], spacing=10))

    # ═══════════════════════ сеть и файлы ═══════════════════════
    async def load_reports(self, manual=False):
        cfg = db.get_config(self.c)
        ok, data = await sync.fetch_reports(cfg.get("server_url"),
                                            cfg.get("server_token"))
        if ok:
            # уже подтверждённые не показываем второй раз: сервер мог не
            # успеть удалить их, если интернет отвалился на полпути
            self.reports = [r for r in data
                            if not db.is_report_confirmed(self.c, r["id"])]
            self.server_ok = True
        else:
            self.server_ok = False
            self.reports_status = data
        self._paint_badge()
        if self.selected == "reports" or manual:
            self.refresh()
        else:
            self.page.update()

    async def _drop_report_on_server(self, report_id):
        cfg = db.get_config(self.c)
        await sync.delete_report(cfg.get("server_url"),
                                 cfg.get("server_token"), report_id)

    async def _check_server(self, status):
        cfg = db.get_config(self.c)
        ok, data = await sync.whoami(cfg.get("server_url"),
                                     cfg.get("server_token"))
        if ok:
            status.value = (f"✓ Połączono: {data.get('kitchen')} · "
                            f"w kolejce {data.get('pending', 0)}")
            status.color = OK_COLOR
        else:
            status.value = f"✗ {data}"
            status.color = BAD_COLOR
        self.page.update()

    async def _push_catalog(self, status):
        cfg = db.get_config(self.c)
        ok, data = await sync.push_catalog(cfg.get("server_url"),
                                           cfg.get("server_token"),
                                           db.catalog_for_server(self.c))
        if ok:
            status.value = (f"✓ Wysłano {data.get('items', 0)} pozycji — "
                            "kucharze widzą już nowy katalog")
            status.color = OK_COLOR
        else:
            status.value = f"✗ {data}"
            status.color = BAD_COLOR
        self.page.update()

    async def export_excel(self, month):
        name = (f"Straty-{month}.xlsx" if month
                else "Straty-caly-okres.xlsx")
        path = await self.fp.save_file(dialog_title="Zapisz raport",
                                       file_name=name,
                                       allowed_extensions=["xlsx"])
        if not path:
            return
        if not str(path).lower().endswith(".xlsx"):
            path = f"{path}.xlsx"
        try:
            export_xlsx.build(self.c, db.get_config(self.c, "kitchen_name"),
                              month, path)
        except (OSError, PermissionError) as err:
            self.dialog("error", "Nie udało się zapisać",
                        f"{err}\n\nCzy plik nie jest otwarty w Excelu?")
            return
        self.dialog("success", "Raport gotowy", str(path))

    async def backup_db(self):
        stamp = date.today().isoformat()
        path = await self.fp.save_file(dialog_title="Kopia zapasowa bazy",
                                       file_name=f"kuchnia-{stamp}.db",
                                       allowed_extensions=["db"])
        if not path:
            return
        try:
            # .backup, а не копирование файла: снимок корректен даже если
            # в базу прямо сейчас пишут
            target = db.connect(str(path))
            with target:
                self.c.backup(target)
            target.close()
        except Exception as err:                          # noqa: BLE001
            self.dialog("error", "Nie udało się", str(err))
            return
        self.dialog("success", "Kopia zapisana", str(path))


def log_crash() -> str:
    """Пишет трейсбек в файл рядом с программой и возвращает путь.

    У собранного .exe нет консоли: без этого файла падение выглядит как
    «приложение просто не запустилось», и чинить нечего.
    """
    path = os.path.join(data_dir(), "kuchnia-error.log")
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write(f"\n=== {datetime.now().isoformat(timespec='seconds')} "
                    f"· v{APP_VERSION} · python {sys.version.split()[0]}\n")
            traceback.print_exc(file=f)
    except OSError:
        pass          # некуда писать — хотя бы покажем ошибку в окне
    return path


def main(page: ft.Page):
    try:
        KuchniaApp(page)
    except Exception:                                     # noqa: BLE001
        # Показываем ошибку прямо в окне: менеджеру нужно что-то, что можно
        # прислать мне, а не пустой экран.
        path = log_crash()
        page.title = "Straty Kuchnia — błąd startu"
        page.add(ft.Container(
            padding=ft.Padding(28, 28, 28, 28),
            content=ft.Column([
                ft.Row([ft.Icon(ft.Icons.ERROR, color=BAD_COLOR, size=28),
                        ft.Text("Program nie mógł się uruchomić", size=20,
                                weight=ft.FontWeight.W_800)], spacing=10),
                ft.Text(f"Szczegóły zapisano w:\n{path}", size=14.5,
                        selectable=True),
                ft.Text(traceback.format_exc(), size=12, selectable=True,
                        font_family="Consolas"),
            ], spacing=14, scroll=ft.ScrollMode.AUTO, expand=True)))
        page.update()


if __name__ == "__main__":
    try:
        ft.run(main, assets_dir=resource_path("assets"))
    except Exception:                                     # noqa: BLE001
        # Падение ещё до окна (нет рантайма Flet, занят порт, права на папку)
        print("Błąd startu — szczegóły w", log_crash(), file=sys.stderr)
        raise
