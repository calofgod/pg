import streamlit as st
import yt_dlp
from youtube_transcript_api import YouTubeTranscriptApi
import re
import json
import io
from google import genai
from google.genai import types
from weasyprint import HTML
from pypdf import PdfReader
from docx import Document
from docx.shared import Pt, RGBColor, Cm

# ---------------------------------------------------------
# 1. 페이지 설정 및 비밀번호 인증 (팀원 공유용)
# ---------------------------------------------------------
st.set_page_config(page_title="주일설교 요약 생성기", page_icon="📖", layout="wide")

def check_password():
    if "APP_PASSWORD" not in st.secrets:
        return True
    if st.session_state.get("authenticated", False):
        return True
    st.title("🔒 주일설교 요약 웹 프로그램")
    pwd = st.text_input("팀원 전용 접속 비밀번호를 입력하세요:", type="password")
    if st.button("접속하기", type="primary"):
        if pwd == st.secrets["APP_PASSWORD"]:
            st.session_state["authenticated"] = True
            st.rerun()
        else:
            st.error("비밀번호가 올바르지 않습니다.")
    return False

if not check_password():
    st.stop()

st.title("📖 주일설교 A4 2장 자동 요약 프로그램")
st.caption("유튜브 설교 주소와 1~3대지/적용질문을 입력하면 A4 2장 분량의 PDF, 한글(.hwp), 워드(.docx) 파일을 즉시 생성합니다.")

# API 키 로드 (Secrets 우선, 없으면 사이드바 입력)
api_key = st.secrets.get("GEMINI_API_KEY", "")
with st.sidebar:
    st.header("⚙️ 설정 및 양식 안내")
    if not api_key:
        api_key = st.text_input("Gemini API Key 입력", type="password")
    else:
        st.success("✅ 서버 API 키가 연결되었습니다.")
    st.markdown("---")
    st.markdown("""
    **📋 출력 문서 규격 (A4 2장 고정)**
    - **상단 (0.5장 이내):** 제목, 설교자/일자, 성경구절 (**푸른색**)
    - **본문 (1.5장):** 도입(3~4줄) + 1~3대지 요약(목사님 원문 어조 유지, 시간 표시 제거)
    - **대지 제목:** **<u>진하게 + 밑줄</u>**
    - **적용질문:** <span style='color:#7B1FA2;'>자주색</span>
    """, unsafe_allow_html=True)

# ---------------------------------------------------------
# 2. 유튜브 ID, 설명란(성경구절/제목), 자막 추출 함수
# ---------------------------------------------------------
def extract_video_id(url):
    patterns = [
        r'(?:v=|\/)([0-9A-Za-z_-]{11}).*',
        r'youtu\.be\/([0-9A-Za-z_-]{11})',
        r'youtube\.com\/live\/([0-9A-Za-z_-]{11})'
    ]
    for p in patterns:
        match = re.search(p, url)
        if match:
            return match.group(1)
    return None

def fetch_youtube_data(url):
    video_id = extract_video_id(url)
    if not video_id:
        raise ValueError("올바른 유튜브 주소를 입력해주세요.")

    # 1) yt-dlp로 제목 및 설명란(조회수 아래 영역) 추출
    ydl_opts = {'skip_download': True, 'quiet': True}
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=False)
        yt_title = info.get('title', '')
        yt_desc = info.get('description', '')

    # 2) youtube-transcript-api로 자막 추출 (시간 표시 제외 텍스트만 병합)
    transcript_text = ""
    try:
        ytt_api = YouTubeTranscriptApi()
        fetched = ytt_api.fetch(video_id, languages=['ko'])
        transcript_text = " ".join([snippet.text.replace('\n', ' ').strip() for snippet in fetched])
    except Exception:
        # fallback: yt-dlp 자동자막
        ydl_sub_opts = {
            'writesubtitles': True,
            'writeautomaticsub': True,
            'subtitleslangs': ['ko'],
            'skip_download': True,
            'quiet': True
        }
        with yt_dlp.YoutubeDL(ydl_sub_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            subs = info.get('subtitles', {}).get('ko') or info.get('automatic_captions', {}).get('ko')
            if subs:
                import urllib.request
                for s in subs:
                    if s.get('ext') == 'vtt':
                        vtt = urllib.request.urlopen(s['url']).read().decode('utf-8')
                        lines = [
                            re.sub(r'<[^>]+>', '', line).strip()
                            for line in vtt.splitlines()
                            if '-->' not in line and not line.startswith('WEBVTT') and not line.strip().isdigit()
                        ]
                        transcript_text = " ".join([l for l in lines if l])
                        break

    if not transcript_text:
        raise ValueError("이 영상에서 한국어 자막을 가져올 수 없습니다. 자막이 활성화된 설교 영상인지 확인해주세요.")

    return yt_title, yt_desc, transcript_text

# ---------------------------------------------------------
# 3. Gemini AI 요약 생성 함수
# ---------------------------------------------------------
def generate_sermon_summary(api_key, yt_title, yt_desc, transcript, p1, p2, p3):
    client = genai.Client(api_key=api_key)
    prompt = f"""
당신은 주일설교 영상을 교인들이 묵상하고 목장 모임에서 나눌 수 있도록 A4 용지 정확히 2장 분량의 요약본으로 편집하는 전문 에디터입니다.

[입력 정보]
- 유튜브 영상 제목: {yt_title}
- 유튜브 조회수 아랫부분(설명란): 
{yt_desc[:2000]}
- 설교 대본(자막): 
{transcript[:30000]}
- 1대지 및 적용질문:
{p1}
- 2대지 및 적용질문:
{p2}
- 3대지 및 적용질문:
{p3}

[편집 필수 지침]
1. 상단 정보 (A4 0.5장 이내):
   - 유튜브 제목과 설명란에서 '설교 제목', '날짜_설교자(예: 11/22 주일설교_김진규 목사님)', '성경구절 장절(예: 고린도전서 12장 1~11절)', '성경 본문 전체(절 번호 포함)'를 정확히 추출하세요.
   - 만약 설명란에 성경 본문이 일부 생략되어 있다면 해당 장절의 정확한 개역개정 성경 본문을 보완하여 절 번호와 함께 완성하세요.
2. 도입부 (intro):
   - 설교 서론의 예화나 핵심 화두를 목사님의 실제 표현과 어조를 살려 **3~4줄(공백 포함 약 250~300자)**로 작성하세요. 시간 표시(00:00)는 절대 포함하지 마세요.
3. 대지별 본문 요약 (body):
   - 전체 문서가 A4 용지 **정확히 2장**을 채우도록, 1대지·2대지·3대지 본문은 각각 **공백 포함 약 850~950자** 내외로 작성하세요.
   - 딱딱한 개조식 요약이 아니라 **목사님 설교 대본의 문장, 성경 구절 풀이, 목사님 본인의 간증 및 예화를 최대한 그대로 살려** 자연스러운 2~3개의 서술형 문단(`\n\n`으로 구분)으로 구성하세요.
4. 대지 제목 및 적용질문:
   - 사용자가 입력한 1~3대지 제목과 적용질문(Q. ~)을 누락 없이 정확하게 배치하세요.

반드시 아래 JSON 스키마로만 응답하세요:
{{
  "sermon_title": "설교 제목",
  "date_preacher": "날짜 및 설교자",
  "scripture_ref": "성경구절 장절",
  "scripture_verses": ["1 말씀 내용...", "2 말씀 내용..."],
  "intro": "도입 3~4줄 내용",
  "points": [
    {{
      "heading": "1. 대지 제목",
      "content": "1대지 요약 본문 (문단 구분은 \n\n)",
      "question": "Q. 1대지 적용질문"
    }},
    {{
      "heading": "2. 대지 제목",
      "content": "2대지 요약 본문 (문단 구분은 \n\n)",
      "question": "Q. 2대지 적용질문"
    }},
    {{
      "heading": "3. 대지 제목",
      "content": "3대지 요약 본문 (문단 구분은 \n\n)",
      "question": "Q. 3대지 적용질문"
    }}
  ]
}}
"""
    response = client.models.generate_content(
        model="gemini-2.5-pro",
        contents=prompt,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            temperature=0.25
        )
    )
    return json.loads(response.text)

# ---------------------------------------------------------
# 4. 문서 생성 함수 (PDF A4 2장 자동 보정 / HWP / DOCX)
# ---------------------------------------------------------
def render_pdf_html(data, font_size=9.5, line_height=1.50, margin_v=14):
    verses_html = "<br>".join(data["scripture_verses"])
    points_html = ""
    for p in data["points"]:
        paragraphs = "".join([
            f"<p class='body-text'>{para.strip()}</p>"
            for para in p["content"].split("\n\n") if para.strip()
        ])
        points_html += f"""
        <div class="point-block">
            <div class="point-heading">{p['heading']}</div>
            {paragraphs}
            <div class="point-question">{p['question']}</div>
        </div>
        """
    return f"""
    <!DOCTYPE html>
    <html lang="ko">
    <head>
    <meta charset="UTF-8">
    <style>
        @page {{
            size: A4;
            margin: {margin_v}mm 16mm {margin_v}mm 16mm;
        }}
        body {{
            font-family: 'NanumGothic', 'Malgun Gothic', sans-serif;
            font-size: {font_size}pt;
            line-height: {line_height};
            color: #111111;
            margin: 0;
            padding: 0;
            word-break: keep-all;
        }}
        .header-title {{ font-size: {font_size + 3.5}pt; font-weight: bold; margin-bottom: 2px; }}
        .header-meta {{ font-size: {font_size + 0.5}pt; font-weight: bold; margin-bottom: 6px; }}
        .scripture-box {{ color: #1B4F9C; font-size: {font_size - 0.4}pt; line-height: {line_height - 0.06}; margin-bottom: 9px; }}
        .scripture-ref {{ font-weight: bold; margin-bottom: 2px; }}
        .intro-box {{ margin-bottom: 9px; text-align: justify; }}
        .point-block {{ margin-bottom: 8px; }}
        .point-heading {{
            font-size: {font_size + 0.7}pt;
            font-weight: bold;
            text-decoration: underline;
            text-underline-offset: 3px;
            margin-top: 5px;
            margin-bottom: 4px;
        }}
        .body-text {{ margin: 0 0 4px 0; text-align: justify; }}
        .point-question {{ color: #7B1FA2; font-weight: 600; margin-top: 3px; margin-bottom: 5px; }}
    </style>
    </head>
    <body>
        <div class="header-title">{data['sermon_title']}</div>
        <div class="header-meta">{data['date_preacher']}</div>
        <div class="scripture-box">
            <div class="scripture-ref">{data['scripture_ref']}</div>
            <div>{verses_html}</div>
        </div>
        <div class="intro-box">{data['intro']}</div>
        {points_html}
    </body>
    </html>
    """

def create_exact_2page_pdf(data):
    # A4 2페이지에 딱 맞도록 폰트/줄간격 미세 자동 조정
    font_size = 9.6
    line_height = 1.52
    margin_v = 14
    pdf_bytes = HTML(string=render_pdf_html(data, font_size, line_height, margin_v)).write_pdf()
    
    for _ in range(8):
        page_count = len(PdfReader(io.BytesIO(pdf_bytes)).pages)
        if page_count == 2:
            break
        elif page_count > 2:
            font_size -= 0.25
            line_height -= 0.04
            margin_v = max(10, margin_v - 1)
        else:
            font_size += 0.3
            line_height += 0.05
        pdf_bytes = HTML(string=render_pdf_html(data, font_size, line_height, margin_v)).write_pdf()
    return pdf_bytes

def create_hwp_file(data):
    # 아래아한글에서 열었을 때 푸른색/자주색/진한밑줄/A4 2장 여백이 그대로 유지되는 HWP 문서
    verses_html = "<br>".join(data["scripture_verses"])
    points_html = ""
    for p in data["points"]:
        paragraphs = "".join([
            f"<p style='margin:0 0 4pt 0; line-height:150%; text-align:justify;'>{para.strip()}</p>"
            for para in p["content"].split("\n\n") if para.strip()
        ])
        points_html += f"""
        <p style='margin:7pt 0 3pt 0; font-size:10.2pt; font-weight:bold; text-decoration:underline;'>{p['heading']}</p>
        {paragraphs}
        <p style='margin:3pt 0 7pt 0; color:#7B1FA2; line-height:148%;'>{p['question']}</p>
        """
    hwp_html = f"""<html xmlns:o="urn:schemas-microsoft-com:office:office" xmlns:w="urn:schemas-microsoft-com:office:word">
    <head><meta charset="utf-8">
    <style>
        @page {{ size: A4; margin: 14mm 16mm 14mm 16mm; }}
        body {{ font-family: '맑은 고딕', '함초롬바탕', sans-serif; font-size: 9.5pt; color: #000000; }}
    </style>
    </head>
    <body>
        <p style="font-size:13pt; font-weight:bold; margin:0 0 2pt 0;">{data['sermon_title']}</p>
        <p style="font-size:10pt; font-weight:bold; margin:0 0 6pt 0;">{data['date_preacher']}</p>
        <p style="color:#1B4F9C; font-weight:bold; margin:0 0 2pt 0;">{data['scripture_ref']}</p>
        <p style="color:#1B4F9C; font-size:9pt; line-height:142%; margin:0 0 9pt 0;">{verses_html}</p>
        <p style="line-height:150%; text-align:justify; margin:0 0 9pt 0;">{data['intro']}</p>
        {points_html}
    </body></html>"""
    return hwp_html.encode('utf-8')

def create_docx_file(data):
    doc = Document()
    for section in doc.sections:
        section.top_margin = Cm(1.4)
        section.bottom_margin = Cm(1.4)
        section.left_margin = Cm(1.6)
        section.right_margin = Cm(1.6)

    style = doc.styles['Normal']
    style.font.name = '맑은 고딕'
    style.font.size = Pt(9.5)

    p_title = doc.add_paragraph()
    p_title.paragraph_format.space_after = Pt(2)
    r_title = p_title.add_run(data['sermon_title'])
    r_title.bold = True
    r_title.font.size = Pt(13)

    p_meta = doc.add_paragraph()
    p_meta.paragraph_format.space_after = Pt(6)
    r_meta = p_meta.add_run(data['date_preacher'])
    r_meta.bold = True
    r_meta.font.size = Pt(10)

    blue_color = RGBColor(27, 79, 156)
    p_ref = doc.add_paragraph()
    p_ref.paragraph_format.space_after = Pt(2)
    r_ref = p_ref.add_run(data['scripture_ref'])
    r_ref.bold = True
    r_ref.font.color.rgb = blue_color

    for v in data['scripture_verses']:
        p_v = doc.add_paragraph()
        p_v.paragraph_format.space_after = Pt(1)
        p_v.paragraph_format.line_spacing = 1.25
        r_v = p_v.add_run(v)
        r_v.font.color.rgb = blue_color
        r_v.font.size = Pt(9)

    p_intro = doc.add_paragraph(data['intro'])
    p_intro.paragraph_format.space_before = Pt(6)
    p_intro.paragraph_format.space_after = Pt(7)
    p_intro.paragraph_format.line_spacing = 1.35

    purple_color = RGBColor(123, 31, 162)
    for pt in data['points']:
        p_head = doc.add_paragraph()
        p_head.paragraph_format.space_before = Pt(6)
        p_head.paragraph_format.space_after = Pt(3)
        r_head = p_head.add_run(pt['heading'])
        r_head.bold = True
        r_head.underline = True
        r_head.font.size = Pt(10.2)

        for para in pt['content'].split("\n\n"):
            if para.strip():
                p_body = doc.add_paragraph(para.strip())
                p_body.paragraph_format.space_after = Pt(4)
                p_body.paragraph_format.line_spacing = 1.35

        p_q = doc.add_paragraph()
        p_q.paragraph_format.space_after = Pt(6)
        r_q = p_q.add_run(pt['question'])
        r_q.font.color.rgb = purple_color

    bio = io.BytesIO()
    doc.save(bio)
    return bio.getvalue()

# ---------------------------------------------------------
# 5. 메인 화면 입력 폼 및 결과 출력
# ---------------------------------------------------------
col1, col2 = st.columns([1, 1], gap="large")

with col1:
    st.subheader("1️⃣ 설교 정보 입력")
    youtube_url = st.text_input("설교 유튜브 주소", placeholder="https://www.youtube.com/watch?v=...")
    point1 = st.text_area("1대지와 적용질문", height=115, placeholder="1. 신령한 것을 알아야 합니다.\nQ. 여러분은 하나님이 은혜로 주신 것을 가지고 나를 높이고 있지는 않습니까?")
    point2 = st.text_area("2대지와 적용질문", height=115, placeholder="2. 섬기는 자가 되어야 합니다.\nQ. 여러분은 맡겨진 자리에서 섬기고 있습니까?")
    point3 = st.text_area("3대지와 적용질문", height=115, placeholder="3. 서로의 유익을 구해야 합니다.\nQ. 여러분은 지금 누구를 믿음이 부족하다고 판단하고 있습니까?")

with col2:
    st.subheader("2️⃣ 생성 및 파일 다운로드")
    if st.button("✨ A4 2장 설교 요약본 생성하기", type="primary", use_container_width=True):
        if not api_key:
            st.error("Gemini API Key를 설정해주세요.")
        elif not youtube_url or not point1 or not point2 or not point3:
            st.warning("유튜브 주소와 1~3대지 및 적용질문을 모두 입력해주세요.")
        else:
            try:
                with st.spinner("1/3 유튜브 영상 정보와 자막을 추출하는 중..."):
                    yt_title, yt_desc, transcript = fetch_youtube_data(youtube_url)
                with st.spinner("2/3 목사님 어조를 살려 A4 2장 분량으로 요약·편집하는 중..."):
                    summary_data = generate_sermon_summary(api_key, yt_title, yt_desc, transcript, point1, point2, point3)
                with st.spinner("3/3 PDF, 한글(.hwp), 워드(.docx) 파일을 생성하는 중..."):
                    pdf_bytes = create_exact_2page_pdf(summary_data)
                    hwp_bytes = create_hwp_file(summary_data)
                    docx_bytes = create_docx_file(summary_data)

                safe_title = re.sub(r'[\\/*?:"<>|]', '', summary_data['sermon_title']).strip()
                st.success("✅ 생성이 완료되었습니다! 원하는 형식으로 다운로드하세요.")

                b1, b2, b3 = st.columns(3)
                with b1:
                    st.download_button("📄 PDF (A4 2장)", data=pdf_bytes, file_name=f"{safe_title}_요약.pdf", mime="application/pdf", use_container_width=True)
                with b2:
                    st.download_button("🇰🇷 한글 (.hwp)", data=hwp_bytes, file_name=f"{safe_title}_요약.hwp", mime="application/x-hwp", use_container_width=True)
                with b3:
                    st.download_button("📝 워드 (.docx)", data=docx_bytes, file_name=f"{safe_title}_요약.docx", mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document", use_container_width=True)

                st.markdown("---")
                st.markdown(f"#### {summary_data['sermon_title']}")
                st.markdown(f"**{summary_data['date_preacher']}**")
                st.markdown(f"<span style='color:#1B4F9C; font-weight:bold;'>{summary_data['scripture_ref']}</span>", unsafe_allow_html=True)
                st.markdown(f"<div style='color:#1B4F9C; font-size:0.9em; margin-bottom:10px;'>{'<br>'.join(summary_data['scripture_verses'])}</div>", unsafe_allow_html=True)
                st.write(summary_data['intro'])
                for p in summary_data['points']:
                    st.markdown(f"**<u>{p['heading']}</u>**", unsafe_allow_html=True)
                    st.write(p['content'])
                    st.markdown(f"<span style='color:#7B1FA2;'>{p['question']}</span>", unsafe_allow_html=True)
            except Exception as e:
                st.error(f"오류가 발생했습니다: {e}")
