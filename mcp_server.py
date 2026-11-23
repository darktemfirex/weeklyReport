"""주간보고 도구를 가진 MCP 서버 (stdio 방식)
도구: read_report(직전 보고서 읽기) / calc_weeks(주차 계산) / search_items(과거 건 검색) / save_report(저장 - 승인 뒤에만)
Claude Code 연결: .mcp.json
주의: stdio 서버는 화면(print)에 아무것도 찍으면 안 된다 - 통신 통로가 깨진다
"""
import os
import subprocess
import sys
from pathlib import Path

# 가상환경 밖 파이썬으로 실행됐으면 이 폴더의 .venv 파이썬으로 다시 띄운다
if sys.prefix == sys.base_prefix:
    _py = Path(__file__).resolve().parent / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if _py.exists():
        sys.exit(subprocess.call([str(_py), str(Path(__file__).resolve()), *sys.argv[1:]]))

from mcp.server.fastmcp import FastMCP

sys.path.insert(0, str(Path(__file__).resolve().parent))
import tools

mcp = FastMCP("weekly-report", log_level="WARNING")


@mcp.tool()
def read_report(file_name: str) -> str:
    """직전 주간보고 hwpx(data 폴더, 예: 20261123.hwpx)를 읽어 건·일정·CSR을 JSON으로 돌려줄 때 쓴다."""
    return tools.tool_read_report(file_name)


@mcp.tool()
def calc_weeks(report_date: str) -> str:
    """보고일(월요일, YYYY-MM-DD)로 금주·차주·CSR 접수기간(월~금)을 계산할 때 쓴다."""
    return tools.tool_calc_weeks(report_date)


@mcp.tool()
def search_items(query: str) -> str:
    """과거 주간보고에서 비슷한 건(타이틀·사유·내용)을 찾을 때 쓴다. query는 건의 주제."""
    return tools.tool_search_items(query)


@mcp.tool()
def save_report(report_json: str, out_name: str, overwrite: bool = False) -> str:
    """작성한 보고서(JSON)를 reports 폴더에 hwpx로 저장한다. 사용자 승인 뒤에만 쓴다. 이미 있으면 overwrite=True일 때만 덮어쓴다."""
    return tools.tool_save_report(report_json, out_name, overwrite)


if __name__ == "__main__":
    mcp.run()  # stdio
