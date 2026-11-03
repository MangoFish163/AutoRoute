"""Run the real NSIS directory validator without copying any application files."""
from pathlib import Path
import os
import subprocess
import sys
import tempfile


def main(makensis):
    source = Path('frontend/src-tauri/windows/installer.nsi').read_text(encoding='utf-8')
    start = source.index('Function ValidateInstallLocation')
    end = source.index('FunctionEnd', start) + len('FunctionEnd')
    validator = source[start:end]
    with tempfile.TemporaryDirectory(prefix='router-path-tests-') as temporary:
        root = Path(temporary)
        script = root / 'check.nsi'
        script.write_text('''Unicode true
Name "Installer directory regression"
OutFile "check.exe"
RequestExecutionLevel user
SilentInstall silent
!include LogicLib.nsh
!include FileFunc.nsh
Var PreviousInstallLocation
Section
Call ValidateInstallLocation
SectionEnd
Function LogInstall
Pop $0
FunctionEnd
''' + validator, encoding='utf-8')
        # Preserve registers in the diagnostic stub, as the real logger does.
        script.write_text(script.read_text(encoding='utf-8').replace(
            'Function LogInstall\nPop $0', 'Function LogInstall\nExch $0\nPop $0'), encoding='utf-8')
        subprocess.run([str(Path(makensis).resolve()), '/INPUTCHARSET', 'UTF8', '/V1', str(script)], check=True)
        empty = root / 'empty'
        empty.mkdir()
        occupied = root / 'occupied'
        occupied.mkdir()
        (occupied / 'keep.txt').write_text('keep')
        cases = [(root / 'new' / 'app', True), (root / '中文 路径' / '应用', True),
                 (empty, True), (occupied, False), (Path(root.anchor), False),
                 (Path(os.environ['WINDIR']) / 'not-allowed', False)]
        for path, expected in cases:
            command = subprocess.list2cmdline([str(root / 'check.exe'), '/S']) + f' /D={path}'
            result = subprocess.run(command, timeout=15, creationflags=subprocess.CREATE_NO_WINDOW)
            assert (result.returncode == 0) == expected, (str(path), result.returncode)
        print('NSIS path regression passed: new/empty/Chinese paths accepted; occupied/root/system paths rejected.')


if __name__ == '__main__':
    main(sys.argv[1])
