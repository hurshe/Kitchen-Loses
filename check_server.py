"""Автопроверки сервера зашлушений: форма, приём, очередь, изоляция кухонь.

    python check_server.py

Тестовая база очереди подставляется ДО импорта сервера — иначе проверка
писала бы в боевой kitchen_queue.db.
"""

import json
import os
import sqlite3
import sys
import tempfile
from datetime import datetime, timedelta, timezone

# Консоль с cp1251/cp1252 роняет print() на польских буквах — см. check_db.py
for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        try:
            _s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:                                  # noqa: BLE001
            pass

TMP = tempfile.mkdtemp(prefix="kuchnia-srv-")
os.environ["KUCHNIA_SERVER_DB"] = os.path.join(TMP, "queue.db")
os.environ["KUCHNIA_ADMIN_PASSWORD"] = "tajne-haslo-testowe"
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "server"))

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


ks.app.config["TESTING"] = True
cl = ks.app.test_client()

# ────────────────────── подготовка: две кухни ──────────────────────
adm = ks.app.test_client()
adm.post("/admin/login", data={"password": "tajne-haslo-testowe"})
adm.post("/admin/tenants", data={"name": "Kuchnia A"})
adm.post("/admin/tenants", data={"name": "Kuchnia B"})

c = ks.db_conn()
rows = c.execute("SELECT id, name, token FROM tenants ORDER BY name").fetchall()
c.close()
A = {"id": rows[0]["id"], "token": rows[0]["token"]}
B = {"id": rows[1]["id"], "token": rows[1]["token"]}


def get(path, tok=None):
    sep = "&" if "?" in path else "?"
    return cl.get(f"{path}{sep}t={tok}" if tok else path)


def post(path, body, tok=None, raw=None):
    sep = "&" if "?" in path else "?"
    url = f"{path}{sep}t={tok}" if tok else path
    return cl.post(url, data=raw if raw is not None else json.dumps(body),
                   content_type="application/json")


def delete(path, tok=None):
    sep = "&" if "?" in path else "?"
    return cl.delete(f"{path}{sep}t={tok}" if tok else path)


REPORT = {"id": "r-1", "name": "Ola", "shift_date": "2026-08-10",
          "lines": [{"item_id": "i1", "item_name": "Łosoś", "qty": "1,2",
                     "unit": "kg", "reason": "Zepsute", "note": ""},
                    {"item_id": "i2", "item_name": "Bułka", "qty": "3",
                     "unit": "szt", "reason": "", "note": "upadły"}]}

# ────────────────────── доступ ──────────────────────
section("dostęp i formularz")
ok(get("/").status_code == 200, "strona / działa")
ok(get("/form").status_code == 403, "formularz bez tokenu → 403")
ok(get("/form", "zly").status_code == 403, "zły token → 403")
r = get("/form", A["token"])
ok(r.status_code == 200 and b"Wy\xc5\x9blij" in r.data,
   "formularz z tokenem się otwiera")
ok(b"Podsumowanie" not in r.data and b"miesi\xc4\x85c" not in r.data.lower(),
   "w formularzu nie ma raportów — tylko wpis")
r = cl.get("/sw.js")
ok(r.status_code == 200 and b"kuchnia-form" in r.data, "service worker")

section("instalacja na tablecie (manifest)")
ok(get("/manifest.webmanifest").status_code == 403, "manifest bez tokenu → 403")
ok(get("/manifest.webmanifest", "zly").status_code == 403, "manifest, zły token → 403")
r = get("/manifest.webmanifest", A["token"])
m = r.get_json(force=True) if r.status_code == 200 else {}
ok(r.status_code == 200 and "manifest" in r.content_type, "manifest się otwiera")
ok(m.get("start_url") == f"/form?t={A['token']}",
   "start_url niesie token — ikona otworzy formularz, nie «Brak dostępu»")
ok(m.get("display") == "fullscreen", "pełny ekran")
ok("Kuchnia A" in m.get("name", ""), "nazwa z nazwą kuchni")
mB = get("/manifest.webmanifest", B["token"]).get_json(force=True)
ok(mB["id"] != m.get("id") and B["token"] in mB["start_url"],
   "każda kuchnia ma własną aplikację")
sizes = {i["sizes"] for i in m.get("icons", [])}
ok({"192x192", "512x512"} <= sizes, "ikony 192 i 512 (wymóg Chrome)")
ok(any(i.get("purpose") == "maskable" for i in m.get("icons", [])),
   "ikona maskable")
for path in ("/icon.png", "/icon-512.png"):
    ok(cl.get(path).status_code == 200, f"{path} się otwiera")
r = get("/form", A["token"])
ok(b"manifest.webmanifest" in r.data and b'id="install"' in r.data,
   "formularz podpina manifest i ma przycisk instalacji")
ok(get("/api/catalog").status_code == 403, "katalog bez tokenu → 403")

section("katalog")
r = get("/api/catalog", A["token"])
ok(r.status_code == 200 and r.get_json()["items"] == [],
   "pusty katalog to nie błąd")
cat = {"items": [{"id": "i1", "name": "Łosoś", "category": "Ryby",
                  "unit": "kg", "units": ["kg", "g"]},
                 {"id": "i2", "name": "Bułka", "category": "Pieczywo",
                  "unit": "szt", "units": ["szt"]}],
       "reasons": ["Zepsute", "Spalone"], "kitchen": "HRC Warszawa"}
r = post("/api/catalog", cat, A["token"])
ok(r.status_code == 200 and r.get_json()["items"] == 2, "aplikacja wysyła katalog")
r = get("/api/catalog", A["token"])
d = r.get_json()
ok(len(d["items"]) == 2 and d["reasons"] == ["Zepsute", "Spalone"],
   "formularz dostaje katalog")
ok(d["kitchen"] == "HRC Warszawa", "nazwa kuchni z aplikacji")
ok(d["today"] == ks.current_shift_date(), "data zmiany od serwera")
ok(get("/api/catalog", B["token"]).get_json()["items"] == [],
   "katalog nie przecieka do drugiej kuchni")
ok(post("/api/catalog", {"reasons": []}, A["token"]).status_code == 400,
   "katalog bez pozycji → 400")

# ────────────────────── приём зашлушений ──────────────────────
section("przyjmowanie zgłoszeń")
r = post("/api/report", REPORT, A["token"])
ok(r.status_code == 200 and r.get_json()["lines"] == 2, "zgłoszenie przyjęte")
ok(post("/api/report", REPORT).status_code == 403, "bez tokenu → 403")

r = post("/api/report", REPORT, A["token"])
ok(r.status_code == 200, "powtórka przyjęta bez błędu")
ok(len(get("/api/reports", A["token"]).get_json()["reports"]) == 1,
   "…ale drugiego zgłoszenia nie tworzy (to samo id)")

bad = dict(REPORT)
ok(post("/api/report", {**REPORT, "id": "x1", "lines": []},
        A["token"]).status_code == 400, "puste zgłoszenie → 400")
ok(post("/api/report", {**REPORT, "id": "x2", "name": "X"},
        A["token"]).status_code == 400, "imię za krótkie → 400")
ok(post("/api/report", {**REPORT, "id": "x3", "name": "<script>"},
        A["token"]).status_code == 400, "dziwne imię → 400")
ok(post("/api/report", {**REPORT, "id": "x4", "shift_date": "10.08.2026"},
        A["token"]).status_code == 400, "zła data → 400")
ok(post("/api/report", {**REPORT, "id": "x5",
                        "lines": [{"item_name": "", "qty": "1"}]},
        A["token"]).status_code == 400, "linia bez pozycji → 400")
ok(post("/api/report", {**REPORT, "id": "x6",
                        "lines": [{"item_name": "Łosoś", "qty": ""}]},
        A["token"]).status_code == 400, "linia bez ilości → 400")
ok(post("/api/report", {**REPORT, "id": "x7",
                        "lines": [dict(REPORT["lines"][0])] * 61},
        A["token"]).status_code == 400, "za dużo linii → 400")
ok(post("/api/report", None, A["token"], raw=b"{nie json")
   .status_code == 400, "zły JSON → 400")
ok(post("/api/report", None, A["token"],
        raw=b'{"lines":[]}' + b" " * (ks.MAX_REPORT_BYTES + 10))
   .status_code == 413, "za duże ciało → 413")

section("kolejka dla aplikacji")
r = get("/api/reports", A["token"])
d = r.get_json()
ok(d["count"] == 1 and d["kitchen"] == "Kuchnia A", "aplikacja widzi kolejkę")
rep = d["reports"][0]
ok(rep["name"] == "Ola" and rep["shift_date"] == "2026-08-10", "nagłówek")
ok(len(rep["data"]["lines"]) == 2, "linie na miejscu")
ok(rep["data"]["lines"][0]["qty"] == "1,2",
   "ilość jedzie tekstem — serwer nie liczy i nie zaokrągla")
ok(get("/api/reports", B["token"]).get_json()["count"] == 0,
   "druga kuchnia nie widzi cudzych zgłoszeń")

ok(delete(f"/api/reports/{rep['id']}", B["token"]).status_code == 404,
   "nie da się skasować cudzego zgłoszenia")
ok(delete(f"/api/reports/{rep['id']}", A["token"]).status_code == 200,
   "aplikacja kasuje zatwierdzone zgłoszenie")
ok(get("/api/reports", A["token"]).get_json()["count"] == 0, "kolejka pusta")
ok(delete(f"/api/reports/{rep['id']}", A["token"]).status_code == 404,
   "drugie kasowanie → 404")

section("whoami")
r = get("/api/whoami", A["token"])
ok(r.status_code == 200 and r.get_json()["kitchen"] == "Kuchnia A",
   "sprawdzenie połączenia z aplikacji")
ok(get("/api/whoami", "zly").status_code == 403, "zły token → 403")

section("limit kolejki i sprzątanie")
saved = ks.MAX_PENDING
ks.MAX_PENDING = 2
for n in range(2):
    post("/api/report", {**REPORT, "id": f"lim-{n}"}, A["token"])
r = post("/api/report", {**REPORT, "id": "lim-x"}, A["token"])
ok(r.status_code == 429, "przepełniona kolejka → 429 z wyjaśnieniem")
ks.MAX_PENDING = saved

c = ks.db_conn()
old = (datetime.now(timezone.utc) - timedelta(days=ks.MAX_AGE_DAYS + 1)).isoformat()
c.execute("INSERT INTO reports (id,tenant_id,name,shift_date,created_at,"
          "data_json) VALUES (?,?,?,?,?,?)",
          ("stare", A["id"], "Ola", "2020-01-01", old,
           json.dumps({"lines": []})))
c.commit()
c.close()
ok(all(x["id"] != "stare" for x in get("/api/reports", A["token"]).get_json()["reports"]),
   f"zgłoszenia starsze niż {ks.MAX_AGE_DAYS} dni znikają same")

# ────────────────────── /admin ──────────────────────
section("panel /admin")
fresh = ks.app.test_client()
ok(fresh.get("/admin").status_code == 302, "bez logowania → przekierowanie")
ok(fresh.post("/admin/login", data={"password": "zle"}).status_code == 401,
   "złe hasło → 401")
r = adm.get("/admin")
ok(r.status_code == 200 and "Kuchnia A" in r.get_data(as_text=True),
   "panel pokazuje kuchnie")
ok(A["token"] in r.get_data(as_text=True), "panel pokazuje token i link")
ok(adm.get(f"/admin/qr/{A['id']}.svg").status_code == 200, "QR generowany")

adm.post(f"/admin/tenants/{B['id']}/rename", data={"name": "Kuchnia B2"})
c = ks.db_conn()
ok(c.execute("SELECT name FROM tenants WHERE id=?",
             (B["id"],)).fetchone()["name"] == "Kuchnia B2", "zmiana nazwy")
adm.post(f"/admin/tenants/{B['id']}/toggle")
c.close()
ok(get("/api/whoami", B["token"]).status_code == 403,
   "wyłączona kuchnia nie wpuszcza")
adm.post(f"/admin/tenants/{B['id']}/toggle")
ok(get("/api/whoami", B["token"]).status_code == 200, "włączona wpuszcza znowu")

adm.post(f"/admin/tenants/{B['id']}/regenerate")
ok(get("/api/whoami", B["token"]).status_code == 403,
   "nowy token unieważnia stary QR")

adm.post(f"/admin/tenants/{B['id']}/delete", data={"confirm": "zla nazwa"})
c = ks.db_conn()
ok(c.execute("SELECT 1 FROM tenants WHERE id=?", (B["id"],)).fetchone(),
   "usunięcie bez potwierdzenia nazwy nie działa")
adm.post(f"/admin/tenants/{B['id']}/delete", data={"confirm": "Kuchnia B2"})
ok(not c.execute("SELECT 1 FROM tenants WHERE id=?", (B["id"],)).fetchone(),
   "usunięcie z potwierdzeniem działa")
c.close()

print(f"\n{'=' * 46}\nOK: {PASS}   BŁĘDY: {FAIL}\nKolejka testowa: "
      f"{os.environ['KUCHNIA_SERVER_DB']}")
raise SystemExit(1 if FAIL else 0)
