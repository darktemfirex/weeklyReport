import asyncio

import mcp_server
import tools


def test_registered_tools_match_permission_table():
    names = {t.name for t in asyncio.run(mcp_server.mcp.list_tools())}
    assert names == {"read_report", "calc_weeks", "search_items", "save_report"}
    assert names == set(tools.PERMISSION)


def test_save_report_description_mentions_approval():
    tool = next(t for t in asyncio.run(mcp_server.mcp.list_tools()) if t.name == "save_report")
    assert "승인" in tool.description


def test_calc_weeks_through_mcp():
    result = asyncio.run(mcp_server.mcp.call_tool("calc_weeks", {"report_date": "2026-11-30"}))
    text = str(result)
    assert "2026-12-04" in text
