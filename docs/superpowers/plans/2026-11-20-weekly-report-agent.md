# 주간보고 자동 작성 에이전트 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 건별 사유/내용/개발일정/요청부서/완료(예정)일과 CSR 수치를 웹 폼으로 입력받아, 직전 주간보고 hwpx와 같은 서식의 다음 주 `YYYYMMDD.hwpx`를 생성한다.

**Architecture:** 날짜·일정·(계속)·hwpx 파싱/렌더링은 결정적 코드, LLM은 타이틀과 사유/내용 문체 정리만 맡는다. 참고 프로젝트(`C:\1.devkjh\axedu\nh-agent-lab\완성본\5교시`)와 같은 계층(RAG 검색 · ReAct Agent · LangGraph 승인 interrupt · guardrails · RunLog · 검증 케이스표)으로 만든다. 모든 파일은 프로젝트 루트에 평평하게 둔다.

**Tech Stack:** Python 3.12, langchain 1.5.2 / langgraph 1.2.23 / langchain-openai 1.8.3 (참고 프로젝트 `requirements.txt` 고정 버전), mcp, python-dotenv, streamlit, pytest, 표준 라이브러리(zipfile, xml.etree).

**Spec:** `docs/superpowers/specs/2026-11-20-weekly-report-agent-design.md` (10-2절이 10-1절보다 우선)

## Global Constraints

- 패키지 버전은 참고 프로젝트 `requirements.txt`와 동일하게 고정, 추가는 `streamlit`·`pytest`(설치된 버전으로 고정)뿐. lxml 사용 금지(표준 라이브러리만).
- 모델 연결은 `.env`(`OPENAI_API_KEY`, `OPENAI_BASE_URL`, `CHAT_MODEL`, `EMBED_MODEL`) + `python-dotenv`; `ChatOpenAI(model=os.getenv("CHAT_MODEL"), temperature=0)`, `OpenAIEmbeddings(model=os.getenv("EMBED_MODEL"), check_embedding_ctx_length=False)`; 벡터 저장소는 `InMemoryVectorStore`. **`.env`는 읽거나 복사하거나 출력하지 않는다**(사용자가 직접 복사). 키 값은 화면/로그에 출력 금지.
- 파일은 모두 UTF-8, 스크립트 시작 시 `sys.stdout.reconfigure(encoding="utf-8")`. 실행 명령은 PowerShell 기준 `.venv\Scripts\python.exe 파일.py`.
- MCP 서버(stdio)는 print 금지.
- 코드는 짧고 단순하게. 요청하지 않은 기능 금지. 모델이 할 일(타이틀·문체)을 코드에 미리 적지 않는다.
- 주는 항상 월~금, 공휴일 보정 없음. 새 보고일 = 최신 `data/*.hwpx` 파일명 날짜 + 7일. 금주 = 그 주 월~금, 차주 = +7일, CSR 접수기간 = 금주 직전 주 월~금.
- 날짜 표기: `‘26.11.30.(월)` (여는 따옴표는 U+2018 `‘`, 요일은 한글 한 글자). 표1/헤더 기간: `11.30 ~ 12.04`. 일정 줄: `분석/설계 : ‘26.11.09.(월) ~ ‘26.11.13.(금)`, 종료일 없음 `본적용 및 모니터링 : ‘26.11.30.(월) ~`, 하루 `…모니터링 : ‘26.11.16.(월)`.
- (계속) 규칙: 건의 개발 단계(이름이 `본적용`으로 시작하지 않는 단계, 시작일 있는 것)가 해당 주와 겹치고 시작일이 그 주 월요일보다 앞서면 타이틀 끝에 ` (계속)`. 금주·차주 칸 동일 적용.
- 고정 문구 유지: 부서명 `유통운영1팀`, 소제목 `7. 농/축/수산물 결함 및 개선사항`, `CSR처리현황 및 문의 응대 현황`(접수기간 날짜만 변경). CSR 표 총계 행 = 물류 행 값. 처리율 = 처리/요청 정수 % (요청 0이면 `-`).
- LLM 검증 실패는 최대 2회 재작성(`MAX_RETRY = 2`) 후 사람 이관. 개인정보(주민·카드·전화번호) 원문은 로그·산출물에 남기지 않고 가린다.
- 사용자가 요청하기 전에는 git 커밋하지 않는다(이 폴더는 git 저장소가 아님). 계획에 커밋 단계 없음.
- 모든 테스트는 오프라인(가짜 임베딩·가짜 LLM)으로 돌고, 실제 모델 호출은 Task 1의 연결 확인과 Task 11의 선택 실행에서만 한다.

## Review Focus

1. 연말·연초에 걸친 주(예: 2026-12-28 ~ 2027-01-01): 연도 넘김 날짜 계산과 `‘27.01.01.(금)` 표기, 파일명 `20261228.hwpx`.
2. 금주 또는 차주에 건이 0개인 경우: 소제목만 남고 한글에서 열리는 유효한 hwpx여야 함.
3. 사유/내용에 `&`, `<`, `>`, 따옴표, 여러 줄이 들어간 경우: XML 이스케이프되어 문서가 깨지지 않아야 함.
4. CSR 요청 0건, 처리 > 요청 입력: 처리율 `-` 표시, 처리>요청은 폼에서 오류로 막음.
5. 같은 보고일 `reports/YYYYMMDD.hwpx`가 이미 있을 때: 조용히 덮어쓰지 않고 사용자 확인을 거친다.

## File Structure (프로젝트 루트)

| 파일 | 책임 |
|---|---|
| `schedule.py` | Phase/Item/Period/Weeks 모델, 날짜 표기, 주차 계산, 겹침·(계속) 판정 |
| `hwpx_io.py` | hwpx → `Report` 파싱(`read_report`) |
| `hwpx_render.py` | `Report` → hwpx 렌더(`render_report`), 구조 검증(`verify_hwpx`) |
| `guardrails.py` | `check`, `mask`, `check_facts` (참고 프로젝트 형태) |
| `item_search.py` | 과거 건 RAG 색인·검색 |
| `tools.py` | 도구 함수 묶음(읽기·계산·검색·저장)과 권한표 `PERMISSION` |
| `agent.py` | ReAct `Agent` 클래스 + 작성용 시스템 프롬프트 + `compose_item` |
| `graph.py` | LangGraph 그래프, `RunLog`, `run` |
| `app.py` | Streamlit 폼 |
| `mcp_server.py`, `.mcp.json` | 같은 도구의 MCP(stdio) 노출 |
| `check_connection.py` | 모델 연결 확인 |
| `검증/cases.json`, `검증/run_cases.py`, `검증/비교표.md` | 하네스 |
| `tests/` | pytest (`conftest.py`가 루트를 `sys.path`에 추가) |

---

### Task 1: 환경과 모델 연결

**Files:**
- Create: `requirements.txt`, `check_connection.py`, `tests/conftest.py`, `tests/test_smoke.py`

- [ ] **Step 1:** `requirements.txt`를 참고 프로젝트 것과 동일 버전으로 쓰고 끝에 `streamlit`, `pytest`를 추가한다. `py -3.12 -m venv .venv` 후 `.venv\Scripts\python.exe -m pip install -r requirements.txt`. 설치된 `streamlit`·`pytest` 버전을 `==`로 고정해 다시 저장한다.
- [ ] **Step 2:** `check_connection.py`를 참고 프로젝트의 같은 이름 파일과 동일하게 만든다(루트 `.env` 로드, `ChatOpenAI` 한 번 호출, 응답과 모델명 출력). 파일 안의 루트 탐색은 `Path(__file__).resolve().parent`를 쓴다.
- [ ] **Step 3:** `tests/conftest.py`에 프로젝트 루트를 `sys.path`에 넣는 코드를 쓰고, `tests/test_smoke.py::test_samples_exist`가 `data/20261109.hwpx`, `20261116.hwpx`, `20261123.hwpx` 존재를 단언한다.
- [ ] **Step 4:** Run `.venv\Scripts\python.exe -m pytest tests -v` → PASS. 사용자가 `.env`를 복사한 뒤 `.venv\Scripts\python.exe check_connection.py` → 모델 응답 출력(복사 전이면 이 확인만 보류하고 사용자에게 알린다).

---

### Task 2: 일정·날짜 로직 (`schedule.py`)

**Files:** Create `schedule.py`, `tests/test_schedule.py`

**Interfaces — Produces:**
```python
@dataclass(frozen=True)
class Period: start: date; end: date
@dataclass(frozen=True)
class Phase: name: str; start: date | None; end: date | None; open_ended: bool = False  # end==start 이고 open_ended False → 하루
@dataclass
class Item:
    title: str            # (계속) 제외한 타이틀
    continued: bool
    reason: list[str]     # 사유 줄들 (앞 공백/기호 제외한 본문 텍스트)
    content_label: str    # "내용" 또는 "개발내용"
    content: list[str]    # 내용 줄들 ("· " 하위 항목은 줄 앞에 "· " 포함)
    schedule_label: str   # "개발일정" 또는 "주요일정"
    phases: list[Phase]
    requester: str
    due: date | None
@dataclass(frozen=True)
class Weeks: report_date: date; this_week: Period; next_week: Period; csr_week: Period
def week_of(d: date) -> Period                      # d가 속한 주의 월~금
def next_report_date(latest: date) -> date          # +7일
def calc_weeks(report_date: date) -> Weeks
def fmt_date(d: date) -> str                        # ‘26.11.30.(월)
def fmt_range(p: Period) -> str                     # 11.30 ~ 12.04
def fmt_phase(ph: Phase) -> str                     # 일정 줄 텍스트 (Global Constraints 형식)
def dev_phases(item: Item) -> list[Phase]           # 본적용* 제외, start 있는 것
def overlaps(item: Item, week: Period) -> bool      # 모든 단계 대상, open_ended는 끝없음으로 취급
def is_continuing(item: Item, week: Period) -> bool
```

- [ ] **Step 1: Write the failing tests** — `test_calc_weeks`(`calc_weeks(date(2026,11,30))` → this `11.30~12.04`, next `12.07~12.11`, csr `11.23~11.27`), `test_next_report_date`(`date(2026,11,23)` → `date(2026,11,30)`), `test_fmt_date`(`date(2026,11,30)` → `"‘26.11.30.(월)"`), `test_fmt_phase`(범위·open_ended·하루 3형식), `test_year_end`(`calc_weeks(date(2026,12,28))` this 끝이 `date(2027,1,1)`, `fmt_date` → `"‘27.01.01.(금)"`), 그리고 (계속) 판정 6케이스를 손으로 만든 Item으로: ① 개발 `11.06~11.12`, week `11.09~11.13` → True ② 같은 건, week `11.16~11.20`(본적용 `11.16` 하루 포함) → False ③ 분석/설계 `11.23~11.27`, week `11.23~11.27` → False(시작일이 월요일) ④ 개발/테스트 `11.16~11.27` + 분석/설계 `11.09~11.13`, week `11.23~11.27` → True ⑤ 위 ④ 건, week `11.30~12.04` → False(본적용 open_ended만 남음) ⑥ 개발 `11.30~12.11`, week `12.07~12.11` → True. 추가 `test_overlaps_includes_cutover`: ⑤ 건의 week `11.30~12.04` → `overlaps` True.
- [ ] **Step 2:** Run `.venv\Scripts\python.exe -m pytest tests/test_schedule.py -v` → FAIL (모듈 없음).
- [ ] **Step 3:** 위 인터페이스대로 `schedule.py` 구현. 요일은 `"월화수목금토일"[d.weekday()]`.
- [ ] **Step 4:** 같은 명령 → PASS.

---

### Task 3: hwpx 파싱 (`hwpx_io.py`)

**Files:** Create `hwpx_io.py`, `tests/test_hwpx_io.py`

**Interfaces — Consumes:** Task 2의 `Item, Phase, Period`. **Produces:**
```python
@dataclass
class Csr: period: Period; requested: int; handled: int; calls: int; outages: int; on_time: int
@dataclass
class Report:
    report_date: date; dept: str
    this_week: Period; next_week: Period
    this_items: list[Item]; next_items: list[Item]
    csr: Csr
def read_report(path: Path) -> Report     # report_date는 파일명 YYYYMMDD
def parse_phase_line(text: str) -> Phase   # fmt_phase의 역
```
구현 노트(파서가 의존할 사실): zip 안 `Contents/section0.xml`, 표 4개 — 표1(부서명·금주/차주 기간 헤더), 표2(본문 2칸: 금주·차주), 표3 이하 CSR. 본문 한 줄 = 문단 하나(`hp:p`), 줄 종류는 `paraPrIDRef`, 한 줄 안은 `charPrIDRef`가 다른 `hp:run`들. 타이틀은 `○ ` 로 시작, 항목은 `  - 사유 :` / `  - 내용 :` 또는 `개발내용` / `  - 개발일정` 또는 `주요일정` / `    · 단계 : 날짜…` / `  - 요청부서 :` / `  - 완료(예정)일 :`. 사유·내용의 이어지는 줄은 들여쓰기만 있는 줄. 줄 앞 공백·`○`·`-`·`:` 같은 기호는 모델에 넣지 않고 렌더러가 복원한다. 타이틀 끝 `(계속)`은 떼어 `continued=True`.

- [ ] **Step 1: Write the failing tests** — 실제 샘플 사용:
  - `test_dates_0123`: `read_report(Path("data/20261123.hwpx"))` → `report_date==date(2026,11,23)`, `this_week==Period(date(2026,11,23),date(2026,11,27))`, `next_week==Period(date(2026,11,30),date(2026,12,4))`, `dept=="유통운영1팀"`, `csr.period==Period(date(2026,11,16),date(2026,11,20))`, `(csr.requested, csr.handled, csr.calls, csr.outages, csr.on_time)==(2,2,6,0,0)`.
  - `test_item_0123`: `this_items[0]` → `title=="축수산 일반매입등록(70-2048) 개선 개발"`, `continued is True`, `requester=="농협유통 IT지원팀"`, `due==date(2026,11,30)`, `schedule_label=="개발일정"`, phases 3개: `분석/설계 11.09~11.13`, `개발/테스트 11.16~11.27`, `본적용 및 모니터링 11.30~(open_ended)`; `next_items[0].continued is False`.
  - `test_csr_other_weeks`: 0116 → `csr.calls==7`, 접수기간 `11.09~11.13`; 0109 → `csr.calls==5`, 접수기간 `11.02~11.06`.
  - `test_continued_matches_rule`: 샘플 3개 × (금주, 차주) 6개 칸의 모든 건에서 `is_continuing(item, week) == item.continued` (Task 2 규칙이 샘플과 일치함을 고정).
  - `test_parse_phase_line`: 세 형식 왕복(`parse_phase_line(fmt_phase(p)) == p`).
- [ ] **Step 2:** Run `.venv\Scripts\python.exe -m pytest tests/test_hwpx_io.py -v` → FAIL.
- [ ] **Step 3:** `hwpx_io.py` 구현(`zipfile` + `xml.etree.ElementTree`, 네임스페이스 `hp`). 샘플에서 어긋나는 파싱 사례(예: 0109의 `주요일정` 아래 `개발일정` 단계, 하루짜리 날짜)를 만나면 모델이 아니라 파서를 고친다. 테스트의 기대 값이 샘플과 다르다고 판명되면 구현을 멈추고 사용자에게 보고한다.
- [ ] **Step 4:** 같은 명령 → PASS.

---

### Task 4: hwpx 렌더링과 구조 검증 (`hwpx_render.py`)

**Files:** Create `hwpx_render.py`, `tests/test_hwpx_render.py`

**Interfaces — Consumes:** Task 2·3의 모델과 `read_report`. **Produces:**
```python
def render_report(template: Path, report: Report, out: Path) -> None
def verify_hwpx(path: Path) -> list[str]     # 빈 목록 = 통과
def preview_text(report: Report) -> str      # 사람이 읽는 텍스트 요약 (폼 미리보기·승인용)
```
`render_report`: 템플릿 zip을 복제하고 `Contents/section0.xml`만 바꾼다. 건 영역은 템플릿의 줄 유형별 견본 문단(타이틀, 사유, 내용, 일정 머리, 일정 단계, 요청부서, 완료일)을 복제해 `hp:t`와 같은 run 구성으로 채운다. `linesegarray`는 복제한 문단에서 제거한다. 출력 XML의 네임스페이스 접두사(`hp:`, `hs:` 등)는 원본 그대로여야 한다. `mimetype`은 zip의 첫 항목·무압축. `Preview/PrvText.txt`는 `preview_text`가 아니라 새 본문 텍스트로 갱신하고 `Preview/PrvImage.png`는 제거하거나 유지(빌더 판단, 어느 쪽이든 `verify_hwpx` 통과). 사유/내용은 XML 이스케이프한다. 템플릿에 해당 줄 유형 견본이 없으면 다른 샘플에서 찾고, 그래도 없으면 `ValueError`.
`verify_hwpx`: zip 유효 / `mimetype` 첫 항목·무압축 / `section0.xml` 파싱 / 본문이 쓰는 모든 `paraPrIDRef`·`charPrIDRef`가 `header.xml`에 존재 / 고정 문구 3개 존재 / 날짜 표기 정규식 `‘\d\d\.\d\d\.\d\d\.\([월화수목금토일]\)` 불일치 없음 — 각 위반을 문자열로 반환.

- [ ] **Step 1: Write the failing tests** (`tmp_path` 사용):
  - `test_roundtrip_samples` (3개 샘플 파라미터화): `r = read_report(sample)`; `render_report(Path("data/20261123.hwpx"), r, out)`; `read_report(out) == r` 이고 `verify_hwpx(out) == []`.
  - `test_style_ids_preserved`: 0123을 자기 자신 템플릿으로 렌더한 결과의 본문 문단별 `(paraPrIDRef, charPrIDRef 목록)` 시퀀스가 원본과 같다.
  - `test_prefix_preserved`: 출력 `section0.xml` 바이트에 `b"<hp:p "`가 있고 `b"ns0:"`가 없다.
  - `test_empty_column` (Review Focus 2): `this_items=[]`로 렌더 → `verify_hwpx==[]`, `read_report(out).this_items == []`, 소제목 `7. 농/축/수산물 결함 및 개선사항` 유지.
  - `test_xml_escape` (3): 사유에 `"A&B <C> '따옴표'"`와 두 줄을 넣어 렌더 → `read_report(out).this_items[0].reason`이 입력과 같다.
  - `test_csr_rate` (4): `Csr(requested=0, handled=0, ...)`이면 본문 텍스트의 처리율 칸이 `-`, `(3,3)`이면 `100%`, `(7,5)`이면 `71%`.
  - `test_verify_catches_bad_style`: 렌더 결과에서 `paraPrIDRef="9999"`로 바꾼 사본은 `verify_hwpx`가 비어 있지 않다.
  - `test_year_end_dates` (1): 2026-12-28 주 보고서를 만들어 렌더 → 텍스트에 `‘27.01.01.(금)`.
- [ ] **Step 2:** Run `.venv\Scripts\python.exe -m pytest tests/test_hwpx_render.py -v` → FAIL.
- [ ] **Step 3:** 위 시그니처대로 구현. 처리율은 `round(handled/requested*100)`에 `%`.
- [ ] **Step 4:** 같은 명령 → PASS. 그리고 `.venv\Scripts\python.exe -c "from pathlib import Path; from hwpx_io import read_report; from hwpx_render import render_report; render_report(Path('data/20261123.hwpx'), read_report(Path('data/20261123.hwpx')), Path('tmp_check.hwpx'))"` 로 파일을 만들어 사용자가 한글에서 열 수 있게 경로를 알린다(열림 확인은 사용자가 수행, 확인 후 파일 삭제).

---

### Task 5: 가드레일 (`guardrails.py`)

**Files:** Create `guardrails.py`, `tests/test_guardrails.py`

**Interfaces — Produces:**
```python
def mask(text: str) -> str                       # 참고 프로젝트와 같은 PII 가리기
def find_pii(text: str) -> list[str]
def check(text: str) -> list[str]                # 규칙2(개인정보 원문)만, 위반 목록
def check_facts(output: str, allowed: str) -> list[str]   # 규칙1: output의 숫자 덩어리가 allowed에 없으면 위반
```
`check_facts`: 정규식 `\d+`로 숫자 덩어리를 뽑아 `output`에만 있는 것을 `"규칙1 입력에 없는 숫자: 2099"` 형태로 반환(중복 제거, 정렬). `(계속)`·`(완료)` 같은 글자는 숫자가 없으므로 영향 없음.

- [ ] **Step 1: Write the failing tests:** `test_mask_phone`(`"010-1234-5678"` → `"010-****-5678"`), `test_check_pii`(주민번호 `900101-1234567` 포함 문장은 `check`가 `규칙2` 위반을 반환, 평범한 문장은 `[]`), `test_check_facts_ok`(`check_facts("매입등록(70-2048) 개선", "70-2048 화면 개선")==[]`), `test_check_facts_invented`(`check_facts("매입등록(70-2099) 개선", "70-2048 화면 개선")==["규칙1 입력에 없는 숫자: 2099"]`).
- [ ] **Step 2:** Run `.venv\Scripts\python.exe -m pytest tests/test_guardrails.py -v` → FAIL.
- [ ] **Step 3:** `mask`·`find_pii`·`PII`는 참고 프로젝트 `guardrails.py`와 같은 규칙으로 구현하고, `check`는 규칙2만, `check_facts`를 새로 구현.
- [ ] **Step 4:** 같은 명령 → PASS.

---

### Task 6: 과거 건 RAG와 도구 묶음 (`item_search.py`, `tools.py`)

**Files:** Create `item_search.py`, `tools.py`, `tests/test_item_search.py`, `tests/test_tools.py`

**Interfaces — Consumes:** `read_report`, `calc_weeks`, `render_report`, `verify_hwpx`. **Produces:**
```python
# item_search.py
def build_store(data_dir: Path, embeddings=None) -> InMemoryVectorStore
    # 건마다 Document 하나. page_content=타이틀+사유+내용 줄, metadata["source"]="20261123/금주/1"
    # embeddings None이면 .env의 OpenAIEmbeddings(check_embedding_ctx_length=False)
def search_items(store, query: str, k: int = 4) -> list[dict]   # {"source","title","reason","content"}
# tools.py
PERMISSION = {"read_report": "자동", "calc_weeks": "자동", "search_items": "자동", "save_report": "승인 후"}
def latest_report_path(data_dir: Path) -> Path
def tool_read_report(file_name: str) -> str        # JSON 문자열 (Report를 dict로)
def tool_calc_weeks(report_date: str) -> str       # JSON (ISO 날짜 입력)
def tool_search_items(query: str) -> str           # JSON
def tool_save_report(report_json: str, out_name: str, overwrite: bool = False) -> str   # reports/out_name 저장 경로 JSON, 이미 있으면 overwrite=False일 때 {"오류": "이미 존재"}
def report_to_json(r: Report) -> str; def report_from_json(s: str) -> Report
```

- [ ] **Step 1: Write the failing tests:** `test_build_store_counts`(가짜 임베딩 `DeterministicFakeEmbedding(size=32)`로 `data/` 색인 → `search_items(store, "축수산 거래명세서 첨부", k=2)`가 2건 이하이며 각 `source`가 `\d{8}/(금주|차주)/\d+` 형식), `test_latest_report_path`(`data/`에서 `20261123.hwpx`), `test_report_json_roundtrip`(0123 `read_report` → `report_from_json(report_to_json(r)) == r`), `test_calc_weeks_tool`(`tool_calc_weeks("2026-11-30")` JSON의 `this_week` 시작이 `"2026-11-30"`), `test_save_report_refuses_overwrite`(tmp `reports` 디렉터리에 같은 이름을 두 번 저장 → 두 번째는 `"이미 존재"` 오류 JSON, `overwrite=True`면 저장; 저장 전에 `verify_hwpx`로 검증하고 실패하면 저장하지 않음). 도구 함수들은 저장 디렉터리·데이터 디렉터리를 모듈 변수(`DATA_DIR`, `REPORTS_DIR`)로 두어 테스트가 monkeypatch한다.
- [ ] **Step 2:** Run `.venv\Scripts\python.exe -m pytest tests/test_item_search.py tests/test_tools.py -v` → FAIL.
- [ ] **Step 3:** 구현. `tool_search_items`는 모듈 수준 저장소를 처음 호출 때 한 번만 만든다(참고 프로젝트 `_chain` 패턴). 저장 도구가 렌더에 쓰는 템플릿은 `latest_report_path`.
- [ ] **Step 4:** 같은 명령 → PASS.

---

### Task 7: 작성 에이전트 (`agent.py`)

**Files:** Create `agent.py`, `tests/test_agent.py`

**Interfaces — Consumes:** `tools.tool_search_items`, `guardrails.check_facts`. **Produces:**
```python
class Agent:   # 참고 프로젝트 agent.py의 Agent와 같은 모양(도구 바인딩, max_steps, on_event)
    def __init__(self, tools, system: str, max_steps: int = 8, llm=None)
    async def ainvoke(self, inp: dict, on_event=None) -> dict
@dataclass
class ItemInput: reason: str; content: str; phases: list[Phase]; requester: str; due: date | None
@dataclass
class Composed: title: str; reason: list[str]; content: list[str]
async def compose_item(inp: ItemInput, agent: Agent, feedback: str = "") -> Composed
COMPOSE_SYSTEM: str
```
`COMPOSE_SYSTEM`(한국어, 짧게): 유사한 과거 건을 `search_items`로 찾아 타이틀과 사유/내용을 과거 문체로 정리한다 / 입력에 없는 숫자·화면번호·사실을 쓰지 않는다 / 결과는 JSON(`title`, `reason`[], `content`[])만 출력한다. 일정·날짜·(계속)은 쓰지 않는다. `compose_item`은 입력과 `feedback`(이전 검증 실패 사유)을 사용자 메시지로 만들어 에이전트를 호출하고 마지막 메시지의 JSON을 `Composed`로 파싱한다. `llm` 인자로 가짜 모델 주입 가능(`temperature=0`의 `ChatOpenAI`가 기본).

- [ ] **Step 1: Write the failing tests** (`langchain_core`의 가짜 채팅 모델로 오프라인):
  - `test_agent_loop_runs_tool`: 첫 응답이 `search_items` 도구 요청, 둘째 응답이 JSON 답인 가짜 모델 → 도구가 정확히 한 번 실행되고 `compose_item`이 `Composed(title="…")`를 반환한다.
  - `test_agent_max_steps`: 도구 요청만 계속하는 가짜 모델 → `max_steps` 후 종료하고 마지막 메시지에 `반복 상한 도달`이 들어간다.
  - `test_compose_passes_feedback`: `feedback="규칙1 입력에 없는 숫자: 2099"`를 주면 가짜 모델이 받은 사용자 메시지에 그 문자열이 포함된다.
- [ ] **Step 2:** Run `.venv\Scripts\python.exe -m pytest tests/test_agent.py -v` → FAIL.
- [ ] **Step 3:** 참고 프로젝트 `agent.py`의 `Agent` 루프를 같은 구조로 구현하되 MCP 로딩 없이 LangChain 도구 목록(`tools.py` 함수를 `langchain_core.tools.tool`로 감싼 것)을 받는다. 이 감싸기는 `agent.py`의 `search_tools() -> list`로 제공.
- [ ] **Step 4:** 같은 명령 → PASS.

---

### Task 8: 그래프와 실행 로그 (`graph.py`)

**Files:** Create `graph.py`, `tests/test_graph.py`

**Interfaces — Consumes:** Task 2~7. **Produces:**
```python
MAX_RETRY = 2
class RunLog(AsyncCallbackHandler): ...        # logs/run-시각-보고일.jsonl, event(kind, **data), mask 적용, LLM 호출 수·토큰
def build_graph(compose, call_tool, approver: str = "작성자", guards: bool = True)
    # compose: async (ItemInput, feedback: str) -> Composed
    # call_tool: async (name: str, args: dict) -> str   (권한·가드레일 통과 후 실행, 막히면 None)
async def run(app, report_date: date | None, answers: list, show=True) -> tuple[list[str], dict]   # (노드 순서, 최종 상태)
```
State: `report_date, prev(dict), weeks(dict), candidates(list), selected(list), new_inputs(list), csr, composed(list), problems(list), retry_count, approved, overwrite, result, out_path`.
노드와 간선: `직전보고 읽기`(최신 파일 읽기, `report_date` 없으면 +7일) → `주차 계산` → `이월 후보`(직전 보고서의 모든 건 중 새 금주/차주와 일정이 겹치는 것을 칸별로 후보 제안) → `사용자 확인`(**interrupt #1**: 입력 = 후보 선택, 신규 건들(`ItemInput`), CSR 값) → `초안`(신규 건마다 `compose`; 이월 건은 문장 그대로 재사용) → `계속 표시`(코드: 각 칸 week로 `is_continuing`→`continued`) → `검증`(`check_facts(출력, 입력)`·`check`·임시 렌더 후 `verify_hwpx`; 실패하면 `retry_count`+1, `retry_count <= MAX_RETRY`면 `초안`으로 `feedback` 전달, 초과면 `사람 전담`) → `사용자 승인`(**interrupt #2**: `preview_text` 보여주고 `승인`/`반려: 사유`; 이미 `reports/YYYYMMDD.hwpx`가 있으면 `overwrite` 여부를 함께 묻는다) → `저장`(`save_report`, 권한 "승인 후") → END. `사람 전담`은 `result`에 `사람 이관: 사유` 기록. 반려는 `feedback`을 넣고 `초안`으로, 반려 2회째에는 `사람 전담`.

- [ ] **Step 1: Write the failing tests** (가짜 `compose` 함수 + 파이썬 직접 호출 `call_tool`, 임시 `REPORTS_DIR`):
  - `test_happy_path`: 답 = `[후보 전부 선택 + 신규 1건 + CSR, "승인"]` → 노드 순서가 `직전보고 읽기 → 주차 계산 → 이월 후보 → 사용자 확인 → 초안 → 계속 표시 → 검증 → 사용자 승인 → 저장`, `reports/20261130.hwpx` 생성, `verify_hwpx==[]`, 읽어 보면 금주 기간 `11.30~12.04`.
  - `test_carry_candidates`: `이월 후보` 결과에 0123의 `축수산 일반매입등록(70-2048)` 건이 금주 후보로 들어 있다(본적용 11.30이 새 금주와 겹침).
  - `test_invented_number_retries_then_handoff`: 가짜 `compose`가 항상 입력에 없는 숫자를 내면 `초안`이 `1+MAX_RETRY`번 실행된 뒤 `사람 전담`으로 끝나고 `reports/`에 파일이 없다.
  - `test_reject_twice_handoff`: `["…확인 답", "반려: 문구 수정", "반려: 다시"]` → `사람 전담`.
  - `test_existing_file_needs_overwrite` (Review Focus 5): `reports/20261130.hwpx`가 이미 있을 때 승인 답에 덮어쓰기 동의가 없으면 저장되지 않고 기존 파일이 그대로다.
  - `test_pii_masked_in_log`: 입력 내용에 `010-1234-5678`이 있으면 `logs/*.jsonl`에 원문이 없다.
- [ ] **Step 2:** Run `.venv\Scripts\python.exe -m pytest tests/test_graph.py -v` → FAIL.
- [ ] **Step 3:** 참고 프로젝트 `graph.py`의 구조(`StateGraph(State)`, `InMemorySaver`, `interrupt`, `Command(resume=…)`, `RunLog`, `call_tool` 권한 검사, `run` 루프)를 따라 구현. `run`은 `answers`를 순서대로 `Command(resume=…)`로 넣는다.
- [ ] **Step 4:** 같은 명령 → PASS.

---

### Task 9: Streamlit 폼 (`app.py`)

**Files:** Create `app.py`, `tests/test_app_helpers.py`

**Interfaces — Consumes:** `graph.build_graph/run`, `tools.*`, `agent.compose_item`. **Produces:** `app.py`(실행 `.venv\Scripts\python.exe -m streamlit run app.py`)와 테스트 가능한 순수 함수:
```python
def validate_item_form(reason: str, content: str, phases: list[dict], requester: str) -> list[str]   # 오류 목록
def validate_csr(requested: int, handled: int, calls: int, outages: int, on_time: int) -> list[str]
```
화면 흐름: ① 보고일 자동 표시(최신 파일 +7일)와 금주/차주/접수기간 → ② 이월 후보 체크 목록(금주/차주별, 건 제목·일정 표시) → ③ 신규 건 입력(사유, 내용, 개발일정 단계 행 추가(이름·시작·종료 선택), 요청부서, 완료(예정)일) → ④ CSR 숫자 5개 → ⑤ `생성` 누르면 그래프 실행, 텍스트 미리보기와 검증 결과 표시 → ⑥ `승인` / `반려(사유)` 버튼, 기존 파일이 있으면 덮어쓰기 체크박스 → 저장 경로와 다운로드 버튼. 그래프 앱·`thread_id`·중단 상태는 `st.session_state`에 보관. 키 값은 화면에 표시하지 않는다.

- [ ] **Step 1: Write the failing tests:** `test_validate_item_form`(사유/내용/요청부서가 비면 각각 오류, 단계의 종료일이 시작일보다 앞서면 오류, 단계가 하나도 없으면 오류, 정상 입력은 `[]`), `test_validate_csr`(처리 > 요청이면 오류, 음수 오류, `(0,0,0,0,0)` 정상, `(3,3,5,0,0)` 정상).
- [ ] **Step 2:** Run `.venv\Scripts\python.exe -m pytest tests/test_app_helpers.py -v` → FAIL.
- [ ] **Step 3:** 구현. 검증 함수는 `app.py` 맨 위에 두고 Streamlit 호출은 `main()` 안에서만 해서 import만으로는 화면이 뜨지 않게 한다.
- [ ] **Step 4:** 같은 명령 → PASS. 그 뒤 `.venv\Scripts\python.exe -m streamlit run app.py`로 실제 기동해 브라우저에서 이월 후보가 보이는지 확인하고, 모델 연결(`.env`)이 있으면 신규 1건으로 끝까지 생성해 `reports/20261130.hwpx`를 만들어 본다(연결 전이면 화면 기동까지만 확인하고 사용자에게 알린다).

---

### Task 10: MCP 서버 (`mcp_server.py`, `.mcp.json`)

**Files:** Create `mcp_server.py`, `.mcp.json`, `tests/test_mcp_tools.py`

**Interfaces — Consumes:** Task 6의 `tool_*` 함수와 `PERMISSION`. **Produces:** FastMCP 서버 `weekly-report`(stdio)에 도구 `read_report`, `calc_weeks`, `search_items`, `save_report`를 등록. 도구 설명(docstring)은 모델이 언제 쓸지 알 수 있게 한 줄. `save_report`는 설명에 "사용자 승인 뒤에만 쓴다"를 넣는다. 화면 출력(print) 금지. 참고 프로젝트처럼 시스템 파이썬으로 실행되면 `.venv` 파이썬으로 재실행하는 머리말을 둔다. `.mcp.json`은 `.venv\Scripts\python.exe` 절대경로로 서버를 등록.

- [ ] **Step 1: Write the failing test:** `test_registered_tools`: `import mcp_server` 후 등록된 도구 이름 집합이 `{"read_report","calc_weeks","search_items","save_report"}`와 같고, `PERMISSION`의 키 집합과도 같다(비동기 목록 조회 API는 `await mcp_server.mcp.list_tools()`).
- [ ] **Step 2:** Run `.venv\Scripts\python.exe -m pytest tests/test_mcp_tools.py -v` → FAIL.
- [ ] **Step 3:** 구현.
- [ ] **Step 4:** 같은 명령 → PASS. 사용자에게 `.mcp.json` 변경은 Claude Code를 `/exit` 후 다시 열어야 적용된다고 안내한다.

---

### Task 11: 하네스 (`검증/`)

**Files:** Create `검증/cases.json`, `검증/run_cases.py`, `tests/test_cases_runner.py`

**Interfaces — Consumes:** `graph.build_graph/run`, 가짜 `compose`(오프라인 기본) 또는 실제 `compose_item`(`--live`). **Produces:** `run_cases.py`: `python 검증/run_cases.py [--live]` → 케이스별 통과/실패와 `검증/비교표.md`(| 케이스 | 기대 동작 | 결과 |). `judge(case, path, final, out_files, log_path) -> (bool, list[str])`(참고 프로젝트 `judge` 모양).
`cases.json` 각 항목: `케이스`, `기대`, `입력`(후보 선택·신규 건·CSR·승인 답 목록·보고일), `끝`(마지막 노드), `기록`(저장 파일 수), 선택적 `사유`(이관 사유 문자열), `초안`(초안 실행 횟수). 케이스 목록: ① 정상 생성(이월 + 신규 1건) ② 입력에 없는 숫자(가짜 compose가 2099를 냄) → 사람 이관 ③ 개인정보가 든 입력 → 로그·산출물에 원문 없음 ④ 반려 2회 → 사람 이관 ⑤ 금주 칸 0건 → 유효한 hwpx 저장 ⑥ 기존 파일 존재 + 덮어쓰기 거부 → 저장 0건 ⑦ 샘플 3개 골든(`read_report`→`render_report`→`read_report` 같음).

- [ ] **Step 1: Write the failing test:** `test_cases_runner.py::test_all_cases_pass_offline` — `run_cases.run_all(live=False)`의 모든 케이스가 `ok`이고 케이스가 7개이다.
- [ ] **Step 2:** Run `.venv\Scripts\python.exe -m pytest tests/test_cases_runner.py -v` → FAIL.
- [ ] **Step 3:** `cases.json`과 `run_cases.py` 구현(`judge`는 끝 노드·저장 파일 수·이관 사유·초안 횟수·`find_pii` 노출을 비교). `--live`는 실제 모델로 케이스 ①만 실행한다.
- [ ] **Step 4:** Run `.venv\Scripts\python.exe -m pytest tests -v` → 전체 PASS, `.venv\Scripts\python.exe 검증/run_cases.py` → `검증/비교표.md` 생성.
