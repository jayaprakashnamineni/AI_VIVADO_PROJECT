"""Shared Ollama chat client with a configurable request timeout.

The module-level ``ollama.chat`` uses a single global client created with
``timeout=None`` (unbounded).  A hung or overloaded Ollama server therefore
blocks the pipeline forever.  This helper builds a client whose timeout is
read from ``config.json`` (``llm_timeout_seconds``), so every LLM call is
bounded and a stalled request raises instead of hanging.
"""

import json
from pathlib import Path

from ollama import Client

project_dir = Path(__file__).parent.parent

DEFAULT_TIMEOUT_SECONDS = 600


def _load_timeout_seconds():
    """Return the configured LLM timeout in seconds."""
    config_file = project_dir / "config.json"

    timeout = DEFAULT_TIMEOUT_SECONDS

    if config_file.exists():
        try:
            config = json.loads(
                config_file.read_text(encoding="utf-8")
            )
            timeout = config.get(
                "llm_timeout_seconds", DEFAULT_TIMEOUT_SECONDS
            )
        except Exception:
            timeout = DEFAULT_TIMEOUT_SECONDS

    try:
        timeout = float(timeout)
    except (TypeError, ValueError):
        timeout = DEFAULT_TIMEOUT_SECONDS

    return timeout


_client = None


def get_llm_client():
    """Return a lazily-created Ollama client with the timeout applied."""
    global _client

    if _client is None:
        _client = Client(timeout=_load_timeout_seconds())

    return _client


def chat(*args, **kwargs):
    """Timeout-bounded drop-in replacement for ``ollama.chat``."""
    return get_llm_client().chat(*args, **kwargs)
