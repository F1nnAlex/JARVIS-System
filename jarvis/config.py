"""Settings storage. Lives in the user's app-data folder.

The Anthropic API key goes into the operating system's credential store
(Windows Credential Manager, macOS Keychain, Secret Service on Linux) when
the `keyring` package can reach one, and into the settings file otherwise.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict, dataclass, fields
from pathlib import Path

try:
    import keyring
except ImportError:  # optional dependency
    keyring = None

KEYRING_SERVICE = "JARVIS"
KEYRING_USER = "anthropic-api-key"


def data_dir() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
        path = base / "JARVIS"
    elif sys.platform == "darwin":
        path = Path.home() / "Library" / "Application Support" / "JARVIS"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
        path = base / "jarvis"
    path.mkdir(parents=True, exist_ok=True)
    return path


@dataclass
class Settings:
    model: str = "claude-opus-5-5"
    effort: str = "low"
    web_search: bool = True
    user_name: str = ""
    address_as: str = ""
    location: str = ""
    voice_name: str = ""
    voice_rate: float = 1.0
    wake_word: bool = True
    mic_sensitivity: float = 0.5
    whisper_model: str = "base.en"
    allow_open_apps: bool = True
    remember_conversations: bool = True
    api_key_in_file: str = ""  # only used when no OS credential store exists

    @classmethod
    def load(cls) -> "Settings":
        try:
            raw = json.loads((data_dir() / "settings.json").read_text("utf-8"))
        except (OSError, ValueError):
            raw = {}
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in raw.items() if k in known})

    def save(self) -> None:
        path = data_dir() / "settings.json"
        path.write_text(json.dumps(asdict(self), indent=2), "utf-8")
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass

    # ---- API key -------------------------------------------------------

    def api_key(self) -> str:
        if keyring is not None:
            try:
                stored = keyring.get_password(KEYRING_SERVICE, KEYRING_USER)
                if stored:
                    return stored
            except Exception:  # no usable backend
                pass
        return self.api_key_in_file or os.environ.get("ANTHROPIC_API_KEY", "")

    def api_key_source(self) -> str:
        if keyring is not None:
            try:
                if keyring.get_password(KEYRING_SERVICE, KEYRING_USER):
                    return "keychain"
            except Exception:
                pass
        if self.api_key_in_file:
            return "file"
        return "environment" if os.environ.get("ANTHROPIC_API_KEY") else "none"

    def set_api_key(self, key: str) -> None:
        key = key.strip()
        if keyring is not None:
            try:
                keyring.set_password(KEYRING_SERVICE, KEYRING_USER, key)
                self.api_key_in_file = ""
                self.save()
                return
            except Exception:
                pass
        self.api_key_in_file = key
        self.save()
