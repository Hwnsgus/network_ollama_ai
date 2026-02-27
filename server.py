"""
FastAPI 백엔드 서버 - main.py 로직 기반 PDF 규격 분석 API
"""
import os
import uuid
import shutil
import pdfplumber
import ollama
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
import pandas as pd
from io import BytesIO

# main.py의 핵심 로직 임포트
from main import (
    load_internal_db,
    process_pdf,
    save_to_excel,
)

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOAD_DIR = os.path.join(SCRIPT_DIR, "uploads")
OUTPUT_DIR = os.path.join(SCRIPT_DIR, "outputs")

os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(OUTPUT_DIR, exist_ok=True)

app = FastAPI(title="PDF 규격 분석 API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/health")
def health():
    return {"status": "ok"}

@app.get("/api/internal-db/status")
def internal_db_status():
    """내부 DB 로드 상태 확인 (웹 화면 표시용)"""
    content = load_internal_db()
    if content.startswith("No internal") or content.startswith("Error"):
        return {"loaded": False, "message": content}
    return {"loaded": True, "preview": content}

@app.post("/api/process-pdf")
def api_process_pdf(
    file: UploadFile = File(...),
    model: str = Form("gemma3:27b"),
    save_excel: bool = Form(True),
):
    """[Step 1 & 2] 규격서 분석 및 엑셀 추출"""
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="PDF 파일만 가능합니다.")

    file_id = uuid.uuid4().hex
    pdf_path = os.path.join(UPLOAD_DIR, f"{file_id}.pdf")

    try:
        with open(pdf_path, "wb") as f:
            shutil.copyfileobj(file.file, f)

        items = process_pdf(pdf_path, model)

        if not items:
            return {"success": False, "message": "데이터 추출 실패"}

        excel_download_url = None
        if save_excel:
            excel_filename = f"result_{file_id}.xlsx"
            excel_path = os.path.join(OUTPUT_DIR, excel_filename)
            try:
                save_to_excel(items, excel_path, block_on_permission_error=False)
                excel_download_url = f"/api/download/{excel_filename}"
            except Exception as e:
                print(f"엑셀 저장 실패: {e}")

        return {"success": True, "items": items, "excel_path": excel_download_url}

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if os.path.exists(pdf_path):
            os.remove(pdf_path)

@app.get("/api/download/{filename}")
def download_excel(filename: str):
    path = os.path.join(OUTPUT_DIR, filename)
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="파일 없음")
    return FileResponse(path, filename=filename)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)