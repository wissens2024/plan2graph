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
import torch.nn.functional as F


class MultiHeadAttention(nn.Module):
    """checkpoint: blocks.N.attn.{qkv,proj}.weight"""
    def __init__(self, d_model: int, n_head: int):
        super().__init__()
        assert d_model % n_head == 0
        self.d_model = d_model
        self.n_head = n_head
        self.d_k = d_model // n_head
        self.qkv = nn.Linear(d_model, 3 * d_model, bias=False)
        self.proj = nn.Linear(d_model, d_model, bias=False)

    def forward(self, x: torch.Tensor, mask: torch.Tensor | None = None) -> torch.Tensor:
        B, T, D = x.shape
        qkv = self.qkv(x).view(B, T, 3, self.n_head, self.d_k).transpose(1, 3)  # (B,3,nh,T,dk)
        q, k, v = qkv[:, 0], qkv[:, 1], qkv[:, 2]
        scores = (q @ k.transpose(-2, -1)) / (self.d_k ** 0.5)
        if mask is not None:
            scores = scores + mask.unsqueeze(0).unsqueeze(0)
        attn = F.softmax(scores, dim=-1)
        out = attn @ v
        out = out.transpose(1, 2).contiguous().view(B, T, D)
        return self.proj(out)


class FeedForward(nn.Module):
    """checkpoint: blocks.N.mlp.{w1,w3,w2}.weight"""
    def __init__(self, d_model: int, dim_ff: int):
        super().__init__()
        self.w1 = nn.Linear(d_model, dim_ff, bias=False)
        self.w3 = nn.Linear(d_model, dim_ff, bias=False)
        self.w2 = nn.Linear(dim_ff, d_model, bias=False)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.w2(F.gelu(self.w1(x)) * self.w3(x))


class TransformerBlock(nn.Module):
    """checkpoint: blocks.N.{n1,attn,n2,mlp}.* """
    def __init__(self, d_model: int, n_head: int, dim_ff: int):
        super().__init__()
        self.n1 = nn.LayerNorm(d_model)
        self.attn = MultiHeadAttention(d_model, n_head)
        self.n2 = nn.LayerNorm(d_model)
        self.mlp = FeedForward(d_model, dim_ff)

    def forward(self, x: torch.Tensor, mask: torch.Tensor | None = None) -> torch.Tensor:
        x = x + self.attn(self.n1(x), mask=mask)
        x = x + self.mlp(self.n2(x))
        return x


class WallCycleLM(nn.Module):
    def __init__(self, vocab_size: int, d_model: int = 256, n_layer: int = 6,
                 n_head: int = 8, max_len: int = 1152, dropout: float = 0.1):
        super().__init__()
        self.max_len = max_len
        self.tok = nn.Embedding(vocab_size, d_model)
        self.drop = nn.Dropout(dropout)
        dim_ff = 4 * d_model
        self.blocks = nn.ModuleList([
            TransformerBlock(d_model, n_head, dim_ff) for _ in range(n_layer)
        ])
        self.norm = nn.LayerNorm(d_model)
        self.head = nn.Linear(d_model, vocab_size, bias=False)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        """x: (B,T) 토큰 id → (B,T,vocab) logits. causal."""
        B, T = x.shape
        h = self.drop(self.tok(x))
        mask = torch.triu(torch.full((T, T), float("-inf"), device=x.device), diagonal=1)
        for block in self.blocks:
            h = block(h, mask=mask)
        return self.head(self.norm(h))

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


def make_constraint_mask(vocab: dict, orthogonal: bool = False):
    """ADR-0012 §3 constrained decoding — 문법 구조 + corner/room 참조 유효성을 생성 시점 강제.
    무효 토큰을 -inf로 마스킹(닫힘·문법순서·참조범위·cycle≥4=삼각형 차단). 생성 전용(학습 무관).
    orthogonal=True: room cycle 변을 직각 강제(다음 corner는 이전과 x또는y 동일) → 대각선 차단.
    반환: mask_fn(x, logits) → 마스킹된 logits."""
    from plan2graph import wallcycle_codec as wc
    V = wc.V
    nC, nH, nS, nSC = (len(wc.COUNTRIES), len(wc.HOUSING), len(wc.SCHEMAS), len(wc.SCOPES))
    nrole = len(wc.ROLES)
    meta, scope, units = vocab["meta"], vocab["scope"], vocab["units"]
    coord, role, pos, room = vocab["coord"], vocab["role"], vocab["pos"], vocab["room"]
    g, nbins, mu = vocab["grid"], vocab["nbins"], vocab["max_units"]

    def _corners(seq):                              # CORNERS 섹션 → [(qx,qy), ...]
        out = []
        if V.SEC_CORNERS not in seq:
            return out
        i = seq.index(V.SEC_CORNERS) + 1
        while i + 1 < len(seq):
            ta, tb = seq[i], seq[i + 1]
            if coord <= ta <= coord + g and coord <= tb <= coord + g:
                out.append((ta - coord, tb - coord)); i += 2
            else:
                break
        return out

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
            if orthogonal:                                            # 직각 강제: 이전 corner와 x또는y 동일
                lr = None
                for t in reversed(tail):
                    if t == V.ROOM_END or role <= t < role + nrole:
                        break
                    if coord <= t <= coord + g:
                        lr = t - coord; break
                cs = _corners(seq)
                if lr is not None and lr < len(cs):
                    lx, ly = cs[lr]
                    orth = {coord + ci for ci in range(min(ncorners, len(cs)))
                            if (cs[ci][0] == lx or cs[ci][1] == ly) and ci != lr}
                    if orth:
                        a = orth
            # 닫기 = 최소 4코너(삼각형 방 차단) + 직각이면 닫는 변도 축정렬(닫힘 대각선 차단).
            if nref >= 4:
                close_ok = True
                if orthogonal and nref < 8:                            # nref≥8=런어웨이 방지로 닫기 허용
                    cs = _corners(seq)
                    cur = []                                          # 현재 cycle corner 참조(마지막 role 이후)
                    for t in reversed(tail):
                        if role <= t < role + nrole:
                            break
                        if coord <= t < coord + len(cs):
                            cur.append(t - coord)
                    if len(cur) >= 2 and cur[0] < len(cs) and cur[-1] < len(cs):
                        lx2, ly2 = cs[cur[0]]                          # 마지막 ref(닫는 변 끝)
                        fx, fy = cs[cur[-1]]                           # 첫 ref(cycle 시작)
                        if not (fx == lx2 or fy == ly2):
                            close_ok = False                          # 닫는 변 대각선 → 더 진행
                if close_ok:
                    a.add(V.ROOM_END)
            return a
        # ── OPENINGS phase ── door=c c pos r r · window=c c pos · open=r r
        oi = seq.index(V.SEC_OPEN)
        nrooms = seq[ri:oi].count(V.ROOM_END)
        tail = seq[oi + 1:]
        st = "head"
        for t in tail:
            if st == "head":
                if t == V.DOOR:   st = "dc1"
                elif t == V.WINDOW: st = "wc1"
                elif t == V.OPEN: st = "or1"
            elif st == "dc1": st = "dc2"
            elif st == "dc2": st = "dpos"
            elif st == "dpos": st = "dr1"
            elif st == "dr1": st = "dr2"
            elif st == "dr2": st = "head"
            elif st == "wc1": st = "wc2"
            elif st == "wc2": st = "wpos"
            elif st == "wpos": st = "head"
            elif st == "or1": st = "or2"
            elif st == "or2": st = "head"
        if st == "head":
            return {V.DOOR, V.WINDOW, V.OPEN, V.EOS}
        if st in ("dc1", "dc2", "wc1", "wc2"):
            return set(range(coord, coord + ncorners))                # corner 참조
        if st in ("dpos", "wpos"):
            return set(range(pos, pos + nbins + 1))
        return set(range(room, room + max(1, nrooms)))                # room ordinal(dr/or)

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
