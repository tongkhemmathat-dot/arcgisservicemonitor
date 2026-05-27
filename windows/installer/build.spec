# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec — run via build.bat, not directly

a = Analysis(
    ['../monitor_backend.py'],
    pathex=['..'],
    binaries=[],
    datas=[
        ('../index.html', '.'),
    ],
    hiddenimports=[
        'cryptography',
        'cryptography.fernet',
        'cryptography.hazmat.backends.openssl',
        'cryptography.hazmat.primitives.ciphers.algorithms',
        'cryptography.hazmat.primitives.ciphers.modes',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['tkinter', 'unittest', 'email', 'html', 'http.client', 'urllib.robotparser'],
    noarchive=False,
    optimize=1,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='ArcGISMonitor',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
    disable_windowed_traceback=False,
    icon=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='ArcGISMonitor',
)
