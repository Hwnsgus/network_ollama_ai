import pdfplumber
import pandas as pd
import json
import urllib.parse
import os
import sys
import argparse
import re
import math
import datetime
import ollama

# ==========================================
# ★ 0. 로그 및 출력 클래스
# ==========================================
class DualWriter:
    def __init__(self, filename):
        self.terminal = sys.stdout
        self.log = open(filename, "w", encoding="utf-8")

    def write(self, message):
        try:
            self.terminal.write(message)
            self.log.write(message)
            self.flush()
        except Exception:
            pass

    def flush(self):
        try:
            self.terminal.flush()
            self.log.flush()
        except Exception:
            pass

# ==========================================
# ★ 1. 내부 제품 DB 로드 함수 (RAG 기초)
# ==========================================
def load_internal_db():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    db_path = os.path.join(script_dir, "internal_products.json")

    if not os.path.exists(db_path):
        print(f"[System] 'internal_products.json' 파일이 없어 외부 제품만 검색합니다.")
        # 디버깅용: 어디서 찾으려다 실패했는지 보여줌
        # print(f"   (찾는 위치: {db_path})") 
        return "No internal product list found. Proceed with external deduction only."
    
    try:
        with open(db_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            db_text = ""
            for item in data:
                # AI가 읽기 편한 포맷으로 변환
                db_text += f"- [OUR STOCK] Category: {item.get('category','')} | Maker: {item.get('maker','')} | Model: {item.get('model','')} | Specs: {item.get('specs','')}\n"
            return db_text
    except Exception as e:
        print(f"[Warning] 내부 DB 로드 실패: {e}")
        return "Error loading internal database."

# ==========================================
# ★ 2. Ollama 호출 함수
# ==========================================
def run_ollama_chat(model_name, prompt):
    full_response = ""
    print(f"\n      ▼ [AI 실시간 답변 시작] ▼")
    print("-" * 40)

    try:
        stream = ollama.chat(
            model=model_name,
            messages=[{'role': 'user', 'content': prompt}],
            options={
                'num_ctx': 8192,  # 긴 문맥 기억
                'temperature': 0.1, # 정확도 우선 (창의성 낮춤)
            },
            stream=True
        )

        for chunk in stream:
            content = chunk['message']['content']
            print(content, end="", flush=True)
            full_response += content

        print("\n" + "-" * 40)
        print(f"      ▲ [AI 답변 완료] ▲\n")
        return full_response

    except Exception as e:
        print(f"\n[System Error] Ollama 통신 실패: {e}")
        return None

# ==========================================
# ★ 3. JSON 정제 함수
# ==========================================
def clean_json_output(raw_text):
    raw_text = re.sub(r'```json\s*', '', raw_text)
    raw_text = re.sub(r'```', '', raw_text)
    match = re.search(r'(\{.*\}|\[.*\])', raw_text, re.DOTALL)
    if match:
        return match.group(0)
    return raw_text

# ==========================================
# ★ 4. PDF 처리 및 AI 분석 (핵심 로직)
# ==========================================
def process_pdf(pdf_path, model_name):
    # 1. 내부 DB 로드
    internal_products_str = load_internal_db()
    print(f"[System] 자사/협력사 제품 DB 준비 완료.")

    # 2. PDF 텍스트 추출
    pages_content = []
    try:
        with pdfplumber.open(pdf_path) as pdf:
            print(f"[System] '{os.path.basename(pdf_path)}' 로딩 중... (총 {len(pdf.pages)}페이지)")
            
            start_parsing = False
            for i, page in enumerate(pdf.pages):
                text = page.extract_text()
                if not text: continue
                
                # 규격서 시작/끝 감지 로직
                if "물품규격서" in text.replace(" ", "") or "Commodity Description" in text:
                    start_parsing = True
                if "별지" in text and "서식" in text:
                    start_parsing = False
                
                if start_parsing:
                    tables = page.extract_tables()
                    if tables or "품명" in text or "Specifications" in text:
                        pages_content.append(text)

    except Exception as e:
        print(f"[Error] PDF 읽기 실패: {e}")
        return []

    if not pages_content:
        print("[Warning] 특정 섹션을 찾지 못해 전체 페이지를 분석합니다.")
        with pdfplumber.open(pdf_path) as pdf:
            pages_content = [p.extract_text() for p in pdf.pages if p.extract_text()]

    # 3. 배치 처리 (3페이지씩 끊어서 분석)
    BATCH_SIZE = 3
    total_batches = math.ceil(len(pages_content) / BATCH_SIZE)
    all_items = []
    
    print(f"[System] 분석 대상: {len(pages_content)}페이지 / 총 {total_batches}회 실행")

    for i in range(total_batches):
        start_idx = i * BATCH_SIZE
        end_idx = min((i + 1) * BATCH_SIZE, len(pages_content))
        
        chunk_pages = pages_content[start_idx:end_idx]
        chunk_text = "\n".join(chunk_pages)
        
        if len(chunk_text.strip()) < 50: continue

        print(f"   └ [진행] {i+1}/{total_batches}번째 묶음 분석 중 (자사 DB 대조 + 스펙 역추적)...")

        # ▼ [하이브리드 프롬프트] ▼
        prompt = f"""
        Role: You are a Senior Pre-Sales Engineer for Pro AV & IT equipment.
        Task: Analyze the anonymous specifications and identify the EXACT product model.

        [STEP 1: CHECK INTERNAL INVENTORY (PRIORITY)]
        First, verify if any item in the request matches our Internal Product List below.
        If a match is found based on category and key specs, **YOU MUST SELECT THE INTERNAL PRODUCT**.
        
        >>> INTERNAL PRODUCT LIST <<<
        {internal_products_str}
        >>> END OF LIST <<<

        [STEP 2: IF NO INTERNAL MATCH -> DEDUCE EXTERNAL MODEL]
        Only if the item is NOT in our internal list, perform "Reverse Engineering" to find the original external brand.
        
        [RULES FOR DEDUCTION (EXTERNAL ITEMS)]
        1. **Analyze Specs**: Look for unique identifiers.
        2. **Find the Original**: Match specs against major brands.
        3. **Pricing Accuracy (CRITICAL)**: ALWAYS estimate the MSRP or market price in USD ($) first, because your training data is mostly in English. Then, convert that USD price to KRW (₩). Assume a fixed exchange rate of 1 USD = 1,400 KRW.
        
        [TARGET JSON STRUCTURE]
        {{
            "items": [
                {{
                    "item_number": "String",
                    "name": "String (Korean Name)",
                    "quantity": Integer,
                    "maker": "String",
                    "model": "String",
                    "estimated_usd": Integer (Estimated MSRP in USD. Output 0 if unknown),
                    "estimated_krw": Integer (Multiply estimated_usd by 1400 to get KRW. Output 0 if unknown),
                    "official_url": "String (Official manufacturer product URL if known. Otherwise leave empty)",
                    "search_keyword": "String (e.g., 'Maker Model price')"
                }}
            ]
        }}

        [INPUT TEXT (PROCUREMENT SPECS)]
        {chunk_text}
        
        [OUTPUT]
        Output ONLY valid JSON string.
        """
    
        response = run_ollama_chat(model_name, prompt)
        
        if response:
            try:
                cleaned = clean_json_output(response)
                parsed = json.loads(cleaned)
                
                items_list = []
                if isinstance(parsed, dict) and "items" in parsed:
                    items_list = parsed["items"]
                elif isinstance(parsed, list):
                    items_list = parsed
                
                if items_list:
                    all_items.extend(items_list)
                    print(f"      -> {len(items_list)}개 항목 추출 성공")
                else:
                    print(f"      -> [알림] 추출된 항목 없음")

            except json.JSONDecodeError:
                print(f"      -> [실패] JSON 파싱 실패.")
            except Exception as e:
                print(f"      -> [오류] {e}")

    return all_items

# ==========================================
# ★ 5. 엑셀 저장 (단가, 총액, 링크 추가 버전)
# ==========================================
def save_to_excel(data, output_path, block_on_permission_error=True):
    df = pd.DataFrame(data)
    
    # 1. 필수 숫자 컬럼들이 아예 없을 경우를 대비해 0으로 채워진 컬럼 임시 생성
    for col in ['quantity', 'estimated_usd', 'estimated_krw']:
        if col not in df.columns:
            df[col] = 0
        # 안전하게 숫자로 변환 (문자열이 섞여있어도 NaN으로 만들고 0으로 채움)
        df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0)
    
    # 2. 총 금액 계산 (수량 * 원화 단가)
    df['total_price'] = df['quantity'] * df['estimated_krw']
    
    # 3. 스마트 링크 생성 (공식 URL 우선, 없으면 네이버 검색)
    def make_smart_link(row):
        # AI가 공식 URL을 알고 있다면 공식 홈페이지 링크 제공
        if row.get('official_url') and str(row['official_url']).startswith('http'):
            return f'=HYPERLINK("{row["official_url"]}", "🌐 공식 홈페이지 이동")'
        
        # 공식 URL이 없다면 네이버 쇼핑 검색 링크 생성
        query = str(row.get('search_keyword', ''))
        if len(query) < 2 and row.get('maker') and row.get('model'):
            clean_model = re.sub(r'[^\w\s-]', '', str(row['model']))
            query = f"{row['maker']} {clean_model}"
        elif len(query) < 2:
            query = str(row.get('name', ''))
            
        search_url = f"https://search.shopping.naver.com/search/all?query={urllib.parse.quote(query)}"
        return f'=HYPERLINK("{search_url}", "🛒 네이버 쇼핑 검색")'

    df['purchase_link'] = df.apply(make_smart_link, axis=1)
    
    # 4. 엑셀 컬럼 이름 한글화 및 순서 정렬
    cols = {
        'item_number': '물품 번호',
        'name': '품명',
        'maker': '제조사(Maker)', 
        'model': '모델명(Model)',
        'quantity': '수량',
        'estimated_krw': '단가(추정 ₩)',
        'estimated_usd': '단가(추정 $)',
        'total_price': '총 금액(₩)',
        'purchase_link': '참조 링크'
    }
    
    for k in cols.keys():
        if k not in df.columns: df[k] = ""
        
    df = df[list(cols.keys())]
    df.columns = [cols[c] for c in cols.keys()]
    
    # 저장 로직
    while True:
        try:
            df.to_excel(output_path, index=False)
            print(f"\n[System] 엑셀 저장 완료: {output_path}")
            break
        except PermissionError:
            if not block_on_permission_error:
                raise
            print(f"\n[Warning] 엑셀 파일이 열려 있습니다. 닫고 엔터를 누르세요.")
            input()
        except Exception as e:
            print(f"[Error] 저장 실패: {e}")
            break

# ==========================================
# ★ 메인: CLI 인자 처리
# ==========================================
if __name__ == "__main__":
    # 윈도우 한글 인코딩 설정
    if sys.platform == "win32":
        os.system('chcp 65001 > nul')

    parser = argparse.ArgumentParser()
    parser.add_argument('pdf_path', help='PDF 파일 경로')
    parser.add_argument('--model', default='gemma3:27b', help='Ollama 모델명')
    parser.add_argument('--output', help='저장 경로')

    try:
        args = parser.parse_args()
    except:
        print("[Error] 인자 파싱 실패")
        sys.exit(1)

    # 로그 폴더 설정
    log_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
    if not os.path.exists(log_dir):
        os.makedirs(log_dir)

    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = os.path.join(log_dir, f"log_{timestamp}.txt")

    sys.stdout = DualWriter(log_file)
    print(f"[Log] 로그 파일 생성됨: {log_file}")

    if not os.path.exists(args.pdf_path):
        print(f"[Error] PDF 파일 없음: {args.pdf_path}")
        sys.exit(1)

    output_file = args.output if args.output else os.path.splitext(args.pdf_path)[0] + f"_ollama.xlsx"

    # 전체 로직 실행
    items = process_pdf(args.pdf_path, args.model)
    
    if items:
        save_to_excel(items, output_file)
    else:
        print("[System] 추출된 데이터가 없습니다.")