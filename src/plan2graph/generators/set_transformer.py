"""Set-Transformer 신경망 생성기 — train_gen.NeuralGenerator 래핑(ADR-0001).

train_gen 내부 불변. 체크포인트(condition.arch='set-transformer-v2')로 load dispatch.
"""
from __future__ import annotations

from plan2graph.generators import register
from plan2graph.generators.base import Generator


@register("set-transformer-v2")
class SetTransformerGen(Generator):
    def __init__(self, ng):
        self.ng = ng                       # train_gen.NeuralGenerator
        self.run_id = getattr(ng, "run_id", None)
        self.condition = getattr(ng, "condition", {}) or {}

    @classmethod
    def from_checkpoint(cls, path) -> "SetTransformerGen":
        from plan2graph.train_gen import NeuralGenerator
        return cls(NeuralGenerator(str(path)))

    def generate(self, program: dict, rng, **kw):
        # kw 통과: thresh·sample·temperature 등(train_gen.generate 시그니처).
        return self.ng.generate(program, rng, **kw)

    def adj_score(self, a: str, b: str) -> float:
        return 1.0                         # 신경망은 쌍점수를 내부에서 직접 씀
