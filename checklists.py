"""Checklisty — model, baza, kopie zapasowe, wydruk.

Чек-лист устроен как набор секций-таблиц: у секции есть колонки (каждая со
своим типом ячейки) и строки. Этого хватает и на «галочку напротив пункта»,
и на температурный лист «станция × время», и на журнал, где строки дописывает
сам повар (режим `free`).

    szablon (template)
      ├─ header[]   pola nad tabelami (poza datą i osobą — te są zawsze)
      ├─ sections[]
      │    ├─ columns[]  {id, label, type, required, min, max, unit, options}
      │    ├─ rows[]     {id, label, min, max, off[]}
      │    ├─ mode       fixed | free
      │    └─ sign       podpis pod sekcją
      └─ footer[]   pola pod tabelami (np. podpis supervisora)

Заполненный чек-лист (`entry`) несёт **снимок шаблона**, по которому его
заполняли: шаблон потом можно править как угодно, старые копии от этого не
меняются и всегда показываются такими, какими их видел повар.

Значения лежат плоским словарём `values["sekcja|wiersz|kolumna"]` — так их
проще всего и хранить, и проверять, и слать с телефона.
"""

import html
import json
import os
import sqlite3
import zipfile
from datetime import date

import db

# ────────────────────────────── типы ──────────────────────────────
# (ключ, подпись в конструкторе)
FIELD_TYPES = [
    ("check", "Pole wyboru ✓"),
    ("yesno", "Tak / Nie / N/D"),
    ("text", "Tekst"),
    ("number", "Liczba"),
    ("temp", "Temperatura °C"),
    ("time", "Godzina"),
    ("date", "Data"),
    ("choice", "Lista wyboru"),
    ("sign", "Podpis"),
]
TYPE_KEYS = [k for k, _ in FIELD_TYPES]
TYPE_LABELS = dict(FIELD_TYPES)
NUMERIC = ("number", "temp")

PERIODS = [("daily", "Codzienna"), ("weekly", "Tygodniowa"),
           ("monthly", "Miesięczna"), ("any", "Dowolna")]
PERIOD_LABELS = dict(PERIODS)

MAX_SECTIONS = 30
MAX_COLUMNS = 14
MAX_ROWS = 200
MAX_FIELDS = 12
MAX_OPTIONS = 20
BACKUP_KEEP = 30
BACKUP_FORMAT = "kuchnia-checklisty/1"

SIGN_ROW = "_sign"          # values["sekcja|_sign|_"] — podpis pod sekcją


def _short_id() -> str:
    return db.new_id()[:8]


def _txt(v, n) -> str:
    return str(v if v is not None else "").strip()[:n]


def _num(v):
    """Limit z konstruktora: liczba albo None. «-18», «5,5», «» → …"""
    if v is None or v == "":
        return None
    if isinstance(v, bool):
        return None
    try:
        return float(str(v).strip().replace(",", "."))
    except ValueError:
        return None


def parse_number(v):
    """Liczba wpisana przy wypełnianiu. None, gdy to nie liczba."""
    if v is None or isinstance(v, bool):
        return None
    s = str(v).strip().replace(",", ".").replace("−", "-")
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


# ────────────────────────────── нормализация ──────────────────────────
def _uniq(used: set, wanted) -> str:
    wanted = _txt(wanted, 16)
    # «|» — разделитель ключа значения, в id ему нельзя
    if not wanted or wanted in used or "|" in wanted or wanted == SIGN_ROW:
        wanted = _short_id()
        while wanted in used:
            wanted = _short_id()
    used.add(wanted)
    return wanted


def _norm_field(raw, used, cell=True) -> dict:
    raw = raw if isinstance(raw, dict) else {}
    ftype = raw.get("type") if raw.get("type") in TYPE_KEYS else "check"
    out = {"id": _uniq(used, raw.get("id")),
           "label": _txt(raw.get("label"), 120),
           "type": ftype,
           "required": bool(raw.get("required"))}
    if ftype in NUMERIC:
        out["min"] = _num(raw.get("min"))
        out["max"] = _num(raw.get("max"))
        out["unit"] = "°C" if ftype == "temp" else _txt(raw.get("unit"), 8)
    if ftype == "choice":
        opts = []
        for o in (raw.get("options") or [])[:MAX_OPTIONS]:
            o = _txt(o, 40)
            if o and o not in opts:
                opts.append(o)
        out["options"] = opts
    return out


def normalize_template(raw: dict) -> dict:
    """Приводит шаблон к каноническому виду. Бросает ValueError.

    Через эту функцию проходит всё: сохранение из конструктора, импорт из
    файла, стартовые шаблоны и снимок, приехавший с телефона, — поэтому
    остальной код может не проверять структуру.
    """
    if not isinstance(raw, dict):
        raise ValueError("zły format szablonu")
    name = _txt(raw.get("name"), 80)
    if not name:
        raise ValueError("podaj nazwę checklisty")
    period = raw.get("period") if raw.get("period") in PERIOD_LABELS else "any"

    used = set()
    header = [_norm_field(f, used) for f in
              (raw.get("header") or [])[:MAX_FIELDS] if isinstance(f, dict)]
    footer = [_norm_field(f, used) for f in
              (raw.get("footer") or [])[:MAX_FIELDS] if isinstance(f, dict)]

    sections = []
    sec_ids = set()
    for s in (raw.get("sections") or [])[:MAX_SECTIONS]:
        if not isinstance(s, dict):
            continue
        col_ids = set()
        columns = [_norm_field(c, col_ids) for c in
                   (s.get("columns") or [])[:MAX_COLUMNS] if isinstance(c, dict)]
        if not columns:
            columns = [_norm_field({"label": "", "type": "check"}, col_ids)]
        mode = "free" if s.get("mode") == "free" else "fixed"
        row_ids = set()
        rows = []
        for r in (s.get("rows") or [])[:MAX_ROWS]:
            if not isinstance(r, dict):
                continue
            row = {"id": _uniq(row_ids, r.get("id")),
                   "label": _txt(r.get("label"), 240),
                   "min": _num(r.get("min")), "max": _num(r.get("max")),
                   "off": [x for x in (r.get("off") or []) if x in col_ids]}
            rows.append(row)
        try:
            free_rows = max(1, min(40, int(s.get("free_rows") or 3)))
        except (TypeError, ValueError):
            free_rows = 3
        sections.append({
            "id": _uniq(sec_ids, s.get("id")),
            "title": _txt(s.get("title"), 80),
            "mode": mode,
            "label_title": _txt(s.get("label_title"), 60),
            "free_rows": free_rows,
            "sign": bool(s.get("sign")),
            "columns": columns,
            "rows": rows,
        })
    if not sections:
        raise ValueError("checklista musi mieć przynajmniej jedną sekcję")
    for s in sections:
        if s["mode"] == "fixed" and not s["rows"]:
            raise ValueError(
                f"sekcja „{s['title'] or 'bez nazwy'}” nie ma wierszy")

    return {"name": name, "description": _txt(raw.get("description"), 300),
            "period": period, "header": header, "sections": sections,
            "footer": footer}


def blank_template() -> dict:
    """Пустая заготовка для кнопки «Nowy szablon»."""
    return normalize_template({
        "name": "Nowa checklista", "period": "daily",
        "sections": [{"title": "Sekcja 1", "label_title": "Pozycja",
                      "columns": [{"label": "Zrobione", "type": "check"}],
                      "rows": [{"label": "Pierwszy punkt"}]}]})


def template_stats(tpl: dict) -> dict:
    return {"sections": len(tpl["sections"]),
            "rows": sum(len(s["rows"]) for s in tpl["sections"]),
            "columns": sum(len(s["columns"]) for s in tpl["sections"])}


# ────────────────────────────── шаблоны в базе ──────────────────────
def _tpl_row(row) -> dict:
    tpl = json.loads(row["data_json"])
    tpl.update(id=row["id"], name=row["name"], active=row["active"],
               sort_order=row["sort_order"], version=row["version"],
               created_at=row["created_at"], updated_at=row["updated_at"])
    return tpl


def list_templates(c, active_only=False) -> list:
    sql = "SELECT * FROM checklist_templates"
    if active_only:
        sql += " WHERE active=1"
    return [_tpl_row(r) for r in c.execute(
        sql + " ORDER BY sort_order, name COLLATE NOCASE")]


def get_template(c, tid):
    row = c.execute("SELECT * FROM checklist_templates WHERE id=?",
                    (tid,)).fetchone()
    return _tpl_row(row) if row else None


def save_template(c, tpl: dict) -> str:
    """Создаёт или правит шаблон. Возвращает id. Версия растёт на каждой
    правке — по ней видно, по какой редакции заполнена старая копия."""
    data = normalize_template(tpl)
    tid = tpl.get("id")
    low = data["name"].lower()
    for other in c.execute("SELECT id, name FROM checklist_templates"):
        if other["id"] != tid and other["name"].strip().lower() == low:
            raise ValueError(f"checklista „{data['name']}” już istnieje")
    blob = json.dumps(data, ensure_ascii=False)
    stamp = db.now_utc()
    old = c.execute("SELECT version FROM checklist_templates WHERE id=?",
                    (tid,)).fetchone() if tid else None
    if old:
        c.execute("UPDATE checklist_templates SET name=?, period=?,"
                  " data_json=?, active=?, version=?, updated_at=? WHERE id=?",
                  (data["name"], data["period"], blob,
                   1 if tpl.get("active", 1) else 0, old["version"] + 1,
                   stamp, tid))
    else:
        tid = _txt(tid, 16) or db.new_id()
        order = c.execute("SELECT COALESCE(MAX(sort_order),0)+1"
                          " FROM checklist_templates").fetchone()[0]
        c.execute("INSERT INTO checklist_templates (id,name,period,data_json,"
                  "active,sort_order,version,created_at,updated_at)"
                  " VALUES (?,?,?,?,?,?,?,?,?)",
                  (tid, data["name"], data["period"], blob,
                   1 if tpl.get("active", 1) else 0, order,
                   int(tpl.get("version") or 1), stamp, stamp))
    c.commit()
    return tid


def set_template_active(c, tid, active: bool):
    c.execute("UPDATE checklist_templates SET active=? WHERE id=?",
              (1 if active else 0, tid))
    c.commit()


def delete_template(c, tid):
    """Удаляет шаблон. Заполненные копии остаются: у каждой свой снимок."""
    c.execute("DELETE FROM checklist_templates WHERE id=?", (tid,))
    c.commit()


def _free_name(c, name: str) -> str:
    have = {r[0].strip().lower()
            for r in c.execute("SELECT name FROM checklist_templates")}
    if name.lower() not in have:
        return name
    n = 2
    while f"{name} ({n})".lower() in have:
        n += 1
    return f"{name} ({n})"


def duplicate_template(c, tid) -> str:
    tpl = get_template(c, tid)
    if not tpl:
        raise ValueError("nie ma takiego szablonu")
    copy = dict(tpl, id=None, version=1,
                name=_free_name(c, f"{tpl['name']} — kopia"[:76]))
    return save_template(c, copy)


def move_template(c, tid, delta: int):
    ids = [t["id"] for t in list_templates(c)]
    if tid not in ids:
        return
    i = ids.index(tid)
    j = max(0, min(len(ids) - 1, i + delta))
    ids.insert(j, ids.pop(i))
    for n, x in enumerate(ids):
        c.execute("UPDATE checklist_templates SET sort_order=? WHERE id=?",
                  (n, x))
    c.commit()


def import_template(c, raw: dict) -> str:
    """Шаблон из файла или из стартового набора — всегда как новый."""
    data = normalize_template(raw)
    data["name"] = _free_name(c, data["name"])
    return save_template(c, data)


def seed_presets(c) -> int:
    """Добавляет стартовые шаблоны, которых ещё нет (по названию)."""
    import checklist_presets
    have = {r[0].strip().lower()
            for r in c.execute("SELECT name FROM checklist_templates")}
    added = 0
    for raw in checklist_presets.PRESETS:
        if raw["name"].strip().lower() in have:
            continue
        save_template(c, raw)
        added += 1
    return added


def catalog_for_server(c) -> dict:
    """Что уезжает на сервер: только активные шаблоны, с id и версией."""
    out = []
    for t in list_templates(c, active_only=True):
        out.append({k: t[k] for k in ("id", "name", "description", "period",
                                      "version", "header", "sections",
                                      "footer")})
    return {"templates": out, "kitchen": db.get_config(c, "kitchen_name")}


# ────────────────────────────── проверка заполнения ──────────────────
def cell_key(sid, rid, cid) -> str:
    return f"{sid}|{rid}|{cid}"


def limits_for(col: dict, row: dict = None):
    """Допуск ячейки: лимит строки важнее лимита колонки — у «Walk-In
    Freezer» и «Hot Hold» одна колонка, а нормы разные."""
    lo = hi = None
    if row:
        lo, hi = row.get("min"), row.get("max")
    if lo is None and hi is None:
        lo, hi = col.get("min"), col.get("max")
    return lo, hi


def limits_text(lo, hi, unit="") -> str:
    u = f" {unit}" if unit else ""
    if lo is not None and hi is not None:
        return f"{db.fmt_num(lo)}…{db.fmt_num(hi)}{u}"
    if lo is not None:
        return f"≥ {db.fmt_num(lo)}{u}"
    if hi is not None:
        return f"≤ {db.fmt_num(hi)}{u}"
    return ""


def is_empty(v) -> bool:
    return v is None or v is False or (isinstance(v, str) and not v.strip())


def cell_bad(col: dict, row: dict, value) -> bool:
    """Отклонение: число вне допуска или «Nie» в колонке Tak/Nie."""
    if is_empty(value):
        return False
    if col["type"] == "yesno":
        return value == "nie"
    if col["type"] in NUMERIC:
        v = parse_number(value)
        if v is None:
            return True                 # wpisano coś, co nie jest liczbą
        lo, hi = limits_for(col, row)
        return (lo is not None and v < lo) or (hi is not None and v > hi)
    return False


def format_value(col: dict, value) -> str:
    if is_empty(value):
        return ""
    t = col["type"]
    if t == "check":
        return "✓"
    if t == "yesno":
        return {"tak": "Tak", "nie": "Nie", "nd": "N/D"}.get(value, str(value))
    if t in NUMERIC:
        v = parse_number(value)
        text = db.fmt_num(v) if v is not None else str(value)
        return f"{text} {col.get('unit')}".strip() if col.get("unit") else text
    return str(value)


def evaluate(entry: dict) -> dict:
    """Считает заполненность и отклонения готовой копии.

    Возвращает {filled, total, issues, bad: set(ключей)}. Ячейки, выключенные
    в строке (`off`), в счёт не идут — на бумаге они перечёркнуты.
    """
    tpl = entry["template"]
    values = entry.get("values") or {}
    filled = total = 0
    bad = set()
    for s in tpl["sections"]:
        for r in s["rows"]:
            row_used = False
            cells = []
            for col in s["columns"]:
                if col["id"] in r["off"]:
                    continue
                key = cell_key(s["id"], r["id"], col["id"])
                v = values.get(key)
                cells.append((key, col, v))
                if not is_empty(v):
                    row_used = True
            # пустые строки журнала (режим free) — не «недозаполнено»
            if s["mode"] == "free" and not row_used and not r["label"]:
                continue
            for key, col, v in cells:
                total += 1
                if not is_empty(v):
                    filled += 1
                if cell_bad(col, r, v):
                    bad.add(key)
    for part in ("header", "footer"):
        vals = entry.get(part) or {}
        for f in tpl[part]:
            total += 1
            v = vals.get(f["id"])
            if not is_empty(v):
                filled += 1
            if cell_bad(f, None, v):
                bad.add(f"{part}|{f['id']}")
    return {"filled": filled, "total": total, "issues": len(bad), "bad": bad}


# ────────────────────────────── копии (архив) ──────────────────────
def _clean_values(raw) -> dict:
    out = {}
    if not isinstance(raw, dict):
        return out
    for k, v in list(raw.items())[:5000]:
        k = _txt(k, 64)
        if isinstance(v, bool):
            out[k] = v
        elif isinstance(v, (int, float)):
            out[k] = v
        elif v is not None:
            v = _txt(v, 300)
            if v:
                out[k] = v
    return out


def normalize_entry(payload: dict) -> dict:
    """Проверяет заполненный чек-лист, пришедший снаружи (телефон, файл).

    Снимок шаблона проходит ту же `normalize_template`, что и шаблоны из
    конструктора: чужой JSON не должен уметь уронить просмотр архива.
    """
    if not isinstance(payload, dict):
        raise ValueError("zły format")
    eid = _txt(payload.get("id"), 32)
    if not eid:
        raise ValueError("kopia bez id")
    entry_date = _txt(payload.get("date"), 10)
    if not db.DATE_RE.match(entry_date):
        raise ValueError(f"zła data: {entry_date!r}")
    snap_raw = payload.get("template")
    if not isinstance(snap_raw, dict):
        raise ValueError("brak szablonu w kopii")
    snap = normalize_template(snap_raw)
    snap["version"] = int(snap_raw.get("version") or 1)
    return {
        "id": eid,
        "template_id": _txt(payload.get("template_id"), 32),
        "date": entry_date,
        "name": _txt(payload.get("name"), 40),
        "submitted_at": _txt(payload.get("submitted_at"), 32),
        "template": snap,
        "values": _clean_values(payload.get("values")),
        "header": _clean_values(payload.get("header")),
        "footer": _clean_values(payload.get("footer")),
    }


def import_entry(c, payload: dict, source="qr", received_at=None,
                 seen=0) -> tuple:
    """Кладёт заполненный чек-лист в архив. Возвращает (id, новая ли).

    Идемпотентно: id даёт телефон, повторная доставка той же копии (обрыв
    связи между «забрал» и «удалил на сервере») ничего не удваивает.
    """
    e = normalize_entry(payload)
    res = evaluate(e)
    cur = c.execute(
        "INSERT OR IGNORE INTO checklist_entries (id, template_id,"
        " template_name, entry_date, person, submitted_at, received_at,"
        " source, issues, filled, total, seen, data_json)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (e["id"], e["template_id"], e["template"]["name"], e["date"],
         e["name"], e["submitted_at"], received_at or db.now_utc(), source,
         res["issues"], res["filled"], res["total"], 1 if seen else 0,
         json.dumps({k: e[k] for k in ("template", "values", "header",
                                       "footer")}, ensure_ascii=False)))
    c.commit()
    return e["id"], cur.rowcount > 0


def _entry_row(row, full=False) -> dict:
    d = {k: row[k] for k in row.keys() if k != "data_json"}
    if full:
        d.update(json.loads(row["data_json"]))
    return d


SORTS = {"date": "entry_date", "received": "received_at",
         "name": "template_name", "person": "person", "issues": "issues"}


def list_entries(c, template_name=None, month=None, date_from=None,
                 date_to=None, search=None, sort="date", desc=True,
                 limit=None) -> list:
    """Архив без тел копий (они тяжёлые) — для таблицы. Сортировка по дате
    чек-листа либо по любой другой колонке, в обе стороны."""
    lo, hi = (db.month_bounds(month) if month
              else (date_from or "0000-01-01", date_to or "9999-12-31"))
    sql = ("SELECT id, template_id, template_name, entry_date, person,"
           " submitted_at, received_at, source, issues, filled, total, seen"
           " FROM checklist_entries WHERE entry_date BETWEEN ? AND ?")
    args = [lo, hi]
    if template_name:
        sql += " AND template_name=?"
        args.append(template_name)
    rows = [dict(r) for r in c.execute(sql, args)]
    if search:
        q = search.strip().lower()
        rows = [r for r in rows if q in (r["person"] or "").lower()
                or q in (r["template_name"] or "").lower()
                or q in r["entry_date"]]
    col = SORTS.get(sort, "entry_date")
    # вторичный ключ — время получения: копии одного дня идут по порядку
    rows.sort(key=lambda r: (str(r[col] or "").lower() if col != "issues"
                             else r[col] or 0, r["received_at"] or ""),
              reverse=bool(desc))
    return rows[:limit] if limit else rows


def get_entry(c, eid):
    row = c.execute("SELECT * FROM checklist_entries WHERE id=?",
                    (eid,)).fetchone()
    return _entry_row(row, full=True) if row else None


def delete_entry(c, eid) -> bool:
    cur = c.execute("DELETE FROM checklist_entries WHERE id=?", (eid,))
    c.commit()
    return cur.rowcount > 0


def mark_seen(c, eid=None):
    if eid:
        c.execute("UPDATE checklist_entries SET seen=1 WHERE id=?", (eid,))
    else:
        c.execute("UPDATE checklist_entries SET seen=1 WHERE seen=0")
    c.commit()


def unseen_count(c) -> int:
    return c.execute("SELECT COUNT(*) FROM checklist_entries"
                     " WHERE seen=0").fetchone()[0]


def entry_months(c) -> list:
    return [r[0] for r in c.execute(
        "SELECT DISTINCT substr(entry_date,1,7) AS m FROM checklist_entries"
        " ORDER BY m DESC")]


def entry_template_names(c) -> list:
    return [r[0] for r in c.execute(
        "SELECT DISTINCT template_name FROM checklist_entries"
        " ORDER BY template_name COLLATE NOCASE")]


def archive_stats(c, month=None) -> dict:
    lo, hi = db.month_bounds(month) if month else ("0000-01-01", "9999-12-31")
    row = c.execute(
        "SELECT COUNT(*) AS n, COALESCE(SUM(issues>0),0) AS bad,"
        " COALESCE(SUM(seen=0),0) AS fresh FROM checklist_entries"
        " WHERE entry_date BETWEEN ? AND ?", (lo, hi)).fetchone()
    return {"entries": row["n"], "with_issues": row["bad"],
            "unseen": row["fresh"]}


# ────────────────────────────── kopia zapasowa ──────────────────────
def export_backup(c, path) -> dict:
    """ZIP с шаблонами и всеми заполненными копиями.

    Обычный JSON внутри архива: открыть и прочитать можно и без программы,
    даже через десять лет. Пишем во временный файл и переименовываем —
    оборванная запись не оставит битую «копию» под рабочим именем.
    """
    templates = list_templates(c)
    entries = []
    for row in c.execute("SELECT * FROM checklist_entries"
                         " ORDER BY entry_date, received_at"):
        entries.append(_entry_row(row, full=True))
    manifest = {"format": BACKUP_FORMAT, "created_at": db.now_utc(),
                "kitchen": db.get_config(c, "kitchen_name"),
                "templates": len(templates), "entries": len(entries)}
    tmp = f"{path}.tmp"
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("manifest.json",
                   json.dumps(manifest, ensure_ascii=False, indent=1))
        z.writestr("templates.json",
                   json.dumps(templates, ensure_ascii=False, indent=1))
        z.writestr("entries.json", json.dumps(entries, ensure_ascii=False))
    os.replace(tmp, path)
    return manifest


def import_backup(c, path) -> dict:
    """Восстановление из ZIP. Только добавляет: существующие шаблоны и
    копии не трогает, поэтому повторный импорт безопасен."""
    try:
        with zipfile.ZipFile(path) as z:
            manifest = json.loads(z.read("manifest.json"))
            templates = json.loads(z.read("templates.json"))
            entries = json.loads(z.read("entries.json"))
    except (zipfile.BadZipFile, KeyError, ValueError, OSError):
        raise ValueError("to nie jest kopia zapasowa checklist")
    if not str(manifest.get("format", "")).startswith("kuchnia-checklisty/"):
        raise ValueError("to nie jest kopia zapasowa checklist")

    added_t = added_e = skipped = 0
    have_ids = {r[0] for r in c.execute("SELECT id FROM checklist_templates")}
    have_names = {r[0].strip().lower()
                  for r in c.execute("SELECT name FROM checklist_templates")}
    for t in templates:
        try:
            if t.get("id") in have_ids \
                    or str(t.get("name", "")).strip().lower() in have_names:
                continue
            save_template(c, t)
            added_t += 1
        except ValueError:
            skipped += 1
    for e in entries:
        try:
            payload = dict(e, date=e.get("entry_date"), name=e.get("person"))
            _id, new = import_entry(c, payload, source=e.get("source") or "qr",
                                    received_at=e.get("received_at"), seen=1)
            added_e += 1 if new else 0
        except (ValueError, TypeError, AttributeError):
            skipped += 1
    return {"templates": added_t, "entries": added_e, "skipped": skipped}


def backup_signature(c) -> str:
    """Отпечаток данных: изменился — пора делать новую автокопию."""
    t = c.execute("SELECT COUNT(*), COALESCE(MAX(updated_at),'')"
                  " FROM checklist_templates").fetchone()
    e = c.execute("SELECT COUNT(*), COALESCE(MAX(received_at),'')"
                  " FROM checklist_entries").fetchone()
    return f"{t[0]}:{t[1]}:{e[0]}:{e[1]}"


def auto_backup(c, folder, today=None) -> str:
    """Автокопия в выбранную папку (например, OneDrive): один файл в день,
    и только если с прошлого раза что-то изменилось. Хранит последние
    BACKUP_KEEP файлов. Возвращает путь или '' (нечего/некуда писать)."""
    folder = (folder or "").strip()
    if not folder:
        return ""
    sig = backup_signature(c)
    if sig == db.get_config(c, "checklist_backup_sig", ""):
        return ""
    if sig.startswith("0::0:"):
        return ""                               # нечего сохранять
    try:
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(
            folder, f"checklisty-{today or date.today().isoformat()}.zip")
        export_backup(c, path)
        old = sorted(f for f in os.listdir(folder)
                     if f.startswith("checklisty-") and f.endswith(".zip"))
        for f in old[:-BACKUP_KEEP]:
            os.remove(os.path.join(folder, f))
    except OSError:
        return ""
    db.set_config(c, "checklist_backup_sig", sig)
    return path


# ────────────────────────────── wydruk (HTML) ──────────────────────
PRINT_CSS = """
*{box-sizing:border-box}
body{font:13px/1.35 Arial,Helvetica,sans-serif;color:#111;margin:24px}
h1{font-size:19px;margin:0 0 2px;text-align:center;letter-spacing:.5px}
.meta{display:flex;flex-wrap:wrap;gap:6px 22px;margin:10px 0 14px;
  border:1px solid #111;padding:8px 10px}
.meta b{font-weight:700}
.desc{text-align:center;color:#444;margin-bottom:6px}
table{width:100%;border-collapse:collapse;margin:0 0 14px;page-break-inside:auto}
tr{page-break-inside:avoid}
th,td{border:1px solid #111;padding:4px 6px;vertical-align:middle}
th{background:#eee;font-size:11.5px}
caption{caption-side:top;font-weight:700;border:1px solid #111;border-bottom:0;
  padding:4px;background:#ddd}
td.c{text-align:center;white-space:nowrap}
td.bad{background:#fde2e2;font-weight:700}
td.off{background:repeating-linear-gradient(135deg,#fff,#fff 4px,#ccc 4px,#ccc 5px)}
.sign{margin:-8px 0 14px;border:1px solid #111;border-top:0;padding:5px 8px}
.foot{color:#666;font-size:11px;margin-top:18px;text-align:center}
.warn{border:2px solid #b91c1c;color:#b91c1c;padding:6px 10px;margin:0 0 12px;
  font-weight:700}
@media print{body{margin:10mm}.noprint{display:none}}
"""


def entry_html(entry: dict, kitchen="") -> str:
    """Самодостаточная страница для печати / «Zapisz jako PDF» в браузере.

    Без внешних файлов и шрифтов: откроется где угодно и через много лет.
    """
    esc = html.escape
    tpl = entry["template"]
    values = entry.get("values") or {}
    res = evaluate(entry)
    out = ["<!doctype html><html lang='pl'><head><meta charset='utf-8'>",
           f"<title>{esc(tpl['name'])} — {esc(entry.get('entry_date') or '')}"
           f"</title><style>{PRINT_CSS}</style></head><body>",
           f"<h1>{esc(tpl['name'])}</h1>"]
    if tpl.get("description"):
        out.append(f"<div class='desc'>{esc(tpl['description'])}</div>")
    meta = [("Data", entry.get("entry_date") or ""),
            ("Wypełnił(a)", entry.get("person") or "—")]
    if kitchen:
        meta.append(("Kuchnia", kitchen))
    hv = entry.get("header") or {}
    for f in tpl["header"]:
        meta.append((f["label"], format_value(f, hv.get(f["id"])) or "—"))
    out.append("<div class='meta'>" + "".join(
        f"<span><b>{esc(k)}:</b> {esc(str(v))}</span>" for k, v in meta)
        + "</div>")
    if res["issues"]:
        out.append(f"<div class='warn'>Odchylenia: {res['issues']} "
                   "(zaznaczone na czerwono)</div>")

    for s in tpl["sections"]:
        rows = s["rows"]
        if s["mode"] == "free":
            rows = [r for r in rows if r["label"] or any(
                not is_empty(values.get(cell_key(s["id"], r["id"], col["id"])))
                for col in s["columns"])]
        out.append("<table>")
        if s["title"]:
            out.append(f"<caption>{esc(s['title'])}</caption>")
        out.append("<tr><th style='text-align:left'>"
                   f"{esc(s['label_title'] or 'Pozycja')}</th>" + "".join(
                       f"<th>{esc(col['label'])}</th>" for col in s["columns"])
                   + "</tr>")
        for r in rows:
            lim = limits_text(r["min"], r["max"])
            out.append(f"<tr><td>{esc(r['label'])}"
                       + (f" <small>({esc(lim)})</small>" if lim else "")
                       + "</td>")
            for col in s["columns"]:
                key = cell_key(s["id"], r["id"], col["id"])
                if col["id"] in r["off"]:
                    out.append("<td class='off'></td>")
                    continue
                cls = "c bad" if key in res["bad"] else "c"
                if col["type"] in ("text", "sign", "choice"):
                    cls = cls.replace("c", "", 1).strip()
                out.append(f"<td class='{cls}'>"
                           f"{esc(format_value(col, values.get(key)))}</td>")
            out.append("</tr>")
        if not rows:
            out.append(f"<tr><td colspan='{len(s['columns']) + 1}'>—</td></tr>")
        out.append("</table>")
        if s["sign"]:
            sig = values.get(cell_key(s["id"], SIGN_ROW, "_")) or "—"
            out.append(f"<div class='sign'><b>Podpis:</b> {esc(str(sig))}</div>")

    fv = entry.get("footer") or {}
    if tpl["footer"]:
        out.append("<div class='meta'>" + "".join(
            f"<span><b>{esc(f['label'])}:</b> "
            f"{esc(format_value(f, fv.get(f['id'])) or '—')}</span>"
            for f in tpl["footer"]) + "</div>")
    sent = (entry.get("submitted_at") or entry.get("received_at") or "")[:16]
    out.append(f"<div class='foot'>Straty Kuchnia · checklisty · wysłano "
               f"{esc(sent.replace('T', ' '))} UTC · wersja szablonu "
               f"{tpl.get('version', 1)} · id {esc(entry.get('id') or '')}"
               "</div></body></html>")
    return "".join(out)


# `sqlite3` нужен только ради типа исключения у вызывающих
IntegrityError = sqlite3.IntegrityError
