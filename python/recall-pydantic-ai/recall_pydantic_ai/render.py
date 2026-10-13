"""Turn beliefs into the block of text the model reads."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from polign_recall import Belief

DEFAULT_TEMPLATE = "<recall_memory>\n{context}\n</recall_memory>"


@dataclass(frozen=True)
class PredicateSpec:
    """One entry of the Recall predicate registry."""

    name: str
    cardinality: str  # "single" or "multi"
    value_type: str  # "string", "number", or "boolean"
    description: str


def _observed(belief: Belief) -> str:
    observed = getattr(belief, "observed_at", "") or ""
    return observed[:10]


def render_lines(beliefs: Iterable[Belief], registry: Mapping[str, PredicateSpec] | None = None) -> list[str]:
    """One line per single-valued predicate; multi-valued ones grouped on one line."""
    grouped: dict[str, list[Belief]] = {}
    for belief in beliefs:
        grouped.setdefault(belief.predicate, []).append(belief)
    lines: list[str] = []
    for predicate, items in grouped.items():
        spec = registry.get(predicate) if registry else None
        multi = spec is not None and spec.cardinality == "multi"
        if multi or len(items) > 1:
            lines.append(f"- {predicate}: " + "; ".join(str(b.value) for b in items))
        else:
            when = _observed(items[0])
            lines.append(f"- {predicate}: {items[0].value}" + (f" (observed {when})" if when else ""))
    return lines


def render_beliefs(
    beliefs: Iterable[Belief],
    *,
    registry: Mapping[str, PredicateSpec] | None = None,
    template: str = DEFAULT_TEMPLATE,
    who: str = "the user",
    remember_tool: bool = True,
    matches: Iterable[Belief] = (),
) -> str:
    """Render current beliefs as a memory block.

    ``matches`` are extra beliefs found by searching for the prompt when the
    subject has more beliefs than were loaded. ``template`` must contain a
    literal ``{context}``.
    """
    if "{context}" not in template:
        raise ValueError("context template must contain a literal {context} placeholder")

    facts = render_lines(beliefs, registry)
    lines: list[str] = []
    if facts:
        lines.append(
            f"Facts remembered about {who} from earlier conversations. "
            f"Treat them as true unless {who} says otherwise, and use them without "
            "reciting the list."
        )
        lines.extend(facts)
    else:
        lines.append(f"Nothing is remembered about {who} yet.")

    extra = render_lines(matches, registry)
    if extra:
        lines.append(f"More remembered facts about {who} that match this request:")
        lines.extend(extra)

    if remember_tool:
        kinds = ", ".join(registry) if registry else "the registered memory types"
        lines.append(
            f"When {who} states a lasting fact of one of these kinds, call the remember tool "
            f"with it: {kinds}. Corrections go through the same tool. "
            "Do not mention the memory system unless asked."
        )

    return template.replace("{context}", "\n".join(lines))
