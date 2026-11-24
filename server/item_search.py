"""과거 보고서 건 RAG: 건마다 Document 하나(타이틀+사유+내용)로 InMemoryVectorStore에 넣고 유사 건을 검색한다"""
import os
import re
from pathlib import Path

from dotenv import load_dotenv
from langchain_core.documents import Document
from langchain_core.vectorstores import InMemoryVectorStore

from hwpx_io import read_report

ROOT = Path(__file__).resolve().parent.parent


def default_embeddings():
    from langchain_openai import OpenAIEmbeddings
    load_dotenv(ROOT / ".env")
    return OpenAIEmbeddings(model=os.getenv("EMBED_MODEL"), check_embedding_ctx_length=False)


def build_store(data_dir: Path, embeddings=None, extra=()) -> InMemoryVectorStore:
    """data_dir의 보고서와 첨부(extra)한 보고서의 건을 색인한다. 이름이 같으면 첨부한 파일이 우선"""
    docs, seen = [], set()
    paths = {p.stem: p for p in Path(data_dir).glob("*.hwpx") if re.fullmatch(r"\d{8}", p.stem)}   # YYYYMMDD.hwpx만(복사본 등은 건너뜀)
    paths.update({Path(p).stem: Path(p) for p in extra})
    for stem in sorted(paths, reverse=True):  # 최신 문구가 우선
        path = paths[stem]
        report = read_report(path)
        for label, items in (("금주", report.this_items), ("차주", report.next_items)):
            for number, item in enumerate(items, 1):
                key = item.title
                if item.raw is not None or key in seen:   # 형식이 다른 건·같은 건 반복은 제외
                    continue
                seen.add(key)
                reason, content = " ".join(item.reason), " ".join(s.strip() for s in item.content)
                docs.append(Document(page_content=f"{item.title}\n{reason}\n{content}",
                                     metadata={"source": f"{path.stem}/{label}/{number}", "title": item.title,
                                               "reason": reason, "content": content}))
    return InMemoryVectorStore.from_documents(docs, embeddings or default_embeddings())


def search_items(store: InMemoryVectorStore, query: str, k: int = 4) -> list[dict]:
    return [{"source": d.metadata["source"], "title": d.metadata["title"],
             "reason": d.metadata["reason"], "content": d.metadata["content"]}
            for d in store.similarity_search(query, k=k)]
