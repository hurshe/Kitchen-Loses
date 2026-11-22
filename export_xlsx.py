"""Straty Kuchnia — выгрузка в Excel.

Четыре листа:
    Pozycje   — итог по позициям за период (то, что переписывают в свою систему)
    Miesiące  — матрица позиция × месяц, видно динамику
    Dziennik  — все записи подряд
    Powody    — сколько записей на каждую причину

Количество везде лежит **числом**, единица — отдельной колонкой. Иначе
отчёт нельзя просуммировать в самом Excel, а именно этого от него и ждут.
"""

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

import db

HEAD_FILL = PatternFill("solid", fgColor="1F2937")
HEAD_FONT = Font(bold=True, color="FFFFFF", size=11)
TITLE_FONT = Font(bold=True, size=14)
TOTAL_FONT = Font(bold=True)
THIN = Side(style="thin", color="D1D5DB")
BORDER = Border(bottom=THIN)
NUM_FMT = "#,##0.###"


def _head(ws, row, titles, widths):
    for col, (title, width) in enumerate(zip(titles, widths), start=1):
        cell = ws.cell(row=row, column=col, value=title)
        cell.fill = HEAD_FILL
        cell.font = HEAD_FONT
        cell.alignment = Alignment(horizontal="center", vertical="center",
                                   wrap_text=True)
        ws.column_dimensions[get_column_letter(col)].width = width
    ws.row_dimensions[row].height = 26


def _title(ws, text, subtitle=""):
    ws["A1"] = text
    ws["A1"].font = TITLE_FONT
    if subtitle:
        ws["A2"] = subtitle
        ws["A2"].font = Font(size=10, color="6B7280")


def _sheet_items(wb, c, month, period_label):
    ws = wb.active
    ws.title = "Pozycje"
    _title(ws, "Straty — podsumowanie pozycji", period_label)
    _head(ws, 4, ["Kategoria", "Pozycja", "Ilość", "Jednostka", "Wpisów"],
          [22, 38, 12, 12, 10])
    rows = db.by_item(c, month=month or None)
    rows.sort(key=lambda r: ((r["category"] or "").lower(), r["name"].lower()))
    r = 5
    for it in rows:
        ws.cell(row=r, column=1, value=it["category"] or "—")
        ws.cell(row=r, column=2, value=it["name"])
        q = ws.cell(row=r, column=3, value=it["qty"])
        q.number_format = NUM_FMT
        ws.cell(row=r, column=4, value=it["unit"])
        ws.cell(row=r, column=5, value=it["entries"])
        for col in range(1, 6):
            ws.cell(row=r, column=col).border = BORDER
        r += 1
    if rows:
        ws.auto_filter.ref = f"A4:E{r - 1}"
    else:
        ws.cell(row=5, column=1, value="Brak strat w tym okresie")
    ws.freeze_panes = "A5"
    return len(rows)


def _sheet_months(wb, c, date_from, date_to):
    ws = wb.create_sheet("Miesiące")
    _title(ws, "Straty wg miesięcy", "Ilość w jednostce pozycji")
    data = db.by_month(c, date_from=date_from, date_to=date_to)
    months = data["months"]
    titles = ["Pozycja", "Jednostka"] + [db.month_label_pl(m) for m in months] \
        + ["Razem"]
    widths = [38, 12] + [14] * len(months) + [14]
    _head(ws, 4, titles, widths)
    r = 5
    for it in data["items"]:
        ws.cell(row=r, column=1, value=it["name"])
        ws.cell(row=r, column=2, value=it["unit"])
        for n, m in enumerate(months):
            v = it["months_qty"].get(m)
            if v is not None:
                cell = ws.cell(row=r, column=3 + n, value=v)
                cell.number_format = NUM_FMT
        total = ws.cell(row=r, column=3 + len(months), value=it["total"])
        total.number_format = NUM_FMT
        total.font = TOTAL_FONT
        for col in range(1, 4 + len(months)):
            ws.cell(row=r, column=col).border = BORDER
        r += 1
    if not data["items"]:
        ws.cell(row=5, column=1, value="Brak danych")
    ws.freeze_panes = "C5"


def _sheet_journal(wb, c, month):
    ws = wb.create_sheet("Dziennik")
    _title(ws, "Dziennik strat")
    _head(ws, 4, ["Data", "Kategoria", "Pozycja", "Ilość", "Jedn.", "Powód",
                  "Osoba", "Skąd", "Uwagi"],
          [12, 20, 34, 10, 8, 22, 18, 12, 30])
    r = 5
    for e in db.journal(c, month=month or None):
        ws.cell(row=r, column=1, value=e["loss_date"])
        ws.cell(row=r, column=2, value=e["category"] or "—")
        ws.cell(row=r, column=3, value=e["item_name"])
        q = ws.cell(row=r, column=4, value=round(e["qty"], 3))
        q.number_format = NUM_FMT
        ws.cell(row=r, column=5, value=e["unit"])
        ws.cell(row=r, column=6, value=e["reason"] or "")
        ws.cell(row=r, column=7, value=e["person"] or "")
        ws.cell(row=r, column=8,
                value="telefon" if e["source"] == "qr" else "ręcznie")
        ws.cell(row=r, column=9, value=e["note"] or "")
        for col in range(1, 10):
            ws.cell(row=r, column=col).border = BORDER
        r += 1
    if r > 5:
        ws.auto_filter.ref = f"A4:I{r - 1}"
    else:
        ws.cell(row=5, column=1, value="Brak wpisów")
    ws.freeze_panes = "A5"


def _sheet_reasons(wb, c, month):
    ws = wb.create_sheet("Powody")
    _title(ws, "Straty wg powodów",
           "Liczba wpisów — ilości nie sumujemy, bo pozycje mają różne jednostki")
    _head(ws, 4, ["Powód", "Wpisów"], [30, 12])
    r = 5
    for row in db.by_reason(c, month=month or None):
        ws.cell(row=r, column=1, value=row["reason"])
        ws.cell(row=r, column=2, value=row["entries"])
        for col in (1, 2):
            ws.cell(row=r, column=col).border = BORDER
        r += 1
    if r == 5:
        ws.cell(row=5, column=1, value="Brak wpisów")
    ws.freeze_panes = "A5"


def build(c, kitchen_name, month, target):
    """Собирает книгу. `month` пустой = за весь период.

    `target` — путь или файлоподобный объект.
    """
    kitchen = kitchen_name or "Kuchnia"
    if month:
        date_from, date_to = db.month_bounds(month)
        period = f"{kitchen} · {db.month_label_pl(month)}"
    else:
        date_from = date_to = None
        period = f"{kitchen} · cały okres"

    wb = Workbook()
    _sheet_items(wb, c, month, period)
    _sheet_months(wb, c, date_from, date_to)
    _sheet_journal(wb, c, month)
    _sheet_reasons(wb, c, month)
    wb.save(target)
    return target
