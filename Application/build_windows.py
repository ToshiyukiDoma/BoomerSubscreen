"""Run with 64-bit Python 3.12+ after installing build-requirements.txt."""
from pathlib import Path
import os
import shutil
import struct
import subprocess
import sys

root = Path(__file__).resolve().parent
os.chdir(root)
if struct.calcsize("P") != 8:
    raise SystemExit("Build the separate UI application with 64-bit Python.")
subprocess.run([sys.executable, "-m", "PyInstaller", "--noconfirm",
                str(root / "OjiisanSubscreen.spec")], check=True)
output = root / "dist" / "OjiisanSubscreen"
if not (output / "config.json").exists():
    shutil.copy2(root / "config.json", output / "config.json")
(output / "backgrounds").mkdir(exist_ok=True)
print(f"Built {output / 'OjiisanSubscreen.exe'}")
print("Copy the entire output folder, including _internal. No Python installation is needed there.")
