# -*- mode: python ; coding: utf-8 -*-
# PyInstaller 配置檔 - 運動拍檔 Raceshot 上傳工具

block_cipher = None

a = Analysis(
    ['gui_pyqt.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('app_icon.png', '.'),  # 包含圖標檔案
    ],
    hiddenimports=[
        'requests',
        'dotenv',
        'PyQt6.QtCore',
        'PyQt6.QtGui',
        'PyQt6.QtWidgets',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='運動拍檔上傳工具',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,  # 不顯示命令列視窗
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='app_icon.png',  # 執行檔圖標
)

# macOS 專用：建立 .app bundle
import sys
if sys.platform == 'darwin':
    app = BUNDLE(
        exe,
        name='運動拍檔上傳工具.app',
        icon='app_icon.png',
        bundle_identifier='app.raceshot.uploader',
        info_plist={
            'NSPrincipalClass': 'NSApplication',
            'NSHighResolutionCapable': 'True',
            'CFBundleShortVersionString': '1.1.2',
            'CFBundleVersion': '1.1.2',
            'CFBundleName': '運動拍檔上傳工具',
            'CFBundleDisplayName': '運動拍檔上傳工具',
            'LSMinimumSystemVersion': '10.13.0',
        },
    )
