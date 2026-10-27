# `prompts/` — Jinja2 prompt templates

Two templates. The §1 collapse of intent / self_check / decompose /
rewrite / generate / chat into one agent (`docs/WORKFLOW_SANDBOX.md`)
collapses six prompts into one.

## Files

| Template       | Used by                | Variables                                                  |
| -------------- | ---------------------- | ---------------------------------------------------------- |
| `agent.j2`     | `src/graph/agent.py`   | `grade`, `subject`, `max_tool_calls`.                      |
| `verifier.j2` | `src/graph/verifier.py` | `question`, `answer`, `chunks` (list of `Chunk`).          |

## Loading

`src/graph/prompts.py` is the helper. Two entry points:

```python
from src.graph.prompts import render, render_pair

# System-only prompt (no \n---\n separator): returns one string
system = render("agent.j2", grade=7, subject="islamic_studies", max_tool_calls=4)

# System/user pair: split on the top-level \n---\n divider
system, user = render_pair("verifier.j2", question=q, answer=a, chunks=chs)
```

## Conventions

- One file per LLM call. The agent has exactly two LLM calls per turn
  (the agent itself + the topical verifier) → two templates.
- **Render chunks via `{% for %}`**, not a pre-rendered string — the
  template tells the whole grounding story to anyone reading it.
- `loop.index` is the 1-based citation number; the agent's own tool
  output uses the same numbering, so the verifier prompt can refer to
  chunks by `[1] … [5]` directly.
- Templates that need a system/user split use `\n---\n` on its own line.
  `render_pair()` splits there; `render()` returns the raw single string
  for system-only prompts.

## Not here

- Per-grade or per-subject prompt variants — single template per call;
  variation goes through `{% if %}` branches inside the template.
- Conversation/chat-only prompts — gone. The agent handles greetings
  and small talk in its own voice without ever calling retrieve.
