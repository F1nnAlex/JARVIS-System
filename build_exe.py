"""Build a standalone JARVIS app with PyInstaller:  python build_exe.py

The result is in dist/JARVIS/ (run JARVIS.exe on Windows, JARVIS.app on macOS).
"""

import subprocess
import sys

subprocess.check_call([sys.executable, "-m", "pip", "install", "pyinstaller"])
subprocess.check_call([
    sys.executable, "-m", "PyInstaller",
    "--name", "JARVIS",
    "--windowed",
    "--noconfirm",
    "--collect-all", "faster_whisper",
    "--collect-all", "ctranslate2",
    "--collect-data", "anthropic",
    "--hidden-import", "keyring.backends",
    "--hidden-import", "sounddevice",
    "jarvis/__main__.py",
])
print("\nDone. Your app is in dist/JARVIS/")
