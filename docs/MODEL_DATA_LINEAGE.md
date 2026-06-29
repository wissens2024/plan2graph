# 모델 ↔ 데이터 연결(lineage) — 삭제 0, 추적용

## A. AR 체크포인트(ckpts/, Track B=토큰) — 런별

| 런 | 모델수(ep) | 총량 | corpus | grid | 학습데이터(추정) |
|---|--:|--:|---|--:|---|
| `korplan_ar_r_rb256_roomperm.pt` | 12 | 10.4GB | K(한국) | 256 | tokens_korean_clean_g256 + tokens_rplan_rb256 (사전학습) |
| `korplan_ar_rk_gated_seed42_roomperm.pt` | 10 | 8.7GB | RK(RPLAN→한국FT) | 128 | tokens_korean_gated + tokens_rplan (사전학습) |
| `korplan_ar_k_gated_targetonly.pt` | 10 | 8.7GB | K(한국) | 128 | tokens_korean_gated + tokens_rplan (사전학습) |
| `korplan_ar_k_gated_ft.pt` | 10 | 8.7GB | K(한국) | 128 | tokens_korean_gated + tokens_rplan (사전학습) |
| `korplan_ar_k_g256_ftR_b110.pt` | 8 | 7.0GB | RK(RPLAN→한국FT) | 256 | tokens_korean_clean_g256 + tokens_rplan_rb256 (사전학습) |
| `korplan_ar_k_g256_ftR_b100.pt` | 8 | 7.0GB | RK(RPLAN→한국FT) | 256 | tokens_korean_clean_g256 + tokens_rplan_rb256 (사전학습) |
| `korplan_ar_k_g256_ftR_b90.pt` | 8 | 7.0GB | RK(RPLAN→한국FT) | 256 | tokens_korean_clean_g256 + tokens_rplan_rb256 (사전학습) |
| `korplan_ar_r_fmlm80m_pretrain_v2.pt` | 8 | 6.9GB | RK(RPLAN→한국FT) | 128 | tokens_korean_clean + tokens_rplan (사전학습) |
| `korplan_ar_r_roomperm_seed42.pt` | 8 | 6.9GB | K(한국) | 128 | tokens_korean_clean + tokens_rplan (사전학습) |
| `korplan_ar_rk_nosnap.pt` | 5 | 4.3GB | RK(RPLAN→한국FT) | 128 | tokens_korean_clean + tokens_rplan (사전학습) |
| `korplan_ar_rk_snap.pt` | 5 | 4.3GB | RK(RPLAN→한국FT) | 128 | tokens_korean_clean + tokens_rplan (사전학습) |
| `korplan_ar_k_nosnap.pt` | 5 | 4.3GB | K(한국) | 128 | tokens_korean_clean + tokens_rplan (사전학습) |
| `korplan_ar_k_snap.pt` | 5 | 4.3GB | K(한국) | 128 | tokens_korean_clean + tokens_rplan (사전학습) |
| `korplan_ar_k_fmlm80m.pt` | 2 | 1.7GB | K(한국) | 128 | tokens_korean_clean + tokens_rplan (사전학습) |
| `korplan_ar_r_fmlm80m_pretrain.pt` | 1 | 0.9GB | RK(RPLAN→한국FT) | 128 | tokens_korean_clean + tokens_rplan (사전학습) |
| `korplan_diff_r_gsdiff_corner_opt_step250k.pt` | 1 | 0.1GB | K(한국) | 128 | tokens_korean_clean + tokens_rplan (사전학습) |
| `korplan_diff_r_gsdiff_corner_opt.pt` | 1 | 0.1GB | K(한국) | 128 | tokens_korean_clean + tokens_rplan (사전학습) |
| `yolov8m-seg.pt` | 1 | 0.1GB | ? | 128 | ? |
| `checkpoint.pt` | 90 | 0.1GB | K(한국) | 128 | tokens_korean_clean |
| `best.pt` | 6 | 0.1GB | ? | 128 | ? |
| `last.pt` | 6 | 0.1GB | ? | 128 | ? |
| `korplan_diff_r_gsdiff_corner.pt` | 1 | 0.0GB | K(한국) | 128 | tokens_korean_clean + tokens_rplan (사전학습) |
| `korplan_diff_r_gsdiff_corner_step250k.pt` | 1 | 0.0GB | K(한국) | 128 | tokens_korean_clean + tokens_rplan (사전학습) |
| `korplan_ar_korean_ftR.pt` | 1 | 0.0GB | RK(RPLAN→한국FT) | 128 | tokens_korean_clean + tokens_rplan (사전학습) |
| `yolov8s-seg.pt` | 1 | 0.0GB | ? | 128 | ? |
| `ckpt_wallcycle_rplan.pt` | 1 | 0.0GB | K(한국) | 128 | tokens_korean_clean + tokens_rplan (사전학습) |
| `ckpt_wallcycle_kr_v3.pt` | 1 | 0.0GB | K(한국) | 128 | tokens_korean_clean |
| `ckpt_wallcycle_kr_v2.pt` | 1 | 0.0GB | K(한국) | 128 | tokens_korean_clean |
| `raster_ddpm_src.pt` | 1 | 0.0GB | ? | 128 | ? |
| `raster_ddpm128.pt` | 1 | 0.0GB | ? | 128 | ? |
| `raster_ddpm.pt` | 1 | 0.0GB | ? | 128 | ? |
| `yolov8n-seg.pt` | 1 | 0.0GB | ? | 128 | ? |
| `korplan_diff_r_gsdiff_edge.pt` | 1 | 0.0GB | K(한국) | 128 | tokens_korean_clean + tokens_rplan (사전학습) |
| `yolo26n.pt` | 1 | 0.0GB | ? | 128 | ? |
| `gen_v7cap2x_seed42.pt` | 1 | 0.0GB | ? | 128 | ? |
| `gen_v4cap2x_seed42.pt` | 1 | 0.0GB | ? | 128 | ? |
| `gen_v7cap2x_seed4.pt` | 1 | 0.0GB | ? | 128 | ? |
| `gen_v4cap2x_seed3.pt` | 1 | 0.0GB | ? | 128 | ? |
| `gen_v7cap2x_seed2.pt` | 1 | 0.0GB | ? | 128 | ? |
| `gen_v4cap2x_seed2.pt` | 1 | 0.0GB | ? | 128 | ? |
| `gen_v7cap2x_seed3.pt` | 1 | 0.0GB | ? | 128 | ? |
| `gen_v4cap2x_seed1.pt` | 1 | 0.0GB | ? | 128 | ? |
| `gen_v7cap2x_seed1.pt` | 1 | 0.0GB | ? | 128 | ? |
| `gen_v4cap2x_seed4.pt` | 1 | 0.0GB | ? | 128 | ? |
| `gen_v1cap2x_seed42.pt` | 1 | 0.0GB | ? | 128 | ? |
| `gen_v0cap2x_seed42.pt` | 1 | 0.0GB | ? | 128 | ? |
| `gen_v1cap2x_seed1.pt` | 1 | 0.0GB | ? | 128 | ? |
| `gen_v1cap2x_seed4.pt` | 1 | 0.0GB | ? | 128 | ? |
| `gen_v0cap2x_seed2.pt` | 1 | 0.0GB | ? | 128 | ? |
| `gen_v1cap2x_seed3.pt` | 1 | 0.0GB | ? | 128 | ? |
| `gen_v1cap2x_seed2.pt` | 1 | 0.0GB | ? | 128 | ? |
| `gen_v0cap2x_seed1.pt` | 1 | 0.0GB | ? | 128 | ? |
| `gen_v0cap2x_seed3.pt` | 1 | 0.0GB | ? | 128 | ? |
| `gen_v0cap2x_seed4.pt` | 1 | 0.0GB | ? | 128 | ? |
| `gen_v4cap2x.pt` | 1 | 0.0GB | ? | 128 | ? |
| `gen_v7cap2x.pt` | 1 | 0.0GB | ? | 128 | ? |
| `gen_v1cap2x.pt` | 1 | 0.0GB | ? | 128 | ? |
| `gen_v0cap2x.pt` | 1 | 0.0GB | ? | 128 | ? |
| `gen_v0_typed_seed42.pt` | 1 | 0.0GB | ? | 128 | ? |
| `gen_v0_typed_seed4.pt` | 1 | 0.0GB | ? | 128 | ? |
| `gen_v0_typed_seed3.pt` | 1 | 0.0GB | ? | 128 | ? |
| `gen_v0_typed_seed1.pt` | 1 | 0.0GB | ? | 128 | ? |
| `gen_v0_typed_seed2.pt` | 1 | 0.0GB | ? | 128 | ? |
| `gen_v0_typed.pt` | 1 | 0.0GB | ? | 128 | ? |
| `gen_v4_seed42.pt` | 1 | 0.0GB | ? | 128 | ? |
| `gen_v7_seed42.pt` | 1 | 0.0GB | ? | 128 | ? |
| `gen_v3_seed42.pt` | 1 | 0.0GB | ? | 128 | ? |
| `gen_v3_seed2.pt` | 1 | 0.0GB | ? | 128 | ? |
| `gen_v2_seed3.pt` | 1 | 0.0GB | ? | 128 | ? |
| `gen_v4_seed1.pt` | 1 | 0.0GB | ? | 128 | ? |
| `gen_v7_seed1.pt` | 1 | 0.0GB | ? | 128 | ? |
| `gen_v3_seed3.pt` | 1 | 0.0GB | ? | 128 | ? |
| `gen_v4_seed3.pt` | 1 | 0.0GB | ? | 128 | ? |
| `gen_v4_seed4.pt` | 1 | 0.0GB | ? | 128 | ? |
| `gen_v2_seed1.pt` | 1 | 0.0GB | ? | 128 | ? |
| `gen_v7_seed2.pt` | 1 | 0.0GB | ? | 128 | ? |
| `gen_v2_seed2.pt` | 1 | 0.0GB | ? | 128 | ? |
| `gen_v7_seed4.pt` | 1 | 0.0GB | ? | 128 | ? |
| `gen_v2_seed4.pt` | 1 | 0.0GB | ? | 128 | ? |
| `gen_v7_seed3.pt` | 1 | 0.0GB | ? | 128 | ? |
| `gen_v3_seed1.pt` | 1 | 0.0GB | ? | 128 | ? |
| `gen_v4_seed2.pt` | 1 | 0.0GB | ? | 128 | ? |
| `gen_v3_seed4.pt` | 1 | 0.0GB | ? | 128 | ? |
| `gen_v5_seed42.pt` | 1 | 0.0GB | ? | 128 | ? |
| `gen_v0_seed42.pt` | 1 | 0.0GB | ? | 128 | ? |
| `gen_v2_seed42.pt` | 1 | 0.0GB | ? | 128 | ? |
| `gen_v1_seed42.pt` | 1 | 0.0GB | ? | 128 | ? |
| `gen_v6_seed42.pt` | 1 | 0.0GB | ? | 128 | ? |
| `gen_v1_seed1.pt` | 1 | 0.0GB | ? | 128 | ? |
| `gen_v5_seed2.pt` | 1 | 0.0GB | ? | 128 | ? |
| `gen_v6_seed1.pt` | 1 | 0.0GB | ? | 128 | ? |
| `gen_v1_seed2.pt` | 1 | 0.0GB | ? | 128 | ? |
| `gen_v5_seed4.pt` | 1 | 0.0GB | ? | 128 | ? |
| `gen_v0_seed1.pt` | 1 | 0.0GB | ? | 128 | ? |
| `gen_v1_seed3.pt` | 1 | 0.0GB | ? | 128 | ? |
| `gen_v5_seed1.pt` | 1 | 0.0GB | ? | 128 | ? |
| `gen_v6_seed4.pt` | 1 | 0.0GB | ? | 128 | ? |
| `gen_v0_seed2.pt` | 1 | 0.0GB | ? | 128 | ? |
| `gen_v6_seed2.pt` | 1 | 0.0GB | ? | 128 | ? |
| `gen_v6_seed3.pt` | 1 | 0.0GB | ? | 128 | ? |
| `gen_v0_seed3.pt` | 1 | 0.0GB | ? | 128 | ? |
| `gen_v5_seed3.pt` | 1 | 0.0GB | ? | 128 | ? |
| `gen_v1_seed4.pt` | 1 | 0.0GB | ? | 128 | ? |
| `gen_v0_seed4.pt` | 1 | 0.0GB | ? | 128 | ? |
| `gen_v4.pt` | 1 | 0.0GB | ? | 128 | ? |
| `gen_v3.pt` | 1 | 0.0GB | ? | 128 | ? |
| `gen_v7.pt` | 1 | 0.0GB | ? | 128 | ? |
| `gen_v0.pt` | 1 | 0.0GB | ? | 128 | ? |
| `gen_v1.pt` | 1 | 0.0GB | ? | 128 | ? |
| `gen_v2.pt` | 1 | 0.0GB | ? | 128 | ? |
| `gen_v6.pt` | 1 | 0.0GB | ? | 128 | ? |
| `gen_v5.pt` | 1 | 0.0GB | ? | 128 | ? |

## B. runs/ 메타(gen-*, Track A=Diff/그래프) — pretrain/data

| run | pretrain | finetune/version(데이터) |
|---|---|---|
| `gen-v0-neural-set-transformer-typed-noPretrain` (seed×5) | None | None |
| `gen-v0-neural-set-transformer-v1-noPretrain` (seed×1) | None | None |
| `gen-v0-neural-set-transformer-v1-pre_global_cubicasa` (seed×1) | None | None |
| `gen-v0-neural-set-transformer-v2-noPretrain` (seed×5) | None | None |
| `gen-v0-neural-set-transformer-v2-pre_global_all` (seed×5) | None | None |
| `gen-v0-neural-set-transformer-v2-pre_global_cubicasa` (seed×5) | None | None |
| `gen-v0-neural-set-transformer-v2-pre_global_rplan` (seed×5) | None | None |
| `gen-v0cap2x-neural-set-transformer-v2-noPretrain` (seed×5) | None | None |
| `gen-v1-neural-set-transformer-v2-noPretrain` (seed×5) | None | None |
| `gen-v2-neural-set-transformer-v2-noPretrain` (seed×5) | None | None |
| `gen-v2-neural-set-transformer-v2-pre_global_cubicasa` (seed×5) | None | None |
| `gen-v3-neural-set-transformer-v2-noPretrain` (seed×5) | None | None |
| `gen-v4-neural-set-transformer-v2-noPretrain` (seed×5) | None | None |
| `gen-v5-neural-set-transformer-v2-noPretrain` (seed×5) | None | None |
| `gen-v6-neural-set-transformer-v2-noPretrain` (seed×5) | None | None |
| `gen-v7-neural-set-transformer-v2-noPretrain` (seed×5) | None | None |

## C. 데이터 아티팩트 ↔ 연결 모델(역추적)

- **tokens_korean_clean/gated(+g256)** ← AR 한국 모델(ckpts korplan_ar_k*/rk*)
- **tokens_rplan/_rb256** ← AR RPLAN 사전학습(korplan_ar_r*/rk*)
- **releases/parsed/v0·v2** ← Diff gen-v0* + build_aihub 소스
- **releases/parsed/global_rplan·cubicasa·all** ← Diff gen-*-pre_global_* (runs/ 메타 참조)
- **releases/corrected/g0·g1·g_global** ← Diff(보정데이터) HITL ablation
