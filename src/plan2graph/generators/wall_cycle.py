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


def make_constraint_mask(vocab: dict):
    """ADR-0012 §3 constrained decoding — 문법 구조 + corner/room 참조 유효성을 생성 시점 강제.
    무효 토큰을 -inf로 마스킹(닫힘·문법순서·참조범위·cycle≥3). 생성 전용(학습 무관, 파이썬 루프).
    반환: mask_fn(x, logits) → 마스킹된 logits."""
    from plan2graph import wallcycle_codec as wc
    V = wc.V
    nC, nH, nS, nSC = (len(wc.COUNTRIES), len(wc.HOUSING), len(wc.SCHEMAS), len(wc.SCOPES))
    nrole = len(wc.ROLES)
    meta, scope, units = vocab["meta"], vocab["scope"], vocab["units"]
    coord, role, pos, room = vocab["coord"], vocab["role"], vocab["pos"], vocab["room"]
    g, nbins, mu = vocab["grid"], vocab["nbins"], vocab["max_units"]

    def allowed(seq):
        L = len(seq)
        if L == 1:  return set(range(meta, meta + nC))                 # country
        if L == 2:  return set(range(meta + nC, meta + nC + nH))       # housing
        if L == 3:  return set(range(meta + nC + nH, meta + nC + nH + nS))  # schema
        if L == 4:  return set(range(scope, scope + nSC))              # scope
        if L == 5:  return set(range(units, units + mu + 1))           # units
        if L == 6:  return {V.SEC_CORNERS}
        if V.SEC_CORNERS not in seq:
            return {V.SEC_CORNERS}
        ci = seq.index(V.SEC_CORNERS)
        has_rooms = V.SEC_ROOMS in seq
        # ── CORNERS phase ──
        if not has_rooms:
            ncoord = L - 1 - ci
            ncorners = ncoord // 2
            if ncoord % 2 == 1:                                        # mid pair → qy
                return set(range(coord, coord + g + 1))
            a = set(range(coord, coord + g + 1))                       # qx of next corner
            if ncorners >= 3:
                a.add(V.SEC_ROOMS)                                     # 전환(≥3 corner 후)
            return a
        ri = seq.index(V.SEC_ROOMS)
        ncorners = (ri - 1 - ci) // 2
        has_open = V.SEC_OPEN in seq
        # ── ROOMS phase ──
        if not has_open:
            tail = seq[ri + 1:]
            st, nref = "role", 0
            for t in tail:
                if st == "role":
                    st, nref = "refs", 0
                elif st == "refs":
                    if t == V.ROOM_END:
                        st = "role"
                    else:
                        nref += 1
            if st == "role":
                a = set(range(role, role + nrole))                    # 새 방 role
                if tail.count(V.ROOM_END) >= 1:
                    a.add(V.SEC_OPEN)                                  # 전환(≥1 방 후)
                return a
            a = set(range(coord, coord + ncorners))                   # corner 참조(유효 범위)
            if nref >= 3:
                a.add(V.ROOM_END)                                     # cycle≥3 충족 시 닫기
            return a
        # ── OPENINGS phase ──
        oi = seq.index(V.SEC_OPEN)
        nrooms = seq[ri:oi].count(V.ROOM_END)
        tail = seq[oi + 1:]
        st = "head"
        for t in tail:
            if st == "head":
                st = "c1" if t in (V.DOOR, V.WINDOW) else ("r1" if t == V.OPEN else "head")
            elif st == "c1": st = "c2"
            elif st == "c2": st = "pos"
            elif st == "pos": st = "head"
            elif st == "r1": st = "r2"
            elif st == "r2": st = "head"
        if st == "head":
            return {V.DOOR, V.WINDOW, V.OPEN, V.EOS}
        if st in ("c1", "c2"):
            return set(range(coord, coord + ncorners))                # corner 참조
        if st == "pos":
            return set(range(pos, pos + nbins + 1))
        return set(range(room, room + max(1, nrooms)))                # room ordinal 참조

    def mask_fn(x, logits):
        for b in range(x.size(0)):
            a = allowed(x[b].tolist())
            if not a:
                continue
            idx = torch.tensor(sorted(a), device=logits.device)
            m = torch.full_like(logits[b], float("-inf"))
            m[idx] = 0.0
            logits[b] = logits[b] + m
        return logits

    return mask_fn
