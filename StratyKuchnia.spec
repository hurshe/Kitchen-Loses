# -*- mode: python ; coding: utf-8 -*-
# Сборка: pyinstaller StratyKuchnia.spec
# Каталог позиций (seed_kitchen.json) кладём внутрь exe: без него первый
# запуск на чистой машине дал бы пустую программу.

a = Analysis(
    ['main_kuchnia.py'],
    pathex=[],
    binaries=[],
    datas=[('icon.ico', '.'), ('seed_kitchen.json', '.'), ('assets', 'assets')],
    hiddenimports=['checklist_presets'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['matplotlib', 'IPython', 'PyQt5', 'tkinter', 'flask',
              'werkzeug', 'qrcode'],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='StratyKuchnia',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['icon.ico'],
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='StratyKuchnia',
)
