# ADR-0009: 용어 확정 — T/G 폐기, "Parsed / Corrected" + R2G(neuro-symbolic) + HITL ablation

Status: Accepted
Date: 2026-06-14
Deciders: wissens2024

## Supersedes (terminology of) ADR-0002 / ADR-0008
ADR-0002의 "T-라인/G-라인", ADR-0008의 "T=자동/G=자동+인간보정" 표기를 **용어만** 정정한다(개념은 ADR-0008 유지).

## Context
- "T/G"는 옛 두-패러다임(ADR-0002) 잔재. ADR-0008이 "같은 엔진, 입력 품질만 다른 비교"로 합쳤으나 이름이 T/G로 남아 어색하고 **논문 용어로 부적합**.
- "자동(auto)"도 부정확 — 추출은 비전검출·V2V·geomgraph 등 **여러 기술의 파이프라인**이지 trivial "자동"이 아니다. 두 조건의 **유일한 차이는 사람의 검수·보정 유무**.
- 문헌 근거: 이미지→구조그래프 추출은 **확립된 과제**(Raster-to-Vector[Liu], Raster-to-Graph[Hu 2024 CGF], CubiCasa5K) — 추출은 novelty 아님. 사람 교정은 **Human-Corrected Labels(HCL, arXiv:2511.09063)·Human-in-the-Loop(HITL)**가 표준 용어.

## Decision
1. **추출 파이프라인 = "Raster-to-Graph parsing (R2G)"**, 구현은 **neuro-symbolic**(신경 검출 + 기호 규칙·검증). 표준 과제로 서술하고 **novelty로 주장하지 않는다.**
2. **두 조건(옛 T/G 대체)**:
   - **Parsed** — R2G 파서 출력, 사람 교정 없음. (옛 T)
   - **Corrected** (= Human-corrected) — 파서 출력 + HITL 사람 검수·보정. (옛 G)
   - 축 = **correction**: Parsed → Corrected. 비교값 = (Corrected − Parsed) = **사람 교정의 가치**.
3. **프레이밍 = automation vs. automation+human (HITL ablation)** — "human vs machine"(독립 두 방법 대결)이 **아님**. 같은 파서를 공유하고 한쪽만 사람 교정 루프를 더한다.
4. **폐기 용어**: "T/G", "auto/자동"(데이터 조건에 쓰지 말 것 — 파이프라인 설명에만 한정), "gold/silver"([[no-separate-goldset-one-dataset]] 기각 유지).
5. **적용 범위(단계적)**:
   - **지금**: 이 ADR + GUI 라벨 + 문서(논문·사용자 facing)에 Parsed/Corrected 적용.
   - **레거시 별칭 유지**: 내부 식별자 `tline ≡ Parsed`, `gline ≡ Corrected`. 데이터 폴더(`data/{staging,releases}/{tline,gline}`)·함수명(`gline_status` 등) 대량 리네임은 **실행 서비스 + 병렬 에디터 세션을 깨므로 후속 안전 리팩터로** 미룬다.

## Considered Alternatives
1. **Auto / Curated** — 기각: "auto"가 추출을 trivial하게 오인시킴(실제 다기술 파이프라인).
2. **Gold / Silver** — 기각: 과거 사용자 거부([[no-separate-goldset-one-dataset]]).
3. **즉시 전체 리네임(폴더·함수 포함)** — 기각(후속): 실행 대시보드·에디터(8600, 다른 세션 작업중)가 `gline` 경로 의존 → 지금 리네임하면 파손. ADR 매핑으로 대체, 세션 종료 후 안전 수행.
4. **T/G 유지** — 기각: 논문 부적합·어색.

## Consequences
- Positive: 논문/GUI 용어가 명확하고 문헌과 정렬(R2G·HITL·HCL). 추출을 과대포장 안 함(정직).
- Negative: 내부 식별자(tline/gline)와 표기(Parsed/Corrected) 불일치 — 본 ADR 매핑으로 흡수, 후속 리네임 필요(부채).
- Follow-up: ① GUI 라벨·문서 Parsed/Corrected 적용(지금) ② 에디터 세션 종료 후 내부 식별자·폴더 안전 리네임(별도 작업) ③ 논문 메서드 섹션에 R2G+HITL ablation 프레이밍.

## Assumptions
- tline/gline은 내부 코드명일 뿐 논문에 노출되지 않는다(별칭으로 충분).
- 후속 내부 리네임은 병렬 에디터 세션 종료 후 수행한다.

## Related
- 개념: [ADR-0008](0008-data-correction-comparison-edit-medium.md)
- 용어정정 대상: [ADR-0002](0002-tline-gline-separation.md)
- 인용: Raster-to-Vector(Liu et al.), Raster-to-Graph(Hu 2024, CGF 15007), CubiCasa5K, Human-Corrected Labels(arXiv:2511.09063), Human-in-the-Loop(HITL)
