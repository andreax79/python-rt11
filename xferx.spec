from PyInstaller.utils.hooks import collect_submodules
from PyInstaller.building.build_main import Analysis, PYZ, EXE
import os

# Define path to VERSION file
version_file_path = os.path.join('xferx', 'VERSION')

# Main build steps
a = Analysis(
    ['xferx.py'],  # Entry point
    pathex=['.'],
    binaries=[],
    datas=[(version_file_path, 'xferx')],
    hiddenimports=collect_submodules('xferx'),
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=None,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=None)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='xferx',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
    icon=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='xferx',
)
