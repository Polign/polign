"""Turn beliefs into the block of text the model reads."""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from polign_recall import Belief

from .memory import PredicateSpec

DEFAULT_TEMPLATE = "<recall_memory>\n{context}\n</recall_memory>"


def _observed(belief: Belief) -> str:
    observed = getattr(belief, "observed_at", "") or ""
    return observed[:10]


def render_beliefs(
    beliefs: Iterable[Belief],
    *,
    registry: Mapping[str, PredicateSpec] | None = None,
    template: str = DEFAULT_TEMPLATE,
    who: str = "the caller",
    remember_tool: bool = True,
) -> str:
    """Render current beliefs as a memory block.

    Single-valued predicates print one line each; multi-valued ones are
    grouped on one line. ``template`` must contain a literal ``{context}``.
    """
    if "{context}" not in template:
        raise ValueError("context template must contain a literal {context} placeholder")

    grouped: dict[str, list[Belief]] = {}
    for belief in beliefs:
        grouped.setdefault(belief.predicate, []).append(belief)

    lines: list[str] = []
    if grouped:
        lines.append(
            f"Facts remembered about {who} from earlier conversations. "
            f"Treat them as true unless {who} says otherwise, and use them naturally "
            "without reading the list aloud."
        )
        for predicate, items in grouped.items():
            spec = registry.get(predicate) if registry else None
            multi = spec is not None and spec.cardinality == "multi"
            if multi or len(items) > 1:
                values = "; ".join(str(b.value) for b in items)
                lines.append(f"- {predicate}: {values}")
            else:
                belief = items[0]
                when = _observed(belief)
                suffix = f" (observed {when})" if when else ""
                lines.append(f"- {predicate}: {belief.value}{suffix}")
    else:
        lines.append(f"Nothing is remembered about {who} yet.")

    if remember_tool:
        kinds = ", ".join(registry.keys()) if registry else "the registered memory types"
        lines.append(
            f"When {who} states a lasting fact of one of these kinds, call the remember tool "
            f"with it: {kinds}. Corrections go through the same tool. "
            "Do not mention the memory system unless asked."
        )

    return template.replace("{context}", "\n".join(lines))


def compose_instructions(base: str, memory_block: str) -> str:
    """The agent's instructions with the memory block appended."""
    base = base.rstrip()
    return f"{base}\n\n{memory_block}" if base else memory_block
