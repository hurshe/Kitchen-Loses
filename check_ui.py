"""Автопроверки интерфейса приложения — без открытия окна.

Собираем ровно те же контролы, что и настоящая программа, и смотрим, что
в них попало: цифры в подсчётах, строки журнала, карточки зашлушений.
Дополняет check_db.py: тот следит за математикой, этот — за тем, что
математика доезжает до экрана.

    python check_ui.py
"""

import os
import sys
import tempfile

# Консоль с cp1251/cp1252 роняет print() на польских буквах — см. check_db.py
for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        try:
            _s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:                                  # noqa: BLE001
            pass

DB_FILE = os.path.join(tempfile.mkdtemp(prefix="kuchnia-ui-"), "ui.db")
os.environ["KUCHNIA_DB"] = DB_FILE

import flet as ft          # noqa: E402
import db                  # noqa: E402
import main_kuchnia as mk   # noqa: E402

PASS = FAIL = 0


def ok(cond, name):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
        print(f"  ✗ {name}")


def section(title):
    print(f"\n— {title}")


# ────────────────────── минимальный Flet ──────────────────────
class FakeWindow:
    """Столько окна, сколько программа реально трогает."""
    def __init__(self):
        self.min_width = self.min_height = 0
        self.width = self.height = 0
        self.icon = None


class FakePage:
    def __init__(self):
        self.window = FakeWindow()
        self.services = []
        self.controls = []
        self.dialogs = []
        self.tasks = []
        self.updates = 0
        self.theme = self.dark_theme = self.theme_mode = None
        self.title = ""
        self.padding = None

    def add(self, *controls):
        self.controls.extend(controls)

    def update(self):
        self.updates += 1

    def show_dialog(self, dlg):
        self.dialogs.append(dlg)

    def pop_dialog(self):
        if self.dialogs:
            self.dialogs.pop()

    def run_task(self, fn, *args):
        self.tasks.append((getattr(fn, "__name__", str(fn)), args))


CHILD_ATTRS = ("content", "controls", "actions", "tabs", "leading", "trailing",
               "title", "subtitle", "label", "icon")


def walk(node, depth=0):
    """Обход дерева контролов. Flet-контролы — обычные объекты, поэтому
    достаточно пройтись по тем полям, где живут дети."""
    if node is None or depth > 40:
        return
    if isinstance(node, (list, tuple)):
        for x in node:
            yield from walk(x, depth + 1)
        return
    if not hasattr(node, "__dict__"):
        return
    yield node
    for attr in CHILD_ATTRS:
        yield from walk(getattr(node, attr, None), depth + 1)


def texts(node):
    """Все видимые строки поддерева.

    Подписи кнопок лежат в `content` строкой, а не в ft.Text — их тоже
    собираем, иначе проверка «есть ли кнопка X» ничего не находит.
    """
    out = []
    for ctl in walk(node):
        if isinstance(ctl, ft.Text) and ctl.value is not None:
            out.append(str(ctl.value))
        elif isinstance(ctl, ft.TextField):
            out.append(str(ctl.value or ""))
        elif isinstance(getattr(ctl, "content", None), str):
            out.append(ctl.content)
    return out


def find_button(node, label):
    for ctl in walk(node):
        if isinstance(ctl, (ft.FilledButton, ft.TextButton, ft.OutlinedButton,
                            ft.FilledTonalButton)):
            if str(getattr(ctl, "content", "") or "") == label:
                return ctl
    return None


def find_field(node, label):
    for ctl in walk(node):
        if isinstance(ctl, ft.TextField) and ctl.label == label:
            return ctl
    return None


# ────────────────────── данные для теста ──────────────────────
c = db.init_db()
losos = db.save_item(c, {"name": "Łosoś", "unit": "kg", "category": "Ryby"})
bulka = db.save_item(c, {"name": "Bułka", "unit": "szt", "category": "Pieczywo"})
db.save_reason(c, {"name": "Zepsute"})
db.save_reason(c, {"name": "Spalone"})
db.add_loss(c, {"item_id": losos, "qty": 1, "unit": "kg",
                "loss_date": "2026-08-05", "reason": "Zepsute",
                "person": "Marek"})
db.add_loss(c, {"item_id": losos, "qty": 300, "unit": "g",
                "loss_date": "2026-08-06"})
db.add_loss(c, {"item_id": bulka, "qty": 4, "unit": "szt",
                "loss_date": "2026-08-06"})
db.set_config_many(c, {"kitchen_name": "HRC Warszawa"})
c.close()

def wrap_expand_offenders(node):
    """Дети с `expand` внутри Row(wrap=True).

    Flutter разрешает Expanded только внутри Flex, а при wrap строится Wrap —
    и вместо содержимого рисуется серый прямоугольник ошибки. Один такой
    распорщик уже съел весь журнал страт, поэтому проверяем все экраны.
    """
    bad = []
    for ctl in walk(node):
        if isinstance(ctl, ft.Row) and getattr(ctl, "wrap", False):
            for child in (ctl.controls or []):
                if getattr(child, "expand", None):
                    bad.append(type(child).__name__)
    return bad


page = FakePage()
app = mk.KuchniaApp(page)
app.month = "2026-08"

section("start programu")
ok(page.controls, "okno zbudowane")
ok(page.title.startswith("Straty Kuchnia"), "tytuł okna")
ok(app.selected == "summary", "startujemy od podsumowania")
ok(len(app.nav_items) == 3, "trzy pozycje w menu (bez osobnych „Strat”)")
ok("journal" not in app.nav_items, "stary osobny dziennik zniknął")
ok(page.theme is not None and page.dark_theme is not None, "oba motywy")

section("Podsumowanie")
view = app.view_summary()
t = texts(view)
ok("Straty" in t, "nagłówek")
ok("Nowa strata" in t, "ręczny wpis dostępny od razu z podsumowania")
ok("Łosoś" in t and "1.3 kg" in t, f"suma pozycji na ekranie ({'1.3 kg' in t})")
ok("Bułka" in t and "4 szt" in t, "sztuki bez przeliczeń")
ok("3" in t, "KPI: liczba wpisów")

app.summary_tab = "months"
t = texts(app.view_summary())
ok("sie 26" in t or any("sie" in x for x in t), "matryca miesięcy ma kolumnę")
app.summary_tab = "reasons"
t = texts(app.view_summary())
ok("Zepsute" in t and "—" in t, "powody, w tym brak powodu")
app.summary_tab = "categories"
ok("Ryby" in texts(app.view_summary()), "kategorie")
app.summary_tab = "items"

app.summary_sort = "qty"
c = db.connect()
rows_qty = [r["unit"] for r in db.sort_summary(
    db.by_item(c, month="2026-08"), "qty")]
c.close()
ok(rows_qty == ["kg", "szt"], "sortowanie grupuje jednostki")

section("zakładka „Wpisy” (dawne Straty)")
app.summary_tab = "entries"
view = app.view_summary()
t = texts(view)
ok("2026-08-06" in t, "wiersze dziennika")
ok("1 kg" in t and "300 g" in t, "ilości pokazane tak, jak wpisano")
ok("Marek" in t, "osoba w wierszu")
ok("3 wpisy" in " ".join(t), f"polska liczba mnoga w liczniku ({t[:3]})")
ok(mk.KuchniaApp._wpisy_word(1) == "wpis"
   and mk.KuchniaApp._wpisy_word(5) == "wpisów"
   and mk.KuchniaApp._wpisy_word(12) == "wpisów", "1 / 2-4 / 5+ i wyjątek 12")

app.journal_filters["reason"] = "Zepsute"
t = texts(app.view_summary())
ok("1 wpis" in " ".join(t), "filtr po powodzie zawęża listę")
app.journal_filters["reason"] = ""
app.journal_filters["search"] = "Buł"
t = texts(app.view_summary())
ok("Bułka" in t and "Łosoś" not in t, "szukanie po nazwie")
app._clear_filters()

section("brak szarego prostokąta (expand w Row(wrap))")
for tab in ("items", "entries", "months", "reasons", "categories"):
    app.summary_tab = tab
    bad = wrap_expand_offenders(app.view_summary())
    ok(not bad, f"zakładka „{tab}” bez rozpychacza w Wrap ({bad})")
app.summary_tab = "items"
for name, builder in (("Zgłoszenia", app.view_reports),
                      ("Ustawienia", app.view_settings)):
    bad = wrap_expand_offenders(builder())
    ok(not bad, f"{name} bez rozpychacza w Wrap ({bad})")

section("sortowanie klikiem w nagłówek")
view = app.view_summary()
heads = [c for c in walk(view)
         if isinstance(c, ft.Container) and c.on_click
         and isinstance(c.content, ft.Text)
         and str(c.content.value).startswith(("Pozycja", "Kategoria", "Ilość",
                                              "Wpisów"))]
ok(len(heads) == 4, f"wszystkie cztery nagłówki klikalne ({len(heads)})")
ok(any("▼" in str(c.content.value) for c in heads), "aktywna kolumna oznaczona")
before = app.summary_sort
heads[0].on_click(None)
ok(app.summary_sort != before and app.summary_sort == "name",
   "klik w „Pozycja” przełącza sortowanie")
app.summary_sort = "qty"

section("dialog nowej straty")
app.loss_dialog()
dlg = page.dialogs[-1]
ok(isinstance(dlg, ft.AlertDialog), "dialog otwarty")
ok("Nowa strata" in texts(dlg.title), "tytuł dialogu")
# выбираем позицию так же, как менеджер мышью: клик по строке списка
rows = [ctl for ctl in walk(dlg.content)
        if isinstance(ctl, ft.Container) and ctl.on_click
        and "Łosoś" in texts(ctl)]
ok(bool(rows), "pozycja jest na liście wyboru")
rows[0].on_click(None)
qty = find_field(dlg.content, "Ilość")
qty.value = "2,5"
date_f = find_field(dlg.content, "Data (RRRR-MM-DD)")
date_f.value = "2026-08-07"
save = find_button(dlg, "Zapisz")
ok(save is not None, "przycisk Zapisz")
save.on_click(None)
c = db.connect()
added = [e for e in db.journal(c, month="2026-08")
         if e["loss_date"] == "2026-08-07"]
ok(len(added) == 1 and added[0]["qty"] == 2.5,
   "wpis zapisany z przecinkiem dziesiętnym")
ok(added[0]["source"] == "manual", "źródło: ręcznie")
ok({r["name"]: r for r in db.by_item(c, month="2026-08")}["Łosoś"]["qty_text"]
   == "3.8 kg", "suma na ekranie urosła")

section("dialog: błędy")
app.loss_dialog()
dlg = page.dialogs[-1]
find_button(dlg, "Zapisz").on_click(None)
errs = [x for x in texts(dlg.content) if "Wybierz pozycję" in x]
ok(bool(errs), "bez pozycji dialog nie zapisuje i mówi dlaczego")
rows = [ctl for ctl in walk(dlg.content)
        if isinstance(ctl, ft.Container) and ctl.on_click and "Bułka" in texts(ctl)]
rows[0].on_click(None)
find_field(dlg.content, "Ilość").value = "-3"
find_button(dlg, "Zapisz").on_click(None)
ok(any("większa od zera" in x for x in texts(dlg.content)),
   "ujemna ilość odrzucona z komunikatem po polsku")
page.pop_dialog()

section("prawka istniejącego wpisu")
entry = [e for e in db.journal(c, month="2026-08") if e["qty"] == 2.5][0]
app.loss_dialog(entry)
dlg = page.dialogs[-1]
ok("Popraw stratę" in texts(dlg.title), "tytuł dialogu prawki")
find_field(dlg.content, "Ilość").value = "1"
find_button(dlg, "Zapisz").on_click(None)
ok(db.get_loss(c, entry["id"])["qty"] == 1.0, "prawka zapisana")
c.close()

section("Zgłoszenia")
app.cfg = {"server_token": "", "server_url": ""}
t = texts(app.view_reports())
ok(any("Serwer nie jest podłączony" in x for x in t),
   "bez tokenu tłumaczymy, co zrobić")

app.cfg = {"server_token": "abc", "server_url": "http://x"}
app.reports = [{
    "id": "rap-1", "name": "Ola", "shift_date": "2026-08-10",
    "created_at": "2026-08-10T20:00:00",
    "data": {"lines": [
        {"item_id": losos, "item_name": "Łosoś", "qty": "1,2", "unit": "kg",
         "reason": "Zepsute", "note": ""},
        {"item_id": "nie-ma", "item_name": "Coś dziwnego", "qty": "1",
         "unit": "kg", "reason": "", "note": ""}]}}]
view = app.view_reports()
t = texts(view)
ok("Ola · 2026-08-10" in t, "karta zgłoszenia z autorem i datą")
ok("1.2 kg" in t, "ilość z telefonu przeliczona na liczbę")
ok(any("nieznana pozycja" in x for x in t), "nierozpoznana pozycja podświetlona")
zatw = find_button(view, "Zatwierdź")
ok(zatw is not None and zatw.disabled,
   "zatwierdzenie zablokowane, dopóki jest problem")

app.fix_report_dialog(app.reports[0])
dlg = page.dialogs[-1]
dds = [ctl for ctl in walk(dlg.content) if isinstance(ctl, ft.Dropdown)]
ok(len(dds) >= 4, "w dialogu poprawek są listy pozycji i jednostek")
dels = [ctl for ctl in walk(dlg.content) if isinstance(ctl, ft.IconButton)]
dels[-1].on_click(None)                      # выбрасываем битую строку
find_button(dlg, "Zatwierdź").on_click(None)
c = db.connect()
ok(db.is_report_confirmed(c, "rap-1"), "po poprawce zgłoszenie zatwierdzone")
qr = [e for e in db.journal(c, month="2026-08") if e["source"] == "qr"]
ok(len(qr) == 1 and qr[0]["person"] == "Ola",
   "strata z telefonu trafiła do dziennika z podpisem")
ok(app.reports == [], "karta znika z kolejki")
ok(any(name == "_drop_report_on_server" for name, _ in page.tasks),
   "zatwierdzenie kasuje zgłoszenie na serwerze")

section("historia zatwierdzonych — widać co przyjęto")
app.server_ok = True
app._paint_badge()
ok(app.reports_status == "Brak nowych zgłoszeń",
   f"licznik w nagłówku nie kłamie po zatwierdzeniu ({app.reports_status})")
t = texts(app.view_reports())
ok(any("Łosoś 1.2 kg" in x for x in t),
   "w historii widać pozycje i ilości, a nie samo „1 pozycji”")
hist = db.confirmed_reports(c, limit=5)
app.show_report_details(hist[0])
dlg = page.dialogs[-1]
t = texts(dlg)
ok("Łosoś" in t and "1.2 kg" in t, "szczegóły zgłoszenia po kliknięciu wiersza")
ok(any("Cofnij zatwierdzenie" in x for x in t), "stamtąd można cofnąć")
page.pop_dialog()
c.close()

section("Ustawienia")
t = texts(app._settings_items())
ok("Łosoś" in t and "Nowa pozycja" in t, "lista pozycji i przycisk dodania")
t = texts(app._settings_reasons())
ok("Zepsute" in t, "lista powodów")
t = texts(app._settings_server())
ok("HRC Warszawa" in t, "nazwa kuchni w polu")
ok(any("Wyślij katalog" in x for x in t), "przycisk wysyłki katalogu")
t = texts(app._settings_about())
ok(any(mk.APP_VERSION in x for x in t), "wersja programu")
ok(any(DB_FILE in x for x in t), "ścieżka do bazy widoczna")

section("dialog pozycji")
app.item_dialog()
dlg = page.dialogs[-1]
find_field(dlg.content, "Nazwa").value = "Kalmary"
find_field(dlg.content, "Kategoria").value = "Ryby"
find_button(dlg, "Zapisz").on_click(None)
c = db.connect()
ok(db.find_item_by_name(c, "Kalmary") is not None, "nowa pozycja zapisana")
app.item_dialog()
dlg = page.dialogs[-1]
find_field(dlg.content, "Nazwa").value = "kalmary"
find_button(dlg, "Zapisz").on_click(None)
ok(any("już istnieje" in x for x in texts(dlg.content)),
   "duplikat nazwy zatrzymany w dialogu")
page.pop_dialog()
c.close()

section("przełączanie widoków")
for key in ("summary", "reports", "settings"):
    app.select(key)
    ok(app.content.content is not None, f"widok „{key}” buduje się bez błędu")
app.toggle_theme()
ok(page.theme_mode is not None, "przełącznik motywu działa")

print(f"\n{'=' * 46}\nOK: {PASS}   BŁĘDY: {FAIL}\nBaza testowa: {DB_FILE}")
raise SystemExit(1 if FAIL else 0)
