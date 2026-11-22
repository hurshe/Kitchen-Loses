"""Сквозная проверка: телефон → сервер → приложение менеджера.

Поднимает настоящий сервер на localhost и гоняет через него весь путь
страты: выгрузку каталога, отправку зашлушения «как из формы», приём
приложением, подтверждение и удаление с сервера.

Ловит то, чего не поймают отдельные проверки: несовпадение форматов между
формой, сервером и приложением.

    python check_e2e.py
"""

import asyncio
import json
import os
import socket
import sys
import tempfile
import threading
import time
import uuid
from datetime import datetime, timezone

# Консоль с cp1251/cp1252 роняет print() на польских буквах — см. check_db.py
for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        try:
            _s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:                                  # noqa: BLE001
            pass

TMP = tempfile.mkdtemp(prefix="kuchnia-e2e-")
os.environ["KUCHNIA_DB"] = os.path.join(TMP, "local.db")
os.environ["KUCHNIA_SERVER_DB"] = os.path.join(TMP, "queue.db")
os.environ["KUCHNIA_ADMIN_PASSWORD"] = "haslo-e2e"
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "server"))

import httpx                    # noqa: E402
import db                       # noqa: E402
import sync                     # noqa: E402
import kitchen_server as ks     # noqa: E402

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


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


PORT = free_port()
URL = f"http://127.0.0.1:{PORT}"

# ────────────────────── поднимаем сервер ──────────────────────
threading.Thread(
    target=lambda: ks.app.run(host="127.0.0.1", port=PORT, debug=False,
                              use_reloader=False),
    daemon=True).start()

for _ in range(50):
    try:
        httpx.get(URL + "/", timeout=1)
        break
    except httpx.HTTPError:
        time.sleep(0.1)

section("serwer wstał")
r = httpx.get(URL + "/", timeout=3)
ok(r.status_code == 200 and "Straty Kuchnia" in r.text, "strona główna")

# кухня заводится так же, как её заведёт владелец в /admin
c = ks.db_conn()
TOKEN = uuid.uuid4().hex[:24]
c.execute("INSERT INTO tenants (id,name,token,active,created_at)"
          " VALUES (?,?,?,1,?)",
          ("t1", "HRC Warszawa — kuchnia", TOKEN,
           datetime.now(timezone.utc).isoformat()))
c.commit()
c.close()

# ────────────────────── приложение менеджера ──────────────────────
section("aplikacja menedżera")
local = db.init_db()
db.seed(local)
db.set_config_many(local, {"kitchen_name": "HRC Warszawa — kuchnia",
                           "server_url": URL, "server_token": TOKEN})
ok(len(db.list_items(local)) > 50, "katalog startowy wgrany lokalnie")

okk, data = asyncio.run(sync.whoami(URL, TOKEN))
ok(okk and data["kitchen"] == "HRC Warszawa — kuchnia", f"whoami ({data})")
okk, data = asyncio.run(sync.whoami(URL, "zly-token"))
ok(not okk and "token" in data.lower(), f"zły token daje czytelny błąd ({data})")
okk, data = asyncio.run(sync.whoami("http://127.0.0.1:1", TOKEN))
ok(not okk and data == "Serwer niedostępny", f"brak serwera → komunikat ({data})")
okk, data = asyncio.run(sync.whoami("", TOKEN))
ok(not okk and "adres" in data.lower(), "pusty adres → komunikat")

okk, data = asyncio.run(sync.push_catalog(URL, TOKEN,
                                          db.catalog_for_server(local)))
ok(okk and data["items"] > 50, f"katalog wysłany na serwer ({data})")

# ────────────────────── телефон повара ──────────────────────
section("telefon kucharza")
cat = httpx.get(f"{URL}/api/catalog", params={"t": TOKEN}, timeout=5).json()
ok(len(cat["items"]) > 50, "formularz widzi katalog")
ok(cat["kitchen"] == "HRC Warszawa — kuchnia", "formularz zna nazwę kuchni")
ok(cat["reasons"], "formularz zna powody")

losos = [i for i in cat["items"] if i["name"] == "Łosoś"][0]
bulka = [i for i in cat["items"] if i["name"] == "Bułka do burgera"][0]
ok(losos["units"] == ["kg", "g"], "formularz dostał dozwolone jednostki")

# ровно то тело, которое шлёт form.html
report = {
    "id": uuid.uuid4().hex[:20], "name": "Ola",
    "shift_date": ks.current_shift_date(),
    "lines": [
        {"item_id": losos["id"], "item_name": losos["name"], "qty": "1,2",
         "unit": "kg", "reason": cat["reasons"][0], "note": ""},
        {"item_id": losos["id"], "item_name": losos["name"], "qty": "300",
         "unit": "g", "reason": "", "note": "resztka z wieczora"},
        {"item_id": bulka["id"], "item_name": bulka["name"], "qty": "4",
         "unit": "szt", "reason": "", "note": ""},
    ]}
r = httpx.post(f"{URL}/api/report", params={"t": TOKEN}, json=report, timeout=5)
ok(r.status_code == 200 and r.json()["lines"] == 3, "zgłoszenie wysłane")

# повтор из офлайн-очереди — то же id
r = httpx.post(f"{URL}/api/report", params={"t": TOKEN}, json=report, timeout=5)
ok(r.status_code == 200, "powtórka z kolejki offline przyjęta")

# ────────────────────── приём в приложении ──────────────────────
section("odbiór i zatwierdzenie")
okk, reports = asyncio.run(sync.fetch_reports(URL, TOKEN))
ok(okk and len(reports) == 1, f"aplikacja widzi jedno zgłoszenie ({len(reports)})")
rep = reports[0]
ok(rep["name"] == "Ola", "autor zgłoszenia")

lines = db.resolve_report_lines(local, rep["data"]["lines"])
ok(len(lines) == 3 and not any(l["problem"] for l in lines),
   "wszystkie linie rozpoznane po id z katalogu")
ok(lines[0]["qty"] == 1.2, "przecinek z telefonu → liczba")

res = db.confirm_report(local, rep)
ok(res["lines"] == 3, "zatwierdzone")
summary = {r["name"]: r for r in db.by_item(local, month=db.current_month())}
ok(summary["Łosoś"]["qty_text"] == "1.5 kg",
   f"1,2 kg + 300 g = 1.5 kg ({summary['Łosoś']['qty_text']})")
ok(summary["Bułka do burgera"]["qty_text"] == "4 szt", "sztuki bez przeliczeń")
ok(all(e["source"] == "qr" and e["person"] == "Ola"
       for e in db.journal(local)), "wpisy podpisane jako telefonowe")

okk, _ = asyncio.run(sync.delete_report(URL, TOKEN, rep["id"]))
ok(okk, "zgłoszenie skasowane z serwera")
okk, reports = asyncio.run(sync.fetch_reports(URL, TOKEN))
ok(okk and reports == [], "kolejka pusta")
okk, _ = asyncio.run(sync.delete_report(URL, TOKEN, rep["id"]))
ok(okk, "powtórne kasowanie nie jest błędem — cel osiągnięty")

section("zgłoszenie ze starym katalogiem")
# кухню переименовали позицию после того, как форма получила каталог
item = db.find_item_by_name(local, "Bułka do burgera")
db.save_item(local, {"id": item["id"], "name": "Bułka burgerowa",
                     "category": item["category"], "unit": item["unit"]})
stale = {"id": uuid.uuid4().hex[:20], "name": "Piotr",
         "shift_date": ks.current_shift_date(),
         "lines": [{"item_id": "id-z-kosmosu", "item_name": "Bułka burgerowa",
                    "qty": "2", "unit": "szt", "reason": "", "note": ""}]}
httpx.post(f"{URL}/api/report", params={"t": TOKEN}, json=stale, timeout=5)
okk, reports = asyncio.run(sync.fetch_reports(URL, TOKEN))
lines = db.resolve_report_lines(local, reports[0]["data"]["lines"])
ok(lines[0]["item_id"] == item["id"] and not lines[0]["problem"],
   "pozycja znaleziona po nazwie mimo nieznanego id")
db.confirm_report(local, reports[0])
asyncio.run(sync.delete_report(URL, TOKEN, reports[0]["id"]))

section("zgłoszenie z pozycją, której nie ma")
ghost = {"id": uuid.uuid4().hex[:20], "name": "Piotr",
         "shift_date": ks.current_shift_date(),
         "lines": [{"item_id": "x", "item_name": "Trufle z Marsa",
                    "qty": "1", "unit": "kg", "reason": "", "note": ""}]}
httpx.post(f"{URL}/api/report", params={"t": TOKEN}, json=ghost, timeout=5)
okk, reports = asyncio.run(sync.fetch_reports(URL, TOKEN))
lines = db.resolve_report_lines(local, reports[0]["data"]["lines"])
ok(lines[0]["problem"] == "nieznana pozycja", "problem opisany, nie przemilczany")
try:
    db.confirm_report(local, reports[0])
    ok(False, "zatwierdzenie z problemem powinno się zatrzymać")
except ValueError as e:
    ok("Trufle" in str(e), f"komunikat mówi, o którą pozycję chodzi ({e})")

# менеджер поправил строку в диалоге
fixed = db.resolve_report_lines(local, [{"item_id": item["id"], "qty": "1",
                                         "unit": "szt"}])
db.confirm_report(local, reports[0], fixed)
ok(db.is_report_confirmed(local, reports[0]["id"]), "po poprawce przechodzi")

n_before = len(db.journal(local))
db.confirm_report(local, reports[0], fixed)
ok(len(db.journal(local)) == n_before, "powtórne zatwierdzenie nie dubluje")

local.close()
print(f"\n{'=' * 46}\nOK: {PASS}   BŁĘDY: {FAIL}\nDane testowe: {TMP}")
# Сервер живёт в демоне-потоке, поэтому выходим жёстко — но os._exit не
# сбрасывает буферы, и без flush отчёт о проверках просто исчезает.
sys.stdout.flush()
sys.stderr.flush()
os._exit(1 if FAIL else 0)
