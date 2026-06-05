"""Generator 추상 인터페이스 (ADR-0001).

모든 생성기가 지키는 단일 계약. 소비자(eval_gen·gen_loop·admin)는 이 인터페이스에만 의존하고,
구체 모델(baseline·set-transformer·type조건·diffusion…)은 generators/ 하위에 plugin으로 둔다.
"""
from __future__ import annotations

from abc import ABC, abstractmethod

import networkx as nx


class Generator(ABC):
    """제약(program) → 배치 위상 그래프 생성기.

    arch       : 레지스트리 키(@register가 주입). 체크포인트 dispatch에 사용.
    run_id     : 학습 조건 식별(프로비넌스). 없으면 None.
    condition  : 학습 조건 dict(arch·pretrain·seed 등). 평가 기록용.
    """
    arch: str = "base"
    run_id: str | None = None
    condition: dict = {}

    @abstractmethod
    def generate(self, program: dict, rng, **kw) -> nx.Graph:
        """program(방 타입→개수) → nx.Graph (노드=방, 엣지=via). gen_loop/eval 공용."""
        ...

    def adj_score(self, a: str, b: str) -> float:
        """방 타입쌍 선호도(gen_loop 국소수정의 연결 우선순위). 기본 균일(1.0)."""
        return 1.0

    @classmethod
    def from_checkpoint(cls, path) -> "Generator":
        """체크포인트 파일 → 인스턴스. 학습형이 오버라이드(레지스트리 load가 호출)."""
        raise NotImplementedError(f"{cls.__name__}는 체크포인트 로드 미지원")
