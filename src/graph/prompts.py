"""Jinja2 prompt template loader.

Loads templates from `prompts/` at repo root (per L10, L19). Templates use
`---` on its own line to separate the system prompt from the user message;
nodes call `render_pair(name, **vars)` to get back a `(system, user)` tuple.

One `Environment` is built at import time; templates are cached.
"""

from __future__ import annotations

from functools import cache
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

REPO_ROOT = Path(__file__).resolve().parents[2]
PROMPTS_DIR = REPO_ROOT / "prompts"

_env = Environment(
    loader=FileSystemLoader(PROMPTS_DIR),
    autoescape=False,           # prompts are not HTML
    trim_blocks=True,
    lstrip_blocks=True,
    keep_trailing_newline=False,
)


@cache
def _template(name: str):
    return _env.get_template(name)


def render(template_name: str, **kwargs) -> str:
    """Render a template to a single string."""
    return _template(template_name).render(**kwargs)


def render_pair(template_name: str, **kwargs) -> tuple[str, str]:
    """Render a template and split into (system, user) on the `---` line.

    Convention: templates have a top-level `---` divider on its own line
    separating the system message from the user message. If no divider is
    found, the entire output is treated as the user message and the system
    message is empty.
    """
    rendered = render(template_name, **kwargs)
    sep = "\n---\n"
    if sep in rendered:
        system, user = rendered.split(sep, maxsplit=1)
        return system.strip(), user.strip()
    return "", rendered.strip()
