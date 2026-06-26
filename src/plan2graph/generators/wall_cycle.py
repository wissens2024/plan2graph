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
import torch.utils.checkpoint
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
        qkv = self.qkv(x).view(B, T, 3, self.n_head, self.d_k).permute(0, 2, 3, 1, 4)  # (B,3,nh,T,dk)
        q, k, v = qkv[:, 0], qkv[:, 1], qkv[:, 2]  # 각각 (B,nh,T,dk)
        out = F.scaled_dot_product_attention(q, k, v, is_causal=True)  # (B,nh,T,dk)
        out = out.transpose(1, 2).contiguous().view(B, T, D)  # (B,T,nh,dk) → (B,T,D)
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
        self.n1 = nn.LayerNorm(d_model, bias=False)
        self.attn = MultiHeadAttention(d_model, n_head)
        self.n2 = nn.LayerNorm(d_model, bias=False)
        self.mlp = FeedForward(d_model, dim_ff)

    def forward(self, x: torch.Tensor, mask: torch.Tensor | None = None) -> torch.Tensor:
        x = x + self.attn(self.n1(x), mask=mask)
        x = x + self.mlp(self.n2(x))
        return x


class WallCycleLM(nn.Module):
    def __init__(self, vocab_size: int, d_model: int = 256, n_layer: int = 6,
                 n_head: int = 8, max_len: int = 1152, dim_ff: int | None = None, dropout: float = 0.1,
                 grad_ckpt: bool = False):
        super().__init__()
        self.max_len = max_len
        self.grad_ckpt = grad_ckpt
        self.tok = nn.Embedding(vocab_size, d_model)
        self.drop = nn.Dropout(dropout)
        if dim_ff is None:
            dim_ff = 4 * d_model
        self.blocks = nn.ModuleList([
            TransformerBlock(d_model, n_head, dim_ff) for _ in range(n_layer)
        ])
        self.norm = nn.LayerNorm(d_model, bias=False)
        self.head = nn.Linear(d_model, vocab_size, bias=False)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        """x: (B,T) 토큰 id → (B,T,vocab) logits. causal."""
        B, T = x.shape
        h = self.drop(self.tok(x))
        mask = torch.triu(torch.full((T, T), float("-inf"), device=x.device), diagonal=1)
        for block in self.blocks:
            if self.grad_ckpt and self.training:
                h = torch.utils.checkpoint.checkpoint(block, h, mask, use_reentrant=False)
            else:
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


def make_constraint_mask(vocab: dict, orthogonal: bool = False, guide: dict = None):
    """ADR-0012 §3 constrained decoding — 문법 구조 + corner/room 참조 유효성을 생성 시점 강제.
    무효 토큰을 -inf로 마스킹(닫힘·문법순서·참조범위·cycle≥4=삼각형 차단). 생성 전용(학습 무관).
    orthogonal=True: room cycle 변을 직각 강제(다음 corner는 이전과 x또는y 동일) → 대각선 차단.
    ★guide(규제/스펙 guided decoding, 선택): 하드마스크 위에 *소프트 bias*(logit +)로 유도.
      guide={"bedrooms":N, "bathrooms":M, "daylight":True, "bias":6.0}.
      - 스펙: role 선택 시 부족한 침실/욕실 타입 부스트  - 채광: 창 없는 거실/침실 벽으로 WINDOW 유도(+EOS 억제).
      None이면 현행 동작과 100% 동일.
    반환: mask_fn(x, logits) → 마스킹된 logits."""
    import torch
    from plan2graph import wallcycle_codec as wc
    V = wc.V
    nC, nH, nS, nSC = (len(wc.COUNTRIES), len(wc.HOUSING), len(wc.SCHEMAS), len(wc.SCOPES))
    nrole = len(wc.ROLES)
    meta, scope, units = vocab["meta"], vocab["scope"], vocab["units"]
    coord, role, pos, room = vocab["coord"], vocab["role"], vocab["pos"], vocab["room"]
    cref = vocab.get("cref", vocab["coord"])    # 코너 참조(room cycle·opening) = cref 섹션, 좌표(coord)와 별개
    g, nbins, mu = vocab["grid"], vocab["nbins"], vocab["max_units"]

    # ── guided decoding 설정 (규제/스펙 유도) ──
    _gb = float((guide or {}).get("bias", 6.0))
    _g_bed = (guide or {}).get("bedrooms"); _g_bath = (guide or {}).get("bathrooms")
    _g_day = bool((guide or {}).get("daylight", True))
    HAB_R, BED_R, BATH_R = {0, 1, 2}, {1, 2}, {4, 5}    # 거실·안방·침실 / 안방·침실 / 화장실·욕실

    def _guide_state(seq):
        """rooms=[(role,corner set)], 창없는 habitable 코너 union, 침실수, 욕실수, 창수."""
        rooms = []
        if V.SEC_ROOMS not in seq:
            return [], set(), 0, 0, 0
        ri = seq.index(V.SEC_ROOMS)
        oi = seq.index(V.SEC_OPEN) if V.SEC_OPEN in seq else len(seq)
        i = ri + 1
        while i < oi:
            t = seq[i]
            if role <= t < role + nrole:
                rl = t - role; cs = set(); i += 1
                while i < oi and seq[i] != V.ROOM_END:
                    if cref <= seq[i] < cref + 4096:
                        cs.add(seq[i] - cref)
                    i += 1
                rooms.append((rl, cs)); i += 1
            else:
                i += 1
        wins = []; j = oi + 1
        while j < len(seq):
            t = seq[j]
            if t == V.DOOR:
                j += 5
            elif t == V.WINDOW:
                if j + 2 < len(seq):
                    wins.append({seq[j + 1] - cref, seq[j + 2] - cref})
                j += 4
            elif t == V.OPEN:
                j += 3
            else:
                j += 1
        nbed = sum(1 for rl, _ in rooms if rl in BED_R)
        nbath = sum(1 for rl, _ in rooms if rl in BATH_R)
        uncov = set()
        for rl, cs in rooms:
            if rl in HAB_R and not any(w <= cs for w in wins if w):
                uncov |= cs
        return rooms, uncov, nbed, nbath, len(wins)

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
            a = set(range(cref, cref + ncorners))                     # corner 참조(유효 범위)
            if orthogonal:                                            # 직각 강제: 이전 corner와 x또는y 동일
                lr = None
                for t in reversed(tail):
                    if t == V.ROOM_END or role <= t < role + nrole:
                        break
                    if cref <= t < cref + ncorners:
                        lr = t - cref; break
                cs = _corners(seq)
                if lr is not None and lr < len(cs):
                    lx, ly = cs[lr]
                    orth = {cref + ci for ci in range(min(ncorners, len(cs)))
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
                        if cref <= t < cref + len(cs):
                            cur.append(t - cref)
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
            return set(range(cref, cref + ncorners))                  # corner 참조
        if st in ("dpos", "wpos"):
            return set(range(pos, pos + nbins + 1))
        return set(range(room, room + max(1, nrooms)))                # room ordinal(dr/or)

    def mask_fn(x, logits):
        for b in range(x.size(0)):
            seq = x[b].tolist()
            a = allowed(seq)
            if not a:
                continue
            idx = torch.tensor(sorted(a), device=logits.device)
            m = torch.full_like(logits[b], float("-inf"))
            m[idx] = 0.0
            logits[b] = logits[b] + m
            # ── guided bias (규제/스펙) — 하드마스크 통과 토큰 안에서만 ──
            if guide:
                rooms, uncov, nbed, nbath, nwin = _guide_state(seq)
                boost = []
                if role in a:                                       # 새 방 role 선택 위치
                    if _g_bed is not None and nbed < _g_bed:
                        boost += [role + r for r in BED_R]
                    if _g_bath is not None and nbath < _g_bath:
                        boost += [role + r for r in BATH_R]
                _hard = bool((guide or {}).get("hard_daylight"))
                if _g_day and uncov and V.SEC_OPEN in seq:          # OPEN phase, 창없는 habitable 존재
                    if V.WINDOW in a:                               # head
                        boost.append(V.WINDOW)
                        if _hard and nwin < 24:                     # 강제: 미덮 있으면 WINDOW만(EOS/DOOR/OPEN 차단)
                            for tok in (V.EOS, V.DOOR, V.OPEN):
                                if tok in a:
                                    logits[b][tok] = float("-inf")
                        elif V.EOS in a and nwin < 16:
                            logits[b][V.EOS] -= _gb
                    if cref in a:                                   # 창 코너 ref: 미덮 habitable 코너로
                        if _hard:
                            keep = [cref + c for c in uncov if (cref + c) in a]
                            if keep:
                                mm = torch.full_like(logits[b], float("-inf"))
                                mm[torch.tensor(keep, device=logits.device)] = 0.0
                                logits[b] = logits[b] + mm
                        else:
                            boost += [cref + c for c in uncov]
                boost = [t for t in set(boost) if t in a]
                if boost:
                    logits[b][torch.tensor(boost, device=logits.device)] += _gb
        return logits

    return mask_fn
