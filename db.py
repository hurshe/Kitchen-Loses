"""Straty Kuchnia — локальная база менеджерского приложения (SQLite).

База лежит на компьютере менеджера и является **единственным источником
правды**. Сервер на дроплете — лишь почтовый ящик: повара кладут туда
зашлушения, приложение их забирает и подтверждает. Ровно как в Beer Count.

Модель проще барной: позиция = название + единица, и всё. Ни рецептур,
ни складников, ни денег.

Единицы живут в трёх измерениях, база каждого — самая мелкая единица:
    count  → szt   (штуки)
    mass   → g     (kg = 1000 g)
    volume → ml    (l  = 1000 ml)

В `losses.qty` лежит то, что ввёл повар, в `losses.qty_base` — то же самое
в базовой единице. Суммируем всегда по `qty_base`, показываем в единице
позиции (`items.unit`), поэтому «3400 g» превращается в «3.4 kg».
"""

import json
import os
import re
import sqlite3
import uuid
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

WARSAW_TZ = ZoneInfo("Europe/Warsaw")
APP_DIR = os.path.dirname(os.path.abspath(__file__))

# KUCHNIA_DB подменяет базу — этим пользуются автопроверки. Без такого
# переключателя тест молча пишет в боевую базу (грабли Beer Count).
DB_PATH = os.environ.get("KUCHNIA_DB") or os.path.join(APP_DIR, "kuchnia.db")

# ────────────────────────────── единицы ──────────────────────────────
UNITS = {
    "szt": ("count", 1.0),
    "g": ("mass", 1.0),
    "kg": ("mass", 1000.0),
    "ml": ("volume", 1.0),
    "l": ("volume", 1000.0),
}
BASE_UNIT = {"count": "szt", "mass": "g", "volume": "ml"}
# Повар списывает 300 g даже у позиции «в kg» — пересчёт наш, не его.
UNITS_BY_DIM = {"count": ["szt"], "mass": ["kg", "g"], "volume": ["l", "ml"]}
DIM_ORDER = {"mass": 0, "volume": 1, "count": 2}

MONTH_RE = re.compile(r"^\d{4}-\d{2}$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def unit_dim(unit: str) -> str:
    try:
        return UNITS[unit][0]
    except KeyError:
        raise ValueError(f"nieznana jednostka: {unit!r}")


def units_for(unit: str) -> list:
    """Единицы, в которых можно ввести страту позиции с такой единицей."""
    return list(UNITS_BY_DIM[unit_dim(unit)])


def to_base(qty: float, unit: str) -> float:
    return float(qty) * UNITS[unit][1]


def compatible(unit_a: str, unit_b: str) -> bool:
    return unit_dim(unit_a) == unit_dim(unit_b)


def parse_qty(raw) -> float:
    """Число из того, что ввёл человек: «1,5» и «1.5» — одно и то же."""
    try:
        return float(str(raw).strip().replace(",", "."))
    except (TypeError, ValueError):
        raise ValueError("ilość musi być liczbą")


def fmt_num(v: float) -> str:
    """Число без хвоста нулей: 3.4 → «3.4», 2.0 → «2», 0.125 → «0.125»."""
    v = round(float(v) + 0.0, 3)
    if v == int(v):
        return str(int(v))
    return f"{v:.3f}".rstrip("0").rstrip(".")


def qty_in(qty_base: float, unit: str) -> float:
    """Базовое количество в заданной единице — числом.

    Числом, а не текстом: Excel должен уметь просуммировать колонку.
    """
    return round(float(qty_base) / UNITS[unit][1], 4)


def format_qty(qty_base: float, unit: str) -> str:
    return f"{fmt_num(qty_in(qty_base, unit))} {unit}"


# ────────────────────────────── даты ──────────────────────────────
def current_shift_date() -> str:
    """Дата смены: до 5:00 утра списание относится ко вчерашнему дню.

    Кухня закрывается за полночь, и повар, списывающий в 01:30, имеет в виду
    закончившуюся смену. Та же логика, что в Beer Count.
    """
    now = datetime.now(WARSAW_TZ)
    d = now.date()
    if now.hour < 5:
        d -= timedelta(days=1)
    return d.isoformat()


def current_month() -> str:
    return current_shift_date()[:7]


def month_bounds(month: str):
    """'2026-08' → ('2026-08-01', '2026-08-31')."""
    if not MONTH_RE.match(month or ""):
        raise ValueError(f"zły miesiąc: {month!r}")
    y, m = int(month[:4]), int(month[5:7])
    first = date(y, m, 1)
    last = date(y + (m == 12), (m % 12) + 1, 1) - timedelta(days=1)
    return first.isoformat(), last.isoformat()


MONTHS_PL = ["styczeń", "luty", "marzec", "kwiecień", "maj", "czerwiec",
             "lipiec", "sierpień", "wrzesień", "październik", "listopad",
             "grudzień"]


def month_label_pl(month: str) -> str:
    return f"{MONTHS_PL[int(month[5:7]) - 1]} {month[:4]}"


def month_short_pl(month: str) -> str:
    return f"{MONTHS_PL[int(month[5:7]) - 1][:3]} {month[2:4]}"


def shift_month(month: str, delta: int) -> str:
    y, m = int(month[:4]), int(month[5:7])
    total = y * 12 + (m - 1) + delta
    return f"{total // 12:04d}-{total % 12 + 1:02d}"


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_id() -> str:
    return uuid.uuid4().hex[:12]


# ────────────────────────────── схема ──────────────────────────────
def connect(path=None):
    c = sqlite3.connect(path or DB_PATH, timeout=10)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    return c


def _protect_checklist_entries(c):
    """Заполненный чек-лист — документ: его нельзя ни править, ни удалять.

    Иначе менеджер мог бы задним числом «исправить» температуру или убрать
    неудобный лист и заполнить новый. Ошибочную копию можно только
    АННУЛИРОВАТЬ с причиной (voided_at / void_reason) — она остаётся в
    архиве и в печати. Аннулирование окончательное.

    Защита стоит в самой базе (триггеры), а не только в интерфейсе: никакой
    код программы, включая будущий, не обойдёт её случайно.
    """
    cols = {r[1] for r in c.execute("PRAGMA table_info(checklist_entries)")}
    if "voided_at" not in cols:
        c.execute("ALTER TABLE checklist_entries ADD COLUMN voided_at TEXT")
    if "void_reason" not in cols:
        c.execute("ALTER TABLE checklist_entries ADD COLUMN void_reason TEXT")
    c.executescript("""
    CREATE TRIGGER IF NOT EXISTS cl_entries_no_delete
    BEFORE DELETE ON checklist_entries
    BEGIN
        SELECT RAISE(ABORT, 'checklist: kopii nie można usuwać, tylko anulować');
    END;

    CREATE TRIGGER IF NOT EXISTS cl_entries_no_edit
    BEFORE UPDATE ON checklist_entries
    WHEN NEW.id IS NOT OLD.id
      OR NEW.template_id IS NOT OLD.template_id
      OR NEW.template_name IS NOT OLD.template_name
      OR NEW.entry_date IS NOT OLD.entry_date
      OR NEW.person IS NOT OLD.person
      OR NEW.submitted_at IS NOT OLD.submitted_at
      OR NEW.received_at IS NOT OLD.received_at
      OR NEW.source IS NOT OLD.source
      OR NEW.issues IS NOT OLD.issues
      OR NEW.filled IS NOT OLD.filled
      OR NEW.total IS NOT OLD.total
      OR NEW.data_json IS NOT OLD.data_json
    BEGIN
        SELECT RAISE(ABORT, 'checklist: wypełnionej kopii nie można zmieniać');
    END;

    CREATE TRIGGER IF NOT EXISTS cl_entries_void_final
    BEFORE UPDATE ON checklist_entries
    WHEN OLD.voided_at IS NOT NULL
     AND (NEW.voided_at IS NOT OLD.voided_at
          OR NEW.void_reason IS NOT OLD.void_reason)
    BEGIN
        SELECT RAISE(ABORT, 'checklist: anulowania nie można cofnąć');
    END;
    """)


def init_db(path=None):
    """Создаёт таблицы. Безопасно вызывать на каждом старте."""
    c = connect(path)
    # WAL: приложение и фоновая синхронизация не мешают друг другу
    c.execute("PRAGMA journal_mode=WAL")
    c.executescript("""
    CREATE TABLE IF NOT EXISTS items (
        id         TEXT PRIMARY KEY,
        name       TEXT NOT NULL,
        category   TEXT,
        unit       TEXT NOT NULL,          -- szt | kg | g | l | ml
        active     INTEGER NOT NULL DEFAULT 1,
        sort_order INTEGER NOT NULL DEFAULT 0,
        created_at TEXT);
    CREATE UNIQUE INDEX IF NOT EXISTS idx_items_name
        ON items(name COLLATE NOCASE);

    CREATE TABLE IF NOT EXISTS reasons (
        id         TEXT PRIMARY KEY,
        name       TEXT NOT NULL,
        active     INTEGER NOT NULL DEFAULT 1,
        sort_order INTEGER NOT NULL DEFAULT 0);
    CREATE UNIQUE INDEX IF NOT EXISTS idx_reasons_name
        ON reasons(name COLLATE NOCASE);

    CREATE TABLE IF NOT EXISTS losses (
        id           TEXT PRIMARY KEY,
        loss_date    TEXT NOT NULL,        -- YYYY-MM-DD, дата смены
        item_id      TEXT NOT NULL,
        qty          REAL NOT NULL,        -- как ввели
        unit         TEXT NOT NULL,        -- в чём ввели
        qty_base     REAL NOT NULL,        -- то же в базовой единице
        reason       TEXT,
        person       TEXT,
        note         TEXT,
        source       TEXT,                 -- 'qr' | 'manual'
        report_id    TEXT,                 -- зашлушение с сервера (НЕ unique)
        created_at   TEXT NOT NULL,
        confirmed_at TEXT,
        FOREIGN KEY (item_id) REFERENCES items(id));
    CREATE INDEX IF NOT EXISTS idx_losses_date ON losses(loss_date);
    CREATE INDEX IF NOT EXISTS idx_losses_item ON losses(item_id);
    CREATE INDEX IF NOT EXISTS idx_losses_report ON losses(report_id);

    -- какие зашлушения уже приняты. Отдельная таблица, а не UNIQUE в
    -- losses: одно зашлушение — это несколько строк с одним report_id.
    CREATE TABLE IF NOT EXISTS losses_confirmed (
        report_id    TEXT PRIMARY KEY,
        confirmed_at TEXT,
        lines        INTEGER,
        person       TEXT,
        loss_date    TEXT);

    CREATE TABLE IF NOT EXISTS config (
        key TEXT PRIMARY KEY, value TEXT);

    -- Checklisty (см. checklists.py). Шаблон целиком лежит JSON-ом:
    -- структура у него древовидная и читается всегда вся разом.
    CREATE TABLE IF NOT EXISTS checklist_templates (
        id         TEXT PRIMARY KEY,
        name       TEXT NOT NULL,
        period     TEXT,
        data_json  TEXT NOT NULL,
        active     INTEGER NOT NULL DEFAULT 1,
        sort_order INTEGER NOT NULL DEFAULT 0,
        version    INTEGER NOT NULL DEFAULT 1,
        created_at TEXT,
        updated_at TEXT);

    -- Заполненные копии. data_json несёт снимок шаблона + значения, поэтому
    -- правка или удаление шаблона старые копии не меняет.
    CREATE TABLE IF NOT EXISTS checklist_entries (
        id            TEXT PRIMARY KEY,
        template_id   TEXT,
        template_name TEXT,
        entry_date    TEXT NOT NULL,       -- YYYY-MM-DD, дата чек-листа
        person        TEXT,
        submitted_at  TEXT,                -- когда отправили с телефона
        received_at   TEXT,                -- когда забрало приложение
        source        TEXT,
        issues        INTEGER NOT NULL DEFAULT 0,
        filled        INTEGER NOT NULL DEFAULT 0,
        total         INTEGER NOT NULL DEFAULT 0,
        seen          INTEGER NOT NULL DEFAULT 0,
        data_json     TEXT NOT NULL);
    CREATE INDEX IF NOT EXISTS idx_cl_entries_date
        ON checklist_entries(entry_date);
    """)
    _protect_checklist_entries(c)
    c.commit()
    return c


# ────────────────────────────── настройки ──────────────────────────────
DEFAULT_CONFIG = {
    "kitchen_name": "Kuchnia",
    "server_url": "",
    "server_token": "",
    "auto_sync": "1",
}


def get_config(c, key=None, default=None):
    if key is not None:
        row = c.execute("SELECT value FROM config WHERE key=?", (key,)).fetchone()
        if row:
            return row[0]
        return default if default is not None else DEFAULT_CONFIG.get(key, "")
    cfg = dict(DEFAULT_CONFIG)
    for row in c.execute("SELECT key, value FROM config"):
        cfg[row["key"]] = row["value"]
    return cfg


def set_config(c, key, value):
    c.execute("INSERT OR REPLACE INTO config VALUES (?,?)", (key, str(value)))
    c.commit()


def set_config_many(c, values: dict):
    for k, v in values.items():
        c.execute("INSERT OR REPLACE INTO config VALUES (?,?)", (k, str(v)))
    c.commit()


# ────────────────────────────── каталог позиций ──────────────────────
def _name_taken(c, table, name, own_id=None) -> bool:
    """Занято ли имя (без учёта регистра, с польскими буквами).

    Сравнение в Python, а не через COLLATE NOCASE: SQLite приводит регистр
    только у ASCII, и «Łosoś» с «łosoś» проехали бы как разные позиции.
    """
    low = name.strip().lower()
    for row in c.execute(f"SELECT id, name FROM {table}"):
        if row["id"] != own_id and row["name"].strip().lower() == low:
            return True
    return False


def list_items(c, active_only=True, category=None) -> list:
    sql = "SELECT * FROM items WHERE 1=1"
    args = []
    if active_only:
        sql += " AND active=1"
    if category:
        sql += " AND category=?"
        args.append(category)
    sql += " ORDER BY sort_order, name COLLATE NOCASE"
    return [dict(r) for r in c.execute(sql, args)]


def get_item(c, item_id):
    row = c.execute("SELECT * FROM items WHERE id=?", (item_id,)).fetchone()
    return dict(row) if row else None


def items_map(c) -> dict:
    return {i["id"]: i for i in list_items(c, active_only=False)}


def find_item_by_name(c, name):
    """Поиск по имени — нужен при подтверждении зашлушений со старыми id."""
    low = (name or "").strip().lower()
    for i in list_items(c, active_only=False):
        if i["name"].strip().lower() == low:
            return i
    return None


def item_categories(c, active_only=True) -> list:
    sql = ("SELECT DISTINCT category FROM items WHERE category IS NOT NULL"
           " AND category<>''")
    if active_only:
        sql += " AND active=1"
    return [r[0] for r in c.execute(sql + " ORDER BY category COLLATE NOCASE")]


def save_item(c, item: dict) -> str:
    """Создаёт или правит позицию. Возвращает id.

    Единицу позиции с историей можно менять только внутри измерения:
    «kg → g» пересчитается само, «kg → szt» превратило бы 3.4 kg в
    3.4 штуки. Такой переход отклоняем.
    """
    name = (item.get("name") or "").strip()[:80]
    if not name:
        raise ValueError("pusta nazwa pozycji")
    unit = (item.get("unit") or "").strip()
    if unit not in UNITS:
        raise ValueError(f"nieznana jednostka: {unit!r}")
    category = (item.get("category") or "").strip()[:40]
    iid = item.get("id")
    active = 1 if item.get("active", 1) else 0
    sort_order = int(item.get("sort_order") or 0)

    if _name_taken(c, "items", name, iid):
        raise ValueError(f"pozycja „{name}” już istnieje")

    if iid:
        old = get_item(c, iid)
        if not old:
            raise ValueError("pozycja nie istnieje")
        if old["unit"] != unit and not compatible(old["unit"], unit):
            used = item_usage(c, iid)
            if used:
                raise ValueError(
                    "nie można zmienić rodzaju jednostki — pozycja ma już "
                    f"{used} zapisanych strat")
        sql, args = ("UPDATE items SET name=?, category=?, unit=?, active=?,"
                     " sort_order=? WHERE id=?",
                     (name, category, unit, active, sort_order, iid))
    else:
        iid = new_id()
        sql, args = ("INSERT INTO items (id,name,category,unit,active,"
                     "sort_order,created_at) VALUES (?,?,?,?,?,?,?)",
                     (iid, name, category, unit, active, sort_order, now_utc()))
    try:
        c.execute(sql, args)
    except sqlite3.IntegrityError:
        raise ValueError(f"pozycja „{name}” już istnieje")
    c.commit()
    return iid


def item_usage(c, item_id) -> int:
    return c.execute("SELECT COUNT(*) FROM losses WHERE item_id=?",
                     (item_id,)).fetchone()[0]


def delete_item(c, item_id, force=False) -> bool:
    """Удаляет позицию. Если по ней есть страты — только с force=True.

    Без force позицию правильнее выключить (`active=0`): она исчезнет из
    формы ввода, но прошлые месяцы останутся целыми.
    """
    used = item_usage(c, item_id)
    if used and not force:
        return False
    if used:
        c.execute("DELETE FROM losses WHERE item_id=?", (item_id,))
    c.execute("DELETE FROM items WHERE id=?", (item_id,))
    c.commit()
    return True


def set_item_active(c, item_id, active: bool):
    c.execute("UPDATE items SET active=? WHERE id=?",
              (1 if active else 0, item_id))
    c.commit()


def rename_category(c, old, new):
    c.execute("UPDATE items SET category=? WHERE category=?",
              ((new or "").strip()[:40], old))
    c.commit()


# ────────────────────────────── причины ──────────────────────────────
def list_reasons(c, active_only=True) -> list:
    sql = "SELECT * FROM reasons"
    if active_only:
        sql += " WHERE active=1"
    return [dict(r) for r in c.execute(
        sql + " ORDER BY sort_order, name COLLATE NOCASE")]


def reason_names(c, active_only=True) -> list:
    return [r["name"] for r in list_reasons(c, active_only)]


def save_reason(c, reason: dict) -> str:
    name = (reason.get("name") or "").strip()[:40]
    if not name:
        raise ValueError("pusty powód")
    rid = reason.get("id")
    if _name_taken(c, "reasons", name, rid):
        raise ValueError(f"powód „{name}” już istnieje")
    order = int(reason.get("sort_order") or 0)
    active = 1 if reason.get("active", 1) else 0
    if rid:
        sql, args = ("UPDATE reasons SET name=?, sort_order=?, active=?"
                     " WHERE id=?", (name, order, active, rid))
    else:
        rid = new_id()
        sql, args = ("INSERT INTO reasons (id,name,active,sort_order)"
                     " VALUES (?,?,?,?)", (rid, name, active, order))
    try:
        c.execute(sql, args)
    except sqlite3.IntegrityError:
        raise ValueError(f"powód „{name}” już istnieje")
    c.commit()
    return rid


def delete_reason(c, rid):
    """Причина хранится в страте строкой, поэтому удаление ничего не ломает."""
    c.execute("DELETE FROM reasons WHERE id=?", (rid,))
    c.commit()


# ────────────────────────────── страты ──────────────────────────────
def add_loss(c, entry: dict) -> str:
    """Записывает страту. Возвращает id."""
    item_id = (entry.get("item_id") or "").strip()
    item = get_item(c, item_id)
    if not item:
        raise ValueError("nieznana pozycja")

    unit = (entry.get("unit") or item["unit"]).strip()
    if unit not in UNITS:
        raise ValueError(f"nieznana jednostka: {unit!r}")
    if not compatible(unit, item["unit"]):
        raise ValueError(
            f"jednostka {unit} nie pasuje do pozycji ({item['unit']})")

    qty = parse_qty(entry.get("qty"))
    if not (qty > 0):
        raise ValueError("ilość musi być większa od zera")
    if qty > 1_000_000:
        raise ValueError("ilość wygląda na pomyłkę")

    loss_date = (entry.get("loss_date") or "").strip() or current_shift_date()
    if not DATE_RE.match(loss_date):
        raise ValueError(f"zła data: {loss_date!r}")

    lid = (entry.get("id") or "").strip()[:32] or new_id()
    c.execute(
        "INSERT OR IGNORE INTO losses (id,loss_date,item_id,qty,unit,qty_base,"
        "reason,person,note,source,report_id,created_at,confirmed_at)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (lid, loss_date, item_id, qty, unit, to_base(qty, unit),
         (entry.get("reason") or "").strip()[:40],
         (entry.get("person") or "").strip()[:40],
         (entry.get("note") or "").strip()[:200],
         (entry.get("source") or "manual")[:10],
         entry.get("report_id"), now_utc(), entry.get("confirmed_at")))
    c.commit()
    return lid


def update_loss(c, lid, entry: dict):
    """Правка страты в журнале: количество, единица, причина, дата, заметка."""
    row = c.execute("SELECT * FROM losses WHERE id=?", (lid,)).fetchone()
    if not row:
        raise ValueError("nie ma takiego wpisu")
    item = get_item(c, entry.get("item_id") or row["item_id"])
    if not item:
        raise ValueError("nieznana pozycja")
    unit = (entry.get("unit") or row["unit"]).strip()
    if not compatible(unit, item["unit"]):
        raise ValueError(f"jednostka {unit} nie pasuje do pozycji")
    qty = parse_qty(entry.get("qty", row["qty"]))
    if not (qty > 0):
        raise ValueError("ilość musi być większa od zera")
    loss_date = (entry.get("loss_date") or row["loss_date"]).strip()
    if not DATE_RE.match(loss_date):
        raise ValueError(f"zła data: {loss_date!r}")
    c.execute("UPDATE losses SET loss_date=?, item_id=?, qty=?, unit=?,"
              " qty_base=?, reason=?, person=?, note=? WHERE id=?",
              (loss_date, item["id"], qty, unit, to_base(qty, unit),
               (entry.get("reason") or row["reason"] or "").strip()[:40],
               (entry.get("person") or row["person"] or "").strip()[:40],
               (entry.get("note") if entry.get("note") is not None
                else row["note"] or "").strip()[:200], lid))
    c.commit()


def get_loss(c, lid):
    row = c.execute("SELECT * FROM losses WHERE id=?", (lid,)).fetchone()
    return dict(row) if row else None


def delete_loss(c, lid) -> bool:
    cur = c.execute("DELETE FROM losses WHERE id=?", (lid,))
    c.commit()
    return cur.rowcount > 0


def _range(date_from=None, date_to=None, month=None):
    if month:
        return month_bounds(month)
    return date_from or "0000-01-01", date_to or "9999-12-31"


def journal(c, date_from=None, date_to=None, month=None, item_id=None,
            category=None, reason=None, person=None, search=None,
            limit=None) -> list:
    """Журнал страт от новых к старым, с названием и единицей позиции."""
    lo, hi = _range(date_from, date_to, month)
    sql = ("SELECT l.*, i.name AS item_name, i.unit AS item_unit,"
           " i.category AS category"
           " FROM losses l JOIN items i ON i.id=l.item_id"
           " WHERE l.loss_date BETWEEN ? AND ?")
    args = [lo, hi]
    if item_id:
        sql += " AND l.item_id=?"
        args.append(item_id)
    if category:
        sql += " AND i.category=?"
        args.append(category)
    if reason:
        sql += " AND l.reason=?"
        args.append(reason)
    if person:
        sql += " AND l.person=?"
        args.append(person)
    if search:
        sql += " AND (i.name LIKE ? OR l.note LIKE ?)"
        args += [f"%{search}%", f"%{search}%"]
    sql += " ORDER BY l.loss_date DESC, l.created_at DESC"
    if limit:
        sql += f" LIMIT {int(limit)}"
    out = []
    for r in c.execute(sql, args):
        d = dict(r)
        d["qty_text"] = f"{fmt_num(d['qty'])} {d['unit']}"
        out.append(d)
    return out


def by_item(c, date_from=None, date_to=None, month=None, category=None) -> list:
    """Итог по позициям за период — главный ответ приложения.

    Сумма считается по `qty_base`, поэтому 300 g + 1 kg дают 1.3 kg, а не
    «301 чего-то». Отдаём и число (в единице позиции), и готовый текст:
    число нужно Excel'ю, текст — таблице на экране.
    """
    lo, hi = _range(date_from, date_to, month)
    sql = ("SELECT i.id, i.name, i.category, i.unit,"
           " SUM(l.qty_base) AS base, COUNT(*) AS n"
           " FROM losses l JOIN items i ON i.id=l.item_id"
           " WHERE l.loss_date BETWEEN ? AND ?")
    args = [lo, hi]
    if category:
        sql += " AND i.category=?"
        args.append(category)
    rows = c.execute(sql + " GROUP BY i.id", args)
    out = []
    for r in rows:
        out.append({
            "item_id": r["id"], "name": r["name"], "category": r["category"],
            "unit": r["unit"], "qty_base": r["base"],
            "qty": qty_in(r["base"], r["unit"]),
            "qty_text": format_qty(r["base"], r["unit"]),
            "entries": r["n"],
        })
    out.sort(key=lambda x: x["name"].lower())
    return out


def sort_summary(rows: list, key: str) -> list:
    """Сортировка итогов. «По количеству» — ВНУТРИ групп единиц: сравнивать
    7 l с 3 kg бессмысленно, а «в килограммах больше всего ушло łososia» —
    ровно тот вопрос, который задаёт менеджер."""
    if key == "qty":
        return sorted(rows, key=lambda r: (DIM_ORDER[unit_dim(r["unit"])],
                                           -r["qty_base"]))
    if key == "entries":
        return sorted(rows, key=lambda r: (-r["entries"], r["name"].lower()))
    if key == "category":
        return sorted(rows, key=lambda r: ((r["category"] or "").lower(),
                                           r["name"].lower()))
    return sorted(rows, key=lambda r: r["name"].lower())


def by_month(c, date_from=None, date_to=None) -> dict:
    """Матрица позиция × месяц + итог за весь период.

    Отвечает на вопрос «а в июле этого столько же уходило?».
    """
    lo, hi = _range(date_from, date_to)
    rows = c.execute(
        "SELECT i.id, i.name, i.category, i.unit,"
        " substr(l.loss_date,1,7) AS m, SUM(l.qty_base) AS base"
        " FROM losses l JOIN items i ON i.id=l.item_id"
        " WHERE l.loss_date BETWEEN ? AND ?"
        " GROUP BY i.id, m", (lo, hi))
    months, items = set(), {}
    for r in rows:
        months.add(r["m"])
        it = items.setdefault(r["id"], {
            "item_id": r["id"], "name": r["name"], "category": r["category"],
            "unit": r["unit"], "months": {}, "total_base": 0.0})
        it["months"][r["m"]] = r["base"]
        it["total_base"] += r["base"]
    for it in items.values():
        it["total"] = qty_in(it["total_base"], it["unit"])
        it["total_text"] = format_qty(it["total_base"], it["unit"])
        it["months_qty"] = {m: qty_in(v, it["unit"])
                            for m, v in it["months"].items()}
        it["months_text"] = {m: format_qty(v, it["unit"])
                             for m, v in it["months"].items()}
    return {"months": sorted(months),
            "items": sorted(items.values(), key=lambda x: x["name"].lower())}


def by_reason(c, date_from=None, date_to=None, month=None) -> list:
    """Сколько записей на каждую причину. Количества не складываем — у
    разных позиций разные единицы, сумма «kg + szt» смысла не имеет."""
    lo, hi = _range(date_from, date_to, month)
    rows = c.execute(
        "SELECT COALESCE(NULLIF(reason,''),'—') AS reason, COUNT(*) AS n"
        " FROM losses WHERE loss_date BETWEEN ? AND ?"
        " GROUP BY reason ORDER BY n DESC", (lo, hi))
    return [{"reason": r["reason"], "entries": r["n"]} for r in rows]


def by_category(c, date_from=None, date_to=None, month=None) -> list:
    lo, hi = _range(date_from, date_to, month)
    rows = c.execute(
        "SELECT COALESCE(NULLIF(i.category,''),'—') AS cat, COUNT(*) AS n"
        " FROM losses l JOIN items i ON i.id=l.item_id"
        " WHERE l.loss_date BETWEEN ? AND ? GROUP BY cat ORDER BY n DESC",
        (lo, hi))
    return [{"category": r["cat"], "entries": r["n"]} for r in rows]


def by_day(c, month: str) -> list:
    lo, hi = month_bounds(month)
    rows = c.execute(
        "SELECT loss_date, COUNT(*) AS n FROM losses"
        " WHERE loss_date BETWEEN ? AND ? GROUP BY loss_date"
        " ORDER BY loss_date", (lo, hi))
    return [{"date": r["loss_date"], "entries": r["n"]} for r in rows]


def months_with_data(c) -> list:
    return [r[0] for r in c.execute(
        "SELECT DISTINCT substr(loss_date,1,7) AS m FROM losses ORDER BY m DESC")]


def persons_used(c) -> list:
    return [r[0] for r in c.execute(
        "SELECT DISTINCT person FROM losses WHERE person<>''"
        " ORDER BY person COLLATE NOCASE")]


def reasons_used(c) -> list:
    return [r[0] for r in c.execute(
        "SELECT DISTINCT reason FROM losses WHERE reason<>''"
        " ORDER BY reason COLLATE NOCASE")]


def stats(c, month: str) -> dict:
    lo, hi = month_bounds(month)
    row = c.execute(
        "SELECT COUNT(*) AS n, COUNT(DISTINCT item_id) AS items"
        " FROM losses WHERE loss_date BETWEEN ? AND ?", (lo, hi)).fetchone()
    today = c.execute("SELECT COUNT(*) FROM losses WHERE loss_date=?",
                      (current_shift_date(),)).fetchone()[0]
    return {"entries": row["n"], "items": row["items"], "today": today}


# ────────────────────── подтверждение зашлушений ──────────────────────
def is_report_confirmed(c, report_id) -> bool:
    return c.execute("SELECT 1 FROM losses_confirmed WHERE report_id=?",
                     (report_id,)).fetchone() is not None


def confirmed_reports(c, limit=50) -> list:
    return [dict(r) for r in c.execute(
        "SELECT * FROM losses_confirmed ORDER BY confirmed_at DESC LIMIT ?",
        (limit,))]


def losses_for_report(c, report_id) -> list:
    return [dict(r) for r in c.execute(
        "SELECT l.*, i.name AS item_name FROM losses l"
        " JOIN items i ON i.id=l.item_id WHERE l.report_id=?", (report_id,))]


def resolve_report_lines(c, lines: list) -> list:
    """Готовит строки зашлушения к показу в диалоге подтверждения.

    Позицию ищем сначала по id, потом по имени: каталог на сервере может
    отстать от локального, а имя переживает и переустановку приложения.
    Нераспознанные строки не выбрасываем — менеджер выберет позицию руками,
    иначе страта просто потерялась бы.
    """
    out = []
    for n, line in enumerate(lines or []):
        item = None
        if line.get("item_id"):
            item = get_item(c, line["item_id"])
        if not item and line.get("item_name"):
            item = find_item_by_name(c, line["item_name"])
        try:
            qty = parse_qty(line.get("qty"))
        except ValueError:
            qty = 0.0
        unit = (line.get("unit") or (item or {}).get("unit") or "szt").strip()
        problem = None
        if not item:
            problem = "nieznana pozycja"
        elif unit not in UNITS:
            problem = "nieznana jednostka"
        elif not compatible(unit, item["unit"]):
            problem = f"jednostka {unit} nie pasuje do pozycji"
        elif qty <= 0:
            problem = "zła ilość"
        out.append({
            "n": n,
            "item_id": item["id"] if item else None,
            "item_name": (item["name"] if item
                          else (line.get("item_name") or "—")),
            "unit": unit,
            "item_unit": item["unit"] if item else unit,
            "qty": qty,
            "reason": (line.get("reason") or "").strip()[:40],
            "note": (line.get("note") or "").strip()[:200],
            "problem": problem,
        })
    return out


def confirm_report(c, report: dict, lines=None) -> dict:
    """Принимает зашлушение в журнал. Идемпотентно.

    Перед записью удаляются строки с тем же `report_id`: падение в середине
    или повторное нажатие не приводят к удвоению страт.

    `lines` — уже поправленные менеджером строки; если не передать, берутся
    из самого зашлушения.
    """
    rid = report.get("id") or report.get("report_id")
    if not rid:
        raise ValueError("zgłoszenie bez id")
    data = report.get("data") or {}
    lines = lines if lines is not None else resolve_report_lines(
        c, data.get("lines") or [])
    loss_date = (report.get("shift_date") or data.get("shift_date")
                 or current_shift_date())
    person = (report.get("name") or data.get("name") or "").strip()[:40]

    bad = [l for l in lines if l.get("problem")]
    if bad:
        raise ValueError("; ".join(f"{l['item_name']}: {l['problem']}"
                                   for l in bad[:3]))

    c.execute("DELETE FROM losses WHERE report_id=?", (rid,))
    stamp = now_utc()
    for line in lines:
        add_loss(c, {
            "loss_date": line.get("loss_date") or loss_date,
            "item_id": line["item_id"], "qty": line["qty"],
            "unit": line["unit"], "reason": line.get("reason"),
            "person": line.get("person") or person, "note": line.get("note"),
            "source": "qr", "report_id": rid, "confirmed_at": stamp,
        })
    c.execute("INSERT OR REPLACE INTO losses_confirmed"
              " (report_id, confirmed_at, lines, person, loss_date)"
              " VALUES (?,?,?,?,?)",
              (rid, stamp, len(lines), person, loss_date))
    c.commit()
    return {"report_id": rid, "lines": len(lines), "date": loss_date}


def unconfirm_report(c, report_id) -> int:
    """Отменяет подтверждение: убирает строки и отметку.

    Нужно, когда менеджер принял зашлушение с чужой смены — иначе
    исправлять пришлось бы по одной строке в журнале.
    """
    cur = c.execute("DELETE FROM losses WHERE report_id=?", (report_id,))
    c.execute("DELETE FROM losses_confirmed WHERE report_id=?", (report_id,))
    c.commit()
    return cur.rowcount


# ────────────────────────────── стартовые данные ──────────────────────
def seed(c, path=None, overwrite=False) -> dict:
    """Заливает стартовый каталог позиций и причин из seed_kitchen.json.

    По умолчанию не трогает то, что уже есть: повторный вызов на живой
    кухне безопасен и просто добавит недостающее.
    """
    path = path or os.path.join(APP_DIR, "seed_kitchen.json")
    with open(path, encoding="utf-8") as f:
        data = json.load(f)

    added_i = added_r = 0
    have = {i["name"].lower() for i in list_items(c, active_only=False)}
    for n, it in enumerate(data.get("items", [])):
        if it["name"].lower() in have and not overwrite:
            continue
        try:
            save_item(c, {"name": it["name"], "unit": it["unit"],
                          "category": it.get("category", ""),
                          "sort_order": it.get("sort_order", n)})
            added_i += 1
        except ValueError:
            pass
    have_r = {r["name"].lower() for r in list_reasons(c, active_only=False)}
    for n, name in enumerate(data.get("reasons", [])):
        if name.lower() in have_r:
            continue
        save_reason(c, {"name": name, "sort_order": n})
        added_r += 1
    return {"items": added_i, "reasons": added_r}


def catalog_for_server(c) -> dict:
    """Каталог в том виде, в каком его ждёт форма на телефоне.

    Сервер ничего не считает и не знает про единицы — он просто отдаёт
    форме готовый список. Вся математика остаётся здесь.
    """
    return {
        "items": [{"id": i["id"], "name": i["name"],
                   "category": i["category"] or "", "unit": i["unit"],
                   "units": units_for(i["unit"])}
                  for i in list_items(c)],
        "reasons": reason_names(c),
        "kitchen": get_config(c, "kitchen_name"),
    }
