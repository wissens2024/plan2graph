"""FastAPI 도면생성 앱 - Streamlit과 독립적"""
import json
import base64
from pathlib import Path
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
import sys

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

import config
from plan2graph import wallcycle_codec as wc
from plan2graph import cadrender as cr

app = FastAPI(title="KorPlan Floor Plan Generator")

# CORS 활성화
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Static 폴더 서빙
static_dir = ROOT / "static"
if static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

@app.get("/")
@app.get("/floorplan/")
@app.get("/floorplan")
async def root():
    """도면생성 페이지"""
    return FileResponse(ROOT / "static" / "floorplan.html")

@app.get("/health")
async def health():
    return {"status": "ok"}

@app.post("/api/generate")
async def generate_floorplan(bedrooms: int = 3, bathrooms: int = 2):
    """도면 생성 엔드포인트"""
    try:
        # 입력 검증
        if not (1 <= bedrooms <= 5):
            raise ValueError("침실은 1-5개여야 합니다")
        if not (1 <= bathrooms <= 3):
            raise ValueError("욕실은 1-3개여야 합니다")

        # 1️⃣ 모델 로드
        import torch
        from plan2graph.generators.wall_cycle import WallCycleLM

        ckpt_path = ROOT / "ckpts" / "korplan_ar_k_fmlm80m.pt"
        ckpt = torch.load(ckpt_path, map_location="cpu")
        a = ckpt["args"]

        mlp_w1_shape = ckpt["model"]["blocks.0.mlp.w1.weight"].shape
        dim_ff = mlp_w1_shape[0]

        model = WallCycleLM(wc.V["size"], d_model=a["d_model"], n_layer=a["n_layer"],
                           n_head=a.get("n_head", 8), max_len=a["max_len"],
                           dim_ff=dim_ff)
        model.load_state_dict(ckpt["model"])
        model.eval()

        # 2️⃣ 토큰 생성 (Prefix)
        vocab = wc.V
        prefix = [
            vocab.BOS,
            vocab.KOR,          # country: KR
            vocab.APT,          # housing: apartment
            vocab.SCHEMA_G0,    # schema: g-0.4
            vocab.SCOPE_UNIT,   # scope: unit
            1,                  # units: 1세대
        ]
        prefix_tensor = torch.tensor([prefix], dtype=torch.long)

        # 3️⃣ 도면 생성
        eos = vocab.EOS
        with torch.no_grad():
            generated = model.generate(prefix_tensor, max_new=650, eos=eos, temperature=1.0, top_k=40)

        row = generated[0].tolist()

        # 4️⃣ 디코딩 + 기하 구성
        canon = wc.decode(row, vocab)
        g = wc.canon_to_graph(canon)

        # 5️⃣ 렌더링
        geom = cr.from_geomgraph(g)
        geom = cr.autocorrect(geom)
        png_bytes = cr.render_png(geom)

        # Base64 인코딩
        png_b64 = base64.b64encode(png_bytes).decode()

        return JSONResponse({
            "status": "success",
            "bedrooms": bedrooms,
            "bathrooms": bathrooms,
            "image": f"data:image/png;base64,{png_b64}",
            "message": f"✅ {bedrooms}침실 {bathrooms}욕실 도면 생성 완료!"
        })

    except Exception as e:
        import traceback
        return JSONResponse(
            status_code=400,
            content={
                "status": "error",
                "error": str(e),
                "traceback": traceback.format_exc()
            }
        )

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8502)
