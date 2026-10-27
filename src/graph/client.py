"""LLM client factory.

One entry point — `get_llm(temperature=..., structured=...)` — returns a
LangChain `Runnable`. Nodes call `.ainvoke()` / `.astream()` on it;
they never import a specific provider.

`with_retry()` returns a `RunnableRetry` that does NOT carry the
`with_structured_output` method (it's defined on `BaseChatModel`, not
generic Runnables). So the chain order must be:

    ChatOpenAI(...).with_structured_output(Schema).with_retry(...)

…and `get_llm` does that composition for callers — pass the schema via
the `structured=` kwarg.

Backends (L3, L17, L20):
  - `fake`       — FakeListChatModel, no network, no API key. Phase-A default.
  - `openrouter` — ChatOpenAI pointed at openrouter.ai, optionally
                   structured-output-wrapped, then wrapped with the L20
                   transient-retry policy.
  - `ollama`     — placeholder. Wire up when local target is decided.

Swapping backends is a single line in `config.yaml`.
"""

from __future__ import annotations

from typing import Any, TypeVar

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langchain_core.runnables import Runnable
from pydantic import BaseModel

from src.config import settings

T = TypeVar("T", bound=BaseModel)

# Canned responses for the `fake` backend. The agent path (for_agent=True)
# uses these as the model's text reply; without tool_calls in the AIMessage
# the prebuilt agent terminates after one LLM call, so smoke_run with
# backend=fake returns this string with no chunks retrieved.
_FAKE_RESPONSES = [
    "stubbed answer (fake backend — no real LLM call made)",
]


class _FakeAgentModel(FakeListChatModel):
    """`FakeListChatModel` + passthrough `bind_tools`.

    `create_react_agent` calls `model.bind_tools(...)` at compile time;
    the base class raises NotImplementedError. This subclass returns
    self so `for_agent=True` works under `backend=fake` for smoke_run
    and other non-test entry points. (Test code provides its own
    queued-AIMessage fakes — see `tests/test_agent.py`.)
    """

    def bind_tools(
        self,
        tools: Any,  # noqa: ARG002
        *,
        tool_choice: str | None = None,  # noqa: ARG002
        **kwargs: Any,  # noqa: ARG002
    ) -> _FakeAgentModel:
        return self


def _transient_exceptions() -> tuple[type[BaseException], ...]:
    """Exception types eligible for retry per L20.

    Importing lazily so the openai dep is only required when the
    openrouter backend is actually selected.
    """
    from openai import (
        APIConnectionError,
        APITimeoutError,
        InternalServerError,
        RateLimitError,
    )

    return (APIConnectionError, APITimeoutError, InternalServerError, RateLimitError)


def _wrap_retry(runnable: Runnable) -> Runnable:
    return runnable.with_retry(
        retry_if_exception_type=_transient_exceptions(),
        stop_after_attempt=3,
        wait_exponential_jitter=True,
    )


def get_llm(
    temperature: float | None = None,
    structured: type[T] | None = None,
    *,
    for_agent: bool = False,
) -> BaseChatModel | Runnable:
    """Return a chat model / structured runnable for the current backend.

    Args:
        temperature: Overrides the per-backend default. Pass
            `settings.llm.classifier_temperature` for deterministic nodes
            and `settings.llm.generation_temperature` for the generate node.
        structured: Pydantic schema class. When provided, the returned
            runnable will call `.with_structured_output(schema)` BEFORE
            the retry wrapper — so retries still work and the result is a
            typed schema instance.
        for_agent: Skip the L20 retry wrap. `create_react_agent`'s
            `_should_bind_tools` check doesn't unwrap `RunnableRetry`,
            so a retry-wrapped model breaks the agent at compile time.
            Use this for the workflow-sandbox agent path; retries for
            the agent should happen at the turn level, not per LLM call.

    Returns:
        For `fake`: `FakeListChatModel` (structured= is unsupported and
        callers should branch on `settings.llm.backend == "fake"` first).
        For `openrouter`: an `RunnableRetry` wrapping either the bare
        chat model or `model.with_structured_output(schema)` — or, when
        `for_agent=True`, the bare `ChatOpenAI` itself so the agent's
        `bind_tools()` step works.
    """
    backend = settings.llm.backend

    if backend == "fake":
        if structured is not None:
            # FakeListChatModel doesn't support with_structured_output.
            # Nodes are expected to short-circuit on backend=="fake" with
            # a canned return *before* calling get_llm() in this mode.
            raise RuntimeError(
                "get_llm(structured=...) is not supported with backend=fake. "
                "Nodes must return canned values directly when backend is fake."
            )
        if for_agent:
            return _FakeAgentModel(responses=_FAKE_RESPONSES)
        return FakeListChatModel(responses=_FAKE_RESPONSES)

    if backend == "openrouter":
        from langchain_openai import ChatOpenAI

        if not settings.openrouter_api_key:
            raise RuntimeError(
                "llm.backend=openrouter requires OPENROUTER_API_KEY in .env"
            )

        temp = (
            temperature
            if temperature is not None
            else settings.llm.classifier_temperature
        )
        llm: BaseChatModel | Runnable = ChatOpenAI(
            model=settings.llm.openrouter.model,
            base_url=settings.llm.openrouter.base_url,
            api_key=settings.openrouter_api_key,
            temperature=temp,
            max_tokens=settings.llm.max_tokens,
            timeout=settings.llm.timeout_seconds,
        )
        if for_agent:
            return llm
        if structured is not None:
            llm = llm.with_structured_output(structured)
        return _wrap_retry(llm)

    if backend == "ollama":
        raise NotImplementedError(
            "Ollama backend not yet implemented. "
            "Set `llm.backend: fake` or `openrouter` in config.yaml."
        )

    raise ValueError(f"Unknown llm.backend: {backend!r}")


def get_verifier_llm(
    structured: type[T] | None = None,
) -> BaseChatModel | Runnable:
    """Topical-verifier model (docs/WORKFLOW_SANDBOX.md §4 / §8).

    Defaults to `settings.llm.openrouter.model` when `settings.verifier.model`
    is empty (the conservative starting point on the OpenRouter free tier).
    Set `verifier.model` in `config.yaml` to pin a smaller / faster model
    once one is available.

    Uses `classifier_temperature` (0.0 by default) because the verifier is
    a yes/no judgement and benefits from determinism. Same retry policy as
    `get_llm()`.
    """
    backend = settings.llm.backend

    if backend == "fake":
        if structured is not None:
            raise RuntimeError(
                "get_verifier_llm(structured=...) is not supported with "
                "backend=fake. Call sites must short-circuit on "
                "backend == 'fake' with a canned VerifierDecision."
            )
        return FakeListChatModel(responses=_FAKE_RESPONSES)

    if backend == "openrouter":
        from langchain_openai import ChatOpenAI

        if not settings.openrouter_api_key:
            raise RuntimeError(
                "llm.backend=openrouter requires OPENROUTER_API_KEY in .env"
            )

        model_id = settings.verifier.model or settings.llm.openrouter.model
        llm: BaseChatModel | Runnable = ChatOpenAI(
            model=model_id,
            base_url=settings.llm.openrouter.base_url,
            api_key=settings.openrouter_api_key,
            temperature=settings.llm.classifier_temperature,
            max_tokens=settings.llm.max_tokens,
            timeout=settings.llm.timeout_seconds,
        )
        if structured is not None:
            llm = llm.with_structured_output(structured)
        return _wrap_retry(llm)

    if backend == "ollama":
        raise NotImplementedError(
            "Ollama verifier backend not yet implemented. "
            "Set `llm.backend: fake` or `openrouter` in config.yaml."
        )

    raise ValueError(f"Unknown llm.backend: {backend!r}")
