# GongsilAuto

공실닷컴(gongsil.com) 매물 자동 재등록 자동화 도구

오래된 매물을 자동으로 신규 등록하고 기존 매물을 삭제하여 매물 순위를 유지합니다.

---

## 작동 방식

1. 최초등록일 기준 가장 오래된 매물 순으로 정렬
2. 해당 매물의 모든 정보(주소, 면적, 가격, 사진 등) 추출
3. 신규매물등록 → 매물 유형 선택 → 저장한 내용 그대로 입력 → 등록
4. 기존 매물 삭제

---

## 설치

### 요구사항

- Python 3.9 이상
- pip

### 패키지 설치

```bash
pip install -r requirements.txt
playwright install chromium
```

---

## 설정

`.env.example`을 복사해서 `.env` 파일을 만들고 정보를 입력합니다.

```bash
cp .env.example .env
```

`.env` 파일 내용:

```env
# 공실닷컴 로그인 정보
GONGSIL_ID=아이디입력
GONGSIL_PW=비밀번호입력
GONGSIL_PAGE=페이지번호

# 스케줄 설정 (쉼표로 구분, 24시 형식)
SCHEDULE_TIMES=09:00,18:00

# 브라우저 표시 여부 (true=숨김, false=보임)
HEADLESS=true

# 1회 실행당 재등록할 매물 수 (0=1개)
MAX_PER_RUN=0
```

> `GONGSIL_PAGE`는 공실닷컴 내 마이페이지 URL에 포함된 숫자 ID입니다.

---

## 실행

### 스케줄 자동 실행 (매일 09:00, 18:00 KST)

```bash
python run.py
```

### 즉시 1회 실행

```bash
python run.py --now
```

### 브라우저 화면 보면서 실행 (디버깅용)

```bash
python run.py --now --show
```

---

## 파일 구조

```
GongsilAuto/
├── gongsil.py        # 자동화 메인 로직
├── run.py            # 스케줄러 및 실행 진입점
├── requirements.txt  # 패키지 목록
├── .env.example      # 환경변수 템플릿
├── .env              # 실제 설정 (gitignore됨 - 직접 생성)
└── logs/             # 실행 로그 (gitignore됨)
```

---

## 주의사항

- `.env` 파일은 절대 GitHub에 업로드하지 마세요. (`.gitignore`에 포함되어 있습니다)
- 공실닷컴 이용약관을 준수하여 사용하세요.
