"""darling-ai 3.0 跨会话长期记忆（sqlite，零依赖）。

每次对话结束后异步把关键信息沉淀为记忆条目，下次会话注入 system prompt。
记忆按"主题标签"分类存储，容量上限防止无限膨胀。
"""
import json
import os
import sqlite3
import threading
import time

_lock = threading.Lock()
_db_path = None


def memory_path():
    global _db_path
    if _db_path:
        return _db_path
    from ai_console import config_path
    base = os.path.dirname(config_path())
    _db_path = os.path.join(base, "memory.db")
    return _db_path


def _conn():
    path = memory_path()
    c = sqlite3.connect(path, timeout=5)
    c.execute("""CREATE TABLE IF NOT EXISTS memories(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        tag TEXT, content TEXT, ts REAL)""")
    c.execute("CREATE INDEX IF NOT EXISTS idx_mem_tag ON memories(tag)")
    return c


def save_memories(entries, limit_per_tag=40):
    """entries: [{"tag": str, "content": str}, ...]"""
    if not entries:
        return
    with _lock:
        try:
            c = _conn()
            for e in entries:
                tag = (e.get("tag") or "general")[:40]
                content = (e.get("content") or "").strip()
                if not content:
                    continue
                c.execute("INSERT INTO memories(tag, content, ts) VALUES(?,?,?)",
                          (tag, content, time.time()))
                # 每个 tag 只保留最近 limit_per_tag 条
                c.execute("""DELETE FROM memories WHERE id NOT IN (
                    SELECT id FROM memories WHERE tag=? ORDER BY ts DESC, id DESC LIMIT ?)""",
                          (tag, limit_per_tag))
            c.commit()
            c.close()
        except Exception:
            pass


def load_memories(max_per_tag=6, max_total=30):
    with _lock:
        try:
            c = _conn()
            rows = c.execute("SELECT tag, content FROM memories ORDER BY ts DESC").fetchall()
            c.close()
        except Exception:
            return []
    grouped = {}
    for tag, content in rows:
        grouped.setdefault(tag, []).append(content)
    out = []
    for tag, items in grouped.items():
        for item in items[:max_per_tag]:
            out.append({"tag": tag, "content": item})
        if len(out) >= max_total:
            break
    return out[:max_total]


def clear_memories(tag=None):
    with _lock:
        try:
            c = _conn()
            if tag:
                c.execute("DELETE FROM memories WHERE tag=?", (tag,))
            else:
                c.execute("DELETE FROM memories")
            c.commit()
            c.close()
        except Exception:
            pass


def summarize_history(history, max_chars=600):
    """把对话历史转成记忆条目（启发式抽取，不额外调用 LLM）。

    取每轮 user 问题 + assistant 回复要点，存为 general 标签条目。
    """
    if not history:
        return []
    picked = []
    # 取开头 2 轮（开场信息）+ 结尾 4 轮（最新状态）
    n = len(history)
    idxs = list(range(min(2, n))) + list(range(max(2, n - 4), n))
    for i in sorted(set(idxs)):
        m = history[i]
        role = m.get("role")
        content = m.get("content")
        if not isinstance(content, str):
            continue
        content = content.strip()
        if not content or content.startswith("[命令执行结果]") or content.startswith("[插件执行结果]"):
            continue
        if role == "user":
            picked.append(f"用户说：{content[:200]}")
        elif role == "assistant":
            picked.append(f"助手回复：{content[:200]}")
    text = "\n".join(picked)
    if len(text) > max_chars:
        text = text[:max_chars] + "…"
    if text:
        return [{"tag": "对话摘要", "content": text}]
    return []
