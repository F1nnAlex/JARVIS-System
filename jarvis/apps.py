"""Finds the applications installed on this computer and launches them.

JARVIS can only start apps that appear in the system's own app list
(Start Menu, /Applications, or .desktop entries). It never runs arbitrary
commands.
"""

from __future__ import annotations

import configparser
import difflib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import threading
from dataclasses import dataclass
from pathlib import Path

SKIP_WORDS = re.compile(r"\b(uninstall|uninstaller|readme|release notes|license|help|documentation|website|manual)\b", re.I)


@dataclass(frozen=True)
class App:
    name: str
    target: str  # shortcut path, .app bundle, desktop-file id, or AppsFolder id
    kind: str  # "shortcut" | "appx" | "macapp" | "desktop"


def _normalise(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", name.lower()).strip()


class AppCatalog:
    def __init__(self):
        self._apps: list[App] = []
        self._lock = threading.Lock()
        self._loaded = threading.Event()

    def refresh_async(self) -> None:
        threading.Thread(target=self.refresh, daemon=True, name="app-scan").start()

    def refresh(self) -> None:
        try:
            if sys.platform == "win32":
                apps = _scan_windows()
            elif sys.platform == "darwin":
                apps = _scan_macos()
            else:
                apps = _scan_linux()
        except Exception as exc:  # never let a scan failure take JARVIS down
            print(f"[apps] scan failed: {exc}")
            apps = []
        unique: dict[str, App] = {}
        for app in apps:
            key = _normalise(app.name)
            if key and not SKIP_WORDS.search(app.name) and key not in unique:
                unique[key] = app
        with self._lock:
            self._apps = sorted(unique.values(), key=lambda a: a.name.lower())
        self._loaded.set()

    def apps(self) -> list[App]:
        self._loaded.wait(timeout=20)
        with self._lock:
            return list(self._apps)

    def find(self, query: str, limit: int = 5) -> list[App]:
        """Best matches for a spoken or typed app name, best first."""
        q = _normalise(query)
        if not q:
            return []
        scored: list[tuple[float, App]] = []
        for app in self.apps():
            n = _normalise(app.name)
            if n == q:
                score = 1.0
            elif n.startswith(q + " ") or n.endswith(" " + q):
                score = 0.92
            elif f" {q} " in f" {n} ":
                score = 0.88
            elif q in n:
                score = 0.75
            else:
                score = difflib.SequenceMatcher(None, q, n).ratio() * 0.85
            if score >= 0.55:
                scored.append((score - len(n) * 0.001, app))  # prefer shorter names on ties
        scored.sort(key=lambda s: s[0], reverse=True)
        return [app for _, app in scored[:limit]]

    def launch(self, app: App) -> None:
        if app.kind == "shortcut":
            os.startfile(app.target)  # type: ignore[attr-defined]  # Windows only
        elif app.kind == "appx":
            subprocess.Popen(["explorer.exe", f"shell:AppsFolder\\{app.target}"])
        elif app.kind == "macapp":
            subprocess.Popen(["open", app.target])
        elif app.kind == "desktop":
            _launch_desktop_entry(app.target)
        else:
            raise ValueError(f"unknown app kind {app.kind}")


# ---- Windows --------------------------------------------------------------


def _scan_windows() -> list[App]:
    apps: list[App] = []
    roots = [
        Path(os.environ.get("PROGRAMDATA", r"C:\ProgramData")) / "Microsoft/Windows/Start Menu/Programs",
        Path(os.environ.get("APPDATA", "")) / "Microsoft/Windows/Start Menu/Programs",
    ]
    for root in roots:
        if root.is_dir():
            for path in root.rglob("*"):
                if path.suffix.lower() in (".lnk", ".url", ".appref-ms"):
                    apps.append(App(path.stem, str(path), "shortcut"))
    # Store / UWP apps (Calculator, Spotify from the Store, Xbox, ...).
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command", "Get-StartApps | ConvertTo-Json -Compress"],
            capture_output=True, text=True, timeout=20,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        ).stdout
        data = json.loads(out or "[]")
        if isinstance(data, dict):
            data = [data]
        shortcut_names = {_normalise(a.name) for a in apps}
        for entry in data:
            name, app_id = entry.get("Name"), entry.get("AppID")
            if name and app_id and _normalise(name) not in shortcut_names and "!" in app_id:
                apps.append(App(name, app_id, "appx"))
    except (OSError, ValueError, subprocess.SubprocessError):
        pass
    return apps


# ---- macOS ----------------------------------------------------------------


def _scan_macos() -> list[App]:
    apps: list[App] = []
    roots = [Path("/Applications"), Path("/System/Applications"),
             Path("/System/Applications/Utilities"), Path("/Applications/Utilities"),
             Path.home() / "Applications"]
    for root in roots:
        if root.is_dir():
            for path in root.glob("*.app"):
                apps.append(App(path.stem, str(path), "macapp"))
    return apps


# ---- Linux ----------------------------------------------------------------

_DESKTOP_DIRS = [
    Path.home() / ".local/share/applications",
    Path("/usr/local/share/applications"),
    Path("/usr/share/applications"),
    Path("/var/lib/flatpak/exports/share/applications"),
    Path.home() / ".local/share/flatpak/exports/share/applications",
    Path("/var/lib/snapd/desktop/applications"),
]


def _scan_linux() -> list[App]:
    apps: list[App] = []
    for root in _DESKTOP_DIRS:
        if not root.is_dir():
            continue
        for path in root.glob("*.desktop"):
            parser = configparser.ConfigParser(interpolation=None, strict=False)
            try:
                parser.read(path, encoding="utf-8")
                entry = parser["Desktop Entry"]
            except (configparser.Error, KeyError, UnicodeDecodeError):
                continue
            if entry.get("Type") != "Application" or entry.get("NoDisplay", "").lower() == "true":
                continue
            if entry.get("Hidden", "").lower() == "true" or not entry.get("Exec"):
                continue
            apps.append(App(entry.get("Name", path.stem), str(path), "desktop"))
    return apps


def _launch_desktop_entry(path: str) -> None:
    if shutil.which("gtk-launch"):
        subprocess.Popen(["gtk-launch", Path(path).stem], start_new_session=True)
        return
    parser = configparser.ConfigParser(interpolation=None, strict=False)
    parser.read(path, encoding="utf-8")
    exec_line = parser["Desktop Entry"]["Exec"]
    args = [a for a in shlex.split(exec_line) if not re.fullmatch(r"%[a-zA-Z]", a)]
    subprocess.Popen(args, start_new_session=True)
