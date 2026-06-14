"""GSDiff 생성 평면 -> plan2graph cadrender(건축 라인 도면 + DXF).

my_sample.py 의 재구성 로직을 재사용하되, 한 샘플의 기하 프리미티브
(room polygons + room semantics + wall segments)를 RETURN 하도록 리팩터링.
그 프리미티브로 cadrender.Geometry 를 만들어 autocorrect -> render_fig/render_dxf.
"""
import sys, os, math
import numpy as np
import torch
import torch.nn.functional as F

REPO = os.path.expanduser('~/gsdiff_work')
sys.path.append(REPO)
sys.path.append(os.path.join(REPO, 'datasets'))
sys.path.append(os.path.join(REPO, 'gsdiff'))
sys.path.append(os.path.expanduser('~/plan2graph/src'))

from gsdiff.house_nn1 import HeterHouseModel
from gsdiff.house_nn3 import EdgeModel
from gsdiff.utils import (
    inverse_normalize_and_remove_padding_100,
    edges_remove_padding,
    edges_to_coordinates,
    get_cycle_basis_and_semantic_3_semansimplified,
)

device = 'cuda:0' if torch.cuda.is_available() else 'cpu'
print('device:', device)
torch.manual_seed(0)
np.random.seed(0)

diffusion_steps = 1000
BS = 4

alpha_bar = lambda t: math.cos((t) / 1.000 * math.pi / 2) ** 2
betas = []
max_beta = 0.999
for i in range(diffusion_steps):
    t1 = i / diffusion_steps
    t2 = (i + 1) / diffusion_steps
    betas.append(min(1 - alpha_bar(t2) / alpha_bar(t1), max_beta))
betas = np.array(betas, dtype=np.float64)
alphas = 1.0 - betas
alphas_cumprod = np.cumprod(alphas)
alphas_cumprod_prev = np.append(1.0, alphas_cumprod[:-1])
sqrt_recip_alphas_cumprod = np.sqrt(1.0 / alphas_cumprod)
sqrt_recipm1_alphas_cumprod = np.sqrt(1.0 / alphas_cumprod - 1)
posterior_variance = betas * (1.0 - alphas_cumprod_prev) / (1.0 - alphas_cumprod)
posterior_mean_coef1 = betas * np.sqrt(alphas_cumprod_prev) / (1.0 - alphas_cumprod)
posterior_mean_coef2 = (1.0 - alphas_cumprod_prev) * np.sqrt(alphas) / (1.0 - alphas_cumprod)


def reconstruct_samples():
    """전 BS 샘플을 재구성해 [(polys, sems, edges_xy, ncorners), ...] 반환.
    polys: list[list[(x,y)]] (px, 256 canvas), sems: list[int] (cycle별 GSDiff 의미인덱스),
    edges_xy: list[((x1,y1),(x2,y2))] 벽 세그먼트."""
    node = HeterHouseModel().to(device)
    node.load_state_dict(torch.load(os.path.join(REPO, 'outputs/outputs/structure-1/model1000000.pt'),
                                    map_location='cpu'))
    node.eval()
    global_attn = torch.ones((BS, 53, 53), dtype=torch.bool, device=device)
    x = torch.randn((BS, 53, 10), device=device, dtype=torch.float64)
    with torch.no_grad():
        for step in range(diffusion_steps - 1, -1, -1):
            t = torch.tensor([step] * BS, device=device)
            o1, o2 = node(x, global_attn, t)
            eps = torch.cat((o1, o2), dim=2).double()
            pred_xstart = (sqrt_recip_alphas_cumprod[step] * x
                           - sqrt_recipm1_alphas_cumprod[step] * eps)
            pred_xstart[:, :, 0:2] = torch.clamp(pred_xstart[:, :, 0:2], -1, 1)
            pred_xstart[:, :, 2:] = (pred_xstart[:, :, 2:] >= 0.5).double()
            model_mean = (posterior_mean_coef1[step] * pred_xstart
                          + posterior_mean_coef2[step] * x)
            var = posterior_variance[step]
            if step > 0:
                x = model_mean + math.sqrt(var) * torch.randn_like(x)
            else:
                x = pred_xstart

    results_corners = [x[i, :, :2][None, :, :] for i in range(BS)]
    results_semantics = [x[i, :, 2:9][None, :, :] for i in range(BS)]
    results_pad = [x[i, :, 9:10].view(-1) for i in range(BS)]
    corners_inv, semantics_inv = inverse_normalize_and_remove_padding_100(
        results_corners, results_semantics, results_pad)

    edge = EdgeModel().to(device)
    edge.load_state_dict(torch.load(os.path.join(REPO, 'outputs/outputs/structure-3/model_stage2_best_010300.pt'),
                                    map_location='cpu'))
    edge.eval()
    results_edges, results_corners_numbers = [], []
    with torch.no_grad():
        for i in range(BS):
            n = corners_inv[i].shape[1]
            corners_s2 = torch.zeros((1, 53, 2), dtype=torch.float64, device=device)
            ct = (torch.tensor(corners_inv[i], dtype=torch.float64, device=device) - 128) / 128
            corners_s2[:, 0:n, :] = ct
            semantics_s2 = torch.zeros((1, 53, 7), dtype=torch.float64, device=device)
            semantics_s2[:, 0:n, :] = torch.tensor(semantics_inv[i], dtype=torch.float64, device=device)
            gam = torch.zeros((1, 53, 53), dtype=torch.bool, device=device)
            gam[:, 0:n, 0:n] = True
            pad_mask = torch.zeros((1, 53, 1), dtype=torch.uint8, device=device)
            pad_mask[:, 0:n, :] = 1
            out = edge(corners_s2, gam, pad_mask, semantics_s2)
            out = F.softmax(out, dim=2)
            out = torch.argmax(out, dim=2)
            out = F.one_hot(out, num_classes=2)
            results_edges.append(out)
            results_corners_numbers.append(int(n))
    edges_all = edges_remove_padding(results_edges, results_corners_numbers)

    samples = []
    for i in range(BS):
        corners_i = corners_inv[i]
        edges_i = edges_all[i]
        semantics_i = semantics_inv[i]
        sem_idx = np.indices(semantics_i.shape)[-1]
        sem_transformed = np.where(semantics_i == 1, sem_idx, 99999)
        output_points = [tuple(c) for c in
                         np.concatenate((corners_i, sem_transformed), axis=-1).tolist()[0]]
        output_edges = edges_to_coordinates(
            np.triu(edges_i[0, :, 1].reshape(len(output_points), len(output_points))).reshape(-1),
            output_points)
        # 벽 세그먼트(좌표쌍) -- cycle 함수가 output_edges 를 in-place 수정하므로 먼저 캡처
        edges_xy = [((float(e[0][0]), float(e[0][1])), (float(e[1][0]), float(e[1][1])))
                    for e in output_edges]
        d_rev, simple_cycles, simple_cycles_semantics = \
            get_cycle_basis_and_semantic_3_semansimplified(output_points, output_edges)
        polys = [[(float(p[0]), float(p[1])) for p in poly] for poly in simple_cycles]
        sems = [int(s) for s in simple_cycles_semantics]
        samples.append((polys, sems, edges_xy, int(corners_i.shape[1])))
        print(f'sample {i}: corners={corners_i.shape[1]}, rooms={len(polys)}, sems={sems}, '
              f'wall_segs={len(edges_xy)}')
    return samples


# -- GSDiff 의미 인덱스 -> plan2graph 한글 role(ROLE_KO 키) --
GSDIFF_SEM_TO_ROLE = {
    0: '거실',       # living_room
    1: '침실',       # bedroom
    2: '주방',       # kitchen
    3: '화장실',     # bathroom
    4: '발코니',     # balcony
    5: '다목적공간',  # storage -> 다용도실
    6: '기타',       # outdoor/wall class
}


def polygon_area_px(poly):
    """shoelace, px^2."""
    n = len(poly)
    a = 0.0
    for i in range(n):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % n]
        a += x1 * y2 - x2 * y1
    return abs(a) / 2.0


def build_geometry(polys, sems, edges_xy, plan_id, W=256, H=256):
    from plan2graph import cadrender as cr
    # GSDiff 256px 캔버스. 한국 아파트 외곽 가로 ~12 m 가정 -> scale_mm_per_px.
    xs = [p[0] for poly in polys for p in poly] or [0, W]
    ys = [p[1] for poly in polys for p in poly] or [0, H]
    plan_w_px = max(max(xs) - min(xs), 1.0)
    ASSUMED_PLAN_WIDTH_MM = 12000.0  # 12 m 외곽 가정
    scale_mm_per_px = ASSUMED_PLAN_WIDTH_MM / plan_w_px
    m_per_px = scale_mm_per_px / 1000.0

    rooms = []
    for idx, (poly, sem) in enumerate(zip(polys, sems)):
        if len(poly) < 3:
            continue
        area_m2 = polygon_area_px(poly) * (m_per_px ** 2)
        cx = sum(p[0] for p in poly) / len(poly)
        cy = sum(p[1] for p in poly) / len(poly)
        role = GSDIFF_SEM_TO_ROLE.get(sem, '기타')
        rooms.append(cr.RoomG(id=idx, role=role, polygon=poly, area_m2=area_m2,
                              label_pt=(cx, cy), fixtures=[]))

    bx0, by0 = min(xs), min(ys)
    bx1, by1 = max(xs), max(ys)

    def on_border(p, tol=2.0):
        return (abs(p[0] - bx0) < tol or abs(p[0] - bx1) < tol or
                abs(p[1] - by0) < tol or abs(p[1] - by1) < tol)

    walls = []
    for (a, b) in edges_xy:
        ext = on_border(a) and on_border(b)
        walls.append(cr.WallG(seg=(a, b), type='exterior' if ext else 'interior'))

    bbox = (bx0, by0, bx1 - bx0, by1 - by0)
    geom = cr.Geometry(plan_id=plan_id, house='APT(가정)', scale_mm_per_px=scale_mm_per_px,
                       bbox=bbox, rooms=rooms, walls=walls, doors=[], windows=[])
    return geom


def main():
    from plan2graph import cadrender as cr
    samples = reconstruct_samples()
    best = max(range(len(samples)), key=lambda i: len(samples[i][0]))
    polys, sems, edges_xy, ncorners = samples[best]
    print(f'\n>>> selected best sample index={best}: rooms={len(polys)}, '
          f'wall_segs={len(edges_xy)}, corners={ncorners}')

    geom = build_geometry(polys, sems, edges_xy, plan_id=f'gsdiff_{best}')
    print(f'Geometry: rooms={len(geom.rooms)}, walls={len(geom.walls)}, '
          f'scale_mm_per_px={geom.scale_mm_per_px:.2f}, bbox={tuple(round(v,1) for v in geom.bbox)}')
    for r in geom.rooms:
        print(f'  room id={r.id} role={r.role} area={r.area_m2:.2f}m2 npts={len(r.polygon)}')

    geom = cr.autocorrect(geom)
    print('autocorrect correct_log:', geom.correct_log)
    print('residual issues:', geom.issues)

    fig = cr.render_fig(geom)
    fig.savefig('/tmp/gsdiff_cadrender.png', dpi=150)
    print('wrote /tmp/gsdiff_cadrender.png')

    dxf_bytes = cr.render_dxf(geom)
    with open('/tmp/gsdiff_cadrender.dxf', 'wb') as f:
        f.write(dxf_bytes)
    print(f'wrote /tmp/gsdiff_cadrender.dxf ({len(dxf_bytes)} bytes)')

    import shutil
    shutil.copy('/tmp/gsdiff_cadrender.png', '/tmp/gsdiff_cadrender_best.png')
    print('copied -> /tmp/gsdiff_cadrender_best.png')
    print('DONE')


if __name__ == '__main__':
    main()
