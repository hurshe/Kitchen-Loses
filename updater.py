"""Автообновление программы менеджера — та же схема, что в Beer Count.

Как это работает:
    1. Разработчик ставит тег vX.Y.Z и пушит его на GitHub.
    2. GitHub Actions (.github/workflows/release.yml) гоняет проверки,
       собирает exe и установщик и публикует их как Release в репозитории
       UPDATE_REPO.
    3. Программа при запуске спрашивает GitHub, какая версия последняя.
       Если новее своей — в боковой панели появляется «Aktualizacja X.Y.Z».
    4. Клик → установщик скачивается, сверяется с SHA256SUMS.txt и ставится
       тихо (/VERYSILENT); программа закрывается и после установки
       запускается сама. База kuchnia.db остаётся на месте — установщик её
       не трогает (см. Excludes в setup.iss).

Здесь только логика без интерфейса — её проверяет check_updater.py.
"""

import hashlib
import os
import tempfile

UPDATE_REPO = "hurshe/Kitchen-Loses"     # публичное репо, там же релизы
INSTALLER_PREFIX = "StratyKuchnia-Setup-"
SUMS_NAME = "SHA256SUMS.txt"
API_URL = f"https://api.github.com/repos/{UPDATE_REPO}/releases/latest"


def ver_tuple(v: str):
    """'v1.2.10' → (1, 2, 10). Мусор → (0,), чтобы никогда не считаться новее."""
    try:
        return tuple(int(x) for x in str(v).strip().lstrip("vV").split("."))
    except ValueError:
        return (0,)


def parse_release(rel: dict, current: str):
    """Ответ GitHub /releases/latest → описание обновления или None.

    None — если версия не новее текущей, если это черновик/пробная версия
    или если в релизе нет установщика. Возвращает словарь
    {version, installer_url, installer_name, sums_url}.
    """
    if not isinstance(rel, dict):
        return None
    if rel.get("draft") or rel.get("prerelease"):
        return None
    tag = str(rel.get("tag_name") or "")
    if "-" in tag:                       # v1.2.0-rc.1 — не для кухни
        return None
    if ver_tuple(tag) <= ver_tuple(current):
        return None
    installer = sums = None
    for a in rel.get("assets") or []:
        name = str(a.get("name") or "")
        url = a.get("browser_download_url") or ""
        if name.startswith(INSTALLER_PREFIX) and name.lower().endswith(".exe"):
            installer = (name, url)
        elif name == SUMS_NAME:
            sums = url
    if not installer or not installer[1]:
        return None
    return {"version": tag.lstrip("vV"), "installer_name": installer[0],
            "installer_url": installer[1], "sums_url": sums}


def expected_hash(sums_text: str, file_name: str):
    """Строка 'HASH  имя' из SHA256SUMS.txt → hash в нижнем регистре."""
    for line in (sums_text or "").splitlines():
        parts = line.strip().split()
        if len(parts) >= 2 and parts[-1].lstrip("*") == file_name:
            return parts[0].lower()
    return None


def sha256_of(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


async def check_latest(current: str, client=None):
    """Спросить GitHub. Любая ошибка сети → None: программа работает дальше."""
    import httpx
    own = client is None
    client = client or httpx.AsyncClient(timeout=8, follow_redirects=True)
    try:
        r = await client.get(API_URL,
                             headers={"Accept": "application/vnd.github+json"})
        if r.status_code != 200:
            return None
        return parse_release(r.json(), current)
    except Exception:
        return None
    finally:
        if own:
            await client.aclose()


async def download(update: dict, progress=None, client=None) -> str:
    """Скачать установщик во временную папку и сверить SHA256.

    progress(done_bytes, total_bytes) вызывается по ходу. Возвращает путь
    к файлу. Если сумма не сходится — файл удаляется и летит ValueError:
    битый установщик ставить нельзя.
    """
    import httpx
    own = client is None
    client = client or httpx.AsyncClient(timeout=None, follow_redirects=True)
    dest = os.path.join(tempfile.gettempdir(), update["installer_name"])
    try:
        want = None
        if update.get("sums_url"):
            r = await client.get(update["sums_url"])
            r.raise_for_status()
            want = expected_hash(r.text, update["installer_name"])
        async with client.stream("GET", update["installer_url"]) as r:
            r.raise_for_status()
            total = int(r.headers.get("content-length", 0) or 0)
            done = 0
            with open(dest, "wb") as f:
                async for chunk in r.aiter_bytes(256 * 1024):
                    f.write(chunk)
                    done += len(chunk)
                    if progress:
                        progress(done, total)
    finally:
        if own:
            await client.aclose()
    if want and sha256_of(dest) != want:
        try:
            os.remove(dest)
        except OSError:
            pass
        raise ValueError("Suma kontrolna pobranego pliku się nie zgadza.")
    return dest


def install_command(path: str):
    """Команда тихой установки. Установщик сам закроет программу, если она
    ещё открыта, и запустит её после установки (см. [Run] в setup.iss)."""
    return [path, "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART",
            "/FORCECLOSEAPPLICATIONS"]
