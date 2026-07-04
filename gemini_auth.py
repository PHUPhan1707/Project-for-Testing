"""Shared Gemini API key resolution: .env → env vars → interactive prompt."""

from __future__ import annotations

import os
import sys


def load_dotenv_file() -> None:
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except Exception:  # pragma: no cover
        pass


def get_gemini_api_key(*, prompt: bool = True) -> str:
    """
    Return a Gemini API key from (in order):
      1. `.env` / environment (GEMINI_API_KEY, GOOGLE_API_KEY, API_KEY)
      2. Interactive prompt (hidden input) when running in a terminal

    Set prompt=False to skip the interactive fallback (e.g. in tests).
    """
    load_dotenv_file()

    for var in ("GEMINI_API_KEY", "GOOGLE_API_KEY", "API_KEY"):
        val = os.environ.get(var, "").strip()
        if val:
            return val

    if prompt and sys.stdin.isatty():
        import getpass

        print("GEMINI_API_KEY not found (check .env or environment).")
        print("Get a free key at https://aistudio.google.com/apikey")
        key = getpass.getpass("Enter your Gemini API key: ").strip()
        if key:
            os.environ["GEMINI_API_KEY"] = key
            return key

    sys.exit(
        "ERROR: no API key. Set GEMINI_API_KEY in .env (copy from .env.sample) "
        "or export it in your shell."
    )
