# Windows x64, one-directory bundle. Invoked from scripts/build-windows.ps1.
from pathlib import Path
root = Path(SPECPATH).parent
a = Analysis([str(root / 'backend' / 'desktop_entry.py')],
    pathex=[str(root / 'backend')],
    datas=[(str(root / 'frontend' / 'package.json'), 'metadata')],
    hiddenimports=['uvicorn.logging', 'uvicorn.loops.asyncio',
                   'uvicorn.protocols.http.h11_impl', 'uvicorn.lifespan.on'],
    excludes=['watchfiles', 'httptools', 'websockets'], noarchive=False)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='router-backend',
          debug=False, strip=False, upx=False, console=True,
          icon=str(root / 'frontend' / 'src-tauri' / 'icons' / 'icon.ico'))
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='backend')
