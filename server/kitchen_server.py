"""Straty Kuchnia — сервер очереди зашлушений.

Сервер — почтовый ящик, не система учёта. Он:
  * отдаёт повару форму `/form?t=ТОКЕН` и каталог позиций к ней;
  * принимает зашлушения и держит их в очереди;
  * отдаёт очередь менеджерскому приложению и удаляет принятое.

Чего он НЕ делает (сознательно, как в Beer Count):
  * не считает единицы и не знает про конверсии — `qty` хранится строкой,
    всю математику делает приложение на компьютере менеджера;
  * не хранит историю страт: правда живёт в локальной базе менеджера, а не
    на арендованной машине.

Запуск:
    KUCHNIA_ADMIN_PASSWORD=... python kitchen_server.py
"""

import io
import json
import os
import re
import secrets
import sqlite3
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from functools import wraps
from urllib.parse import quote
from zoneinfo import ZoneInfo

from flask import (Flask, Response, jsonify, redirect, request, send_file,
                   session, url_for)
from werkzeug.security import check_password_hash, generate_password_hash

for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        try:
            _s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

HERE = os.path.dirname(os.path.abspath(__file__))
# KUCHNIA_PORT — наш родной ключ; PORT понимают среды, которые назначают
# порт сами (превью, PaaS). На дроплете порт всё равно задаёт gunicorn.
PORT = int(os.environ.get("KUCHNIA_PORT") or os.environ.get("PORT") or 8788)
DB = os.environ.get("KUCHNIA_SERVER_DB") or os.path.join(HERE, "kitchen_queue.db")

VERSION = "1.0.0"
# 30 дней, а не 3 как у бара: менеджер кухни открывает приложение реже, и
# зашлушение, исчезнувшее до подтверждения, — это потерянная страта.
MAX_AGE_DAYS = 30
MAX_PENDING = 300               # предел очереди НА КУХНЮ — защита от флуда
MAX_REPORT_BYTES = 64 * 1024
MAX_LINES = 60
LOGIN_MAX_ATTEMPTS = 5
LOGIN_WINDOW_SEC = 300
NAME_RE = re.compile(r"^[\w \-.'ąćęłńóśźżĄĆĘŁŃÓŚŹŻ]{2,40}$", re.UNICODE)
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
WARSAW_TZ = ZoneInfo("Europe/Warsaw")

app = Flask(__name__)
# Каталог кухни (~100 позиций) приезжает от приложения одним куском.
# У зашлушения от повара — свой, куда более жёсткий лимит в submit_report().
app.config["MAX_CONTENT_LENGTH"] = 512 * 1024
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"] = os.environ.get("KUCHNIA_HTTPS") == "1"

_login_attempts = {}


def current_shift_date() -> str:
    """До 5:00 утра зашлушение относится к предыдущему дню — смена кухни
    заканчивается за полночь."""
    now = datetime.now(WARSAW_TZ)
    d = now.date()
    if now.hour < 5:
        d -= timedelta(days=1)
    return d.isoformat()


@app.after_request
def security_headers(resp):
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["Referrer-Policy"] = "no-referrer"
    resp.headers["X-Frame-Options"] = "DENY"
    return resp


def clean(v, maxlen):
    return str(v or "").strip()[:maxlen]


# ────────────────────────────── база очереди ──────────────────────────
def db_conn():
    c = sqlite3.connect(DB, timeout=10)
    c.row_factory = sqlite3.Row
    c.execute("""CREATE TABLE IF NOT EXISTS tenants (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        token TEXT UNIQUE NOT NULL,
        active INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL)""")
    c.execute("""CREATE TABLE IF NOT EXISTS reports (
        id TEXT PRIMARY KEY,
        tenant_id TEXT NOT NULL,
        name TEXT, shift_date TEXT,
        created_at TEXT, data_json TEXT)""")
    c.execute("""CREATE TABLE IF NOT EXISTS config (
        tenant_id TEXT NOT NULL,
        key TEXT NOT NULL,
        value TEXT,
        PRIMARY KEY (tenant_id, key))""")
    c.execute("""CREATE TABLE IF NOT EXISTS meta (
        key TEXT PRIMARY KEY, value TEXT)""")
    return c


def meta_get(c, key, default=None):
    row = c.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return row[0] if row else default


def meta_set(c, key, value):
    c.execute("INSERT OR REPLACE INTO meta VALUES (?,?)", (key, value))


_c0 = db_conn()
_secret = meta_get(_c0, "secret_key")
if not _secret:
    _secret = secrets.token_hex(32)
    meta_set(_c0, "secret_key", _secret)
    _c0.commit()
app.secret_key = _secret

_admin_pw = os.environ.get("KUCHNIA_ADMIN_PASSWORD")
if _admin_pw:
    meta_set(_c0, "admin_password_hash", generate_password_hash(_admin_pw))
    _c0.commit()
    print("Panel /admin: hasło ustawione z KUCHNIA_ADMIN_PASSWORD.")
elif not meta_get(_c0, "admin_password_hash"):
    print("UWAGA: brak hasła admina — /admin niedostępny, dopóki nie "
          "ustawisz KUCHNIA_ADMIN_PASSWORD.")
_c0.close()


# ────────────────────────────── доступ ──────────────────────────────
def request_token():
    tok = request.args.get("t") or request.headers.get("X-Token")
    if not tok and request.is_json:
        tok = (request.get_json(silent=True) or {}).get("token")
    return tok


def get_tenant_by_token(c, token):
    if not token:
        return None
    row = c.execute("SELECT id, name, active FROM tenants WHERE token=?",
                    (token,)).fetchone()
    if not row or not row["active"]:
        return None
    return {"id": row["id"], "name": row["name"]}


def api_tenant(fn):
    @wraps(fn)
    def wrapper(*a, **kw):
        c = db_conn()
        try:
            tenant = get_tenant_by_token(c, request_token())
            if not tenant:
                return jsonify(error="zły token"), 403
            return fn(c, tenant, *a, **kw)
        finally:
            c.close()
    return wrapper


def cleanup(c):
    limit = (datetime.now(timezone.utc)
             - timedelta(days=MAX_AGE_DAYS)).isoformat()
    c.execute("DELETE FROM reports WHERE created_at < ?", (limit,))


def _throttled(key) -> bool:
    now = time.time()
    tries = [t for t in _login_attempts.get(key, []) if now - t < LOGIN_WINDOW_SEC]
    _login_attempts[key] = tries
    return len(tries) >= LOGIN_MAX_ATTEMPTS


def _bad_try(key):
    _login_attempts.setdefault(key, []).append(time.time())


# ────────────────────────────── форма повара ──────────────────────────
@app.route("/")
def root():
    return Response(f"Straty Kuchnia — serwer zgłoszeń {VERSION} OK\n",
                    mimetype="text/plain")


@app.route("/form")
def form_page():
    c = db_conn()
    try:
        tenant = get_tenant_by_token(c, request_token())
    finally:
        c.close()
    if not tenant:
        return Response(
            "<meta charset='utf-8'><body style='font:16px system-ui;"
            "background:#12151c;color:#eef2f8;text-align:center;padding:60px'>"
            "<h2>Brak dostępu</h2><p>Link jest nieprawidłowy lub wygasł.<br>"
            "Poproś menedżera o nowy QR-kod.</p></body>",
            mimetype="text/html", status=403)
    path = os.path.join(HERE, "form.html")
    resp = send_file(path, mimetype="text/html")
    resp.headers["Cache-Control"] = "no-cache"
    return resp


@app.route("/icon.png")
@app.route("/icon-512.png")
def icon():
    path = os.path.join(HERE, request.path.lstrip("/"))
    if not os.path.exists(path):
        return "", 404
    return send_file(path, mimetype="image/png")


@app.route("/manifest.webmanifest")
def manifest():
    """Манифест для «Dodaj do ekranu głównego» / «Zainstaluj aplikację».

    Свой на каждую кухню: start_url несёт токен — без него установленное
    приложение открылось бы на «Brak dostępu». display=fullscreen убирает
    адресную строку и статус-бар: на кухонном планшете форма — единственное
    окно.
    """
    tok = request_token()
    c = db_conn()
    try:
        tenant = get_tenant_by_token(c, tok)
    finally:
        c.close()
    if not tenant:
        return jsonify(error="zły token"), 403
    start = "/form?t=" + quote(tok, safe="")
    data = {
        "id": "/form?k=" + tenant["id"],
        "name": f"Straty — {tenant['name']}",
        "short_name": "Straty",
        "description": "Zgłaszanie strat kuchni",
        "lang": "pl",
        "start_url": start,
        "scope": "/",
        "display": "fullscreen",
        "display_override": ["fullscreen", "standalone"],
        "orientation": "any",
        "background_color": "#12151c",
        "theme_color": "#12151c",
        "icons": [
            {"src": "/icon.png", "sizes": "192x192", "type": "image/png",
             "purpose": "any"},
            {"src": "/icon-512.png", "sizes": "512x512", "type": "image/png",
             "purpose": "any"},
            {"src": "/icon-512.png", "sizes": "512x512", "type": "image/png",
             "purpose": "maskable"},
        ],
    }
    resp = Response(json.dumps(data, ensure_ascii=False),
                    mimetype="application/manifest+json")
    resp.headers["Cache-Control"] = "no-cache"
    return resp


# Service worker кэширует только оболочку формы. Каталог и отправка —
# всегда сеть: устаревший каталог хуже, чем честное «brak połączenia».
# Записи повара при этом не теряются — их держит очередь в localStorage.
SW_JS = """
const CACHE = 'kuchnia-form-v1';
// Оболочку НЕ кладём в кэш при установке: '/form' без токена отдаёт 403,
// cache.add на этом падает, и в итоге не кэшируется вообще ничего — форма
// не открылась бы в офлайне. Кэш наполняет обработчик fetch ниже, из
// ответа на настоящий запрос с токеном.
self.addEventListener('install', e => e.waitUntil(self.skipWaiting()));
self.addEventListener('activate', e => {
  e.waitUntil(caches.keys().then(ks => Promise.all(
    ks.filter(k => k !== CACHE).map(k => caches.delete(k))))
    .then(() => self.clients.claim()));
});
self.addEventListener('fetch', e => {
  const url = new URL(e.request.url);
  if (e.request.method !== 'GET') return;
  if (url.origin !== self.location.origin) return;
  if (!['/form', '/icon.png', '/icon-512.png'].includes(url.pathname)) return;
  e.respondWith(
    fetch(e.request).then(res => {
      if (res && res.ok) {
        const copy = res.clone();
        caches.open(CACHE).then(c => c.put(e.request, copy));
      }
      return res;
    }).catch(() => caches.match(e.request, {ignoreSearch: true}))
  );
});
"""


@app.route("/sw.js")
def service_worker():
    """Service worker обязан лежать в корне — иначе его область видимости
    не покроет `/form`."""
    resp = Response(SW_JS, mimetype="application/javascript")
    resp.headers["Cache-Control"] = "no-cache"
    resp.headers["Service-Worker-Allowed"] = "/"
    return resp


@app.route("/api/catalog")
@api_tenant
def get_catalog(c, tenant):
    """Каталог для формы. Пустой каталог — не ошибка: приложение менеджера
    ещё не нажало «Wyślij katalog»."""
    row = c.execute("SELECT value FROM config WHERE tenant_id=? AND key='catalog'",
                    (tenant["id"],)).fetchone()
    data = json.loads(row["value"]) if row else {"items": [], "reasons": []}
    data["kitchen"] = data.get("kitchen") or tenant["name"]
    data["today"] = current_shift_date()
    data["version"] = VERSION
    return jsonify(data)


@app.route("/api/report", methods=["POST"])
@api_tenant
def submit_report(c, tenant):
    """Приём зашлушения от повара.

    Единственная ручка, открытая наружу по QR, поэтому проверки здесь
    жёсткие: размер тела, число строк, длина полей, глубина очереди.
    """
    raw = request.get_data() or b""
    if len(raw) > MAX_REPORT_BYTES:
        return jsonify(error="zgłoszenie za duże"), 413
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return jsonify(error="zły JSON"), 400
    if not isinstance(payload, dict):
        return jsonify(error="zły format"), 400

    lines = payload.get("lines")
    if not isinstance(lines, list) or not lines:
        return jsonify(error="puste zgłoszenie"), 400
    if len(lines) > MAX_LINES:
        return jsonify(error=f"za dużo pozycji (max {MAX_LINES})"), 400

    name = clean(payload.get("name"), 40)
    if not NAME_RE.match(name or ""):
        return jsonify(error="podaj imię (2–40 znaków)"), 400

    shift_date = clean(payload.get("shift_date"), 10) or current_shift_date()
    if not DATE_RE.match(shift_date):
        return jsonify(error="zła data"), 400

    clean_lines = []
    for line in lines:
        if not isinstance(line, dict):
            return jsonify(error="zła linia"), 400
        # qty едет строкой: сервер не считает и не округляет, чтобы «0,5»
        # с польской клавиатуры доехало до приложения ровно таким, как ввели
        clean_lines.append({
            "item_id": clean(line.get("item_id"), 32),
            "item_name": clean(line.get("item_name"), 80),
            "qty": clean(line.get("qty"), 16),
            "unit": clean(line.get("unit"), 8),
            "reason": clean(line.get("reason"), 40),
            "note": clean(line.get("note"), 200),
        })
        if not clean_lines[-1]["item_name"] or not clean_lines[-1]["qty"]:
            return jsonify(error="linia bez pozycji lub ilości"), 400

    cleanup(c)
    pending = c.execute("SELECT COUNT(*) FROM reports WHERE tenant_id=?",
                        (tenant["id"],)).fetchone()[0]
    if pending >= MAX_PENDING:
        return jsonify(error="kolejka pełna — menedżer musi zatwierdzić "
                             "wcześniejsze zgłoszenia"), 429

    # id от телефона: повторная отправка из офлайн-очереди не создаёт дубль
    rid = clean(payload.get("id"), 32) or uuid.uuid4().hex[:16]
    c.execute("INSERT OR IGNORE INTO reports"
              " (id, tenant_id, name, shift_date, created_at, data_json)"
              " VALUES (?,?,?,?,?,?)",
              (rid, tenant["id"], name, shift_date,
               datetime.now(timezone.utc).isoformat(timespec="seconds"),
               json.dumps({"name": name, "shift_date": shift_date,
                           "lines": clean_lines}, ensure_ascii=False)))
    c.commit()
    return jsonify(ok=True, id=rid, lines=len(clean_lines))


# ────────────────────── API менеджерского приложения ──────────────────
@app.route("/api/reports")
@api_tenant
def list_reports(c, tenant):
    cleanup(c)
    c.commit()
    rows = c.execute(
        "SELECT id, name, shift_date, created_at, data_json FROM reports"
        " WHERE tenant_id=? ORDER BY created_at", (tenant["id"],)).fetchall()
    out = []
    for r in rows:
        try:
            data = json.loads(r["data_json"])
        except ValueError:
            continue
        out.append({"id": r["id"], "name": r["name"],
                    "shift_date": r["shift_date"], "created_at": r["created_at"],
                    "data": data})
    return jsonify(reports=out, count=len(out), kitchen=tenant["name"])


@app.route("/api/reports/<rid>", methods=["DELETE"])
@api_tenant
def delete_report(c, tenant, rid):
    """Приложение удаляет зашлушение, когда приняло его или отклонило."""
    cur = c.execute("DELETE FROM reports WHERE id=? AND tenant_id=?",
                    (rid, tenant["id"]))
    c.commit()
    if not cur.rowcount:
        return jsonify(error="nie ma takiego zgłoszenia"), 404
    return jsonify(ok=True)


@app.route("/api/catalog", methods=["POST"])
@api_tenant
def push_catalog(c, tenant):
    """Приложение выгружает каталог на сервер — форма показывает его поварам."""
    payload = request.get_json(silent=True) or {}
    items = payload.get("items")
    if not isinstance(items, list):
        return jsonify(error="brak listy pozycji"), 400
    data = {
        "items": [{"id": clean(i.get("id"), 32),
                   "name": clean(i.get("name"), 80),
                   "category": clean(i.get("category"), 40),
                   "unit": clean(i.get("unit"), 8),
                   "units": [clean(u, 8) for u in (i.get("units") or [])][:4]}
                  for i in items[:2000]],
        "reasons": [clean(r, 40) for r in (payload.get("reasons") or [])][:40],
        "kitchen": clean(payload.get("kitchen"), 60) or tenant["name"],
    }
    c.execute("INSERT OR REPLACE INTO config VALUES (?,'catalog',?)",
              (tenant["id"], json.dumps(data, ensure_ascii=False)))
    c.commit()
    return jsonify(ok=True, items=len(data["items"]))


@app.route("/api/whoami")
@api_tenant
def whoami(c, tenant):
    """Кнопка «Sprawdź połączenie» в настройках приложения."""
    pending = c.execute("SELECT COUNT(*) FROM reports WHERE tenant_id=?",
                        (tenant["id"],)).fetchone()[0]
    return jsonify(ok=True, kitchen=tenant["name"], pending=pending,
                   version=VERSION)


# ────────────────────────────── /admin ──────────────────────────────
def admin_required(fn):
    @wraps(fn)
    def wrapper(*a, **kw):
        if not session.get("admin"):
            return redirect(url_for("admin_login"))
        return fn(*a, **kw)
    return wrapper


ADMIN_CSS = """
:root{--bg:#12151c;--bg2:#191d27;--line:#2c3444;--fg:#eef2f8;--muted:#8e9ab0;
--accent:#D9952E;--danger:#f87171}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);padding:20px 16px 60px;
font:16px/1.45 -apple-system,"Segoe UI",Roboto,system-ui,sans-serif}
.wrap{max-width:900px;margin:0 auto}h1{font-size:22px;margin:0 0 4px}
.muted{color:var(--muted)}.small{font-size:13px}a{color:var(--accent)}
.card{background:var(--bg2);border:1px solid var(--line);border-radius:14px;
padding:16px;margin:14px 0}
.head{display:flex;align-items:center;justify-content:space-between;gap:12px;
flex-wrap:wrap}.name{font-size:19px;font-weight:700}.off{opacity:.55}
.badge{font-size:12px;padding:3px 9px;border-radius:999px;border:1px solid
var(--line);color:var(--muted)}
input,button,select{font:inherit}
input[type=text],input[type=password]{height:42px;padding:0 12px;color:var(--fg);
background:#222836;border:1px solid var(--line);border-radius:10px}
.btn{height:42px;padding:0 16px;border-radius:10px;border:1px solid var(--line);
background:#222836;color:var(--fg);cursor:pointer}
.btn.primary{background:var(--accent);border-color:var(--accent);color:#1b1200;
font-weight:700}
.btn.danger{border-color:rgba(248,113,113,.5);color:var(--danger)}
form.inline{display:inline-flex;gap:8px;align-items:center;margin:6px 8px 0 0}
.link{display:flex;gap:8px;align-items:center;margin:12px 0}
.link code{background:#222836;border:1px solid var(--line);border-radius:10px;
padding:10px 12px;font-size:13px;overflow-x:auto;white-space:nowrap;flex:1}
.qr{background:#fff;padding:10px;border-radius:12px;width:190px}
.qr img{display:block;width:100%}
details summary{cursor:pointer;color:var(--muted);margin-top:10px}
.grid{display:flex;gap:18px;flex-wrap:wrap;align-items:flex-start}
.grid>div:first-child{flex:1 1 340px}
"""


@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    c = db_conn()
    try:
        pw_hash = meta_get(c, "admin_password_hash")
    finally:
        c.close()
    error = ""
    status = 200
    if request.method == "POST":
        ip = request.remote_addr or "?"
        if _throttled(f"admin:{ip}"):
            error, status = "Za dużo prób. Spróbuj za 5 minut.", 429
        elif pw_hash and check_password_hash(pw_hash,
                                             request.form.get("password", "")):
            session.permanent = True
            session["admin"] = True
            return redirect(url_for("admin_home"))
        else:
            _bad_try(f"admin:{ip}")
            error, status = "Złe hasło.", 401
    body = ("<form method='post' class='card' style='max-width:340px;margin:60px auto'>"
            "<h1>Straty Kuchnia</h1><p class='muted small'>panel serwera zgłoszeń</p>"
            + ("<input type='password' name='password' placeholder='Hasło' "
               "style='width:100%;margin:14px 0' autofocus>"
               "<button class='btn primary' style='width:100%'>Wejdź</button>"
               if pw_hash else
               "<p class='muted'>Hasło nie ustawione. Uruchom serwer ze "
               "zmienną <code>KUCHNIA_ADMIN_PASSWORD</code>.</p>")
            + (f"<p class='small' style='color:#f87171'>{error}</p>" if error else "")
            + "</form>")
    return Response(_page("Logowanie", body), mimetype="text/html", status=status)


def _page(title, body):
    return (f"<!doctype html><html lang='pl'><head><meta charset='utf-8'>"
            f"<meta name='viewport' content='width=device-width,initial-scale=1'>"
            f"<title>{title}</title><style>{ADMIN_CSS}</style></head>"
            f"<body><div class='wrap'>{body}</div></body></html>")


@app.route("/admin/logout")
def admin_logout():
    session.pop("admin", None)
    return redirect(url_for("admin_login"))


@app.route("/admin")
@admin_required
def admin_home():
    c = db_conn()
    try:
        cleanup(c)
        c.commit()
        rows = c.execute("SELECT * FROM tenants ORDER BY name").fetchall()
        cards = []
        for t in rows:
            pending = c.execute("SELECT COUNT(*) FROM reports WHERE tenant_id=?",
                                (t["id"],)).fetchone()[0]
            cat = c.execute("SELECT value FROM config WHERE tenant_id=?"
                            " AND key='catalog'", (t["id"],)).fetchone()
            n_items = len(json.loads(cat["value"])["items"]) if cat else 0
            link = (request.url_root.rstrip("/") + url_for("form_page")
                    + f"?t={t['token']}")
            cards.append(f"""
<div class="card {'' if t['active'] else 'off'}">
  <div class="head"><span class="name">{t['name']}</span>
    <span><span class="badge">{n_items} pozycji w katalogu</span>
      <span class="badge">{pending} w kolejce</span>
      {'' if t['active'] else '<span class="badge">wyłączona</span>'}</span></div>
  <div class="grid"><div>
    <div class="link"><code id="l{t['id']}">{link}</code>
      <button class="btn" onclick="cp('l{t['id']}')">Kopiuj</button></div>
    <p class="muted small">Link do formularza dla kucharzy. Ten sam token
      wpisuje się w aplikacji menedżera: <b>Ustawienia → Serwer</b>.</p>
    <div class="link"><code>{t['token']}</code></div>
    <form method="post" action="/admin/tenants/{t['id']}/rename" class="inline">
      <input type="text" name="name" value="{t['name']}" required>
      <button class="btn">Zmień nazwę</button></form>
    <form method="post" action="/admin/tenants/{t['id']}/toggle" class="inline">
      <button class="btn">{'Wyłącz' if t['active'] else 'Włącz'}</button></form>
    <details><summary>Operacje niebezpieczne</summary>
      <form method="post" action="/admin/tenants/{t['id']}/regenerate"
            class="inline" onsubmit="return confirm('Stary QR przestanie działać. Kontynuować?')">
        <button class="btn danger">Nowy token</button></form>
      <form method="post" action="/admin/tenants/{t['id']}/delete" class="inline">
        <input type="text" name="confirm" placeholder="wpisz nazwę">
        <button class="btn danger">Usuń kuchnię</button></form>
    </details>
  </div>
  <div class="qr"><img src="/admin/qr/{t['id']}.svg" alt="QR"></div></div>
</div>""")
    finally:
        c.close()
    body = f"""
<div class="head"><div><h1>Straty Kuchnia</h1>
  <div class="muted small">serwer zgłoszeń · v{VERSION}</div></div>
  <a class="btn" href="/admin/logout">Wyloguj</a></div>
<div class="card"><b>Nowa kuchnia</b>
  <form method="post" action="/admin/tenants" class="inline">
    <input type="text" name="name" placeholder="np. HRC Warszawa — kuchnia" required>
    <button class="btn primary">Dodaj</button></form></div>
{''.join(cards) or '<p class="muted">Nie ma jeszcze żadnej kuchni.</p>'}
<script>function cp(id){{const t=document.getElementById(id).textContent.trim();
navigator.clipboard.writeText(t).then(()=>alert('Skopiowano'),
()=>prompt('Skopiuj ręcznie:',t));}}</script>"""
    return Response(_page("Panel", body), mimetype="text/html")


@app.route("/admin/tenants", methods=["POST"])
@admin_required
def admin_add_tenant():
    name = clean(request.form.get("name"), 60)
    if name:
        c = db_conn()
        try:
            c.execute("INSERT INTO tenants (id,name,token,active,created_at)"
                      " VALUES (?,?,?,1,?)",
                      (uuid.uuid4().hex[:12], name, uuid.uuid4().hex[:24],
                       datetime.now(timezone.utc).isoformat(timespec="seconds")))
            c.commit()
        finally:
            c.close()
    return redirect(url_for("admin_home"))


@app.route("/admin/tenants/<tid>/<action>", methods=["POST"])
@admin_required
def admin_tenant_action(tid, action):
    c = db_conn()
    try:
        row = c.execute("SELECT * FROM tenants WHERE id=?", (tid,)).fetchone()
        if row:
            if action == "rename":
                c.execute("UPDATE tenants SET name=? WHERE id=?",
                          (clean(request.form.get("name"), 60) or row["name"], tid))
            elif action == "toggle":
                c.execute("UPDATE tenants SET active=? WHERE id=?",
                          (0 if row["active"] else 1, tid))
            elif action == "regenerate":
                c.execute("UPDATE tenants SET token=? WHERE id=?",
                          (uuid.uuid4().hex[:24], tid))
            elif action == "delete":
                # подтверждение вводом названия: кнопка «Usuń» рядом с живой
                # кухней слишком легко нажимается с телефона
                if clean(request.form.get("confirm"), 60) == row["name"]:
                    c.execute("DELETE FROM reports WHERE tenant_id=?", (tid,))
                    c.execute("DELETE FROM config WHERE tenant_id=?", (tid,))
                    c.execute("DELETE FROM tenants WHERE id=?", (tid,))
            c.commit()
    finally:
        c.close()
    return redirect(url_for("admin_home"))


@app.route("/admin/qr/<tid>.svg")
@admin_required
def admin_qr(tid):
    c = db_conn()
    try:
        row = c.execute("SELECT token FROM tenants WHERE id=?", (tid,)).fetchone()
    finally:
        c.close()
    if not row:
        return "", 404
    link = (request.url_root.rstrip("/") + url_for("form_page")
            + f"?t={row['token']}")
    try:
        import qrcode
        import qrcode.image.svg as qsvg
        img = qrcode.make(link, image_factory=qsvg.SvgPathImage, box_size=10,
                          border=2)
        buf = io.BytesIO()
        img.save(buf)
        return Response(buf.getvalue(), mimetype="image/svg+xml")
    except ImportError:
        return Response(
            "<svg xmlns='http://www.w3.org/2000/svg' width='200' height='60'>"
            "<text x='4' y='34' font-size='13'>brak modułu qrcode</text></svg>",
            mimetype="image/svg+xml")


if __name__ == "__main__":
    print(f"Straty Kuchnia — serwer zgłoszeń {VERSION} → http://127.0.0.1:{PORT}")
    print(f"Kolejka: {DB}")
    app.run(host="0.0.0.0", port=PORT,
            debug=os.environ.get("KUCHNIA_DEBUG") == "1")
