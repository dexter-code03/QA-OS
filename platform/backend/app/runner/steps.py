from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Optional


SelectorStrategy = Literal["accessibilityId", "id", "xpath", "className"]


@dataclass(frozen=True)
class Selector:
    using: SelectorStrategy
    value: str


@dataclass(frozen=True)
class Step:
    type: str
    selector: Optional[Selector] = None
    text: Optional[str] = None
    ms: Optional[int] = None
    expect: Optional[str] = None
    meta: dict[str, Any] = None


def parse_steps(raw_steps: list[dict[str, Any]]) -> list[Step]:
    steps: list[Step] = []
    for raw in raw_steps:
        st = raw.get("type") or raw.get("action")
        if not st:
            raise ValueError("Step missing 'type'")

        selector = None
        if "selector" in raw and raw["selector"]:
            sel = raw["selector"]
            selector = Selector(using=sel["using"], value=sel["value"])

        steps.append(
            Step(
                type=st,
                selector=selector,
                text=raw.get("text"),
                ms=raw.get("ms"),
                expect=raw.get("expect"),
                meta=raw.get("meta") or {},
            )
        )
    return steps

