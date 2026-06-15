"""wall-cycle + opening 토큰 생성기 — decoder-only transformer (ADR-0012 §1, ADR-0015).

토큰 시퀀스(wallcycle_codec) autoregressive LM. bbox 회귀(폐기) 대신 corner/room-cycle/
opening 토큰을 next-token으로 생성. 겹침0은 표현(corner 공유)이 구조적으로 보장.

- 조건 = 시퀀스 앞 META(country/housing/scope/units) prefix.
- 생성 = corner → room-cycle(role) → opening.
- constrained decoding(ADR-0012 §3: 닫힘·문 on-wall·창 exterior)은 `mask_fn` 훅으로 점진 적용.

1차 목표(ADR-0012 §6) = 미니셋 collapse 진단(이 표현이 학습되나·모드붕괴하나). 성능 아님.
의존: torch. 학습/추론은 서버 GPU.
"""
from __future__ import annotations

import torch
import torch.nn as nn


class WallCycleLM(nn.Module):
    def __init__(self, vocab_size: int, d_model: int = 256, n_layer: int = 6,
                 n_head: int = 8, max_len: int = 1152, dropout: float = 0.1):
        super().__init__()
        self.max_len = max_len
        self.tok = nn.Embedding(vocab_size, d_model)
        self.pos = nn.Embedding(max_len, d_model)
        self.drop = nn.Dropout(dropout)
        layer = nn.TransformerEncoderLayer(
            d_model, n_head, dim_feedforward=4 * d_model, dropout=dropout,
            activation="gelu", batch_first=True, norm_first=True)
        self.blocks = nn.TransformerEncoder(layer, n_layer)
        self.ln = nn.LayerNorm(d_model)
        self.head = nn.Linear(d_model, vocab_size, bias=False)
        self.head.weight = self.tok.weight              # weight tying
        self.apply(self._init)

    @staticmethod
    def _init(m):
        if isinstance(m, nn.Linear):
            nn.init.normal_(m.weight, std=0.02)
            if m.bias is not None:
                nn.init.zeros_(m.bias)
        elif isinstance(m, nn.Embedding):
            nn.init.normal_(m.weight, std=0.02)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        """x: (B,T) 토큰 id → (B,T,vocab) logits. causal."""
        B, T = x.shape
        pos = torch.arange(T, device=x.device)
        h = self.drop(self.tok(x) + self.pos(pos)[None])
        mask = torch.triu(torch.full((T, T), float("-inf"), device=x.device), diagonal=1)
        h = self.blocks(h, mask=mask, is_causal=True)
        return self.head(self.ln(h))

    @torch.no_grad()
    def generate(self, prefix: torch.Tensor, max_new: int, eos: int,
                 temperature: float = 1.0, top_k: int | None = None, mask_fn=None):
        """prefix:(B,T0) → 토큰 autoregressive 생성. mask_fn(x,logits)=constrained decoding 훅."""
        x = prefix
        done = torch.zeros(x.size(0), dtype=torch.bool, device=x.device)
        for _ in range(max_new):
            logits = self(x[:, -self.max_len:])[:, -1]          # (B,vocab)
            if mask_fn is not None:
                logits = mask_fn(x, logits)
            logits = logits / max(1e-6, temperature)
            if top_k:
                v, _ = torch.topk(logits, top_k)
                logits[logits < v[:, [-1]]] = float("-inf")
            probs = logits.softmax(-1)
            nxt = torch.multinomial(probs, 1)                   # (B,1)
            nxt[done] = eos
            x = torch.cat([x, nxt], dim=1)
            done = done | (nxt.squeeze(1) == eos)
            if done.all():
                break
        return x


def causal_lm_loss(logits: torch.Tensor, tokens: torch.Tensor, ignore_index: int = -100):
    """next-token CE. logits:(B,T,V), tokens:(B,T). shift 내부 처리."""
    import torch.nn.functional as F
    return F.cross_entropy(
        logits[:, :-1].reshape(-1, logits.size(-1)),
        tokens[:, 1:].reshape(-1),
        ignore_index=ignore_index)
