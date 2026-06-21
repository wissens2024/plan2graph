"""FastAPI 도면생성 앱 - Streamlit과 독립적"""
import json
from pathlib import Path
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
import sys

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

import config

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

        # TODO: 실제 도면생성 로직 구현

        return JSONResponse({
            "status": "success",
            "bedrooms": bedrooms,
            "bathrooms": bathrooms,
            "message": "준비 중"
        })

    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8502)
