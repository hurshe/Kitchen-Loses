"""Раздел «Checklisty» менеджерского приложения.

Три экрана в одном пункте меню:
    Archiwum  — заполненные копии: фильтры, сортировка, превью, печать
    Szablony  — список шаблонов и их отправка на сервер
    редактор  — конструктор шаблона (секции, колонки, строки, допуски)

Вынесено из main_kuchnia.py примесью: там и так полторы тысячи строк, а
этот раздел от страт не зависит — ему нужны только общие «кирпичи»
(card, table, chips, dialog), которые он берёт у KuchniaApp.
"""

import copy
import json
import os
import tempfile
import webbrowser
from datetime import date

import flet as ft

import checklists as cl
import db
import sync

ACCENT = "#D9952E"
OK_COLOR = "#34D399"
BAD_COLOR = "#F87171"
WARN_COLOR = "#FBBF24"

AUTO_FETCH_SEC = 300
# ширина колонок в превью (на экране менеджера), по типу ячейки
PREVIEW_W = {"check": 96, "yesno": 80, "text": 180, "number": 90, "temp": 90,
             "time": 76, "date": 104, "choice": 120, "sign": 130}
PREVIEW_LABEL_W = 320           # минимум; узкий лист растягивается до окна
PREVIEW_SHEET_W = 1040


def _lim_str(v) -> str:
    """Лимит для поля ввода: число → «5», строка (ещё печатают) → как есть."""
    if v is None or v == "":
        return ""
    if isinstance(v, (int, float)):
        return db.fmt_num(v)
    return str(v)


def _move(lst: list, i: int, delta: int):
    j = i + delta
    if 0 <= j < len(lst):
        lst[i], lst[j] = lst[j], lst[i]


class ChecklistsMixin:
    # ─────────────────────────── состояние ───────────────────────────
    def cl_init(self):
        self.cl_tab = "archive"
        self.cl_month = ""                  # "" = cały okres
        self.cl_tpl_filter = ""
        self.cl_search = ""
        self.cl_sort = "date"
        self.cl_desc = True
        self.cl_edit = None                 # рабочая копия шаблона в редакторе
        self.cl_dirty = False
        self.cl_status = ""                 # строка про связь с сервером
        self.cl_status_ok = True

    def cl_paint_badge(self):
        if "checklists" not in self.nav_items:
            return
        badge = self.nav_items["checklists"][3]
        n = cl.unseen_count(self.c)
        badge.visible = bool(n)
        badge.content.value = str(n)

    # ─────────────────────────── экран ───────────────────────────
    def view_checklists(self):
        if self.cl_edit is not None:
            return self._cl_editor()

        status = ft.Text(self.cl_status, size=14,
                         color=(ft.Colors.ON_SURFACE_VARIANT
                                if self.cl_status_ok else BAD_COLOR))
        head = ft.Row([
            ft.Text("Checklisty", size=22, weight=ft.FontWeight.W_800),
            ft.Container(expand=True),
            status,
            ft.FilledTonalButton("Odśwież", icon=ft.Icons.REFRESH,
                                 tooltip="Pobierz wypełnione checklisty "
                                         "z serwera",
                                 on_click=lambda e: self.page.run_task(
                                     self.load_reports, True)),
        ], spacing=10, vertical_alignment=ft.CrossAxisAlignment.CENTER)
        n_tpl = len(cl.list_templates(self.c))
        tabs = self.chips([("archive", "Archiwum"),
                           ("templates", f"Szablony ({n_tpl})")],
                          self.cl_tab, self._cl_set_tab)
        body = (self._cl_archive() if self.cl_tab == "archive"
                else self._cl_templates())
        return ft.Column([head, tabs, body], spacing=14, expand=True)

    def _cl_set_tab(self, key):
        self.cl_tab = key
        self.refresh()

    # ═══════════════════════ Archiwum ═══════════════════════
    def _cl_archive(self):
        entries = cl.list_entries(
            self.c, template_name=self.cl_tpl_filter or None,
            month=self.cl_month or None, search=self.cl_search or None,
            sort=self.cl_sort, desc=self.cl_desc)
        stats = cl.archive_stats(self.c, self.cl_month or None)

        kpis = ft.Row([
            self.kpi("kopii w okresie", stats["entries"]),
            self.kpi("z odchyleniami", stats["with_issues"],
                     color=BAD_COLOR if stats["with_issues"] else None),
            self.kpi("nowych (nieotwartych)", stats["unseen"],
                     color=ACCENT if stats["unseen"] else None),
        ], spacing=12)

        def dd(label, value, options, on_pick, width=230):
            return ft.Dropdown(
                label=label, value=value, width=width, dense=True,
                text_size=14.5, border_color=ft.Colors.OUTLINE_VARIANT,
                border_radius=14, bgcolor=ft.Colors.SURFACE_CONTAINER_LOWEST,
                options=options, on_select=lambda e: on_pick(e.control.value))

        months = cl.entry_months(self.c)
        if self.cl_month and self.cl_month not in months:
            months.append(self.cl_month)
        month_dd = dd("Okres", self.cl_month,
                      [ft.DropdownOption(key="", text="Cały okres")]
                      + [ft.DropdownOption(key=m, text=db.month_label_pl(m))
                         for m in sorted(months, reverse=True)],
                      lambda v: self._cl_set("cl_month", v), width=200)
        tpl_dd = dd("Checklista", self.cl_tpl_filter,
                    [ft.DropdownOption(key="", text="wszystkie")]
                    + [ft.DropdownOption(key=n, text=n)
                       for n in cl.entry_template_names(self.c)],
                    lambda v: self._cl_set("cl_tpl_filter", v), width=280)
        search = self.field("Szukaj (osoba, data)", self.cl_search, width=210,
                            on_change=lambda e: setattr(
                                self, "cl_search", e.control.value or ""))
        search.on_submit = lambda e: self.refresh()

        # Row(wrap=True) — без распорок с expand, см. _summary_entries
        filters = ft.Row([
            month_dd, tpl_dd, search,
            ft.OutlinedButton("Oznacz wszystkie jako przeczytane",
                              icon=ft.Icons.DONE_ALL,
                              visible=bool(cl.unseen_count(self.c)),
                              on_click=lambda e: self._cl_mark_all()),
            ft.OutlinedButton("Kopie zapasowe", icon=ft.Icons.BACKUP,
                              on_click=lambda e: self.cl_backup_dialog()),
        ], spacing=10, wrap=True, run_spacing=8,
            vertical_alignment=ft.CrossAxisAlignment.CENTER)

        rows = []
        for e in entries:
            fresh = not e["seen"]
            rows.append((
                ft.Row([
                    ft.Container(width=8, height=8, border_radius=4,
                                 bgcolor=ACCENT if fresh else None),
                    ft.Text(e["entry_date"], size=14.5,
                            weight=ft.FontWeight.W_700 if fresh else None),
                ], spacing=8),
                e["template_name"] or "—",
                e["person"] or "—",
                f"{e['filled']} / {e['total']}",
                ft.Text(str(e["issues"]) if e["issues"] else "—", size=14.5,
                        color=BAD_COLOR if e["issues"] else None,
                        weight=ft.FontWeight.W_700 if e["issues"] else None,
                        text_align=ft.TextAlign.RIGHT),
                (e["received_at"] or "")[:16].replace("T", " "),
                ft.Row([
                    ft.IconButton(ft.Icons.PRINT, icon_size=16,
                                  tooltip="Drukuj / zapisz jako PDF",
                                  on_click=lambda ev, x=e: self.cl_print(
                                      x["id"])),
                    ft.IconButton(ft.Icons.DELETE_OUTLINE, icon_size=16,
                                  tooltip="Usuń kopię", icon_color=BAD_COLOR,
                                  on_click=lambda ev, x=e: self.cl_delete_entry(
                                      x)),
                ], spacing=0)))

        table = self.table(
            [("Data", "l", "date"), ("Checklista", "l", "name"),
             ("Osoba", "l", "person"), ("Wypełniono", "r"),
             ("Odchylenia", "l", "issues"), ("Odebrano", "l", "received"),
             ("",)],
            rows, widths=[130, None, 150, 110, 110, 150, 90],
            empty=("Archiwum jest puste. Wypełnione checklisty pojawią się "
                   "tutaj po kliknięciu „Odśwież”."
                   if not (self.cl_month or self.cl_tpl_filter
                           or self.cl_search)
                   else "Brak kopii dla tych filtrów."),
            on_row_click=lambda n: self.cl_preview_entry(entries[n]["id"]),
            on_sort=self._cl_set_sort, sort_active=self.cl_sort,
            sort_arrow="▼" if self.cl_desc else "▲")
        hint = ft.Text(
            "Kliknij nagłówek, żeby posortować (drugie kliknięcie odwraca "
            "kolejność) · kliknij wiersz, żeby zobaczyć kopię.",
            size=12.5, color=ft.Colors.ON_SURFACE_VARIANT)
        return ft.Column([kpis, filters, table, hint], spacing=12,
                         expand=True)

    def _cl_set(self, attr, value):
        setattr(self, attr, value or "")
        self.refresh()

    def _cl_set_sort(self, key):
        if key == self.cl_sort:
            self.cl_desc = not self.cl_desc
        else:
            self.cl_sort = key
            self.cl_desc = key in ("date", "received", "issues")
        self.refresh()

    def _cl_mark_all(self):
        cl.mark_seen(self.c)
        self.cl_paint_badge()
        self.refresh()

    def cl_delete_entry(self, e):
        self.confirm(
            "Usunąć kopię?",
            f"{e['template_name']} · {e['entry_date']} · "
            f"{e['person'] or '—'}\n\nTej operacji nie da się cofnąć "
            "(chyba że masz kopię zapasową).",
            lambda: (cl.delete_entry(self.c, e["id"]), self.cl_paint_badge(),
                     self.refresh(), self.toast("Kopia usunięta")),
            yes_text="Usuń")

    # ─── превью: одна и та же сетка для копии и для пустого шаблона ───
    def _cl_grid(self, s, values, bad):
        def cell(content, width, center=False, bg=None):
            if not isinstance(content, ft.Control):
                content = ft.Text(str(content), size=13.5)
            return ft.Container(
                content=content, width=width, bgcolor=bg,
                padding=ft.Padding(8, 7, 8, 7),
                alignment=(ft.Alignment.CENTER if center
                           else ft.Alignment.CENTER_LEFT),
                border=ft.Border(
                    right=ft.BorderSide(1, ft.Colors.OUTLINE_VARIANT)))

        def head(text, width, center=True):
            return cell(ft.Text(text, size=12, color=ACCENT,
                                weight=ft.FontWeight.W_700,
                                text_align=(ft.TextAlign.CENTER if center
                                            else ft.TextAlign.LEFT)),
                        width, center)

        widths = [PREVIEW_W.get(c["type"], 110) for c in s["columns"]]
        label_w = max(PREVIEW_LABEL_W, PREVIEW_SHEET_W - 24 - sum(widths))
        lines = [ft.Container(
            content=ft.Row(
                [head(s["label_title"] or "Pozycja", label_w, False)]
                + [head(c["label"] + (" *" if c["required"] else ""), w)
                   for c, w in zip(s["columns"], widths)], spacing=0,
                intrinsic_height=True,
                vertical_alignment=ft.CrossAxisAlignment.STRETCH),
            bgcolor=ft.Colors.SURFACE_CONTAINER_HIGH)]
        for n, r in enumerate(s["rows"]):
            lim = cl.limits_text(r["min"], r["max"])
            label = ft.Column(
                [ft.Text(r["label"] or "—", size=13.5)]
                + ([ft.Text(f"norma: {lim}", size=11.5,
                            color=ft.Colors.ON_SURFACE_VARIANT)] if lim else []),
                spacing=1, tight=True)
            cells = [cell(label, label_w)]
            for c, w in zip(s["columns"], widths):
                key = cl.cell_key(s["id"], r["id"], c["id"])
                if c["id"] in r["off"]:
                    cells.append(cell(
                        ft.Text("╳", size=13,
                                color=ft.Colors.OUTLINE_VARIANT), w, True,
                        bg=ft.Colors.SURFACE_CONTAINER_HIGH))
                    continue
                text = cl.format_value(c, values.get(key))
                is_bad = key in bad
                cells.append(cell(
                    ft.Text(text, size=13.5,
                            color=(BAD_COLOR if is_bad else
                                   OK_COLOR if c["type"] == "check" else None),
                            weight=(ft.FontWeight.W_700
                                    if is_bad or c["type"] == "check"
                                    else None)),
                    w, center=c["type"] not in ("text", "sign", "choice"),
                    bg=(ft.Colors.with_opacity(0.14, BAD_COLOR)
                        if is_bad else None)))
            lines.append(ft.Container(
                # intrinsic_height обязателен: STRETCH в прокручиваемой
                # колонке иначе просит бесконечную высоту и сетка не рисуется
                content=ft.Row(cells, spacing=0, intrinsic_height=True,
                               vertical_alignment=ft.CrossAxisAlignment.STRETCH),
                bgcolor=(ft.Colors.SURFACE_CONTAINER_LOWEST if n % 2 else None),
                border=ft.Border(
                    top=ft.BorderSide(1, ft.Colors.OUTLINE_VARIANT))))
        if not s["rows"]:
            lines.append(ft.Container(
                padding=ft.Padding(10, 10, 10, 10),
                content=ft.Text(
                    "wiersze dopisuje osoba wypełniająca"
                    if s["mode"] == "free" else "—", size=13,
                    color=ft.Colors.ON_SURFACE_VARIANT)))
        total = label_w + sum(widths)
        grid = ft.Container(
            content=ft.Column(lines, spacing=0, tight=True), width=total,
            border=ft.Border.all(1, ft.Colors.OUTLINE_VARIANT),
            border_radius=10, clip_behavior=ft.ClipBehavior.HARD_EDGE)
        # широкий лист (10 колонок) прокручивается вбок, а не сжимается
        return ft.Row([grid], scroll=ft.ScrollMode.AUTO)

    def _cl_sheet(self, entry, empty=False):
        """Содержимое листа: мета, секции, подписи. `entry` — копия из
        архива или «пустая копия» шаблона (для кнопки «Podgląd»)."""
        tpl = entry["template"]
        values = entry.get("values") or {}
        res = cl.evaluate(entry)
        body = []
        if tpl.get("description"):
            body.append(ft.Text(tpl["description"], size=14,
                                color=ft.Colors.ON_SURFACE_VARIANT))

        def pair(label, value):
            return ft.Row([
                ft.Text(f"{label}:", size=13.5,
                        color=ft.Colors.ON_SURFACE_VARIANT),
                ft.Text(value or "—", size=14, weight=ft.FontWeight.W_700),
            ], spacing=6, tight=True)

        meta = [pair("Data", "" if empty else entry.get("entry_date")),
                pair("Kto wypełnia", "" if empty else entry.get("person"))]
        hv = entry.get("header") or {}
        for f in tpl["header"]:
            meta.append(pair(f["label"] + (" *" if f["required"] else ""),
                             cl.format_value(f, hv.get(f["id"]))
                             or (f"[{cl.TYPE_LABELS[f['type']]}]" if empty
                                 else "")))
        body.append(ft.Row(meta, spacing=22, wrap=True, run_spacing=6))

        if res["issues"] and not empty:
            body.append(ft.Container(
                content=ft.Row([
                    ft.Icon(ft.Icons.WARNING_AMBER, size=18, color=BAD_COLOR),
                    ft.Text(f"Odchylenia od normy: {res['issues']} — "
                            "zaznaczone na czerwono", size=14)], spacing=8),
                bgcolor=ft.Colors.with_opacity(0.12, BAD_COLOR),
                border_radius=12, padding=ft.Padding(12, 8, 12, 8)))

        for s in tpl["sections"]:
            if s["title"]:
                body.append(ft.Text(s["title"], size=15,
                                    weight=ft.FontWeight.W_800))
            body.append(self._cl_grid(s, values, res["bad"]))
            if s["sign"]:
                sig = values.get(cl.cell_key(s["id"], cl.SIGN_ROW, "_"))
                body.append(pair("Podpis", str(sig) if sig
                                 else ("[podpis]" if empty else "")))

        fv = entry.get("footer") or {}
        if tpl["footer"]:
            body.append(ft.Row(
                [pair(f["label"] + (" *" if f["required"] else ""),
                      cl.format_value(f, fv.get(f["id"]))
                      or (f"[{cl.TYPE_LABELS[f['type']]}]" if empty else ""))
                 for f in tpl["footer"]], spacing=22, wrap=True,
                run_spacing=6))
        return ft.Column(body, spacing=12, scroll=ft.ScrollMode.AUTO,
                         width=PREVIEW_SHEET_W, height=600)

    def cl_preview_entry(self, eid):
        entry = cl.get_entry(self.c, eid)
        if not entry:
            self.toast("Tej kopii już nie ma", good=False)
            return
        if not entry["seen"]:
            cl.mark_seen(self.c, eid)
            self.cl_paint_badge()
            self.refresh()
        sent = (entry.get("submitted_at") or "")[:16].replace("T", " ")
        dlg = ft.AlertDialog(
            title=ft.Column([
                ft.Text(entry["template"]["name"], size=17,
                        weight=ft.FontWeight.W_800),
                ft.Text(f"wypełniono {entry['filled']} z {entry['total']} · "
                        f"wysłano {sent or '—'} UTC · wersja szablonu "
                        f"{entry['template'].get('version', 1)}",
                        size=12.5, color=ft.Colors.ON_SURFACE_VARIANT),
            ], spacing=2, tight=True),
            content=self._cl_sheet(entry),
            actions=[
                ft.TextButton("Usuń", on_click=lambda e: (
                    self.page.pop_dialog(), self.cl_delete_entry(entry))),
                ft.OutlinedButton("Zapisz jako plik HTML",
                                  icon=ft.Icons.FILE_DOWNLOAD,
                                  on_click=lambda e: self.page.run_task(
                                      self.cl_save_html, eid)),
                ft.OutlinedButton("Drukuj / PDF", icon=ft.Icons.PRINT,
                                  on_click=lambda e: self.cl_print(eid)),
                ft.FilledButton("Zamknij",
                                on_click=lambda e: self.page.pop_dialog()),
            ],
            shape=ft.RoundedRectangleBorder(radius=18))
        self.page.show_dialog(dlg)

    def cl_preview_template(self, tpl):
        """Как лист выглядит незаполненным. Заодно проверяет шаблон: если
        в нём ошибка, покажем её здесь, а не при сохранении."""
        try:
            snap = cl.normalize_template(tpl)
        except ValueError as err:
            self.dialog("warning", "Szablon niekompletny", str(err))
            return
        entry = {"template": snap, "values": {}, "header": {}, "footer": {}}
        dlg = ft.AlertDialog(
            title=ft.Column([
                ft.Text(f"Podgląd — {snap['name']}", size=17,
                        weight=ft.FontWeight.W_800),
                ft.Text("Układ jak na wydruku. Na telefonie każdy wiersz "
                        "rozkłada się w kartę, na tablecie — w tabelę.",
                        size=12.5, color=ft.Colors.ON_SURFACE_VARIANT),
            ], spacing=2, tight=True),
            content=self._cl_sheet(entry, empty=True),
            actions=[ft.FilledButton(
                "Zamknij", on_click=lambda e: self.page.pop_dialog())],
            shape=ft.RoundedRectangleBorder(radius=18))
        self.page.show_dialog(dlg)

    def _cl_html(self, eid):
        entry = cl.get_entry(self.c, eid)
        if not entry:
            return None, None
        name = "".join(ch if ch.isalnum() or ch in " -_" else "_"
                       for ch in entry["template"]["name"]).strip()[:60]
        fname = (f"{entry['entry_date']} {name} "
                 f"{(entry['person'] or '').strip()}").strip() + ".html"
        return fname, cl.entry_html(entry, db.get_config(self.c,
                                                         "kitchen_name"))

    def cl_print(self, eid):
        """Открывает копию в браузере — оттуда печать или «Zapisz jako
        PDF». Своего PDF-движка в программе нет сознательно: браузер есть
        на любом компьютере и печатает лучше."""
        fname, text = self._cl_html(eid)
        if not text:
            self.toast("Tej kopii już nie ma", good=False)
            return
        try:
            folder = os.path.join(tempfile.gettempdir(), "kuchnia-checklisty")
            os.makedirs(folder, exist_ok=True)
            path = os.path.join(folder, fname)
            with open(path, "w", encoding="utf-8") as f:
                f.write(text)
            webbrowser.open("file:///" + path.replace("\\", "/").lstrip("/"))
        except OSError as err:
            self.dialog("error", "Nie udało się otworzyć", str(err))
            return
        self.toast("Otwarto w przeglądarce — tam wybierz „Drukuj”")

    async def cl_save_html(self, eid):
        fname, text = self._cl_html(eid)
        if not text:
            return
        path = await self.fp.save_file(dialog_title="Zapisz kopię",
                                       file_name=fname,
                                       allowed_extensions=["html"])
        if not path:
            return
        if not str(path).lower().endswith(".html"):
            path = f"{path}.html"
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(text)
        except OSError as err:
            self.dialog("error", "Nie udało się zapisać", str(err))
            return
        self.toast("Zapisano")

    # ─── kopie zapasowe ───
    def cl_backup_dialog(self):
        folder = db.get_config(self.c, "checklist_backup_dir", "")
        n = cl.archive_stats(self.c)["entries"]
        folder_text = ft.Text(
            folder or "nie ustawiono — kopie automatyczne są wyłączone",
            size=13.5, selectable=True,
            color=None if folder else ft.Colors.ON_SURFACE_VARIANT)

        async def pick_folder(e):
            path = await self.fp.get_directory_path(
                dialog_title="Folder na kopie automatyczne")
            if not path:
                return
            db.set_config(self.c, "checklist_backup_dir", str(path))
            db.set_config(self.c, "checklist_backup_sig", "")
            made = cl.auto_backup(self.c, str(path))
            folder_text.value = str(path)
            folder_text.color = None
            self.page.update()
            self.toast("Folder zapisany" + (" — pierwsza kopia gotowa"
                                            if made else ""))

        def off(e):
            db.set_config(self.c, "checklist_backup_dir", "")
            folder_text.value = ("nie ustawiono — kopie automatyczne są "
                                 "wyłączone")
            folder_text.color = ft.Colors.ON_SURFACE_VARIANT
            self.page.update()

        content = ft.Column([
            ft.Text(f"W archiwum: {n} wypełnionych kopii, "
                    f"{len(cl.list_templates(self.c))} szablonów.", size=14.5),
            ft.Text("Kopia to jeden plik ZIP z szablonami i wszystkimi "
                    "wypełnionymi checklistami (zwykły JSON w środku). "
                    "Przywracanie tylko dodaje brakujące — niczego nie "
                    "nadpisuje ani nie usuwa.", size=13.5,
                    color=ft.Colors.ON_SURFACE_VARIANT),
            ft.Row([
                ft.FilledButton("Zapisz kopię teraz", icon=ft.Icons.BACKUP,
                                on_click=lambda e: self.page.run_task(
                                    self.cl_backup_now)),
                ft.OutlinedButton("Przywróć z kopii", icon=ft.Icons.RESTORE,
                                  on_click=lambda e: self.page.run_task(
                                      self.cl_restore)),
            ], spacing=10),
            ft.Divider(height=18),
            ft.Text("Kopie automatyczne", size=15,
                    weight=ft.FontWeight.W_700),
            ft.Text("Wskaż folder (np. w OneDrive) — program sam zapisze "
                    f"tam kopię raz dziennie, gdy coś się zmieni, i trzyma "
                    f"{cl.BACKUP_KEEP} ostatnich plików.", size=13.5,
                    color=ft.Colors.ON_SURFACE_VARIANT),
            folder_text,
            ft.Row([
                ft.OutlinedButton("Wybierz folder", icon=ft.Icons.FOLDER_OPEN,
                                  on_click=pick_folder),
                ft.TextButton("Wyłącz", on_click=off),
            ], spacing=10),
        ], spacing=10, width=560, height=360, scroll=ft.ScrollMode.AUTO)
        dlg = ft.AlertDialog(
            title=ft.Text("Kopie zapasowe checklist", size=16,
                          weight=ft.FontWeight.W_700),
            content=content,
            actions=[ft.FilledButton(
                "Zamknij", on_click=lambda e: self.page.pop_dialog())],
            shape=ft.RoundedRectangleBorder(radius=18))
        self.page.show_dialog(dlg)

    async def cl_backup_now(self):
        path = await self.fp.save_file(
            dialog_title="Kopia zapasowa checklist",
            file_name=f"checklisty-{date.today().isoformat()}.zip",
            allowed_extensions=["zip"])
        if not path:
            return
        if not str(path).lower().endswith(".zip"):
            path = f"{path}.zip"
        try:
            info = cl.export_backup(self.c, str(path))
        except OSError as err:
            self.dialog("error", "Nie udało się zapisać", str(err))
            return
        self.dialog("success", "Kopia zapisana",
                    f"{info['entries']} kopii, {info['templates']} szablonów"
                    f"\n{path}")

    async def cl_restore(self):
        files = await self.fp.pick_files(
            dialog_title="Wybierz kopię zapasową checklist",
            allowed_extensions=["zip"])
        if not files:
            return
        try:
            res = cl.import_backup(self.c, files[0].path)
        except ValueError as err:
            self.dialog("error", "Nie udało się przywrócić", str(err))
            return
        self.page.pop_dialog()                  # закрыть окно «Kopie zapasowe»
        self.cl_paint_badge()
        self.refresh()
        self.dialog("success", "Przywrócono",
                    f"Dodano {res['entries']} kopii i {res['templates']} "
                    "szablonów."
                    + (f"\nPominięto uszkodzonych wpisów: {res['skipped']}."
                       if res["skipped"] else "")
                    + "\nTo, co już było w programie, zostało bez zmian.")
        if res["templates"]:
            self.cl_autopush()

    # ═══════════════════════ Szablony ═══════════════════════
    def _cl_templates(self):
        templates = cl.list_templates(self.c)
        rows = []
        for n, t in enumerate(templates):
            st = cl.template_stats(t)
            rows.append((
                ft.Row([
                    ft.Text(t["name"], size=14.5,
                            color=(None if t["active"]
                                   else ft.Colors.ON_SURFACE_VARIANT)),
                    ft.Container(
                        visible=not t["active"],
                        bgcolor=ft.Colors.SURFACE_CONTAINER_HIGHEST,
                        border_radius=999, padding=ft.Padding(8, 2, 8, 2),
                        content=ft.Text("ukryta", size=11.5,
                                        color=ft.Colors.ON_SURFACE_VARIANT)),
                ], spacing=8),
                cl.PERIOD_LABELS.get(t["period"], "—"),
                f"{st['sections']} sekcji · {st['rows']} wierszy",
                f"v{t['version']}",
                ft.Row([
                    ft.IconButton(ft.Icons.ARROW_UPWARD, icon_size=16,
                                  tooltip="Wyżej na liście", disabled=n == 0,
                                  on_click=lambda e, x=t: self._cl_tpl_move(
                                      x, -1)),
                    ft.IconButton(ft.Icons.ARROW_DOWNWARD, icon_size=16,
                                  tooltip="Niżej na liście",
                                  disabled=n == len(templates) - 1,
                                  on_click=lambda e, x=t: self._cl_tpl_move(
                                      x, 1)),
                    ft.IconButton(ft.Icons.PREVIEW, icon_size=16,
                                  tooltip="Podgląd",
                                  on_click=lambda e, x=t:
                                  self.cl_preview_template(x)),
                    ft.IconButton(ft.Icons.CONTENT_COPY, icon_size=16,
                                  tooltip="Duplikuj",
                                  on_click=lambda e, x=t: self._cl_tpl_copy(x)),
                    ft.IconButton(
                        ft.Icons.VISIBILITY if t["active"]
                        else ft.Icons.VISIBILITY_OFF, icon_size=16,
                        tooltip=("Schowaj — zniknie z telefonów, archiwum "
                                 "zostaje" if t["active"]
                                 else "Przywróć na telefony"),
                        on_click=lambda e, x=t: self._cl_tpl_toggle(x)),
                    ft.IconButton(ft.Icons.FILE_DOWNLOAD, icon_size=16,
                                  tooltip="Zapisz szablon do pliku",
                                  on_click=lambda e, x=t: self.page.run_task(
                                      self.cl_export_template, x)),
                    ft.IconButton(ft.Icons.DELETE_OUTLINE, icon_size=16,
                                  icon_color=BAD_COLOR, tooltip="Usuń",
                                  on_click=lambda e, x=t: self._cl_tpl_delete(
                                      x)),
                ], spacing=0)))
        table = self.table(
            [("Checklista",), ("Okres",), ("Zawartość",), ("Wersja",), ("",)],
            rows, widths=[None, 120, 190, 70, 290],
            empty="Nie ma jeszcze żadnej checklisty. Zacznij od „Nowy "
                  "szablon” albo wgraj szablony startowe.",
            on_row_click=lambda n: self.cl_open_editor(templates[n]))
        head = ft.Row([
            ft.FilledButton("Nowy szablon", icon=ft.Icons.ADD,
                            on_click=lambda e: self.cl_open_editor(None)),
            ft.OutlinedButton("Wgraj szablony startowe",
                              icon=ft.Icons.DOWNLOAD,
                              on_click=lambda e: self._cl_seed()),
            ft.OutlinedButton("Importuj z pliku", icon=ft.Icons.FILE_OPEN,
                              on_click=lambda e: self.page.run_task(
                                  self.cl_import_template)),
            ft.OutlinedButton("Wyślij na serwer", icon=ft.Icons.CLOUD_UPLOAD,
                              on_click=lambda e: self.cl_autopush(
                                  manual=True)),
        ], spacing=10, wrap=True, run_spacing=8)
        hint = ft.Text(
            "Kliknij wiersz, żeby otworzyć konstruktor. Po każdej zmianie "
            "szablony same jadą na serwer — kucharze widzą je od razu. "
            "Zmiana szablonu nie rusza wypełnionych już kopii.",
            size=12.5, color=ft.Colors.ON_SURFACE_VARIANT)
        return ft.Column([head, table, hint], spacing=12, expand=True)

    def _cl_tpl_move(self, t, delta):
        cl.move_template(self.c, t["id"], delta)
        self.refresh()
        self.cl_autopush()

    def _cl_tpl_copy(self, t):
        cl.duplicate_template(self.c, t["id"])
        self.refresh()
        self.cl_autopush()

    def _cl_tpl_toggle(self, t):
        cl.set_template_active(self.c, t["id"], not t["active"])
        self.refresh()
        self.cl_autopush()

    def _cl_tpl_delete(self, t):
        self.confirm(
            "Usunąć szablon?",
            f"„{t['name']}” zniknie z telefonów. Wypełnione kopie zostają "
            "w archiwum.",
            lambda: (cl.delete_template(self.c, t["id"]), self.refresh(),
                     self.cl_autopush()),
            yes_text="Usuń")

    def _cl_seed(self):
        added = cl.seed_presets(self.c)
        self.refresh()
        if added:
            self.toast(f"Dodano szablonów: {added}")
            self.cl_autopush()
        else:
            self.toast("Szablony startowe już są na liście")

    async def cl_export_template(self, t):
        path = await self.fp.save_file(
            dialog_title="Zapisz szablon", allowed_extensions=["json"],
            file_name=f"checklista-{t['name'][:40]}.json")
        if not path:
            return
        if not str(path).lower().endswith(".json"):
            path = f"{path}.json"
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(cl.normalize_template(t), f, ensure_ascii=False,
                          indent=1)
        except OSError as err:
            self.dialog("error", "Nie udało się zapisać", str(err))
            return
        self.toast("Szablon zapisany do pliku")

    async def cl_import_template(self):
        files = await self.fp.pick_files(dialog_title="Wybierz plik szablonu",
                                         allowed_extensions=["json"])
        if not files:
            return
        try:
            with open(files[0].path, encoding="utf-8") as f:
                cl.import_template(self.c, json.load(f))
        except (OSError, ValueError) as err:
            self.dialog("error", "Nie udało się wczytać",
                        f"To nie wygląda na szablon checklisty.\n{err}")
            return
        self.refresh()
        self.toast("Szablon dodany")
        self.cl_autopush()

    # ═══════════════════════ Konstruktor ═══════════════════════
    def cl_open_editor(self, tpl=None):
        if tpl is None:
            work = cl.blank_template()
            work.update(id=None, active=1, version=0)
        else:
            work = copy.deepcopy(tpl)
        self.cl_edit = work
        self.cl_dirty = False
        self.refresh()

    def _cl_close_editor(self):
        def close():
            self.cl_edit = None
            self.cl_dirty = False
            self.cl_tab = "templates"
            self.refresh()

        if self.cl_dirty:
            self.confirm("Odrzucić zmiany?",
                         "Zmiany w szablonie nie zostały zapisane.", close,
                         yes_text="Odrzuć")
        else:
            close()

    def _cl_save_editor(self):
        try:
            cl.save_template(self.c, self.cl_edit)
        except ValueError as err:
            self.dialog("warning", "Nie można zapisać", str(err))
            return
        self.cl_edit = None
        self.cl_dirty = False
        self.cl_tab = "templates"
        self.refresh()
        self.cl_autopush(saved=True)

    def _cl_tf(self, label, value, width=None, on_change=None, hint=None,
               expand=None, multiline=False):
        return ft.TextField(
            label=label, value=value, width=width, expand=expand, dense=True,
            text_size=14.5, hint_text=hint, multiline=multiline,
            content_padding=ft.Padding(10, 10, 10, 10), border_radius=12,
            border_color=ft.Colors.OUTLINE_VARIANT,
            focused_border_color=ACCENT, on_change=on_change,
            bgcolor=ft.Colors.SURFACE_CONTAINER_LOWEST)

    def _cl_editor(self):
        t = self.cl_edit
        is_new = not t.get("id")

        def dirty():
            self.cl_dirty = True

        def setter(obj, key, conv=None):
            def handler(e):
                v = e.control.value
                obj[key] = conv(v) if conv else v
                dirty()
            return handler

        def icon_btn(icon, tip, fn, color=None, disabled=False):
            return ft.IconButton(icon, icon_size=17, tooltip=tip,
                                 icon_color=color, disabled=disabled,
                                 on_click=lambda e: fn())

        # ── строка поля / колонки: название, тип и то, что типу нужно ──
        def field_rows(items, repaint, noun, add_label, limit):
            out = []
            for i, f in enumerate(items):
                def set_type(e, f=f):
                    f["type"] = e.control.value
                    if f["type"] == "temp":
                        f["unit"] = "°C"
                    dirty()
                    repaint()

                def up(i=i):
                    _move(items, i, -1)
                    dirty()
                    repaint()

                def down(i=i):
                    _move(items, i, 1)
                    dirty()
                    repaint()

                def drop(f=f):
                    items.remove(f)
                    dirty()
                    repaint()

                ctrls = [
                    self._cl_tf(f"Nazwa {noun}", f.get("label", ""), 270,
                                setter(f, "label")),
                    ft.Dropdown(
                        label="Typ", value=f.get("type", "check"), width=190,
                        dense=True, text_size=14.5, border_radius=12,
                        border_color=ft.Colors.OUTLINE_VARIANT,
                        bgcolor=ft.Colors.SURFACE_CONTAINER_LOWEST,
                        options=[ft.DropdownOption(key=k, text=label)
                                 for k, label in cl.FIELD_TYPES],
                        on_select=set_type),
                ]
                if f.get("type") in cl.NUMERIC:
                    ctrls += [
                        self._cl_tf("min", _lim_str(f.get("min")), 74,
                                    setter(f, "min")),
                        self._cl_tf("max", _lim_str(f.get("max")), 74,
                                    setter(f, "max")),
                    ]
                    if f["type"] == "number":
                        ctrls.append(self._cl_tf("jedn.", f.get("unit", ""),
                                                 70, setter(f, "unit")))
                if f.get("type") == "choice":
                    ctrls.append(self._cl_tf(
                        "Opcje (po przecinku)",
                        ", ".join(f.get("options") or []), 250,
                        setter(f, "options", lambda v: [
                            x.strip() for x in (v or "").split(",")
                            if x.strip()]), hint="np. POT, MICRO"))
                ctrls += [
                    # ширина задана явно: в Row(wrap) чекбокс без неё
                    # занимает всю строку и выталкивает кнопки на новую
                    ft.Checkbox(label="wymagane", width=130,
                                value=bool(f.get("required")),
                                on_change=setter(f, "required", bool)),
                    icon_btn(ft.Icons.ARROW_UPWARD, "Przesuń wyżej", up,
                             disabled=i == 0),
                    icon_btn(ft.Icons.ARROW_DOWNWARD, "Przesuń niżej", down,
                             disabled=i == len(items) - 1),
                    icon_btn(ft.Icons.DELETE_OUTLINE, "Usuń", drop, BAD_COLOR),
                ]
                out.append(ft.Row(ctrls, spacing=8, wrap=True, run_spacing=6,
                                  vertical_alignment=ft.CrossAxisAlignment.CENTER))

            def add():
                if len(items) >= limit:
                    self.toast(f"Najwyżej {limit}", good=False)
                    return
                items.append({"id": cl._short_id(), "label": "",
                              "type": "check", "required": False})
                dirty()
                repaint()

            out.append(ft.Row([ft.TextButton(
                add_label, icon=ft.Icons.ADD,
                on_click=lambda e: add())]))
            return out

        def sub(text):
            return ft.Text(text, size=12.5, weight=ft.FontWeight.W_700,
                           color=ACCENT)

        def note(text):
            return ft.Text(text, size=12.5,
                           color=ft.Colors.ON_SURFACE_VARIANT)

        # ── шапка и подвал листа ──
        def fields_card(title, icon, items, hint):
            col = ft.Column(spacing=8)

            def paint(update=True):
                col.controls = [note(hint)] + field_rows(
                    items, paint, "pola", "Dodaj pole", cl.MAX_FIELDS)
                if update:
                    self.page.update()

            paint(update=False)
            return self.card(title, icon, col)

        # ── секция ──
        sections_col = ft.Column(spacing=14)

        def paint_sections(update=True):
            sections_col.controls = [section_card(s, i)
                                     for i, s in enumerate(t["sections"])]
            if update:
                self.page.update()

        def section_card(s, index):
            body = ft.Column(spacing=10)

            def paint(update=True):
                body.controls = build()
                if update:
                    self.page.update()

            def set_mode(mode):
                s["mode"] = mode
                dirty()
                paint()

            def row_line(r, i):
                def up():
                    _move(s["rows"], i, -1)
                    dirty()
                    paint()

                def down():
                    _move(s["rows"], i, 1)
                    dirty()
                    paint()

                def drop():
                    s["rows"].remove(r)
                    dirty()
                    paint()

                def toggle_off(cid):
                    off = r.setdefault("off", [])
                    off.remove(cid) if cid in off else off.append(cid)
                    dirty()
                    paint()

                line = [
                    ft.Text(f"{i + 1}.", size=13, width=28,
                            color=ft.Colors.ON_SURFACE_VARIANT,
                            text_align=ft.TextAlign.RIGHT),
                    self._cl_tf(None, r.get("label", ""),
                                on_change=setter(r, "label"), expand=True,
                                hint="Treść punktu"),
                ]
                if any(c.get("type") in cl.NUMERIC for c in s["columns"]):
                    line += [
                        self._cl_tf("min", _lim_str(r.get("min")), 70,
                                    setter(r, "min")),
                        self._cl_tf("max", _lim_str(r.get("max")), 70,
                                    setter(r, "max")),
                    ]
                if len(s["columns"]) > 1:
                    n_off = len(r.get("off") or [])
                    line.append(ft.PopupMenuButton(
                        icon=ft.Icons.BLOCK, icon_size=17,
                        icon_color=WARN_COLOR if n_off else None,
                        tooltip=("Wyłączone komórki w tym wierszu"
                                 + (f": {n_off}" if n_off else "")
                                 + " — przekreślone jak na papierze"),
                        items=[ft.PopupMenuItem(
                            content=c.get("label") or f"kolumna {k + 1}",
                            checked=c["id"] in (r.get("off") or []),
                            on_click=lambda e, cid=c["id"]: toggle_off(cid))
                            for k, c in enumerate(s["columns"])]))
                line += [
                    icon_btn(ft.Icons.ARROW_UPWARD, "Wyżej", up,
                             disabled=i == 0),
                    icon_btn(ft.Icons.ARROW_DOWNWARD, "Niżej", down,
                             disabled=i == len(s["rows"]) - 1),
                    icon_btn(ft.Icons.DELETE_OUTLINE, "Usuń wiersz", drop,
                             BAD_COLOR),
                ]
                return ft.Row(line, spacing=6,
                              vertical_alignment=ft.CrossAxisAlignment.CENTER)

            def add_row():
                if len(s["rows"]) >= cl.MAX_ROWS:
                    self.toast(f"Najwyżej {cl.MAX_ROWS} wierszy", good=False)
                    return
                s["rows"].append({"id": cl._short_id(), "label": "",
                                  "min": None, "max": None, "off": []})
                dirty()
                paint()

            def paste_rows():
                box = self._cl_tf("Jeden punkt w linii", "", width=620,
                                  multiline=True)
                box.min_lines = 10
                box.max_lines = 16

                def ok(e):
                    lines = [x.strip() for x in (box.value or "").splitlines()
                             if x.strip()]
                    room = cl.MAX_ROWS - len(s["rows"])
                    for text in lines[:room]:
                        s["rows"].append({"id": cl._short_id(),
                                          "label": text[:240], "min": None,
                                          "max": None, "off": []})
                    self.page.pop_dialog()
                    if lines:
                        dirty()
                        paint()
                        self.toast(f"Dodano wierszy: {min(len(lines), room)}")

                self.page.show_dialog(ft.AlertDialog(
                    title=ft.Text("Wklej listę punktów", size=16,
                                  weight=ft.FontWeight.W_700),
                    content=ft.Column([
                        note("Wklej tekst z Worda, Excela albo maila — każda "
                             "linia stanie się osobnym wierszem."), box],
                        spacing=10, width=640, height=380),
                    actions=[ft.TextButton(
                        "Anuluj", on_click=lambda e: self.page.pop_dialog()),
                        ft.FilledButton("Dodaj", on_click=ok)],
                    shape=ft.RoundedRectangleBorder(radius=18)))

            def build():
                out = [
                    ft.Row([
                        self._cl_tf("Tytuł sekcji", s.get("title", ""), 360,
                                    setter(s, "title")),
                        self._cl_tf("Nagłówek pierwszej kolumny",
                                    s.get("label_title", ""), 300,
                                    setter(s, "label_title"),
                                    hint="Pozycja"),
                        ft.Checkbox(label="podpis pod sekcją", width=200,
                                    value=bool(s.get("sign")),
                                    on_change=setter(s, "sign", bool)),
                    ], spacing=10, wrap=True, run_spacing=8,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER),
                    sub("KOLUMNY — co wypełnia się w każdym wierszu"),
                ]
                out += field_rows(s["columns"], paint, "kolumny",
                                  "Dodaj kolumnę", cl.MAX_COLUMNS)
                out.append(sub("WIERSZE"))
                out.append(self.chips(
                    [("fixed", "Stała lista punktów"),
                     ("free", "Dziennik — wiersze dopisuje wypełniający")],
                    s.get("mode", "fixed"), set_mode))
                if s.get("mode") == "free":
                    out.append(ft.Row([
                        self._cl_tf("Puste wiersze na start",
                                    str(s.get("free_rows") or 3), 190,
                                    setter(s, "free_rows")),
                        note("Wypełniający sam wpisuje nazwę (np. potrawę) "
                             "i może dodawać kolejne wiersze."),
                    ], spacing=12, wrap=True,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER))
                else:
                    if any(c.get("type") in cl.NUMERIC for c in s["columns"]):
                        out.append(note(
                            "min / max w wierszu = norma dla tego wiersza "
                            "(np. mroźnia: max -18). Ma pierwszeństwo przed "
                            "normą kolumny. Wartość poza normą świeci się "
                            "na czerwono i trafia do „odchyleń”."))
                    out += [row_line(r, i) for i, r in enumerate(s["rows"])]
                    out.append(ft.Row([
                        ft.TextButton("Dodaj wiersz", icon=ft.Icons.ADD,
                                      on_click=lambda e: add_row()),
                        ft.TextButton("Wklej listę…",
                                      icon=ft.Icons.PLAYLIST_ADD,
                                      on_click=lambda e: paste_rows()),
                    ], spacing=6))
                return out

            def sec_up():
                _move(t["sections"], index, -1)
                dirty()
                paint_sections()

            def sec_down():
                _move(t["sections"], index, 1)
                dirty()
                paint_sections()

            def sec_copy():
                if len(t["sections"]) >= cl.MAX_SECTIONS:
                    self.toast(f"Najwyżej {cl.MAX_SECTIONS} sekcji",
                               good=False)
                    return
                dup = copy.deepcopy(s)
                dup["id"] = cl._short_id()
                t["sections"].insert(index + 1, dup)
                dirty()
                paint_sections()

            def sec_drop():
                def do():
                    t["sections"].remove(s)
                    dirty()
                    paint_sections()
                self.confirm("Usunąć sekcję?",
                             f"„{s.get('title') or 'bez nazwy'}” — "
                             f"{len(s['rows'])} wierszy.", do, yes_text="Usuń")

            tools = ft.Row([
                icon_btn(ft.Icons.ARROW_UPWARD, "Sekcja wyżej", sec_up,
                         disabled=index == 0),
                icon_btn(ft.Icons.ARROW_DOWNWARD, "Sekcja niżej", sec_down,
                         disabled=index == len(t["sections"]) - 1),
                icon_btn(ft.Icons.CONTENT_COPY, "Duplikuj sekcję", sec_copy),
                icon_btn(ft.Icons.DELETE_OUTLINE, "Usuń sekcję", sec_drop,
                         BAD_COLOR),
            ], spacing=0)
            paint(update=False)
            return self.card(f"Sekcja {index + 1}", ft.Icons.TABLE_ROWS, body,
                             trailing=tools)

        def add_section():
            if len(t["sections"]) >= cl.MAX_SECTIONS:
                self.toast(f"Najwyżej {cl.MAX_SECTIONS} sekcji", good=False)
                return
            # новая секция наследует колонки предыдущей: чаще всего лист —
            # это несколько зон кухни с одинаковыми графами
            cols = (copy.deepcopy(t["sections"][-1]["columns"])
                    if t["sections"] else
                    [{"id": cl._short_id(), "label": "Zrobione",
                      "type": "check", "required": False}])
            t["sections"].append({
                "id": cl._short_id(), "title": "", "mode": "fixed",
                "label_title": (t["sections"][-1].get("label_title", "")
                                if t["sections"] else ""),
                "free_rows": 3, "sign": False, "columns": cols,
                "rows": [{"id": cl._short_id(), "label": "", "min": None,
                          "max": None, "off": []}]})
            dirty()
            paint_sections()

        def set_period(p):
            t["period"] = p
            dirty()
            period_box.content = self.chips(cl.PERIODS, t["period"],
                                            set_period)
            self.page.update()

        period_box = ft.Container(
            content=self.chips(cl.PERIODS, t.get("period", "any"), set_period))

        basics = self.card("Podstawowe", ft.Icons.EDIT_NOTE, ft.Column([
            ft.Row([
                self._cl_tf("Nazwa checklisty", t.get("name", ""), 420,
                            setter(t, "name")),
                self._cl_tf("Opis / instrukcja (opcjonalnie)",
                            t.get("description", ""), 520,
                            setter(t, "description")),
            ], spacing=10, wrap=True, run_spacing=8),
            ft.Row([ft.Text("Jak często:", size=13.5,
                            color=ft.Colors.ON_SURFACE_VARIANT), period_box],
                   spacing=10, wrap=True,
                   vertical_alignment=ft.CrossAxisAlignment.CENTER),
            note("Data i imię osoby wypełniającej są na każdej checkliście "
                 "zawsze. Datę wstawia jedno dotknięcie (dzisiejsza), potem "
                 "można ją zmienić."),
        ], spacing=12))

        paint_sections(update=False)
        top = ft.Row([
            ft.IconButton(ft.Icons.ARROW_BACK, tooltip="Wróć do listy",
                          on_click=lambda e: self._cl_close_editor()),
            ft.Text("Nowy szablon" if is_new else "Konstruktor checklisty",
                    size=22, weight=ft.FontWeight.W_800),
            ft.Container(expand=True),
            ft.OutlinedButton("Podgląd", icon=ft.Icons.PREVIEW,
                              on_click=lambda e: self.cl_preview_template(t)),
            ft.FilledButton("Zapisz i wyślij na serwer", icon=ft.Icons.SAVE,
                            on_click=lambda e: self._cl_save_editor()),
        ], spacing=10, vertical_alignment=ft.CrossAxisAlignment.CENTER)

        body = ft.Column([
            basics,
            fields_card("Dodatkowe pola nad tabelami", ft.Icons.VERTICAL_ALIGN_TOP,
                        t.setdefault("header", []),
                        "Np. zmiana, numer dostawy, temperatura otoczenia. "
                        "Zostaw puste, jeśli wystarczą data i osoba."),
            sections_col,
            ft.Row([ft.FilledTonalButton("Dodaj sekcję", icon=ft.Icons.ADD,
                                         on_click=lambda e: add_section())]),
            fields_card("Pola pod tabelami", ft.Icons.VERTICAL_ALIGN_BOTTOM,
                        t.setdefault("footer", []),
                        "Np. podpis supervisora zmiany, uwagi końcowe."),
        ], spacing=14, expand=True, scroll=ft.ScrollMode.AUTO)
        return ft.Column([top, body], spacing=14, expand=True)

    # ═══════════════════════ сервер ═══════════════════════
    def cl_autopush(self, manual=False, saved=False):
        """После любой правки шаблонов каталог сам уезжает на сервер."""
        cfg = db.get_config(self.c)
        if not (cfg.get("server_url") and cfg.get("server_token")):
            if manual or saved:
                self.toast("Zapisano lokalnie. Serwer nie jest podłączony — "
                           "Ustawienia → Serwer.", good=not manual)
            return
        self.page.run_task(self._cl_push, manual or saved)

    async def _cl_push(self, loud=False):
        cfg = db.get_config(self.c)
        ok, data = await sync.push_checklists(
            cfg.get("server_url"), cfg.get("server_token"),
            cl.catalog_for_server(self.c))
        if ok:
            self.cl_status = (f"✓ Na serwerze: {data.get('templates', 0)} "
                              "checklist")
            self.cl_status_ok = True
            if loud:
                self.toast("Zapisano i wysłano na serwer — kucharze widzą "
                           "aktualne checklisty")
        else:
            self.cl_status = f"✗ Szablony nie wysłane: {data}"
            self.cl_status_ok = False
            if loud:
                self.toast(f"Zapisano lokalnie, ale nie wysłano: {data}",
                           good=False)
        if self.selected == "checklists" and self.cl_edit is None:
            self.refresh()
        else:
            self.page.update()

    async def cl_fetch(self) -> int:
        """Забирает заполненные чек-листы с сервера в локальный архив.

        С сервера копия удаляется только после того, как легла в базу.
        То, что не разбирается (чужой или битый JSON), тоже удаляется —
        иначе оно висело бы в очереди до истечения срока.
        """
        cfg = db.get_config(self.c)
        url, token = cfg.get("server_url"), cfg.get("server_token")
        if not (url and token):
            return 0
        ok, data = await sync.fetch_checklist_reports(url, token)
        if not ok:
            self.cl_status = f"✗ {data}"
            self.cl_status_ok = False
            return 0
        added = 0
        for rep in data:
            try:
                _id, new = cl.import_entry(self.c, rep)
                added += 1 if new else 0
            except (ValueError, TypeError, AttributeError):
                pass
            if isinstance(rep, dict) and rep.get("id"):
                await sync.delete_checklist_report(url, token, rep["id"])
        cl.auto_backup(self.c, cfg.get("checklist_backup_dir"))
        self.cl_status = (f"✓ Pobrano nowych: {added}" if added
                          else "✓ Brak nowych checklist")
        self.cl_status_ok = True
        self.cl_paint_badge()
        return added

    async def cl_auto_loop(self):
        """Раз в несколько минут проверяет сервер сама — чтобы счётчик
        новых чек-листов рос без кнопки «Odśwież»."""
        import asyncio
        while True:
            await asyncio.sleep(AUTO_FETCH_SEC)
            try:
                added = await self.cl_fetch()
            except Exception:                              # noqa: BLE001
                continue        # фоновая проверка не должна ронять окно
            if added and self.selected == "checklists" \
                    and self.cl_edit is None:
                self.refresh()
            else:
                self.page.update()
