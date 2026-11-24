from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import graph
import tools
import web
from agent import Composed

DATA = Path(__file__).resolve().parent.parent / "data"
TITLE = "축수산 일반매입등록(70-2048) 개선 개발"
CSR = {"requested": 4, "handled": 4, "calls": 3, "outages": 0, "on_time": 0}
NEW = {"columns": ["next"], "page": 1, "reason": "사유 원문", "content": "내용 원문", "requester": "IT지원팀",
       "due": "2026-12-04", "continued": None,
       "phases": [{"name": "분석/설계", "start": "2026-11-30", "end": "2026-12-04", "open_ended": False}]}


async def fake_compose(inp, feedback=""):
    return Composed("신규 테스트 개발", [inp.reason], ["    · " + inp.content])


def answer(**kw):
    base = {"select": {"this": [TITLE], "next": []}, "new": [NEW], "csr": CSR, "continued": {},
            "add_line": {TITLE: "금주 진행 상황 한 줄"}}
    base.update(kw)
    return base


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(tools, "REPORTS_DIR", tmp_path / "reports")
    monkeypatch.setattr(tools, "UPLOADS_DIR", tmp_path / "uploads")
    monkeypatch.setattr(tools, "_extra", [])
    monkeypatch.setattr(tools, "_store", None)
    monkeypatch.setattr(graph, "LOGS_DIR", tmp_path / "logs")
    monkeypatch.setattr(graph, "MIN_ITEMS", 2)       # 흐름 확인용. 기본 규칙은 전용 테스트에서
    return TestClient(web.create_app(compose_factory=lambda log: fake_compose))


def start(client, prev_name=None):
    r = client.post("/api/runs", json={"prev_name": prev_name})
    assert r.status_code == 200, r.text
    return r.json()


# ---- 화면과 안내 ----
def test_index_page_served(client):
    r = client.get("/")
    assert r.status_code == 200 and "주간보고" in r.text and "text/html" in r.headers["content-type"]


def test_static_assets_served(client):
    assert client.get("/static/app.js").status_code == 200
    assert client.get("/static/style.css").status_code == 200


def test_info_defaults_to_latest_data_file(client):
    info = client.get("/api/info").json()
    assert info["prev_name"] == "20261116.hwpx" and info["source"] == "data"
    assert info["report_date"] == "2026-11-23"
    assert info["weeks"]["this_week"] == {"start": "2026-11-23", "end": "2026-11-27"}
    assert info["weeks"]["csr_week"]["start"] == "2026-11-16"


def test_info_unknown_file_is_404(client):
    assert client.get("/api/info", params={"prev": "19990101.hwpx"}).status_code == 404


# ---- 첨부 ----
def upload(client, name, data, fallback=None):
    return client.post("/api/upload", files={"file": (name, data)}, data={"fallback_date": fallback or ""})


def test_upload_valid_file_then_info_uses_it(client):
    r = upload(client, "20261109.hwpx", (DATA / "20261109.hwpx").read_bytes())
    assert r.status_code == 200 and r.json()["prev_name"] == "20261109.hwpx" and r.json()["source"] == "upload"
    assert r.json()["report_date"] == "2026-11-16"


def test_upload_bad_name_asks_for_date_then_accepts_it(client):
    data = (DATA / "20261109.hwpx").read_bytes()
    bad = upload(client, "주간보고 최종.hwpx", data)
    assert bad.status_code == 400 and bad.json()["need_date"] is True
    ok = upload(client, "주간보고 최종.hwpx", data, fallback="2026-11-09")
    assert ok.status_code == 200 and ok.json()["prev_name"] == "20261109.hwpx"


def test_upload_unreadable_file_rejected(client):
    r = upload(client, "20261109.hwpx", b"not a zip")
    assert r.status_code == 400 and "읽을 수 없습니다" in r.json()["detail"] and not r.json().get("need_date")


def test_upload_non_hwpx_extension_rejected(client):
    assert upload(client, "20261109.pdf", b"x").status_code == 400


# ---- 작성 흐름 ----
def test_full_flow_saves_downloadable_report(client, tmp_path):
    run = start(client)
    assert run["stage"] == "confirm" and run["min_items"] == graph.MIN_ITEMS
    assert any(c["title"] == TITLE and c["column"] == "this" and c["raw"] is False for c in run["candidates"])
    r = client.post(f"/api/runs/{run['run_id']}/confirm", json=answer())
    assert r.status_code == 200 and r.json()["stage"] == "approve"
    assert "신규 테스트 개발" in r.json()["preview"] and r.json()["existing"] is False
    done = client.post(f"/api/runs/{run['run_id']}/approve", json={"answer": "승인"}).json()
    assert done["stage"] == "done" and done["saved"] is True and "저장 완료" in done["result"]
    dl = client.get(f"/api/runs/{run['run_id']}/download")
    assert dl.status_code == 200 and dl.content[:2] == b"PK"
    assert (tmp_path / "reports" / "20261123.hwpx").exists()


def test_confirm_validation_errors_are_422_and_run_stays_open(client, monkeypatch):
    monkeypatch.setattr(graph, "MIN_ITEMS", 5)
    run = start(client)
    r = client.post(f"/api/runs/{run['run_id']}/confirm", json=answer())
    assert r.status_code == 422 and any("3건 부족" in e for e in r.json()["errors"])
    monkeypatch.setattr(graph, "MIN_ITEMS", 2)
    assert client.post(f"/api/runs/{run['run_id']}/confirm", json=answer()).status_code == 200


def test_missing_add_line_is_422(client):
    run = start(client)
    r = client.post(f"/api/runs/{run['run_id']}/confirm", json=answer(add_line={}))
    assert r.status_code == 422 and any("개발내용 1줄" in e for e in r.json()["errors"])


def test_reject_returns_to_content_input_then_hand_off(client):
    run = start(client)
    client.post(f"/api/runs/{run['run_id']}/confirm", json=answer())
    back = client.post(f"/api/runs/{run['run_id']}/approve", json={"answer": "반려: 문구 수정"}).json()
    assert back["stage"] == "confirm" and back["reject_reason"] == "문구 수정"       # 2단계로 돌아온다
    assert back["candidates"] and back["min_items"] == graph.MIN_ITEMS
    again = client.post(f"/api/runs/{run['run_id']}/confirm", json=answer()).json()
    assert again["stage"] == "approve"
    done = client.post(f"/api/runs/{run['run_id']}/approve", json={"answer": "반려: 다시"}).json()   # 두 번째 반려
    assert done["stage"] == "done" and done["saved"] is False and done["result"].startswith("사람 이관")
    assert "2번 반려" in done["result"]
    assert client.get(f"/api/runs/{run['run_id']}/download").status_code == 404


def test_existing_file_requires_overwrite_consent(client, tmp_path):
    (tmp_path / "reports").mkdir()
    old = tmp_path / "reports" / "20261123.hwpx"
    old.write_bytes(b"old")
    run = start(client)
    r = client.post(f"/api/runs/{run['run_id']}/confirm", json=answer()).json()
    assert r["existing"] is True
    done = client.post(f"/api/runs/{run['run_id']}/approve", json={"answer": "승인"}).json()
    assert done["saved"] is False and old.read_bytes() == b"old"
    run2 = start(client)
    client.post(f"/api/runs/{run2['run_id']}/confirm", json=answer())
    done2 = client.post(f"/api/runs/{run2['run_id']}/approve", json={"answer": "승인 덮어쓰기"}).json()
    assert done2["saved"] is True and old.read_bytes() != b"old"


def test_flow_with_attached_previous_report(client, tmp_path):
    upload(client, "20261109.hwpx", (DATA / "20261109.hwpx").read_bytes())
    run = start(client, "20261109.hwpx")
    title = "매입원가산입 등록(70-2013) 개선 개발"
    r = client.post(f"/api/runs/{run['run_id']}/confirm", json=answer(select={"this": [title], "next": []},
                                                                     add_line={title: "진행 중"}))
    assert r.status_code == 200
    client.post(f"/api/runs/{run['run_id']}/approve", json={"answer": "승인"})
    assert (tmp_path / "reports" / "20261116.hwpx").exists()


def test_unknown_run_is_404(client):
    assert client.post("/api/runs/nope/confirm", json=answer()).status_code == 404
    assert client.post("/api/runs/nope/approve", json={"answer": "승인"}).status_code == 404
    assert client.get("/api/runs/nope/download").status_code == 404


def test_confirm_in_wrong_stage_is_409(client):
    run = start(client)
    client.post(f"/api/runs/{run['run_id']}/confirm", json=answer())
    assert client.post(f"/api/runs/{run['run_id']}/confirm", json=answer()).status_code == 409
    assert client.post(f"/api/runs/{run['run_id']}/approve", json={"answer": "승인"}).status_code == 200
    assert client.post(f"/api/runs/{run['run_id']}/approve", json={"answer": "승인"}).status_code == 409


def test_new_item_in_both_columns_is_rejected_by_server(client):
    run = start(client)
    r = client.post(f"/api/runs/{run['run_id']}/confirm", json=answer(new=[dict(NEW, columns=["this", "next"])]))
    assert r.status_code == 422 and any("하나만" in e for e in r.json()["errors"])


def test_new_item_with_title_and_without_content_or_page(client, tmp_path):
    item = {"columns": ["this"], "title": "직접 입력 타이틀 개선 개발", "reason": "사유 원문", "content": "",
            "requester": "IT지원팀", "due": "2026-11-27", "continued": None,
            "phases": [{"name": "분석/설계", "start": "2026-11-23", "end": "2026-11-27", "open_ended": False}]}
    run = start(client)
    r = client.post(f"/api/runs/{run['run_id']}/confirm", json=answer(new=[item]))
    assert r.status_code == 200 and "직접 입력 타이틀 개선 개발" in r.json()["preview"]


def test_custom_phase_name_reaches_the_report(client):
    item = dict(NEW, columns=["next"], phases=[
        {"name": "분석/설계", "start": "2026-11-30", "end": "2026-12-01", "open_ended": False},
        {"name": "사용자 검수", "start": "2026-12-02", "end": "2026-12-04", "open_ended": False}])
    run = start(client)
    r = client.post(f"/api/runs/{run['run_id']}/confirm", json=answer(new=[item]))
    assert r.status_code == 200 and "사용자 검수" in r.json()["preview"]


# ---- 제목과 workflow ----
def test_page_title_has_agent_wording(client):
    html = client.get("/").text
    h1 = html.split("<h1>")[1].split("</h1>")[0]
    assert "<title>주간보고 작성 자동화 Agent</title>" in html
    assert "주간보고 작성 자동화" in h1 and "Agent" in h1                      # 화면 상단 제목
    assert "Weekly Report" not in html                                        # 영문 제목은 더 쓰지 않는다
    assert client.app.title == "주간보고 작성 자동화 Agent"                    # 서버 문서 제목도 같다


def test_workflow_comes_from_the_real_graph(client):
    wf = client.get("/api/workflow").json()
    ids = [n["id"] for n in wf["nodes"]]
    assert ids[:8] == ["직전보고 읽기", "주차 계산", "이월 후보", "사용자 확인", "초안", "AI 점검", "계속 표시", "검증"]
    assert set(ids) == {"직전보고 읽기", "주차 계산", "이월 후보", "사용자 확인", "초안", "AI 점검", "계속 표시", "검증", "사용자 승인", "저장", "사람 전담"}
    assert all(n["kind"] and n["desc"] for n in wf["nodes"])          # 그래프에 노드가 늘면 설명도 있어야 한다
    kinds = {n["id"]: n["kind"] for n in wf["nodes"]}
    assert kinds["초안"] == "LLM" and kinds["사용자 확인"] == "사람" and kinds["검증"] == "가드레일"


def test_workflow_edges_include_branches_with_notes(client):
    wf = client.get("/api/workflow").json()
    edges = {(e["from"], e["to"]): e for e in wf["edges"]}
    assert edges[("직전보고 읽기", "주차 계산")]["conditional"] is False
    assert edges[("검증", "초안")]["conditional"] is True and "재작성" in edges[("검증", "초안")]["note"]
    assert edges[("사용자 승인", "저장")]["conditional"] is True and "승인" in edges[("사용자 승인", "저장")]["note"]
    assert ("사용자 승인", "사용자 확인") in edges and ("검증", "사람 전담") in edges
    assert ("사용자 승인", "초안") not in edges      # 반려는 초안이 아니라 내용 입력으로 돌아간다
    assert {n["id"] for n in wf["nodes"] if n.get("terminal")} == {"저장", "사람 전담"}
    assert not any(e["from"].startswith("__") or e["to"].startswith("__") for e in wf["edges"])


def test_workflow_hand_off_node_is_listed_last(client):
    ids = [n["id"] for n in client.get("/api/workflow").json()["nodes"]]
    assert ids[-3:] == ["사용자 승인", "저장", "사람 전담"]


def test_workflow_edges_have_short_labels_for_the_diagram(client):
    edges = {(e["from"], e["to"]): e for e in client.get("/api/workflow").json()["edges"]}
    assert edges[("검증", "초안")]["label"] == "재작성(최대 2회)"
    assert edges[("사용자 승인", "사용자 확인")]["label"] == "반려 → 수정"
    assert edges[("사용자 승인", "저장")]["label"] == "승인"
    assert edges[("직전보고 읽기", "주차 계산")]["label"] == ""


def test_new_item_with_program_fields_and_no_reason_is_accepted(client):
    item = {"columns": ["next"], "program_name": "배송지시서 출력", "program_code": "70-1234", "work_type": "신규 개발",
            "reason": "", "content": "", "requester": "농협유통 IT지원팀", "due": "2026-12-04", "continued": None,
            "phases": [{"name": "분석/설계", "start": "2026-11-30", "end": "2026-12-04", "open_ended": False}]}
    run = start(client)
    r = client.post(f"/api/runs/{run['run_id']}/confirm", json=answer(new=[item]))
    assert r.status_code == 200 and "배송지시서 출력(70-1234) 신규 개발" in r.json()["preview"]


def test_page_has_no_agent_parts_section(client):
    html = client.get("/").text
    assert "에이전트 구성" not in html and "wf-parts" not in html
    assert "Agent Workflow" in html           # 흐름도 자체는 그대로


# ---- 진행 표시: 서버가 실제 실행 기록(path)을 알려 주고, workflow는 화면 맨 위에 있다 ----
def test_responses_carry_the_executed_node_path(client):
    run = start(client)
    assert run["path"] == ["직전보고 읽기", "주차 계산", "이월 후보"]                       # 사용자 확인에서 대기 중
    approve = client.post(f"/api/runs/{run['run_id']}/confirm", json=answer()).json()
    assert approve["stage"] == "approve"
    assert approve["path"][-5:] == ["사용자 확인", "초안", "AI 점검", "계속 표시", "검증"]            # 사용자 승인에서 대기 중
    back = client.post(f"/api/runs/{run['run_id']}/approve", json={"answer": "반려: 수정"}).json()
    assert back["stage"] == "confirm" and back["path"][-1] == "사용자 승인"               # 반려로 되돌아온 경로도 남는다
    again = client.post(f"/api/runs/{run['run_id']}/confirm", json=answer()).json()
    done = client.post(f"/api/runs/{run['run_id']}/approve", json={"answer": "승인"}).json()
    assert again["path"].count("초안") == 2 and done["path"][-1] == "저장" and done["stage"] == "done"


def test_hand_off_path_ends_at_human_node(client):
    run = start(client)
    client.post(f"/api/runs/{run['run_id']}/confirm", json=answer())
    client.post(f"/api/runs/{run['run_id']}/approve", json={"answer": "반려: 하나"})
    client.post(f"/api/runs/{run['run_id']}/confirm", json=answer())
    done = client.post(f"/api/runs/{run['run_id']}/approve", json={"answer": "반려: 둘"}).json()
    assert done["stage"] == "done" and done["path"][-1] == "사람 전담"


def test_workflow_is_at_the_top_and_step_tabs_are_gone(client):
    html = client.get("/").text
    assert html.index('id="workflow"') < html.index("<main")                 # 본문보다 위
    assert 'id="steps"' not in html and 'class="steps"' not in html          # 단계 탭은 workflow가 대신한다
    assert html.count("Agent Workflow") == 1


def test_ai_review_node_is_an_llm_step_in_the_workflow(client):
    wf = client.get("/api/workflow").json()
    node = [n for n in wf["nodes"] if n["id"] == "AI 점검"][0]
    assert node["kind"] == "LLM" and "점검" in node["desc"]
    edges = {(e["from"], e["to"]) for e in wf["edges"]}
    assert ("초안", "AI 점검") in edges and ("AI 점검", "계속 표시") in edges and ("초안", "계속 표시") not in edges


def test_web_passes_the_factory_review_to_the_graph(tmp_path, monkeypatch):
    monkeypatch.setattr(tools, "REPORTS_DIR", tmp_path / "reports")
    monkeypatch.setattr(tools, "UPLOADS_DIR", tmp_path / "uploads")
    monkeypatch.setattr(graph, "LOGS_DIR", tmp_path / "logs")
    monkeypatch.setattr(graph, "MIN_ITEMS", 2)
    calls = []

    async def review(inp, composed):
        calls.append(composed.title)
        return Composed(composed.title + " 점검", composed.reason, composed.content)

    def factory(log):
        async def compose(inp, feedback=""):        # 공용 fake_compose에 속성을 붙이면 다른 테스트가 오염된다
            return await fake_compose(inp, feedback)
        compose.review = review
        return compose

    client = TestClient(web.create_app(compose_factory=factory))
    run = start(client)
    r = client.post(f"/api/runs/{run['run_id']}/confirm", json=answer())
    assert calls == ["신규 테스트 개발"] and "신규 테스트 개발 점검" in r.json()["preview"]


# ---- 가시성: 버튼 종류 구분, 금주/차주 색 구분, 고정 버튼바 ----
import re  # noqa: E402

STATIC_DIR = Path(__file__).resolve().parent.parent / "client"
VARIANTS = ("btn-primary", "btn-success", "btn-secondary", "btn-danger", "btn-add", "btn-ghost")


def test_every_button_has_a_visible_variant_class():
    for name in ("index.html", "app.js"):
        text = (STATIC_DIR / name).read_text(encoding="utf-8")
        tags = re.findall(r"<button\b[^>]*>", text) + re.findall(r'<a\b[^>]*id="download"[^>]*>', text)
        assert tags, name
        for tag in tags:
            classes = (re.search(r'class="([^"]*)"', tag) or [None, ""])[1].split()
            assert "btn" in classes and any(v in classes for v in VARIANTS), f"{name}: {tag}"


def test_css_defines_button_variants_states_and_column_colors():
    css = (STATIC_DIR / "style.css").read_text(encoding="utf-8")
    for selector in (".btn-primary", ".btn-success", ".btn-secondary", ".btn-danger", ".btn-add", ".btn-ghost", ".btn:disabled",
                     '.item[data-col="this"]', '.item[data-col="next"]', '.cand[data-col="this"]', '.cand[data-col="next"]',
                     ".sticky-bar", ".card-this", ".card-next", ".card-csr", "::file-selector-button"):
        assert selector in css, selector


def test_confirm_screen_cards_are_color_coded_and_generate_bar_is_sticky():
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    assert "card card-this" in html and "card card-next" in html and "card card-csr" in html
    bar = html[html.index("sticky-bar") - 40: html.index('id="btn-generate"') + 40]
    assert 'class="card sticky-bar"' in bar and 'id="btn-generate"' in bar          # 생성 버튼은 고정 바 안에 있다
    assert 'id="btn-add-this"' in html and 'btn-this' in html and 'btn-next' in html  # 추가 버튼도 칸 색을 따른다


# ---- 주요일정 줄: 머리글(단계·시작일·종료일)은 첫 줄에만 ----
def test_only_the_first_phase_row_shows_column_labels():
    js = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
    css = (STATIC_DIR / "style.css").read_text(encoding="utf-8")
    for label in ("단계", "시작일", "종료일"):
        assert f'<span class="lbl">{label}</span>' in js                     # 머리글 글자는 별도 요소라 줄마다 숨길 수 있다
        assert f'aria-label="{label}"' in js                                 # 숨겨도 입력칸 이름은 남는다(화면 낭독)
    assert re.search(r"\.phase\s*\+\s*\.phase\s+\.lbl\s*\{[^}]*display:\s*none", css)   # 둘째 줄부터 머리글 숨김


def test_approve_stage_carries_structured_items_with_new_flags(client):
    run = start(client)
    approve = client.post(f"/api/runs/{run['run_id']}/confirm", json=answer()).json()
    items = approve["items"]
    assert [i["title"] for i in items if i["new"]] == ["신규 테스트 개발"]
    new = [i for i in items if i["new"]][0]
    assert {"column", "title", "reason", "content", "phases", "requester", "due", "continued", "raw", "added"} <= set(new)
    assert approve["preview"]                                        # 텍스트 원문도 그대로 있다


def test_preview_styles_distinguish_new_items_from_carried_over_ones():
    css = (STATIC_DIR / "style.css").read_text(encoding="utf-8")
    js = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    for selector in (".pv-item.new", ".pv-item:not(.new)", ".pv-new", ".pv-added"):
        assert selector in css, selector
    new_rule = css[css.index(".pv-item.new"):].split("}")[0]
    old_rule = css[css.index(".pv-item:not(.new)"):].split("}")[0]
    assert "color" in new_rule and "font-family" in new_rule and "color" in old_rule and "font-family" in old_rule   # 글자색·글꼴이 다르다
    assert new_rule.split("font-family")[1] != old_rule.split("font-family")[1]
    assert "pv-item" in js and "신규" in js and 'id="pv-items"' in html and 'id="pv-legend"' in html
