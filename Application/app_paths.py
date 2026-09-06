from pathlib import Path
import sys

BUNDLE_ROOT = Path(__file__).resolve().parent
APP_ROOT = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else BUNDLE_ROOT

def asset_path(name=""):
    external = APP_ROOT / "ui"
    return (external if external.is_dir() else BUNDLE_ROOT / "ui") / name

def resource_path(name):
    external = APP_ROOT / name
    return external if external.exists() else BUNDLE_ROOT / name
