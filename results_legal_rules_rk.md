# 규칙별 법규 준수율 before→after repair (A 팔 증거)

ckpt=ckpts/korplan_ar_rk_gated_seed42_roomperm_ep180.pt · n=200 · decoded=200 · country=0 · seed=42

전체 법규(legal_ok): before 0/200 (0%) → after 76/200 (38%)

| 규칙 | 적용 도면 | before 통과 | after 통과 |
|---|---|---|---|
| L1_daylight_window (거실·침실 채광창 보유) | 200 | 95/200 (47%) | 152/200 (76%) |
| L2_ventilation_window (거실·침실 환기창 보유) | 200 | 95/200 (47%) | 152/200 (76%) |
| L3_egress_reachable (거실에서 직통 피난 경로) | 200 | 0/200 (0%) | 183/200 (91%) |
| L1_daylight_ratio (거실·침실 채광 면적비) | 200 | 200/200 (100%) | 200/200 (100%) |
| L4_bedroom_min_area (침실 최소 면적) | 200 | 89/200 (44%) | 89/200 (44%) |
| L5_dwelling_min_area (세대 최소 전용면적) | 200 | 200/200 (100%) | 200/200 (100%) |
| L6_refuge_area (발코니 대피공간 최소 면적) | 200 | 200/200 (100%) | 200/200 (100%) |

※ 적용=legal_applied(검사 대상). L1·L2·L3=scale 독립(repair 대상). L4·L5·L6=estimate_scale(전용84㎡ 가정)로 활성. repair는 창(채광·환기)·문/현관(동선)만 수정.
