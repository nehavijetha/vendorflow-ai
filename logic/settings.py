"""Reads settings from the environment, falling back to the project's .env file."""
import os
from pathlib import Path

ENV_FILE = Path(__file__).resolve().parent.parent / ".env"


def _from_env_file():
    values = {}
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, value = line.partition("=")
                values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def get(name, default=None):
    return os.environ.get(name) or _from_env_file().get(name) or default
