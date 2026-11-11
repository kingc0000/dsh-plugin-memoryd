"""中文检索：jieba 分词 + SQLite FTS5（BM25）。"""
import re
import sqlite3

import jieba

_QUOTE = re.compile(r'["\'\\()*:]')
_TOKEN_KEEP = re.compile(r"[\w\u4e00-\u9fff]", re.UNICODE)


def _keep_token(t: str) -> bool:
    """只保留含字母/数字/中日韩的 token（过滤纯标点，如 '-'、'/'、'.'）。"""
    t = t.strip()
    return bool(t) and bool(_TOKEN_KEEP.search(t))


def segment(text: str) -> str:
    """将文本分词为 FTS5 可检索的空白分隔形式。"""
    return " ".join(w for w in jieba.cut_for_search(text) if _keep_token(w))


def build_match(query: str) -> str:
    """把查询分词并构建 FTS5 MATCH 表达式（词间 AND，短语精确匹配）。"""
    tokens = [w for w in jieba.cut_for_search(query) if _keep_token(w)]
    if not tokens:
        return ""
    parts = []
    for t in tokens:
        safe = _QUOTE.sub(" ", t).strip()
        if safe:
            parts.append(f'"{safe}"')
    return " AND ".join(parts) if parts else ""


def index_memory(conn: sqlite3.Connection, mem_id: int, content: str) -> None:
    conn.execute("DELETE FROM memory_fts WHERE rowid=?", (mem_id,))
    conn.execute("INSERT INTO memory_fts(rowid, seg) VALUES(?,?)", (mem_id, segment(content)))


def unindex_memory(conn: sqlite3.Connection, mem_id: int) -> None:
    conn.execute("DELETE FROM memory_fts WHERE rowid=?", (mem_id,))


def _try_match(conn: sqlite3.Connection, match_expr: str, top_k: int):
    try:
        return conn.execute(
            "SELECT rowid, bm25(memory_fts, 5.0) AS rank FROM memory_fts "
            "WHERE memory_fts MATCH ? ORDER BY rank LIMIT ?",
            (match_expr, top_k),
        ).fetchall()
    except sqlite3.OperationalError:
        return []


def bm25_search(conn: sqlite3.Connection, match_query: str, top_k: int):
    """先 AND 精确，空结果降级 OR 召回（中文同义措辞容错）。"""
    if not match_query:
        return []
    rows = _try_match(conn, match_query, top_k)
    if rows:
        return rows
    return _try_match(conn, match_query.replace(" AND ", " OR "), top_k)