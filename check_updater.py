"""Автопроверки автообновления (updater.py + кнопка в программе).

Сеть не нужна: GitHub подменяется httpx.MockTransport.

    python check_updater.py
"""

import asyncio
import hashlib
import os
import re
import sys
import tempfile

for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        try:
            _s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:                                  # noqa: BLE001
            pass

TMP = tempfile.mkdtemp(prefix="kuchnia-upd-")
os.environ["KUCHNIA_DB"] = os.path.join(TMP, "upd.db")

import httpx               # noqa: E402
import main_kuchnia as mk  # noqa: E402
import updater as up       # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
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


def release(tag, assets=None, **kw):
    if assets is None:
        assets = [f"StratyKuchnia-Setup-{tag.lstrip('v')}.exe", "SHA256SUMS.txt"]
    return dict(tag_name=tag, draft=False, prerelease=False,
                assets=[{"name": n, "browser_download_url": f"https://x/{n}"}
                        for n in assets], **kw)


# ─────────────────────────── версии ───────────────────────────
section("porównanie wersji")
ok(up.ver_tuple("v1.2.10") == (1, 2, 10), "v1.2.10")
ok(up.ver_tuple("1.10.0") > up.ver_tuple("1.9.9"), "1.10 > 1.9 (liczby, nie tekst)")
ok(up.ver_tuple("abc") == (0,), "śmieci → (0,)")
ok(up.ver_tuple("") == (0,), "pusto → (0,)")

section("odczyt wydania z GitHub")
u = up.parse_release(release("v1.2.0"), "1.1.0")
ok(u and u["version"] == "1.2.0", "nowsza wersja jest wykryta")
ok(u and u["installer_url"].endswith("StratyKuchnia-Setup-1.2.0.exe"), "link do instalatora")
ok(u and u["sums_url"].endswith("SHA256SUMS.txt"), "link do sum kontrolnych")
ok(up.parse_release(release("v1.1.0"), "1.1.0") is None, "ta sama wersja → nic")
ok(up.parse_release(release("v1.0.5"), "1.1.0") is None, "starsza wersja → nic")
ok(up.parse_release(dict(release("v2.0.0"), prerelease=True), "1.1.0") is None,
   "wydanie próbne → nic")
ok(up.parse_release(dict(release("v2.0.0"), draft=True), "1.1.0") is None, "szkic → nic")
ok(up.parse_release(release("v2.0.0-rc.1"), "1.1.0") is None, "tag z „-” → nic")
ok(up.parse_release(release("v2.0.0", ["notes.txt"]), "1.1.0") is None,
   "bez instalatora → nic")
ok(up.parse_release(release("v2.0.0", ["BeerCount-Setup-2.0.0.exe"]), "1.1.0") is None,
   "obcy instalator nie jest brany")
u = up.parse_release(release("v2.0.0", ["StratyKuchnia-Setup-2.0.0.exe"]), "1.1.0")
ok(u and u["sums_url"] is None, "bez SHA256SUMS też działa")
ok(up.parse_release(None, "1.1.0") is None, "zła odpowiedź → nic")

section("sumy kontrolne")
sums = "ABCDEF  StratyKuchnia-Setup-1.2.0.exe\n123  inny.zip\n"
ok(up.expected_hash(sums, "StratyKuchnia-Setup-1.2.0.exe") == "abcdef", "hash z pliku sum")
ok(up.expected_hash(sums, "brak.exe") is None, "brak pliku w sumach → None")
ok(up.expected_hash("", "x") is None, "puste sumy → None")


# ─────────────────────── сеть (подменённая) ───────────────────────
PAYLOAD = b"MZ" + os.urandom(700_000)
GOOD = hashlib.sha256(PAYLOAD).hexdigest()


def transport(latest=None, status=200, sums_hash=GOOD, boom=False):
    def handler(req):
        if boom:
            raise httpx.ConnectError("offline")
        url = str(req.url)
        if url == up.API_URL:
            return httpx.Response(status, json=latest or {})
        if url.endswith("SHA256SUMS.txt"):
            return httpx.Response(
                200, text=f"{sums_hash.upper()}  StratyKuchnia-Setup-9.9.9.exe\n")
        if url.endswith(".exe"):
            return httpx.Response(200, content=PAYLOAD,
                                  headers={"content-length": str(len(PAYLOAD))})
        return httpx.Response(404)
    return httpx.MockTransport(handler)


def run(coro):
    return asyncio.run(coro)


async def latest_with(**kw):
    async with httpx.AsyncClient(transport=transport(**kw)) as c:
        return await up.check_latest("1.1.0", client=c)


section("pytanie do GitHub")
r = run(latest_with(latest=release("v9.9.9")))
ok(r and r["version"] == "9.9.9", "nowa wersja z API")
ok(run(latest_with(latest=release("v1.1.0"))) is None, "aktualna → nic")
ok(run(latest_with(status=404)) is None, "404 (brak wydań) → nic")
ok(run(latest_with(status=403)) is None, "limit API → nic")
ok(run(latest_with(boom=True)) is None, "brak internetu → nic, bez wyjątku")

section("pobieranie i sprawdzenie pliku")
upd = up.parse_release(release("v9.9.9"), "1.1.0")


async def dl(**kw):
    seen = []
    async with httpx.AsyncClient(transport=transport(**kw)) as c:
        path = await up.download(upd, lambda d, t: seen.append((d, t)), client=c)
    return path, seen

path, seen = run(dl())
ok(os.path.isfile(path), "instalator zapisany")
ok(os.path.getsize(path) == len(PAYLOAD), "cały plik")
ok(seen and seen[-1] == (len(PAYLOAD), len(PAYLOAD)), "postęp dochodzi do 100%")
ok(up.sha256_of(path) == GOOD, "suma się zgadza")
os.remove(path)

try:
    run(dl(sums_hash="00" * 32))
    ok(False, "zła suma powinna przerwać")
except ValueError:
    ok(True, "zła suma → błąd")
ok(not os.path.exists(os.path.join(tempfile.gettempdir(), upd["installer_name"])),
   "uszkodzony plik usunięty")

cmd = up.install_command("C:/t/setup.exe")
ok("/VERYSILENT" in cmd and "/FORCECLOSEAPPLICATIONS" in cmd, "cicha instalacja")


# ─────────────────────────── программа ───────────────────────────
class FakeWindow:
    def __init__(self):
        self.min_width = self.min_height = self.width = self.height = 0
        self.icon = None


class FakePage:
    def __init__(self):
        self.window = FakeWindow()
        self.services, self.controls, self.dialogs, self.tasks = [], [], [], []
        self.theme = self.dark_theme = self.theme_mode = None
        self.title, self.padding = "", None

    def add(self, *c):
        self.controls.extend(c)

    def update(self):
        pass

    def show_dialog(self, d):
        self.dialogs.append(d)

    def pop_dialog(self):
        if self.dialogs:
            self.dialogs.pop()

    def run_task(self, fn, *args):
        self.tasks.append((getattr(fn, "__name__", str(fn)), args))


section("przycisk w programie")
page = FakePage()
app = mk.KuchniaApp(page)
ok(not app.update_btn.visible, "na starcie przycisk ukryty")
ok("check_updates" not in [t[0] for t in page.tasks],
   "ze źródeł nie pytamy GitHub przy starcie")

orig = up.check_latest


async def fake_new(current, client=None):
    return up.parse_release(release("v9.9.9"), current)


async def fake_none(current, client=None):
    return None

up.check_latest = fake_new
run(app.check_updates())
ok(app.update_btn.visible, "jest nowa wersja → przycisk widoczny")
ok(app.update_label.value == "Aktualizacja 9.9.9", "na przycisku numer wersji")

app.start_update()
ok(page.dialogs and "źródeł" in str(page.dialogs[-1].content.value),
   "ze źródeł: wyjaśnienie zamiast instalacji")
page.dialogs.clear()

app2 = mk.KuchniaApp(FakePage())
up.check_latest = fake_none
run(app2.check_updates(manual=True))
ok(not app2.update_btn.visible, "brak nowej → przycisk ukryty")
ok(app2.page.dialogs and "najnowszą" in str(app2.page.dialogs[-1].content.value),
   "ręczne sprawdzenie mówi „masz najnowszą”")
up.check_latest = orig

section("instalator i CI")
iss = open(os.path.join(HERE, "setup.iss"), encoding="utf-8").read()
ok("Check: WizardSilent" in iss, "po cichej aktualizacji program startuje sam")
ok("OutputBaseFilename=StratyKuchnia-Setup-{#MyAppVersion}" in iss,
   "nazwa instalatora zgadza się z tym, czego szuka program")
ok("Excludes: \"kuchnia.db" in iss, "instalator nie nadpisuje bazy")
wf = open(os.path.join(HERE, ".github", "workflows", "release.yml"),
          encoding="utf-8").read()
ok("APP_VERSION" in wf, "CI pilnuje zgodności tagu z APP_VERSION")
ok("check_updater" in wf, "CI odpala te testy")
ok(re.search(r'^APP_VERSION = "\d+\.\d+\.\d+"$', open(
    os.path.join(HERE, "main_kuchnia.py"), encoding="utf-8").read(), re.M),
   "APP_VERSION w formacie X.Y.Z")
ok(up.UPDATE_REPO == "hurshe/Kitchen-Loses", "repo z wydaniami")

print("\n==============================================")
print(f"OK: {PASS}   BŁĘDY: {FAIL}")
print(f"Dane testowe: {TMP}")
sys.exit(1 if FAIL else 0)
