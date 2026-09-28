"""Filter-rule framework shared by the Solana and EVM monitors.

A single DROP hit discards a pool. FLAG hits keep it but add to its risk
score. ``case_refs`` point to incident ids in the chain's case library.
"""

from __future__ import annotations

from dataclasses import dataclass

from .models import DROP, KEEP, Decision, RuleHit

DROP_WEIGHT = 10
# Risk weight when a DROP rule is applied at FLAG level (e.g. a concentration
# between the flag and drop thresholds).
DOWNGRADED_FLAG_WEIGHT = 3


@dataclass(frozen=True)
class Rule:
    rule_id: str
    stage: str
    action: str
    title_zh: str
    rationale_zh: str
    case_refs: tuple[str, ...] = ()
    weight: int = DROP_WEIGHT


def rule_table(*rules: Rule) -> dict[str, Rule]:
    return {r.rule_id: r for r in rules}


def make_hit(
    rules: dict[str, Rule],
    rule_id: str,
    detail: str = "",
    action: str | None = None,
    refs=None,
) -> RuleHit:
    rule = rules[rule_id]
    action = action or rule.action
    if action == DROP:
        weight = DROP_WEIGHT
    elif rule.action == DROP:
        weight = DOWNGRADED_FLAG_WEIGHT
    else:
        weight = rule.weight
    return RuleHit(
        rule_id=rule_id,
        action=action,
        reason_zh=f"{rule.title_zh}{'：' + detail if detail else ''}",
        case_refs=tuple(refs) if refs is not None else rule.case_refs,
        weight=weight,
    )


def decide(hits: list[RuleHit]) -> Decision:
    verdict = DROP if any(h.action == DROP for h in hits) else KEEP
    return Decision(verdict=verdict, risk_score=sum(h.weight for h in hits), hits=hits)
