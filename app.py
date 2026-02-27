import streamlit as st
import requests
import pandas as pd

API_URL = "http://localhost:8000"

st.set_page_config(page_title="Ollama 규격서 분석기", layout="wide")
st.title("🤖 AI 규격서 자동 분석 및 검증 시스템")

# --- 사이드바 (공통 설정) ---
with st.sidebar:
    st.header("⚙️ 설정")
    model_name = st.selectbox("AI 모델 선택", ["gemma3:27b", "gpt-oss:latest", "qwen3-vl:latest"])
    st.divider()
    try:
        requests.get(f"{API_URL}/health")
        st.success("🟢 서버 연결 상태: 정상")
    except:
        st.error("🔴 서버 연결 실패 (주방이 꺼져있습니다!)")

# ==========================================
# 탭 1: 규격서 분석 (기존 기능 + JSON 확인)
# ==========================================
    # ★ 1. 자사 제품 DB(JSON) 사전 학습 상태 확인창

try:
    st.subheader("🏢 자사/협력사 제품 DB (사전 학습 데이터)")
    db_status = requests.get(f"{API_URL}/api/internal-db/status").json()
    if db_status.get("loaded"):
        st.success("✅ 자사 제품(JSON) DB가 정상적으로 로드되어 AI 분석 시 [최우선]으로 반영됩니다.")
        with st.expander("👀 학습된 DB 내용 미리보기 (클릭하여 펼치기)"):
            st.text(db_status.get("preview"))
    else:
        st.warning("⚠️ 내부 DB 파일(internal_products.json)이 없습니다. 외부 제품으로만 역추적합니다.")
except:
    st.error("서버에서 DB 정보를 불러오지 못했습니다.")

st.divider()

    # ★ 2. 기존 PDF 업로드 및 분석
st.subheader("📄 원본 규격서 업로드")
uploaded_file = st.file_uploader("비교할 원본 제안요청서(RFP) PDF를 업로드하세요", type=["pdf"])

if st.button("🚀 견적 분석 시작", type="primary"):
        if uploaded_file is None:
            st.warning("파일을 먼저 업로드해주세요.")
        else:
            with st.spinner("AI가 자사 DB와 대조하며 규격서를 꼼꼼히 읽고 있습니다... (1~3분 소요)"):
                try:
                    files = {"file": (uploaded_file.name, uploaded_file.getvalue(), "application/pdf")}
                    data = {"model": model_name, "save_excel": "true"}
                    
                    response = requests.post(f"{API_URL}/api/process-pdf", files=files, data=data)
                    
                    if response.status_code == 200:
                        result = response.json()
                        if result.get("success"):
                            st.success("🎉 분석 완료!")
                            items = result.get("items", [])
                            if items:
                                st.dataframe(pd.DataFrame(items), use_container_width=True)
                            
                            excel_path = result.get("excel_path")
                            if excel_path:
                                st.markdown(f"### [📥 분석 결과 엑셀 다운로드]({API_URL}{excel_path})")
                        else:
                            st.error(f"분석 실패: {result.get('message')}")
                    else:
                        st.error("서버 에러 발생")
                except Exception as e:
                    st.error(f"통신 오류: {e}")