"""Автопроверки чек-листов: модель, сервер, сквозной путь, интерфейс.

    python check_checklists.py

Поднимает настоящий сервер на localhost и проводит чек-лист по всему пути:
конструктор → сервер → «телефон» → сервер → архив приложения → копия
запасная. Обе базы — временные и подставляются ДО импорта модулей.
"""

import asyncio
import json
import os
import socket
import sys
import tempfile
import threading
import time
import zipfile

for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        try:
            _s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:                                  # noqa: BLE001
            pass

TMP = tempfile.mkdtemp(prefix="kuchnia-cl-")
os.environ["KUCHNIA_DB"] = os.path.join(TMP, "local.db")
os.environ["KUCHNIA_SERVER_DB"] = os.path.join(TMP, "queue.db")
os.environ["KUCHNIA_ADMIN_PASSWORD"] = "haslo-cl"
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "server"))

import flet as ft               # noqa: E402
import httpx                    # noqa: E402
import checklist_presets        # noqa: E402
import checklists as cl         # noqa: E402
import db                       # noqa: E402
import sync                     # noqa: E402
import kitchen_server as ks     # noqa: E402
import main_kuchnia as mk       # noqa: E402

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


def raises(fn, *a):
    try:
        fn(*a)
    except ValueError:
        return True
    return False


# ════════════════════════ модель ════════════════════════
section("szablon: normalizacja")
t = cl.normalize_template({
    "name": "  Test  ", "period": "co-drugi-wtorek",
    "sections": [{"title": "A", "columns": [
        {"id": "x", "label": "Temp", "type": "temp", "max": "5,5"},
        {"id": "x", "label": "Dubel id", "type": "check"},
        {"label": "Wybór", "type": "choice", "options": ["a", "a", "b", ""]},
        {"label": "Zły typ", "type": "hologram"}],
        "rows": [{"label": "R1", "off": ["x", "nie-ma"], "max": -18},
                 {"label": "R2", "min": "abc"}]}]})
s0 = t["sections"][0]
ok(t["name"] == "Test" and t["period"] == "any", "nazwa przycięta, zły okres → any")
ok(len({c["id"] for c in s0["columns"]}) == 4, "zdublowane id kolumn rozdzielone")
ok(s0["columns"][0]["max"] == 5.5 and s0["columns"][0]["unit"] == "°C",
   "limit «5,5» → 5.5, temperatura ma °C")
ok(s0["columns"][2]["options"] == ["a", "b"], "opcje bez dubli i pustych")
ok(s0["columns"][3]["type"] == "check", "nieznany typ → pole wyboru")
ok(s0["rows"][0]["off"] == ["x"], "wyłączone komórki tylko z istniejących kolumn")
ok(s0["rows"][1]["min"] is None, "limit-nie-liczba → brak limitu")
ok(raises(cl.normalize_template, {"name": "", "sections": [{}]}),
   "szablon bez nazwy odrzucony")
ok(raises(cl.normalize_template, {"name": "X", "sections": []}),
   "szablon bez sekcji odrzucony")
ok(raises(cl.normalize_template,
          {"name": "X", "sections": [{"columns": [{}], "rows": []}]}),
   "sekcja ze stałą listą bez wierszy odrzucona")
ok(cl.normalize_template({"name": "X", "sections": [
    {"mode": "free", "columns": [{}]}]})["sections"][0]["rows"] == [],
   "dziennik może nie mieć wierszy")
ok(raises(cl.normalize_template, "nie-słownik"), "śmieci zamiast szablonu")
big = cl.normalize_template({"name": "Duży", "sections": [
    {"columns": [{}] * 50, "rows": [{"label": str(i)} for i in range(500)]}]})
ok(len(big["sections"][0]["columns"]) == cl.MAX_COLUMNS
   and len(big["sections"][0]["rows"]) == cl.MAX_ROWS, "limity rozmiaru")
ok(cl.normalize_template(t) == cl.normalize_template(cl.normalize_template(t)),
   "normalizacja jest stabilna")

section("szablony startowe (z papierowych list)")
for raw in checklist_presets.PRESETS:
    try:
        cl.normalize_template(raw)
        good = True
    except ValueError as err:
        good = str(err)
    ok(good is True, f"„{raw['name']}” przechodzi normalizację ({good})")
names = [p["name"] for p in checklist_presets.PRESETS]
ok(len(names) == 4 and len(set(names)) == 4, "cztery różne szablony")
temp = cl.normalize_template(checklist_presets.TEMPERATURE)
rows = {r["label"]: r for r in temp["sections"][0]["rows"]}
ok(len(rows) == 28, "Temperature Checklist: 28 stacji jak na liście")
ok(rows["Walk-In Freezer (-18°C)"]["max"] == -18, "mroźnia: max -18")
ok(rows["Hot Hold 1 (75°C)"]["min"] == 75
   and "open" in rows["Hot Hold 1 (75°C)"]["off"],
   "hot hold: min 75, bez otwarcia/zamknięcia")
weekly = cl.normalize_template(checklist_presets.WEEKLY)
ok([len(s["rows"]) for s in weekly["sections"]] == [6, 16, 10],
   "Weekly: 6 + 16 + 10 punktów")
daily = cl.normalize_template(checklist_presets.DAILY)
ok([len(s["rows"]) for s in daily["sections"]] == [5, 8, 12, 10],
   "Daily: 5 + 8 + 12 + 10 punktów")
ok(len(daily["footer"]) == 2 and daily["footer"][0]["type"] == "sign",
   "Daily: dwa podpisy supervisora")

section("ocena wypełnienia")
tpl = cl.normalize_template({"name": "Ocena", "sections": [{
    "id": "s", "columns": [
        {"id": "t", "label": "T", "type": "temp", "max": 5},
        {"id": "y", "label": "OK?", "type": "yesno"},
        {"id": "c", "label": "✓", "type": "check"}],
    "rows": [{"id": "a", "label": "Lodówka"},
             {"id": "b", "label": "Mroźnia", "max": -18},
             {"id": "h", "label": "Gorące", "min": 75, "off": ["y"]}]},
    {"id": "f", "mode": "free", "columns": [{"id": "t", "type": "temp"}],
     "rows": [{"id": "r1", "label": ""}, {"id": "r2", "label": "Zupa"}]}],
    "footer": [{"id": "sig", "label": "Podpis", "type": "sign"}]})


def ev(values, **kw):
    return cl.evaluate(dict({"template": tpl, "values": values}, **kw))


r = ev({})
ok(r == {"filled": 0, "total": 10, "issues": 0, "bad": set()},
   f"pusta: 10 pól (bez wyłączonej i bez pustego wiersza dziennika) ({r})")
r = ev({"s|a|t": "4,5", "s|a|c": True, "s|a|y": "tak"})
ok(r["filled"] == 3 and r["issues"] == 0, "w normie — bez odchyleń")
ok(ev({"s|a|t": "5.1"})["bad"] == {"s|a|t"}, "5.1 przy max 5 → odchylenie")
ok(ev({"s|b|t": "-15"})["issues"] == 1, "mroźnia -15 przy max -18 → odchylenie")
ok(ev({"s|b|t": "-20"})["issues"] == 0, "mroźnia -20 → w normie (limit wiersza)")
ok(ev({"s|h|t": "70"})["issues"] == 1 and ev({"s|h|t": "80"})["issues"] == 0,
   "gorące: min 75")
ok(ev({"s|a|y": "nie"})["issues"] == 1, "„Nie” liczy się jako odchylenie")
ok(ev({"s|a|t": "ciepło"})["issues"] == 1, "tekst w polu liczby → odchylenie")
ok(ev({"s|a|c": False})["filled"] == 0, "odznaczone pole = niewypełnione")
ok(ev({"f|r1|t": "3"})["total"] == 11, "użyty wiersz dziennika wchodzi do sumy")
ok(ev({}, footer={"sig": "Ola"})["filled"] == 1, "podpis w stopce liczony")
ok(cl.limits_text(None, 5, "°C") == "≤ 5 °C" and cl.limits_text(75, None) == "≥ 75"
   and cl.limits_text(0, 5) == "0…5", "opis normy")
ok(cl.format_value(tpl["sections"][0]["columns"][0], "4,50") == "4.5 °C"
   and cl.format_value(tpl["sections"][0]["columns"][2], True) == "✓"
   and cl.format_value(tpl["sections"][0]["columns"][1], "nd") == "N/D",
   "formatowanie wartości")

# ════════════════════════ baza ════════════════════════
section("szablony w bazie")
c = db.init_db()
ok(cl.seed_presets(c) == 4 and cl.seed_presets(c) == 0,
   "szablony startowe wgrywają się raz")
tid = cl.save_template(c, dict(tpl))
got = cl.get_template(c, tid)
ok(got["version"] == 1 and got["active"] == 1, "nowy szablon: wersja 1, aktywny")
got["name"] = "Ocena 2"
cl.save_template(c, got)
ok(cl.get_template(c, tid)["version"] == 2
   and cl.get_template(c, tid)["name"] == "Ocena 2", "zapis podbija wersję")
ok(raises(cl.save_template, c, dict(tpl, name="daily cleaning CHECKLIST")),
   "dubel nazwy (bez względu na wielkość liter) odrzucony")
dup = cl.duplicate_template(c, tid)
ok(cl.get_template(c, dup)["name"] == "Ocena 2 — kopia", "duplikat ma własną nazwę")
order = [x["id"] for x in cl.list_templates(c)]
cl.move_template(c, dup, -99)
ok(cl.list_templates(c)[0]["id"] == dup, "przesunięcie na górę listy")
cl.set_template_active(c, dup, False)
cat = cl.catalog_for_server(c)
ok(dup not in [x["id"] for x in cat["templates"]] and len(cat["templates"]) == 5,
   "ukryty szablon nie jedzie na serwer")
ok(all("version" in x and "sections" in x for x in cat["templates"]),
   "katalog niesie wersję i strukturę")
cl.delete_template(c, dup)
ok(cl.get_template(c, dup) is None, "usunięcie szablonu")
imp = cl.import_template(c, cl.get_template(c, tid))
ok(cl.get_template(c, imp)["name"] == "Ocena 2 (2)",
   "import z pliku zawsze jako nowy szablon")
cl.delete_template(c, imp)

section("archiwum kopii")
snap = dict(cl.get_template(c, tid))


def entry(eid, day, person="Ola", values=None):
    return {"id": eid, "template_id": tid, "date": day, "name": person,
            "template": snap, "values": values or {}, "header": {},
            "footer": {"sig": person}, "submitted_at": f"{day}T10:00:00+00:00"}


ok(cl.import_entry(c, entry("e1", "2026-08-10", values={"s|b|t": "-15"}))
   == ("e1", True), "kopia zapisana")
ok(cl.import_entry(c, entry("e1", "2026-08-10")) == ("e1", False),
   "ta sama kopia drugi raz — bez dubla")
cl.import_entry(c, entry("e2", "2026-08-12", "Marek", {"s|a|c": True}))
cl.import_entry(c, entry("e3", "2026-09-01", "Ala"))
ok(raises(cl.import_entry, c, dict(entry("e4", "wczoraj"))), "zła data odrzucona")
ok(raises(cl.import_entry, c, dict(entry("e5", "2026-08-10"), template="x")),
   "kopia bez szablonu odrzucona")
ok(raises(cl.import_entry, c, dict(entry("", "2026-08-10"))), "kopia bez id")
e1 = cl.get_entry(c, "e1")
ok(e1["issues"] == 1 and e1["template"]["version"] == 2 and e1["seen"] == 0,
   "kopia pamięta odchylenia, wersję szablonu i jest „nowa”")
ok([e["id"] for e in cl.list_entries(c)] == ["e3", "e2", "e1"],
   "domyślnie od najnowszej daty")
ok([e["id"] for e in cl.list_entries(c, desc=False)] == ["e1", "e2", "e3"],
   "sortowanie od najstarszej")
ok([e["id"] for e in cl.list_entries(c, month="2026-08")] == ["e2", "e1"],
   "filtr miesiąca")
ok([e["id"] for e in cl.list_entries(c, sort="person", desc=False)]
   == ["e3", "e2", "e1"], "sortowanie po osobie")
ok(cl.list_entries(c, sort="issues")[0]["id"] == "e1", "sortowanie po odchyleniach")
ok([e["id"] for e in cl.list_entries(c, search="mar")] == ["e2"], "szukanie osoby")
ok(len(cl.list_entries(c, template_name="Ocena 2")) == 3
   and not cl.list_entries(c, template_name="Inna"), "filtr szablonu")
ok(cl.entry_months(c) == ["2026-09", "2026-08"], "miesiące z kopiami")
ok(cl.unseen_count(c) == 3, "trzy nieotwarte")
cl.mark_seen(c, "e1")
ok(cl.unseen_count(c) == 2 and cl.archive_stats(c, "2026-08") ==
   {"entries": 2, "with_issues": 1, "unseen": 1, "voided": 0},
   "statystyki archiwum")

section("kopia nie zależy od szablonu")
edited = cl.get_template(c, tid)
edited["sections"][0]["rows"] = edited["sections"][0]["rows"][:1]
edited["name"] = "Ocena 3"
cl.save_template(c, edited)
ok(len(cl.get_entry(c, "e1")["template"]["sections"][0]["rows"]) == 3
   and cl.get_entry(c, "e1")["template_name"] == "Ocena 2",
   "zmiana szablonu nie rusza starej kopii")
cl.delete_template(c, tid)
ok(cl.get_entry(c, "e1") is not None, "usunięcie szablonu nie usuwa kopii")

section("wydruk HTML")
page_html = cl.entry_html(cl.get_entry(c, "e1"), "HRC <Test>")
ok("<h1>Ocena 2</h1>" in page_html and "2026-08-10" in page_html, "tytuł i data")
ok("class='c bad'" in page_html and "-15 °C" in page_html,
   "odchylenie zaznaczone")
ok("class='off'" in page_html, "wyłączona komórka przekreślona")
ok("HRC &lt;Test&gt;" in page_html and "<Test>" not in page_html,
   "tekst jest escapowany")
ok("http://" not in page_html and "https://" not in page_html
   and "<script" not in page_html, "strona samowystarczalna, bez skryptów")

section("kopia zapasowa")
zpath = os.path.join(TMP, "kopia.zip")
info = cl.export_backup(c, zpath)
ok(info["entries"] == 3 and info["templates"] == 4, f"manifest ({info})")
with zipfile.ZipFile(zpath) as z:
    ok(sorted(z.namelist()) == ["entries.json", "manifest.json",
                                "templates.json"], "trzy pliki JSON w ZIP")
ok(not os.path.exists(zpath + ".tmp"), "plik tymczasowy posprzątany")
c2 = db.init_db(os.path.join(TMP, "restore.db"))
res = cl.import_backup(c2, zpath)
ok(res == {"templates": 4, "entries": 3, "skipped": 0}, f"przywrócenie ({res})")
ok(cl.import_backup(c2, zpath) == {"templates": 0, "entries": 0, "skipped": 0},
   "drugie przywrócenie niczego nie dubluje")
r1 = cl.get_entry(c2, "e1")
ok(r1["issues"] == 1 and r1["person"] == "Ola" and r1["seen"] == 1
   and r1["values"] == {"s|b|t": "-15"}, "kopia wraca cała")
bad_zip = os.path.join(TMP, "zly.zip")
with open(bad_zip, "w") as f:
    f.write("to nie zip")
ok(raises(cl.import_backup, c2, bad_zip), "śmieciowy plik odrzucony")
c2.close()

section("kopie automatyczne")
bdir = os.path.join(TMP, "auto")
p1 = cl.auto_backup(c, bdir, today="2026-08-13")
ok(p1.endswith("checklisty-2026-08-13.zip") and os.path.exists(p1),
   "pierwsza kopia automatyczna")
ok(cl.auto_backup(c, bdir, today="2026-08-14") == "",
   "bez zmian w danych — bez nowej kopii")
cl.import_entry(c, entry("e9", "2026-08-14"))
ok(cl.auto_backup(c, bdir, today="2026-08-14") != "", "po zmianie — nowa kopia")
ok(cl.auto_backup(c, "") == "", "bez folderu — wyłączone")
for n in range(40):
    open(os.path.join(bdir, f"checklisty-2025-01-{n:02d}.zip"), "w").close()
cl.void_entry(c, "e9", "test przycinania kopii")
cl.auto_backup(c, bdir, today="2026-08-15")
ok(len(os.listdir(bdir)) == cl.BACKUP_KEEP, "stare kopie są przycinane")

section("nie da się podrobić: brak usuwania i edycji")
import sqlite3 as _sq                                    # noqa: E402
cl.import_entry(c, entry("f1", "2026-08-16", values={"s|b|t": "-30"}))


def blocked(sql, args=()):
    try:
        c.execute(sql, args)
        c.commit()
        return False
    except _sq.DatabaseError:
        c.rollback()
        return True

ok(not hasattr(cl, "delete_entry"), "w kodzie nie ma już usuwania kopii")
ok(blocked("DELETE FROM checklist_entries WHERE id='f1'"),
   "baza nie pozwala usunąć kopii")
ok(blocked("DELETE FROM checklist_entries"), "ani wszystkich naraz")
ok(blocked("UPDATE checklist_entries SET data_json='{}' WHERE id='f1'"),
   "ani zmienić wartości")
ok(blocked("UPDATE checklist_entries SET entry_date='2026-08-01' WHERE id='f1'"),
   "ani przestawić daty")
ok(blocked("UPDATE checklist_entries SET person='Ktoś' WHERE id='f1'"),
   "ani podmienić osoby")
ok(blocked("UPDATE checklist_entries SET issues=5 WHERE id='f1'"),
   "ani zmienić liczby odchyleń")
ok(cl.get_entry(c, "f1")["values"] == {"s|b|t": "-30"}, "kopia nietknięta")
cl.mark_seen(c, "f1")
ok(cl.get_entry(c, "f1")["seen"] == 1, "oznaczenie „przeczytane” dalej działa")
before = cl.import_entry(c, entry("f1", "2026-08-01", values={"s|b|t": "-1"}))
ok(before == ("f1", False) and cl.get_entry(c, "f1")["entry_date"] == "2026-08-16",
   "ponowne wysłanie z tym samym id nie nadpisuje kopii")

section("anulowanie")
ok(raises(cl.void_entry, c, "f1", ""), "bez powodu — nie")
ok(raises(cl.void_entry, c, "f1", "  ab "), "za krótki powód — nie")
ok(cl.void_entry(c, "f1", "  zła   data\n na liście ") is True, "anulowana")
fe = cl.get_entry(c, "f1")
ok(fe["voided_at"] and fe["void_reason"] == "zła data na liście",
   "data i powód zapisane")
ok(fe["values"] == {"s|b|t": "-30"}, "treść została")
ok(cl.void_entry(c, "f1", "drugi raz inny powód") is False,
   "drugi raz — nic się nie zmienia")
ok(cl.get_entry(c, "f1")["void_reason"] == "zła data na liście",
   "powodu nie da się podmienić")
ok(blocked("UPDATE checklist_entries SET voided_at=NULL WHERE id='f1'"),
   "anulowania nie da się cofnąć")
ok(cl.void_entry(c, "brak", "nie ma takiej") is False, "nieistniejąca — False")
row = [r for r in cl.list_entries(c, month="2026-08") if r["id"] == "f1"][0]
ok(row["voided_at"] and row["void_reason"], "archiwum widzi anulowanie")
st = cl.archive_stats(c, "2026-08")
ok(st["voided"] >= 1, "licznik anulowanych")
ok(st["with_issues"] == len([r for r in cl.list_entries(c, month="2026-08")
                             if r["issues"] and not r["voided_at"]]),
   "anulowane nie liczą się do odchyleń")
vh = cl.entry_html(cl.get_entry(c, "f1"))
ok("ANULOWANA" in vh and "zła data na liście" in vh, "wydruk z napisem ANULOWANA")
ok("ANULOWANA" not in cl.entry_html(cl.get_entry(c, "e1")),
   "zwykła kopia bez napisu")
sig = cl.backup_signature(c)
cl.import_entry(c, entry("f2", "2026-08-16"))
sig2 = cl.backup_signature(c)
cl.void_entry(c, "f2", "test kopii automatycznej")
ok(cl.backup_signature(c) != sig2 != sig, "anulowanie wywołuje nową autokopię")

section("anulowanie przeżywa kopię zapasową")
zv = os.path.join(TMP, "void.zip")
cl.export_backup(c, zv)
c3 = db.connect(os.path.join(TMP, "void-restore.db"))
db.init_db(os.path.join(TMP, "void-restore.db"))
c3 = db.connect(os.path.join(TMP, "void-restore.db"))
cl.import_backup(c3, zv)
r3 = cl.get_entry(c3, "f1")
ok(r3 and r3["voided_at"] == fe["voided_at"]
   and r3["void_reason"] == "zła data na liście",
   "po przywróceniu nadal anulowana, z tą samą datą i powodem")
c3.close()

# миграция: старая база без колонок и триггеров
section("stara baza 1.1.0 dostaje ochronę")
old = os.path.join(TMP, "old.db")
oc = _sq.connect(old)
oc.executescript("""CREATE TABLE checklist_entries (id TEXT PRIMARY KEY,
  template_id TEXT, template_name TEXT, entry_date TEXT NOT NULL, person TEXT,
  submitted_at TEXT, received_at TEXT, source TEXT,
  issues INTEGER NOT NULL DEFAULT 0, filled INTEGER NOT NULL DEFAULT 0,
  total INTEGER NOT NULL DEFAULT 0, seen INTEGER NOT NULL DEFAULT 0,
  data_json TEXT NOT NULL);
  INSERT INTO checklist_entries (id, entry_date, data_json)
  VALUES ('old1', '2026-07-01', '{}');""")
oc.commit()
oc.close()
oc = db.init_db(old)
cols = {r[1] for r in oc.execute("PRAGMA table_info(checklist_entries)")}
ok({"voided_at", "void_reason"} <= cols, "kolumny dodane")
try:
    oc.execute("DELETE FROM checklist_entries WHERE id='old1'")
    ok(False, "stara kopia też chroniona")
except _sq.DatabaseError:
    ok(True, "stara kopia też chroniona")
oc.close()
oc = db.init_db(old)          # drugi start — bez błędów
ok(oc.execute("SELECT COUNT(*) FROM checklist_entries").fetchone()[0] == 1,
   "drugi start na tej samej bazie bez problemów")
oc.close()

# ════════════════════════ сервер ════════════════════════
section("serwer: dostęp")


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


PORT = free_port()
URL = f"http://127.0.0.1:{PORT}"
threading.Thread(
    target=lambda: ks.app.run(host="127.0.0.1", port=PORT, debug=False,
                              use_reloader=False), daemon=True).start()
for _ in range(50):
    try:
        httpx.get(URL + "/", timeout=1)
        break
    except httpx.HTTPError:
        time.sleep(0.1)

sc = ks.db_conn()
for tenant, token in (("t1", "token-a"), ("t2", "token-b")):
    sc.execute("INSERT INTO tenants (id,name,token,active,created_at)"
               " VALUES (?,?,?,1,'2026-01-01')", (tenant, f"Kuchnia {tenant}",
                                                  token))
sc.commit()
A, B = "token-a", "token-b"


def get(path, tok):
    return httpx.get(f"{URL}{path}", params={"t": tok}, timeout=5)


def post(path, tok, body=None, raw=None):
    return httpx.post(f"{URL}{path}", params={"t": tok}, timeout=5,
                      content=raw if raw is not None else json.dumps(body),
                      headers={"Content-Type": "application/json"})


ok(httpx.get(URL + "/checklists").status_code == 403, "strona bez tokenu → 403")
r = get("/checklists", A)
ok(r.status_code == 200 and "Checklisty" in r.text, "strona z tokenem")
ok("/checklists" in httpx.get(URL + "/sw.js").text, "service worker zna stronę")
ok("to-checklists" in get("/form", A).text,
   "formularz strat ma przycisk do checklist")
ok(get("/api/checklists", "zly").status_code == 403, "API bez tokenu → 403")
ok(get("/api/checklists", A).json()["templates"] == [],
   "pusty katalog to nie błąd")

section("serwer: katalog szablonów")
ok(post("/api/checklists", A, {"x": 1}).status_code == 400, "brak listy → 400")
ok(post("/api/checklists", A, {"templates": [{"name": "bez id"}]}).status_code
   == 400, "szablon bez id → 400")
okk, data = asyncio.run(sync.push_checklists(URL, A, cl.catalog_for_server(c)))
ok(okk and data["templates"] == 4, f"aplikacja wysyła katalog ({data})")
got = get("/api/checklists", A).json()
ok(len(got["templates"]) == 4 and got["today"] and got["kitchen"],
   "telefon dostaje szablony, datę i nazwę kuchni")
ok(get("/api/checklists", B).json()["templates"] == [],
   "druga kuchnia nie widzi cudzych szablonów")

section("serwer: przyjęcie wypełnionej checklisty")
tpl_srv = next(x for x in got["templates"]
               if x["name"] == "Temperature Checklist")
sec = tpl_srv["sections"][0]
freezer = sec["rows"][0]
REPORT = {"id": "dev-1", "template_id": tpl_srv["id"], "template": tpl_srv,
          "date": "2026-08-20", "name": "Ola",
          "values": {f"{sec['id']}|{freezer['id']}|open": "-15",
                     f"{sec['id']}|{freezer['id']}|i12": "-20"},
          "header": {}, "footer": {}}
r = post("/api/checklist-report", A, REPORT)
ok(r.status_code == 200 and r.json()["id"] == "dev-1", "checklista przyjęta")
ok(post("/api/checklist-report", A, REPORT).status_code == 200
   and get("/api/checklist-reports", A).json()["count"] == 1,
   "powtórna wysyłka z kolejki offline nie dubluje")
ok(post("/api/checklist-report", "zly", REPORT).status_code == 403, "zły token")
ok(post("/api/checklist-report", A, dict(REPORT, id="x1", name="")).status_code
   == 400, "bez imienia → 400")
ok(post("/api/checklist-report", A, dict(REPORT, id="x2", date="")).status_code
   == 400, "bez daty → 400")
ok(post("/api/checklist-report", A, dict(REPORT, id="x3", template=None))
   .status_code == 400, "bez szablonu → 400")
ok(post("/api/checklist-report", A, raw=b"{nie json").status_code == 400,
   "zły JSON → 400")
ok(post("/api/checklist-report", A, raw=json.dumps(dict(
    REPORT, id="x4", note="x" * ks.MAX_CHECKLIST_BYTES)).encode()).status_code
   == 413, "za duża → 413")
ok(get("/api/checklist-reports", B).json()["count"] == 0,
   "druga kuchnia nie widzi cudzych checklist")
ok(httpx.delete(f"{URL}/api/checklist-reports/dev-1",
                params={"t": B}).status_code == 404,
   "druga kuchnia nie usunie cudzej")
ok(get("/api/whoami", A).json()["checklists_pending"] == 1, "whoami liczy kolejkę")

section("serwer: stare checklisty znikają")
sc.execute("INSERT INTO checklist_reports VALUES ('stara','t1','x','Ola',"
           "'2020-01-01','2020-01-01T00:00:00+00:00','{}')")
sc.commit()
ok(get("/api/checklist-reports", A).json()["count"] == 1,
   f"starsze niż {ks.MAX_AGE_DAYS} dni są usuwane")

# ════════════════════════ интерфейс ════════════════════════
class FakeWindow:
    min_width = min_height = width = height = 0
    icon = None


class FakePage:
    def __init__(self):
        self.window = FakeWindow()
        self.services, self.controls, self.dialogs, self.tasks = [], [], [], []
        self.theme = self.dark_theme = self.theme_mode = None
        self.title, self.padding, self.fonts = "", None, None

    def add(self, *controls):
        self.controls.extend(controls)

    def update(self):
        pass

    def show_dialog(self, dlg):
        self.dialogs.append(dlg)

    def pop_dialog(self):
        if self.dialogs:
            self.dialogs.pop()

    def run_task(self, fn, *args):
        self.tasks.append((getattr(fn, "__name__", str(fn)), fn, args))


CHILD_ATTRS = ("content", "controls", "actions", "tabs", "title", "items")


def walk(node, depth=0):
    if node is None or depth > 60:
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
    out = []
    for ctl in walk(node):
        if isinstance(ctl, ft.Text) and ctl.value is not None:
            out.append(str(ctl.value))
        elif isinstance(ctl, ft.TextField):
            out.append(str(ctl.value or ""))
        elif isinstance(getattr(ctl, "content", None), str):
            out.append(ctl.content)
    return out


def button(node, label):
    for ctl in walk(node):
        if isinstance(ctl, (ft.FilledButton, ft.TextButton, ft.OutlinedButton,
                            ft.FilledTonalButton)) \
                and str(getattr(ctl, "content", "") or "") == label:
            return ctl
    return None


def field(node, label):
    for ctl in walk(node):
        if isinstance(ctl, ft.TextField) and ctl.label == label:
            return ctl
    return None


def chip(node, label):
    """«Таблетка» из self.chips — Container с on_click и текстом внутри."""
    for ctl in walk(node):
        if isinstance(ctl, ft.Container) and ctl.on_click \
                and isinstance(ctl.content, ft.Text) \
                and ctl.content.value == label:
            return ctl
    return None


def wrap_expand_offenders(node):
    bad = []
    for ctl in walk(node):
        if isinstance(ctl, ft.Row) and getattr(ctl, "wrap", False):
            bad += [type(ch).__name__ for ch in (ctl.controls or [])
                    if getattr(ch, "expand", None)]
    return bad


class E:
    """Событие Flet: обработчикам нужен только `control`."""
    def __init__(self, control):
        self.control = control


def view():
    return app.content.content


section("aplikacja: połączenie z serwerem")
db.set_config_many(c, {"server_url": URL, "server_token": A,
                       "kitchen_name": "HRC Test"})
cl.mark_seen(c)
page = FakePage()
app = mk.KuchniaApp(page)
ok("cl_auto_loop" in [n for n, _f, _a in page.tasks],
   "tło: okresowe sprawdzanie serwera wystartowało")
added = asyncio.run(app.cl_fetch())
ok(added == 1, f"aplikacja pobrała checklistę z serwera ({added})")
got_e = cl.get_entry(c, "dev-1")
ok(got_e and got_e["issues"] == 1 and got_e["person"] == "Ola"
   and got_e["entry_date"] == "2026-08-20" and got_e["submitted_at"],
   "kopia w archiwum: osoba, data, 1 odchylenie (mroźnia -15)")
ok(get("/api/checklist-reports", A).json()["count"] == 0,
   "po pobraniu serwer jest pusty")
ok(asyncio.run(app.cl_fetch()) == 0, "drugie pobranie nic nie dubluje")
ok(app.nav_items["checklists"][3].visible
   and app.nav_items["checklists"][3].content.value == "1",
   "licznik nowych w menu")

section("aplikacja: śmieci z serwera nie blokują kolejki")
sc.execute("INSERT INTO checklist_reports VALUES ('smiec','t1','x','Ola',"
           "'2026-08-20',?,?)", (db.now_utc(), json.dumps({"id": "smiec"})))
sc.commit()
ok(asyncio.run(app.cl_fetch()) == 0
   and get("/api/checklist-reports", A).json()["count"] == 0
   and cl.get_entry(c, "smiec") is None,
   "nieczytelna checklista jest usuwana z serwera, a nie importowana")

section("aplikacja: archiwum")
app.select("checklists")
tx = texts(view())
ok("Checklisty" in tx and "Archiwum" in tx, "ekran się buduje")
ok("Temperature Checklist" in tx and "2026-08-20" in tx, "kopia w tabeli")
ok(not wrap_expand_offenders(view()), "brak expand w Row(wrap) — archiwum")
ok(any("Data ▼" == x for x in tx), "sortowanie po dacie, malejąco")
app._cl_set_sort("date")
ok(any("Data ▲" == x for x in texts(view())) and not app.cl_desc,
   "drugie kliknięcie odwraca kolejność")
app._cl_set("cl_month", "2026-09")
ok("Temperature Checklist" not in texts(view()), "filtr miesiąca działa")
app._cl_set("cl_month", "")

section("aplikacja: podgląd kopii")
app.cl_preview_entry("dev-1")
dlg = page.dialogs[-1]
dt = texts(dlg)
ok("Walk-In Freezer (-18°C)" in dt and "-15 °C" in dt and "-20 °C" in dt,
   "podgląd pokazuje wiersze i wartości")
ok(any("Odchylenia od normy: 1" in x for x in dt), "baner odchyleń")
ok("╳" in dt, "wyłączone komórki przekreślone")
ok(cl.get_entry(c, "dev-1")["seen"] == 1
   and not app.nav_items["checklists"][3].visible,
   "otwarcie zdejmuje znacznik „nowe”")
ok(not wrap_expand_offenders(dlg), "brak expand w Row(wrap) — podgląd")
fname, text = app._cl_html("dev-1")
ok(fname.startswith("2026-08-20 Temperature Checklist") and "<table>" in text,
   f"plik do druku ({fname})")
page.pop_dialog()

section("aplikacja: lista szablonów")
app._cl_set_tab("templates")
tx = texts(view())
ok(all(n in tx for n in names), "cztery szablony na liście")
ok(not wrap_expand_offenders(view()), "brak expand w Row(wrap) — szablony")

section("aplikacja: konstruktor")
page.tasks.clear()
app.cl_open_editor(None)
ok(app.cl_edit is not None and "Nowy szablon" in texts(view()),
   "konstruktor otwarty na pustym szablonie")
ok(not wrap_expand_offenders(view()), "brak expand w Row(wrap) — konstruktor")
name_f = field(view(), "Nazwa checklisty")
name_f.value = "Kontrola dostaw"
name_f.on_change(E(name_f))
ok(app.cl_edit["name"] == "Kontrola dostaw" and app.cl_dirty,
   "wpisanie nazwy trafia do szablonu")
button(view(), "Dodaj kolumnę").on_click(None)
ok(len(app.cl_edit["sections"][0]["columns"]) == 2, "dodano kolumnę")
types = [x for x in walk(view()) if isinstance(x, ft.Dropdown)
         and x.label == "Typ"]
types[1].value = "temp"
types[1].on_select(E(types[1]))
ok(app.cl_edit["sections"][0]["columns"][1]["type"] == "temp",
   "zmiana typu kolumny")
mx = [x for x in walk(view()) if isinstance(x, ft.TextField)
      and x.label == "max"]
ok(len(mx) == 2, "temperatura odsłania limity: kolumny i wiersza")
mx[0].value = "5"
mx[0].on_change(E(mx[0]))
button(view(), "Dodaj wiersz").on_click(None)
ok(len(app.cl_edit["sections"][0]["rows"]) == 2, "dodano wiersz")
button(view(), "Wklej listę…").on_click(None)
box = field(page.dialogs[-1], "Jeden punkt w linii")
box.value = "Nabiał\n\n  Mięso  \nRyby"
button(page.dialogs[-1], "Dodaj").on_click(None)
labels = [r["label"] for r in app.cl_edit["sections"][0]["rows"]]
ok(labels[-3:] == ["Nabiał", "Mięso", "Ryby"] and len(labels) == 5,
   f"wklejona lista → wiersze ({labels})")
button(view(), "Dodaj sekcję").on_click(None)
ok(len(app.cl_edit["sections"]) == 2
   and len(app.cl_edit["sections"][1]["columns"]) == 2,
   "nowa sekcja dziedziczy kolumny poprzedniej")
chip(view(), "Dziennik — wiersze dopisuje wypełniający").on_click(None)
ok(app.cl_edit["sections"][0]["mode"] == "free", "tryb dziennika")
chip(view(), "Tygodniowa").on_click(None)
ok(app.cl_edit["period"] == "weekly", "okres")
button(view(), "Dodaj pole").on_click(None)
ok(len(app.cl_edit["header"]) == 1, "pole nagłówka dodane")
button(view(), "Podgląd").on_click(None)
ok(any("Podgląd — Kontrola dostaw" in x for x in texts(page.dialogs[-1])),
   "podgląd szablonu z konstruktora")
page.pop_dialog()

section("aplikacja: zapis wysyła szablony na serwer")
app.cl_edit["sections"][1]["rows"][0]["label"] = "Punkt"
button(view(), "Zapisz i wyślij na serwer").on_click(None)
saved = [x for x in cl.list_templates(c) if x["name"] == "Kontrola dostaw"]
ok(len(saved) == 1 and app.cl_edit is None, "szablon zapisany, konstruktor zamknięty")
ok(saved and saved[0]["sections"][0]["columns"][1]["max"] == 5.0
   and saved[0]["period"] == "weekly", "limity i okres zapisane")
pushes = [(f, a) for n, f, a in page.tasks if n == "_cl_push"]
ok(len(pushes) == 1, "zapis sam zleca wysyłkę na serwer")
for f, a in pushes:
    asyncio.run(f(*a))
srv = [x["name"] for x in get("/api/checklists", A).json()["templates"]]
ok("Kontrola dostaw" in srv and len(srv) == 5,
   f"telefony widzą nowy szablon od razu ({len(srv)})")
ok(app.cl_status.startswith("✓"), f"status wysyłki ({app.cl_status})")

section("aplikacja: zmiany na liście też jadą na serwer")
page.tasks.clear()
app._cl_tpl_toggle(saved[0])
for n, f, a in page.tasks:
    if n == "_cl_push":
        asyncio.run(f(*a))
ok("Kontrola dostaw" not in [x["name"] for x in
                             get("/api/checklists", A).json()["templates"]],
   "ukrycie szablonu zdejmuje go z telefonów")

section("aplikacja: bez serwera nic się nie wysypuje")
db.set_config_many(c, {"server_url": "http://127.0.0.1:1"})
ok(asyncio.run(app.cl_fetch()) == 0 and not app.cl_status_ok,
   f"niedostępny serwer → czytelny status ({app.cl_status})")
db.set_config_many(c, {"server_url": "", "server_token": ""})
page.tasks.clear()
app.cl_autopush(saved=True)
ok(not page.tasks, "bez adresu serwera wysyłka nie startuje")
ok(asyncio.run(app.cl_fetch()) == 0, "pobieranie bez serwera = 0")

section("aplikacja: odrzucenie zmian")
app.cl_open_editor(cl.list_templates(c)[0])
app._cl_close_editor()
ok(app.cl_edit is None, "bez zmian — konstruktor zamyka się od razu")
app.cl_open_editor(cl.list_templates(c)[0])
app.cl_dirty = True
app._cl_close_editor()
ok(app.cl_edit is not None and page.dialogs, "ze zmianami — pyta o potwierdzenie")
button(page.dialogs[-1], "Odrzuć").on_click(None)
ok(app.cl_edit is None, "potwierdzenie odrzuca zmiany")

section("aplikacja: anulowanie zamiast usuwania")
cl.import_entry(c, entry("u1", "2026-08-20"))
app.cl_month = ""
target = [e for e in cl.list_entries(c) if e["id"] == "u1"][0]
page.dialogs.clear()
app.cl_void_entry(target)
ok(page.dialogs and button(page.dialogs[-1], "Anuluj kopię"),
   "okno anulowania z przyciskiem")
app.cl_void_reason.value = "x"
app.cl_void_do()
ok(cl.get_entry(c, "u1")["voided_at"] is None, "za krótki powód — nic")
ok(page.dialogs, "okno zostaje otwarte z błędem")
app.cl_void_reason.value = "kucharz wybrał zły dzień"
app.cl_void_do()
ok(cl.get_entry(c, "u1")["voided_at"], "z powodem — anulowana")
page.dialogs.clear()
app.cl_preview_entry("u1")
pv = page.dialogs[-1]
ok(not getattr(button(pv, "Anuluj kopię"), "visible", True),
   "w podglądzie anulowanej nie ma przycisku anulowania")
ok(button(pv, "Usuń") is None, "w podglądzie nie ma „Usuń”")
ok(any("ANULOWANA" in t for t in texts(pv)), "podgląd pokazuje ANULOWANA")
page.dialogs.clear()

sc.close()
c.close()
print(f"\n{'=' * 46}\nOK: {PASS}   BŁĘDY: {FAIL}\nDane testowe: {TMP}")
raise SystemExit(1 if FAIL else 0)
