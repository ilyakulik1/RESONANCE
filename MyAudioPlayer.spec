# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('app/ui/resources/styles', 'app/ui/resources/styles'),
        ('app/ui/resources/tokens.json', 'app/ui/resources'),
        ('app/ui/resources/assets', 'app/ui/resources/assets'),
        ('app/remote/web', 'app/remote/web'),
    ],
    hiddenimports=['pedalboard'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='MyAudioPlayer',
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
    icon=['icon.icns'],
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='MyAudioPlayer',
)
app = BUNDLE(
    coll,
    name='MyAudioPlayer.app',
    icon='icon.icns',
    bundle_identifier=None,
)
