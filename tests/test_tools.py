import json
import re
from pathlib import Path

from langchain_core.embeddings import DeterministicFakeEmbedding

import tools
from hwpx_io import read_report
from item_search import build_store, search_items

DATA = Path(__file__).resolve().parent.parent / "data"


def test_build_store_and_search():
    store = build_store(DATA, DeterministicFakeEmbedding(size=32))
    found = search_items(store, "축수산 거래명세서 첨부", k=3)
    assert 1 <= len(found) <= 3
    for f in found:
        assert re.fullmatch(r"\d{8}/(금주|차주)/\d+", f["source"])
        assert {"title", "reason", "content"} <= set(f)


def test_store_skips_raw_items_and_duplicates():
    store = build_store(DATA, DeterministicFakeEmbedding(size=32))
    titles = [f["title"] for f in search_items(store, "x", k=100)]
    assert len(titles) == len(set(titles))
    assert not any("하나로유통" in t for t in titles)   # 형식이 다른 건(원문 복사)은 색인 제외


def test_latest_report_path():
    assert tools.latest_report_path(DATA).name == "20261116.hwpx"


def test_report_json_roundtrip():
    r = read_report(DATA / "20261116.hwpx")
    assert tools.report_from_json(tools.report_to_json(r)) == r


def test_calc_weeks_tool():
    out = json.loads(tools.tool_calc_weeks("2026-11-30"))
    assert out["this_week"]["start"] == "2026-11-30"
    assert out["csr_week"]["end"] == "2026-11-27"


def test_read_report_tool():
    out = json.loads(tools.tool_read_report("20261116.hwpx"))
    assert out["dept"] == "유통운영1팀"


def test_save_report_refuses_overwrite(tmp_path, monkeypatch):
    monkeypatch.setattr(tools, "REPORTS_DIR", tmp_path / "reports")
    payload = tools.report_to_json(read_report(DATA / "20261116.hwpx"))
    first = json.loads(tools.tool_save_report(payload, "20261130.hwpx"))
    assert Path(first["저장"]).exists()
    second = json.loads(tools.tool_save_report(payload, "20261130.hwpx"))
    assert "이미 존재" in second["오류"]
    third = json.loads(tools.tool_save_report(payload, "20261130.hwpx", overwrite=True))
    assert "저장" in third


def test_save_report_does_not_leave_temp(tmp_path, monkeypatch):
    monkeypatch.setattr(tools, "REPORTS_DIR", tmp_path / "reports")
    tools.tool_save_report(tools.report_to_json(read_report(DATA / "20261116.hwpx")), "20261130.hwpx")
    assert [p.name for p in (tmp_path / "reports").iterdir()] == ["20261130.hwpx"]


def test_permissions():
    assert tools.PERMISSION["save_report"] == "승인 후"
    assert tools.PERMISSION["read_report"] == "자동"


# ---- 직전 보고서 첨부 ----
import pytest  # noqa: E402
from datetime import date  # noqa: E402


@pytest.fixture
def uploads(tmp_path, monkeypatch):
    monkeypatch.setattr(tools, "UPLOADS_DIR", tmp_path / "uploads")
    monkeypatch.setattr(tools, "_extra", [])
    monkeypatch.setattr(tools, "_store", None)
    return tmp_path / "uploads"


def sample_bytes(name="20261109"):
    return (DATA / f"{name}.hwpx").read_bytes()


def test_save_upload_keeps_date_name(uploads):
    assert tools.save_upload("20261109.hwpx", sample_bytes()) == "20261109.hwpx"
    assert (uploads / "20261109.hwpx").exists()


def test_save_upload_bad_name_needs_date(uploads):
    with pytest.raises(ValueError, match="YYYYMMDD"):
        tools.save_upload("주간보고(최종).hwpx", sample_bytes())
    assert tools.save_upload("주간보고(최종).hwpx", sample_bytes(), date(2026, 11, 9)) == "20261109.hwpx"


def test_save_upload_rejects_unreadable_file(uploads):
    with pytest.raises(ValueError, match="읽을 수 없습니다"):
        tools.save_upload("20261109.hwpx", b"not a zip")
    assert not (uploads / "20261109.hwpx").exists()


def test_resolve_report_prefers_upload_then_data(uploads):
    assert tools.resolve_report(None).name == "20261116.hwpx"
    tools.save_upload("20261109.hwpx", sample_bytes())
    assert tools.resolve_report("20261109.hwpx").parent == uploads
    assert tools.resolve_report("20261116.hwpx").parent == DATA
    with pytest.raises(FileNotFoundError):
        tools.resolve_report("19990101.hwpx")


def test_read_report_tool_reads_upload(uploads):
    tools.save_upload("20261109.hwpx", sample_bytes())
    out = json.loads(tools.tool_read_report("20261109.hwpx"))
    assert out["report_date"] == "2026-11-09"


def test_search_store_includes_upload(uploads, monkeypatch):
    seen = {}
    monkeypatch.setattr(tools, "build_store", lambda d, extra=(): seen.setdefault("extra", list(extra)) or "store")
    monkeypatch.setattr(tools, "search_items", lambda store, q: [])
    tools.save_upload("20261109.hwpx", sample_bytes())
    tools.tool_search_items("x")
    assert [p.name for p in seen["extra"]] == ["20261109.hwpx"]
