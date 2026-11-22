"""Связь менеджерского приложения с сервером зашлушений.

Тонкий слой поверх httpx — ровно четыре операции. Вынесен отдельно от
интерфейса, чтобы автопроверки могли гонять его с подменённым транспортом,
не поднимая окно.

Все функции возвращают `(ok, данные_или_текст_ошибки)` и никогда не бросают
исключений наружу: пропавший интернет — обычное состояние, а не авария, и
приложение должно спокойно показать «Serwer niedostępny», а не упасть.
"""

import httpx

TIMEOUT = 8.0


def _base(url: str) -> str:
    return (url or "").strip().rstrip("/")


async def _request(method, url, token, path, **kw):
    base = _base(url)
    if not base:
        return False, "Nie ustawiono adresu serwera"
    if not token:
        return False, "Nie ustawiono tokenu"
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            r = await client.request(method, f"{base}{path}",
                                     params={"t": token}, **kw)
    except httpx.HTTPError:
        return False, "Serwer niedostępny"
    except Exception:                                    # noqa: BLE001
        return False, "Błąd połączenia"
    if r.status_code == 403:
        return False, "Zły token — sprawdź Ustawienia → Serwer"
    if r.status_code == 404:
        return False, "Nie znaleziono (zły adres serwera?)"
    if r.status_code >= 400:
        try:
            return False, r.json().get("error") or f"Błąd {r.status_code}"
        except ValueError:
            return False, f"Błąd {r.status_code}"
    try:
        return True, r.json()
    except ValueError:
        return False, "Serwer odpowiedział czymś dziwnym"


async def whoami(url, token):
    """Проверка связи: имя кухни и сколько зашлушений ждёт."""
    return await _request("GET", url, token, "/api/whoami")


async def fetch_reports(url, token):
    """Очередь зашлушений. Отдаёт список словарей."""
    ok, data = await _request("GET", url, token, "/api/reports")
    if not ok:
        return False, data
    return True, data.get("reports", [])


async def delete_report(url, token, report_id):
    """Убрать зашлушение с сервера — после принятия или отклонения."""
    ok, data = await _request("DELETE", url, token, f"/api/reports/{report_id}")
    if not ok and data.startswith("Nie znaleziono"):
        # Уже удалено (например, вторым нажатием) — для нас это успех:
        # цель «на сервере его нет» достигнута.
        return True, {}
    return ok, data


async def push_catalog(url, token, catalog: dict):
    """Выгрузить каталог позиций и причин — форма покажет его поварам."""
    return await _request("POST", url, token, "/api/catalog", json=catalog)
