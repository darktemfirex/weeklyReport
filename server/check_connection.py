"""모델 연결 확인 - .env의 키로 LangChain에서 모델을 한 번 불러 본다
실행: .venv\\Scripts\\python.exe check_connection.py
"""
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from langchain_openai import ChatOpenAI

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")
sys.stdout.reconfigure(encoding="utf-8")

llm = ChatOpenAI(model=os.getenv("CHAT_MODEL"), temperature=0)
reply = llm.invoke("연결 확인입니다. 한 문장으로 인사해 주세요.")
print("응답:", reply.content)
print("모델:", reply.response_metadata.get("model_name"))
