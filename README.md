# 주일설교 A4 2장 자동 요약 웹 프로그램 배포 가이드

## 1. GitHub 저장소 업로드
1. GitHub에서 새 저장소(Repository)를 생성합니다.
2. 압축 해제한 `app.py`, `requirements.txt`, `packages.txt` 3개 파일을 업로드합니다.

## 2. Streamlit Cloud 무료 웹 배포
1. [share.streamlit.io](https://share.streamlit.io) 접속 후 GitHub 계정으로 로그인합니다.
2. **New app** 버튼을 누르고 저장소와 `app.py`를 선택합니다.
3. **Advanced settings** (또는 배포 후 우측 하단 **Manage app → Settings → Secrets**)에 아래 내용을 입력합니다.

```toml
GEMINI_API_KEY = "발급받은_제미나이_API_키"
APP_PASSWORD = "팀원들과_공유할_접속_비밀번호"
```

4. **Deploy**를 클릭하면 고유 웹 주소(`https://...streamlit.app`)가 생성되며, 팀원들에게 해당 주소와 비밀번호만 공유하면 바로 사용할 수 있습니다.
