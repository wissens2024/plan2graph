"""생성기 레지스트리 (ADR-0001) — arch 이름·체크포인트로 생성기를 찾는 고정점.

새 모델 = generators/ 파일 + `@register("arch")`. 소비자는 make/load만 호출.
  make(arch, **kw)  : 새 인스턴스(미학습/baseline fit 등 클래스별 팩토리는 각자).
  load(path)        : 체크포인트의 arch 태그로 dispatch → 해당 클래스.from_checkpoint.
  archs()           : 등록된 arch 목록.
"""
from __future__ import annotations

from plan2graph.generators.base import Generator  # noqa: F401

REGISTRY: dict[str, type] = {}


def register(arch: str):
    """클래스를 arch 이름으로 등록(+arch 속성 주입)."""
    def deco(cls):
        cls.arch = arch
        REGISTRY[arch] = cls
        return cls
    return deco


def archs() -> list[str]:
    return sorted(REGISTRY)


def make(arch: str, **kw) -> Generator:
    if arch not in REGISTRY:
        raise KeyError(f"미등록 arch: {arch} (등록됨: {archs()})")
    return REGISTRY[arch](**kw)


def _arch_of_checkpoint(path) -> str | None:
    """체크포인트에서 arch 태그 추출(torch .pt: payload.condition.arch / payload.arch)."""
    import torch
    payload = torch.load(str(path), map_location="cpu", weights_only=False)
    cond = payload.get("condition") if isinstance(payload, dict) else None
    return (cond or {}).get("arch") or (payload.get("arch") if isinstance(payload, dict) else None)


def load(path) -> Generator:
    """체크포인트 → 알맞은 Generator(arch dispatch). 모델 교체에도 동일 호출."""
    arch = _arch_of_checkpoint(path)
    if arch not in REGISTRY:
        raise KeyError(f"체크포인트 arch '{arch}' 미등록 (등록됨: {archs()})")
    return REGISTRY[arch].from_checkpoint(path)


# 내장 생성기 등록(@register 발동) — 맨 끝(register 정의 후) import.
from plan2graph.generators import baseline, set_transformer, typed  # noqa: E402,F401
