"""Стартовые шаблоны чек-листов — перенесены с бумажных листов кухни.

Тексты пунктов набраны так, как напечатаны на листах (вместе с опечатками
оригинала) — править их удобнее в конструкторе, чем здесь.

Что в этих шаблонах — толкование, а не текст с листа:
  * допуски температур. На листе стоит только число в скобках: «(-18°C)»,
    «(5°C)», «(75°C)». Здесь оно записано как граница: морозилки — не выше
    −18, холодильники — не выше 5, горячее — не ниже числа. Меняется в
    конструкторе, в строке (поля «min» / «max»);
  * в «Daily Cleaning Checklist» галочки AM / PM стоят у каждого пункта.
    На бумаге это две общие клетки на секцию;
  * журнал «Re-Heating»: названия блюд на фото не читаются, поэтому строки
    повар вписывает сам. Колонка времени на фото размыта — названа «Time».
"""

CHECK = [{"id": "ok", "label": "CHECKED", "type": "check"}]


def _rows(*labels):
    return [{"label": x} for x in labels]


WEEKLY = {
    "name": "Weekly Cleaning Schedule",
    "period": "weekly",
    "sections": [
        {"title": "Magazyny / Storage", "sign": True, "columns": CHECK,
         "rows": _rows(
             "Chłodnia: wentylacja, lampy, ściany / Walk-in-cooler: "
             "ventilation, lights, walls",
             "Mroźnia: wentylacja, lampy, ściany / Walk-in-freezer: "
             "ventilation, lights, walls",
             "Mycie palet, i podłóg/ Cleaning palette, flors",
             "FIFO and FEFO",
             "Magazyn suchy / Dry storage",
             "Magazyn chemiczny / Chemical storage")},
        {"title": "Kuchnia / Kitchen", "sign": True, "columns": CHECK,
         "rows": _rows(
             "Wszystkie lodówki wewnątrz i zewnątrz / All Fridges inside "
             "and outside",
             "Wszystkie zamrażarkii wewnątrz i zewnątrz / All freezers "
             "inside and outside",
             "Odkurzyć wentylatory w lodówkach / Vacum fans in refrigerators",
             "Umyć wszystkie nóżki od sprzętu i kółka / Clean all equipment "
             "legs and wheels",
             "Umyć umywalki i ich odpływy / Clean sinks and their drains",
             "Kratki ściekowe / Drains",
             "Kosze na śmieci / Bins",
             "Piec / Oven",
             "Salamandry / Salamanders",
             "Okapy i filtry / Ventilation Hood and Filters",
             "Światła pod okapem / Lights under the hood",
             "Wirówka / Creamer",
             "Osmolone garnki / Dirty pots",
             "Frytownice / Deep fryers",
             "Mikrofalówki / Microwave",
             "Regały na linie i prepie / Shelfs on Line and Prep")},
        {"title": "Zmywak / Dish", "sign": True, "columns": CHECK,
         "rows": _rows(
             "Zmywarka w środku i na zewnątrz / Dishwasher inside and outside",
             "Włączyć procedurę czyszczenie uzdatniaczy wody/ Turn on "
             "cleaning water conditioner",
             "Regały na sprzęt / Shelves for equipment",
             "Wyskrobane deski do krojenia / Scrape cutting boards",
             "Odkamienianie zmywarek / Descaling dishwashers",
             "Szafa na talerze / Plate Shelf",
             "Wyczyścić bemary i blachy z naklejek / Clean from stickers "
             "all containers",
             "Mycie ścian i glazury / Clean all wals",
             "Umyć wszystkie nóżki od sprzętu i regałów / Clean all "
             "equipment and shelfs legs",
             "Kosz na śmieci / Bins")},
    ],
}

DAILY_COLS = [{"id": "com", "label": "Comments", "type": "text"},
              {"id": "am", "label": "Morning AM", "type": "check"},
              {"id": "pm", "label": "Afternoon PM", "type": "check"}]
FLOOR = ("Podłoga zmieciona i pozmywana, ściany wyczyszczone / Floor swept "
         "and mopped, walls clean")
SINKS = ("Umyte zlewy,umywalki do rąk, blaty,półki / Clean sinks, worktops, "
         "shelfs")
DRAINS = ("Kratki ściekowe wyczyszczone zalane gorącą wodą/ Drains clean and "
          "flooded with hot water")
BINS = "Opróżnienie koszów na śmieci / Refuse bins emptied and cleaned"
MARIES = ("Czyste bemary z żywnością pozostawione w lodówkach wg FIFO / "
          "Maries clean and placed on FIFO rules")
FOOD = ("Cała żywność zabezpieczona, dobrze oznaczona Labelem na właściwym "
        "miejscu / All food stored, covered and labeled properly")

DAILY = {
    "name": "Daily Cleaning Checklist",
    "period": "daily",
    "sections": [
        {"title": "STORAGE", "columns": DAILY_COLS, "rows": _rows(
            FLOOR,
            "Selekcja warzyw i owoców / fruit and vegetables selection",
            "FIFO i FEFO",
            "Czyste regały i półki / Clean shelfs",
            "Sprawdzenie oznaczenia produktów w mroźni i chłodni / Checking "
            "Labels and datedots in fridge and freezer")},
        {"title": "PREP", "columns": DAILY_COLS, "rows": _rows(
            FLOOR, DRAINS, SINKS,
            "Umyte lodówki i szokarka w środku na zewnątrz, uszczelki, "
            "zawiasy / Clean fridges, shocker, inside outside, gaskets, hinge",
            "Przybory kuchenne umyte i pozostawione na swoich miejscach / "
            "Kitchen tools clean and on their places",
            BINS, MARIES, FOOD)},
        {"title": "LINE", "columns": DAILY_COLS, "rows": _rows(
            FLOOR, SINKS,
            "Umyte lodówki w środku na zewnątrz, uszczelki, zawiasy / Clean "
            "fridges, inside outside, gaskets, hinge",
            "Umyty piec, salamandry w środku i na zewnątrz",
            "Umyty Grill ryflowany i flat",
            "Zdjęte okapy włożone do kwasu",
            "Umyte stanowiska frytur, frytury przefiltrowane / Cleaned, "
            "drained and filtered fryers",
            BINS,
            "Wszystkie urządzenia wyłączone / All equipment turned off",
            "Zakręcenie zaworów: gril, kuchenka, frytura / turned off cutut "
            "grill, cooker, fryer",
            MARIES, FOOD)},
        {"title": "DISH", "columns": DAILY_COLS, "rows": _rows(
            FLOOR,
            "Wyczyszczone ściany za zmywarkami / Clean walls behing "
            "dishwasher",
            DRAINS, SINKS, BINS,
            "Czystae zmywarki, regały na sprzęt, pojemnik od okapu",
            "Wyczyszczone bemary z naklejek / marries clean and free of "
            "stickers",
            "Czysty pojemnik na sól / Clean salt container",
            "Wszystkie talerze, bemary i sprzęt kuchenny ustawiony na swoje "
            "miejsce / All plates marries and kitchen equipment placed on "
            "kitchens",
            "Deski do krojenia poprawnie ułożone kolorami i nie stykające "
            "się / place cutting boards correctly, can't touch each other")},
    ],
    "footer": [
        {"label": "Kitchen Supervisor on Duty sign — AM", "type": "sign"},
        {"label": "Kitchen Supervisor on Duty sign — PM", "type": "sign"},
    ],
}

COOLING = {
    "name": "Cooling / Re-Heating Temperature Log",
    "period": "daily",
    "sections": [
        {"title": "Cooling Temperature Log", "mode": "free", "free_rows": 3,
         "label_title": "Food Item",
         "columns": [
             {"label": "Temperature at start of cooling", "type": "temp"},
             {"label": "Time - Start of cooling or Blast Chiller",
              "type": "time"},
             {"label": "Temperature after 2 hours (21°C or below)",
              "type": "temp", "max": 21},
             {"label": "Temperature after 3 hours (if required)",
              "type": "temp"},
             {"label": "Temperature after 4 hours (if required)",
              "type": "temp"},
             {"label": "Time - Out of Blast Chiller", "type": "time"},
             {"label": "Temperature (°C) - Out of Blast Chiller",
              "type": "temp"},
             {"label": "Storage / Disposal / Corrective Action",
              "type": "text"},
             {"label": "Signature", "type": "sign"},
         ]},
        {"title": "Re-Heating Temperature Log", "mode": "free",
         "free_rows": 3, "label_title": "Food Item",
         "columns": [
             {"label": "Time", "type": "time"},
             {"label": "Reheating Method", "type": "choice",
              "options": ["POT", "MICRO"]},
             {"label": "Core Temperature (must reach 82°C)", "type": "temp",
              "min": 82},
             {"label": "Signature", "type": "sign"},
         ]},
    ],
}

TEMP_COLS = [
    {"id": "food", "label": "Food Item", "type": "text"},
    {"id": "open", "label": "OPENING Fridge", "type": "temp"},
    {"id": "i12", "label": "12:00 Item", "type": "temp"},
    {"id": "f12", "label": "12:00 Fridge", "type": "temp"},
    {"id": "i16", "label": "16:00 Item", "type": "temp"},
    {"id": "f16", "label": "16:00 Fridge", "type": "temp"},
    {"id": "i20", "label": "20:00 Item", "type": "temp"},
    {"id": "f20", "label": "20:00 Fridge", "type": "temp"},
    {"id": "close", "label": "CLOSING Fridge", "type": "temp"},
    {"id": "act", "label": "CORRECTIVE ACTION", "type": "text"},
]
# У нижних строк листа «Opening» и «Closing» перечёркнуты, а на каждое
# время — одна клетка вместо пары Item / Fridge.
NO_FRIDGE = ["open", "f12", "f16", "f20", "close"]


def _cold(label, limit=5):
    return {"label": label, "max": limit}


def _hot(label, limit=75):
    return {"label": label, "min": limit, "off": NO_FRIDGE}


TEMPERATURE = {
    "name": "Temperature Checklist",
    "period": "daily",
    "sections": [
        {"title": "TEMPERATURES", "label_title": "Station",
         "columns": TEMP_COLS,
         "rows": [
             _cold("Walk-In Freezer (-18°C)", -18),
             _cold("Walk-In Cooler (5°C)"),
             _cold("Cooler PREP Meat (5°C)"),
             _cold("Cooler PREP Down (5°C)"),
             _cold("Cooler PREP Standing 1 (5°C)"),
             _cold("Cooler PREP Standing 2 (5°C)"),
             _cold("Cooler PREP Standing 3 (5°C)"),
             _cold("Cooler EXPO Down 1 (5°C)"),
             _cold("Cooler EXPO Down 2 (5°C)"),
             _cold("Freezer EXPO Down (-18°C)", -18),
             _cold("Cooler EXPO Nadstawka (5°C)"),
             _cold("Cooler SALAD (5°C)"),
             _cold("Cooler SALAD Nadstawka (5°C)"),
             _cold("Cooler FRY (5°C)"),
             _cold("Cooler GRILL Nadstawka (5°C)"),
             _cold("Cooler GRILL (5°C)"),
             _cold("Cooler GRILL Down (5°C)"),
             _cold("Cooler Flat Top Down (5°C)"),
             _cold("Freezer BACKUP (-18°C)", -18),
             _cold("Cooler BACKUP (5°C)"),
             _hot("Hot Hold 1 (75°C)"),
             _hot("Hot Hold 2 (75°C)"),
             _hot("Delivery Cooking hot (75°C)"),
             {"label": "Delivery Cooking cold (5°C)", "max": 5,
              "off": NO_FRIDGE},
             _hot("Internal Cooking Chicken (75°C)"),
             _hot("Internal Cooking Beef W (75°C)"),
             _hot("Internal Cooking Fish (64°C)", 64),
             {"label": "Dish Washer (64°C washing / 82°C rinsing)",
              "off": NO_FRIDGE},
         ]},
        {"title": "FRYER", "label_title": "Fryer",
         "columns": [{"label": "TEMP", "type": "temp"},
                     {"label": "TPM %", "type": "number", "unit": "%"},
                     {"label": "NOTES", "type": "text"}],
         "rows": _rows("FRY #1 - 165°C (Breading)", "FRY #2 - 177°C (Wings)",
                       "FRY #3 - 177°C (Fries)(GF)", "FRY #4 - 177°C (Fish)")},
        {"title": "GRILL", "label_title": "Grill",
         "columns": [{"label": "AM", "type": "temp"},
                     {"label": "PM", "type": "temp"},
                     {"label": "NOTES", "type": "text"}],
         "rows": _rows("Grill Grooved 1/2 - 288°C", "Grill Grooved 2/2 - 288°C",
                       "Flatop Grill - 204°C")},
    ],
}

PRESETS = [DAILY, WEEKLY, TEMPERATURE, COOLING]
