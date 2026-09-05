from __future__ import annotations

from dataclasses import dataclass

_GROUNDEDNESS_PROMPT = """You are a strict fact-checker. Given a CONTEXT and an ANSWER, decide \
whether every factual claim in the ANSWER is directly supported by the CONTEXT.

CONTEXT:
{context}

ANSWER:
{answer}

Respond with exactly one line: "GROUNDED" if every claim is supported, or \
"UNGROUNDED: <short reason>" if any claim is unsupported or fabricated."""


@dataclass
class GroundednessVerdict:
    grounded: bool
    reason: str
    raw: str


def judge_groundedness(llm, context: str, answer: str) -> GroundednessVerdict:
    """Ask an LLM whether `answer` is fully supported by `context`.

    `llm` is any object exposing `.invoke(str) -> message-like` (e.g. a
    langchain ChatOllama instance) — this keeps the eval harness decoupled
    from any one model provider.

    This is a best-effort, single-call groundedness check layered on top of
    the deterministic retrieval metrics in `rag_eval.py`: it catches the case
    where retrieval succeeded but the agent's generated answer still drifted
    from the retrieved context (the actual hallucination-in-production
    failure mode). On ambiguous judge output, defaults to ungrounded so
    parsing failures surface as review items instead of silently passing.
    """
    prompt = _GROUNDEDNESS_PROMPT.format(context=context, answer=answer)
    response = llm.invoke(prompt)
    text = getattr(response, "content", str(response)).strip()

    if text.upper().startswith("GROUNDED"):
        return GroundednessVerdict(grounded=True, reason="", raw=text)
    if text.upper().startswith("UNGROUNDED"):
        reason = text.split(":", 1)[1].strip() if ":" in text else text
        return GroundednessVerdict(grounded=False, reason=reason, raw=text)
    return GroundednessVerdict(grounded=False, reason=f"Unparseable judge output: {text!r}", raw=text)
