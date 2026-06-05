"""통계 baseline 생성기 — model_baseline 래핑(ADR-0001). 내부 불변, 인터페이스만 제공."""
from __future__ import annotations

from plan2graph.generators import register
from plan2graph.generators.base import Generator


@register("baseline")
class StatBaseline(Generator):
    run_id = "baseline"
    condition = {"arch": "baseline"}

    def __init__(self, model: dict):
        self.model = model

    @classmethod
    def fit(cls, train_records) -> "StatBaseline":
        from plan2graph import model_baseline as mb
        return cls(mb.fit(train_records))

    def generate(self, program: dict, rng, **kw):
        from plan2graph import model_baseline as mb
        return mb.generate(self.model, program, rng)

    def adj_score(self, a: str, b: str) -> float:
        from plan2graph import model_baseline as mb
        return mb._p(self.model, a, b)
