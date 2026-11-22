"""Автопроверки локальной базы: единицы, каталог, страты, подтверждение.

    python check_db.py

База берётся временная — переменная KUCHNIA_DB ставится ДО импорта db,
иначе тест молча пишет в боевую kuchnia.db.
"""

import io
import os
import sys
import tempfile

# Консоль с кодировкой cp1251/cp1252 роняет print() на польских буквах —
# и вместо отчёта о проверках получается трейсбек. Заставляем UTF-8.
for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        try:
            _s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:                                  # noqa: BLE001
            pass

DB_FILE = os.path.join(tempfile.mkdtemp(prefix="kuchnia-test-"), "test.db")
os.environ["KUCHNIA_DB"] = DB_FILE

import db              # noqa: E402 — только после подмены пути
import export_xlsx     # noqa: E402

PASS = FAIL = 0


def ok(cond, name):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
        print(f"  ✗ {name}")


def raises(fn, name, fragment=None):
    try:
        fn()
    except ValueError as e:
        ok(fragment is None or fragment in str(e), f"{name} (tekst: {e})")
        return
    except Exception as e:                                # noqa: BLE001
        ok(False, f"{name} — zły wyjątek {type(e).__name__}: {e}")
        return
    ok(False, f"{name} — brak wyjątku")


def section(title):
    print(f"\n— {title}")


# ────────────────────────── единицы ──────────────────────────
section("jednostki i konwersje")
ok(db.to_base(1, "kg") == 1000, "1 kg = 1000 g")
ok(db.to_base(2.5, "l") == 2500, "2.5 l = 2500 ml")
ok(db.unit_dim("g") == "mass" and db.unit_dim("ml") == "volume", "wymiary")
ok(db.compatible("kg", "g") and not db.compatible("kg", "l"), "zgodność")
ok(db.units_for("kg") == ["kg", "g"], "wybór jednostek dla kg")
ok(db.units_for("szt") == ["szt"], "sztuki bez wyboru")
raises(lambda: db.unit_dim("funt"), "nieznana jednostka wywala się")
ok(db.format_qty(3400, "kg") == "3.4 kg", "3400 g → 3.4 kg")
ok(db.format_qty(250, "l") == "0.25 l", "250 ml → 0.25 l")
ok(db.qty_in(1500, "kg") == 1.5, "qty_in liczbą")
ok(db.parse_qty("1,5") == 1.5, "przecinek dziesiętny")
raises(lambda: db.parse_qty("abc"), "tekst zamiast liczby")

section("daty")
ok(db.month_bounds("2024-02") == ("2024-02-01", "2024-02-29"), "luty przestępny")
ok(db.month_bounds("2026-02") == ("2026-02-01", "2026-02-28"), "luty zwykły")
ok(db.shift_month("2026-01", -1) == "2025-12", "miesiąc wstecz przez rok")
ok(db.shift_month("2026-12", 1) == "2027-01", "miesiąc w przód przez rok")
raises(lambda: db.month_bounds("2026-13-01"), "zły miesiąc odrzucony")
ok("sierpień" in db.month_label_pl("2026-08"), "polska nazwa miesiąca")

# ────────────────────────── каталог ──────────────────────────
section("katalog pozycji")
c = db.init_db()
losos = db.save_item(c, {"name": "Łosoś", "unit": "kg", "category": "Ryby"})
bulka = db.save_item(c, {"name": "Bułka", "unit": "szt", "category": "Pieczywo"})
olej = db.save_item(c, {"name": "Olej", "unit": "l", "category": "Tłuszcze"})
ok(len(db.list_items(c)) == 3, "trzy pozycje")
raises(lambda: db.save_item(c, {"name": "łosoś", "unit": "kg"}),
       "duplikat mimo polskich liter i wielkości", "już istnieje")
raises(lambda: db.save_item(c, {"name": "", "unit": "kg"}), "pusta nazwa")
raises(lambda: db.save_item(c, {"name": "X", "unit": "funt"}), "zła jednostka")
ok(db.item_categories(c) == ["Pieczywo", "Ryby", "Tłuszcze"], "kategorie")
ok(db.find_item_by_name(c, "łOsOś")["id"] == losos, "poszukiwanie po nazwie")

# ────────────────────────── запись страт ──────────────────────
section("zapis strat")
db.add_loss(c, {"item_id": losos, "qty": 1, "unit": "kg",
                "loss_date": "2026-08-05", "reason": "Zepsute",
                "person": "Marek"})
db.add_loss(c, {"item_id": losos, "qty": 300, "unit": "g",
                "loss_date": "2026-08-06", "reason": "Spalone"})
db.add_loss(c, {"item_id": bulka, "qty": 4, "unit": "szt",
                "loss_date": "2026-08-06"})
db.add_loss(c, {"item_id": olej, "qty": 2.5, "unit": "l",
                "loss_date": "2026-07-20"})
db.add_loss(c, {"item_id": losos, "qty": 0.5, "unit": "kg",
                "loss_date": "2026-07-15"})

raises(lambda: db.add_loss(c, {"item_id": losos, "qty": 1, "unit": "l"}),
       "jednostka z innego wymiaru odrzucona", "nie pasuje")
raises(lambda: db.add_loss(c, {"item_id": losos, "qty": 0, "unit": "kg"}),
       "zero odrzucone")
raises(lambda: db.add_loss(c, {"item_id": losos, "qty": -2, "unit": "kg"}),
       "minus odrzucony")
raises(lambda: db.add_loss(c, {"item_id": "brak", "qty": 1, "unit": "kg"}),
       "nieznana pozycja")
raises(lambda: db.add_loss(c, {"item_id": losos, "qty": 1, "unit": "kg",
                               "loss_date": "05.08.2026"}), "zła data")

before = len(db.journal(c))
db.add_loss(c, {"id": "dup-1", "item_id": bulka, "qty": 1, "unit": "szt",
                "loss_date": "2026-08-06"})
db.add_loss(c, {"id": "dup-1", "item_id": bulka, "qty": 1, "unit": "szt",
                "loss_date": "2026-08-06"})
ok(len(db.journal(c)) == before + 1, "to samo id nie tworzy drugiego wpisu")

section("podsumowania")
aug = {r["name"]: r for r in db.by_item(c, month="2026-08")}
ok(aug["Łosoś"]["qty_text"] == "1.3 kg", "1 kg + 300 g = 1.3 kg")
ok(aug["Bułka"]["qty_text"] == "5 szt", "4 + 1 = 5 szt")
ok(aug["Łosoś"]["entries"] == 2, "liczba wpisów")
ok("Olej" not in aug, "lipcowy olej nie wchodzi do sierpnia")
ok({r["name"]: r for r in db.by_item(c)}["Łosoś"]["qty_text"] == "1.8 kg",
   "cały okres sumuje miesiące")

mx = db.by_month(c)
ok(mx["months"] == ["2026-07", "2026-08"], "miesiące w matrycy")
row = [i for i in mx["items"] if i["name"] == "Łosoś"][0]
ok(row["months_qty"]["2026-07"] == 0.5 and row["months_qty"]["2026-08"] == 1.3,
   "matryca pozycja × miesiąc")
ok(row["total_text"] == "1.8 kg", "razem w matrycy")

reasons = {r["reason"]: r["entries"] for r in db.by_reason(c, month="2026-08")}
ok(reasons.get("Zepsute") == 1 and reasons.get("—") == 2,
   "powody, w tym wpisy bez powodu")
ok({r["category"]: r["entries"] for r in db.by_category(c, month="2026-08")}
   .get("Ryby") == 2, "podsumowanie po kategoriach")
ok(db.months_with_data(c) == ["2026-08", "2026-07"], "miesiące z danymi")
ok(db.stats(c, "2026-08")["items"] == 2, "statystyka: pozycje")

section("sortowanie podsumowania")
rows = db.by_item(c)
by_qty = db.sort_summary(rows, "qty")
# 1.8 kg (1800 g) и 2.5 l (2500 ml) — числа сравнимы, но смысла в этом нет,
# поэтому kg-позиции идут своей группой перед l-позициями
ok([r["unit"] for r in by_qty] == ["kg", "l", "szt"],
   f"grupy jednostek nie mieszają się ({[r['unit'] for r in by_qty]})")
ok([r["name"] for r in db.sort_summary(rows, "name")]
   == sorted(r["name"] for r in rows), "sortowanie po nazwie")
ok(db.sort_summary(rows, "entries")[0]["entries"] >= rows[0]["entries"],
   "sortowanie po liczbie wpisów")

section("dziennik, filtry, prawki")
j = db.journal(c, month="2026-08")
ok(j[0]["loss_date"] >= j[-1]["loss_date"], "dziennik od najnowszych")
ok(len(db.journal(c, month="2026-08", item_id=bulka)) == 2, "filtr po pozycji")
ok(len(db.journal(c, category="Ryby")) == 3, "filtr po kategorii")
ok(len(db.journal(c, reason="Zepsute")) == 1, "filtr po powodzie")
ok(len(db.journal(c, person="Marek")) == 1, "filtr po osobie")
ok(len(db.journal(c, search="oso")) == 3, "szukanie po nazwie")
ok(db.persons_used(c) == ["Marek"], "lista osób")

first = db.journal(c, month="2026-08")[0]
db.update_loss(c, first["id"], {"qty": "2,5", "unit": first["unit"]})
ok(db.get_loss(c, first["id"])["qty"] == 2.5, "prawka ilości z przecinkiem")
raises(lambda: db.update_loss(c, first["id"], {"qty": 1, "unit": "l"}),
       "prawka na złą jednostkę odrzucona")
db.update_loss(c, first["id"], {"qty": first["qty"], "unit": first["unit"]})
ok(db.delete_loss(c, first["id"]), "usuwanie wpisu")

section("blokady katalogu")
raises(lambda: db.save_item(c, {"id": losos, "name": "Łosoś", "unit": "szt"}),
       "kg→szt przy istniejących stratach zablokowane", "nie można")
db.save_item(c, {"id": losos, "name": "Łosoś", "unit": "g"})
ok([r for r in db.by_item(c, month="2026-08")
    if r["item_id"] == losos][0]["qty_text"].endswith(" g"),
   "kg→g wolno, suma pokazuje się w gramach")
db.save_item(c, {"id": losos, "name": "Łosoś", "unit": "kg"})
ok(db.delete_item(c, losos) is False, "pozycja ze stratami nie usuwa się")
db.set_item_active(c, losos, False)
ok(len(db.list_items(c)) == 2, "wyłączona pozycja znika z formularza")
ok(len(db.list_items(c, active_only=False)) == 3, "…ale zostaje w katalogu")
ok(db.by_item(c, month="2026-08"), "…a straty nadal się liczą")
db.set_item_active(c, losos, True)

# ────────────────────── подтверждение зашлушений ──────────────────────
section("zgłoszenia z telefonu")
report = {
    "id": "rap-1", "name": "Ola", "shift_date": "2026-08-10",
    "data": {"name": "Ola", "shift_date": "2026-08-10", "lines": [
        {"item_id": losos, "item_name": "Łosoś", "qty": "1,2", "unit": "kg",
         "reason": "Zepsute"},
        {"item_id": bulka, "item_name": "Bułka", "qty": "3", "unit": "szt",
         "reason": ""},
    ]}}
lines = db.resolve_report_lines(c, report["data"]["lines"])
ok(len(lines) == 2 and not any(l["problem"] for l in lines),
   "linie zgłoszenia rozpoznane")
ok(lines[0]["qty"] == 1.2, "przecinek z telefonu")

res = db.confirm_report(c, report)
ok(res["lines"] == 2, "zatwierdzenie zapisało dwie linie")
ok(db.is_report_confirmed(c, "rap-1"), "zgłoszenie oznaczone jako przyjęte")
aug = {r["name"]: r for r in db.by_item(c, month="2026-08")}
ok(aug["Łosoś"]["qty_text"] == "2.5 kg", "1.3 + 1.2 = 2.5 kg")
qr = [e for e in db.journal(c, month="2026-08") if e["source"] == "qr"]
ok(len(qr) == 2 and qr[0]["person"] == "Ola", "wpisy z telefonu podpisane")

n_before = len(db.journal(c))
db.confirm_report(c, report)
ok(len(db.journal(c)) == n_before,
   "powtórne zatwierdzenie nie dubluje strat (idempotencja)")

bad_report = {"id": "rap-2", "name": "Ola", "shift_date": "2026-08-11",
              "data": {"lines": [{"item_name": "Czegoś takiego nie ma",
                                  "qty": "1", "unit": "kg"}]}}
bad_lines = db.resolve_report_lines(c, bad_report["data"]["lines"])
ok(bad_lines[0]["problem"] == "nieznana pozycja",
   "nieznana pozycja oznaczona, nie wyrzucona")
raises(lambda: db.confirm_report(c, bad_report),
       "zgłoszenie z problemem nie przechodzi")

fixed = db.resolve_report_lines(c, [{"item_id": bulka, "qty": "2",
                                     "unit": "szt"}])
db.confirm_report(c, bad_report, fixed)
ok(db.is_report_confirmed(c, "rap-2"), "po poprawce zgłoszenie przechodzi")

removed = db.unconfirm_report(c, "rap-1")
ok(removed == 2 and not db.is_report_confirmed(c, "rap-1"),
   "cofnięcie zatwierdzenia usuwa wpisy i znacznik")
ok({r["name"]: r for r in db.by_item(c, month="2026-08")}["Łosoś"]["qty_text"]
   == "1.3 kg", "…i sumy wracają do stanu sprzed")

section("stara wersja katalogu")
# id позиции на сервере устарел (каталог не выгружали), но имя совпадает
old = db.resolve_report_lines(c, [{"item_id": "id-ktorego-nie-ma",
                                   "item_name": "Bułka", "qty": "2",
                                   "unit": "szt"}])
ok(old[0]["item_id"] == bulka and not old[0]["problem"],
   "pozycja znaleziona po nazwie, mimo starego id")

# ────────────────────────── настройки и seed ──────────────────────────
section("ustawienia")
ok(db.get_config(c, "kitchen_name") == "Kuchnia", "domyślna nazwa kuchni")
db.set_config_many(c, {"kitchen_name": "HRC Warszawa", "server_url": "http://x"})
ok(db.get_config(c)["kitchen_name"] == "HRC Warszawa", "zapis ustawień")
ok(db.get_config(c, "server_token") == "", "brakujący klucz → pusty")

section("powody i seed")
r1 = db.save_reason(c, {"name": "Zepsute"})
db.save_reason(c, {"name": "Spalone"})
raises(lambda: db.save_reason(c, {"name": "zepsute"}), "duplikat powodu")
ok(len(db.list_reasons(c)) == 2, "dwa powody")
db.delete_reason(c, r1)
ok(len(db.list_reasons(c)) == 1 and db.by_item(c, month="2026-08"),
   "usunięcie powodu nie rusza strat")

res = db.seed(c)
ok(res["items"] > 50, f"seed wgrał katalog ({res})")
again = db.seed(c)
ok(again == {"items": 0, "reasons": 0}, "powtórny seed niczego nie dubluje")
ok({i["unit"] for i in db.list_items(c)} <= set(db.UNITS),
   "wszystkie jednostki z seeda są znane")

cat = db.catalog_for_server(c)
ok(len(cat["items"]) > 50 and cat["kitchen"] == "HRC Warszawa",
   "katalog dla serwera")
ok(all(set(i["units"]) for i in cat["items"]),
   "każda pozycja niesie listę dozwolonych jednostek")

# ────────────────────────── Excel ──────────────────────────
section("eksport Excel")
buf = io.BytesIO()
export_xlsx.build(c, "HRC Warszawa", "2026-08", buf)
ok(buf.getvalue()[:2] == b"PK" and len(buf.getvalue()) > 4000, "plik xlsx")

from openpyxl import load_workbook       # noqa: E402
buf.seek(0)
wb = load_workbook(buf)
ok(wb.sheetnames == ["Pozycje", "Miesiące", "Dziennik", "Powody"],
   f"cztery arkusze ({wb.sheetnames})")
ws = wb["Pozycje"]
vals = {ws.cell(row=r, column=2).value: ws.cell(row=r, column=3).value
        for r in range(5, ws.max_row + 1)}
ok(vals.get("Łosoś") == 1.3, f"ilość w Excelu liczbą ({vals.get('Łosoś')!r})")
ok(isinstance(vals.get("Łosoś"), float), "…a nie tekstem")

buf2 = io.BytesIO()
export_xlsx.build(c, "HRC Warszawa", "", buf2)
ok(len(buf2.getvalue()) > 4000, "eksport całego okresu")

c.close()
print(f"\n{'=' * 46}\nOK: {PASS}   BŁĘDY: {FAIL}\nBaza testowa: {DB_FILE}")
raise SystemExit(1 if FAIL else 0)
