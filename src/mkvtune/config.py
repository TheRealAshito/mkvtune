"""XDG paths, settings and logging (debug mode ON by default — standing user
preference: local logs, crash capture, verbose detail until told otherwise)."""
from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass, field, fields
from logging.handlers import RotatingFileHandler
from pathlib import Path

APP_NAME = "mkvtune"


def _xdg(env: str, fallback: str) -> Path:
    base = os.environ.get(env) or os.path.join(os.path.expanduser("~"), fallback)
    return Path(base) / APP_NAME


def config_dir() -> Path:
    p = _xdg("XDG_CONFIG_HOME", ".config")
    p.mkdir(parents=True, exist_ok=True)
    return p


def data_dir() -> Path:
    p = _xdg("XDG_DATA_HOME", ".local/share")
    p.mkdir(parents=True, exist_ok=True)
    return p


def cache_dir() -> Path:
    p = _xdg("XDG_CACHE_HOME", ".cache")
    p.mkdir(parents=True, exist_ok=True)
    return p


def journal_dir() -> Path:
    p = data_dir() / "journals"
    p.mkdir(parents=True, exist_ok=True)
    return p


def policy_dir() -> Path:
    p = config_dir() / "policies"
    p.mkdir(parents=True, exist_ok=True)
    return p


def log_dir() -> Path:
    p = data_dir() / "logs"
    p.mkdir(parents=True, exist_ok=True)
    return p


@dataclass
class Settings:
    theme: str = "dark"                    # "dark" | "amoled" | "bw" | "bw-light" | "htop" | "mc" | "paper"
    debug: bool = True                     # ON by default (standing preference)
    browser_mode: str = "panel"            # "panel" (mc-style) | "tree"
    force_picked_subs: bool = True         # picked sub gets default+forced
    disable_other_subs: bool = False       # also flag-enabled=0 on the rest
    disable_other_audio: bool = False      # also flag-enabled=0 on unpicked audio
    audio_langs: tuple[str, ...] = ("jpn", "ja", "jap")
    sub_langs: tuple[str, ...] = ("eng",)

    @property
    def amoled(self) -> bool:
        return self.theme == "amoled"


def settings_path() -> Path:
    return config_dir() / "settings.json"


def load_settings() -> Settings:
    path = settings_path()
    if not path.exists():
        return Settings()
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return Settings()
    known = {f.name: f for f in fields(Settings)}
    kwargs = {}
    for key, value in data.items():
        if key not in known:
            continue
        if isinstance(known[key].default, tuple) and isinstance(value, list):
            value = tuple(value)
        kwargs[key] = value
    try:
        return Settings(**kwargs)
    except TypeError:
        return Settings()


def save_settings(settings: Settings) -> None:
    try:
        settings_path().write_text(json.dumps(asdict(settings), indent=2) + "\n")
    except OSError:
        pass


def setup_logging(debug: bool | None = None) -> logging.Logger:
    """File log at DEBUG (rotating); nothing leaves the machine — mkvtune has
    no network code at all."""
    settings = load_settings()
    level = settings.debug if debug is None else debug
    logger = logging.getLogger("mkvtune")
    logger.setLevel(logging.DEBUG if level else logging.INFO)
    logger.propagate = False
    if not logger.handlers:
        handler = RotatingFileHandler(log_dir() / "mkvtune.log", maxBytes=512_000, backupCount=3)
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        handler.setLevel(logging.DEBUG)
        logger.addHandler(handler)
    return logger
