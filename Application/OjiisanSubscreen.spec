# Build with: python -m PyInstaller --noconfirm OjiisanSubscreen.spec
from pathlib import Path
root = Path(SPECPATH)
datas = [(str(p), "ui") for p in (root / "ui").glob("*.png")]
datas += [(str(root / "icon.ico"), "."), (str(root / "config.json"), ".")]
datas += [(str(root / "Placeholder.png"), "."), (str(root / "Placeholder.mp4"), ".")]
a = Analysis([str(root / "hook_start.py")], pathex=[str(root)],
             binaries=[], datas=datas,
             hiddenimports=["OjiisanSubscreen", "iidx_sfx", "iidx_ticker", "app_paths"],
             hookspath=[], runtime_hooks=[], excludes=[])
# Qt uses Windows' ICU ABI. The build Python runtime carries a different
# ICU build with the same filename, which must not shadow the system DLL.
a.binaries = [item for item in a.binaries
              if Path(item[0]).name.lower() not in {"icuuc.dll", "icudt78.dll"}]
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True,
          name="OjiisanSubscreen", console=False, debug=False,
          strip=False, upx=False, icon=str(root / "icon.ico"))
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="OjiisanSubscreen")
