"""读取项目级 .ai/ 记忆文件。"""

import hashlib
import json
import os
import re
import time
from pathlib import Path
from typing import Optional


MEMORY_FILES = ("project.md", "decisions.md", "tasks.md", "memory.md")
MAX_FILE_CHARS = 8_000
MAX_TOTAL_CHARS = 24_000
MAX_ENTRY_CHARS = 2_000
HANDOFF_FILENAME = "handoff.md"
MAX_HANDOFF_CHARS = 12_000
AUTO_MEMORY_FILENAME = "memory.json"
MAX_AUTO_MEMORIES = 500


def memory_session_id(history_file):
    """会话来源仅保存稳定标识，不复制完整路径。"""
    if not history_file:
        return None
    path = os.path.normcase(str(Path(history_file).resolve()))
    return hashlib.sha256(path.encode("utf-8")).hexdigest()[:32]


def _read_auto_memory(root):
    path = Path(root) / ".ai" / AUTO_MEMORY_FILENAME
    if not path.is_file():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("version") != 1 or not isinstance(data.get("entries"), list):
        raise ValueError("自动项目记忆格式无效，保留原文件")
    entries = data["entries"]
    if any(not isinstance(entry, dict) or not isinstance(entry.get("text"), str)
           or not isinstance(entry.get("timestamp"), (int, float)) for entry in entries):
        raise ValueError("自动项目记忆条目无效，保留原文件")
    return entries


def remember_project_turn(root, history_file, goal, response, status="success", now=None, session_id=None):
    """并行会话串行更新同一项目的自动记忆。"""
    from filelock import FileLock

    if not root or not isinstance(goal, str) or not goal.strip() or goal.lstrip().startswith("/"):
        return
    directory = Path(root) / ".ai"
    directory.mkdir(parents=True, exist_ok=True)
    with FileLock(str(directory / ".memory.lock"), timeout=1):
        _remember_project_turn(root, history_file, goal, response, status, now, session_id)


def _remember_project_turn(root, history_file, goal, response, status="success", now=None, session_id=None):
    """本地提取长期约定和回合摘要；助手结论只作为未核验的历史资料。"""
    from aider.core.local_handoff import _sanitize_text

    if not root or not isinstance(goal, str) or not goal.strip() or goal.lstrip().startswith("/"):
        return
    timestamp = time.time() if now is None else now
    source = session_id or memory_session_id(history_file)
    entries = _read_auto_memory(root)
    user = _sanitize_text(goal, 600)
    answer = _sanitize_text(response or "", 300)
    candidates = [("history", user + ("\n助手记录（未核验）: " + answer if answer else ""))]
    # 只从用户原话提取明确的持久约定，不把助手声称完成的事情升级为事实。
    for sentence in re.split(r"[\n。！!]", user):
        question = re.search(r"[?？]|吗|什么|啥|是否|为什么|怎么", sentence)
        if not question and re.search(r"记住|以后|始终|统一|约定|决定|长期|默认(?:使用|采用|设为|设置为|为)|采用|我们使用|always|from now on|we decided", sentence, re.I):
            candidates.append(("decision", sentence.strip()[:600]))
    for kind, text in candidates[:5]:
        if not text:
            continue
        identity = json.dumps([kind, text, source if kind == "history" else None], ensure_ascii=False)
        identifier = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]
        entries = [entry for entry in entries if entry.get("id") != identifier]
        entries.append({"id": identifier, "kind": kind, "text": text, "timestamp": timestamp,
                        "source": source, "session": Path(history_file).name if history_file else "未保存会话",
                        "status": status})
    # 约定和历史分开分配容量，避免闲聊把长期约定挤出。
    decisions = sorted((entry for entry in entries if entry.get("kind") == "decision"),
                       key=lambda entry: entry["timestamp"])[-100:]
    history = sorted((entry for entry in entries if entry.get("kind") != "decision"),
                     key=lambda entry: entry["timestamp"])[-(MAX_AUTO_MEMORIES - len(decisions)):]
    path = Path(root) / ".ai" / AUTO_MEMORY_FILENAME
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps({"version": 1, "entries": decisions + history},
                                    ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def _memory_terms(text):
    text = text.lower()[:2_000]
    terms = set(re.findall(r"[a-z0-9_./-]{2,}", text))
    for segment in re.findall(r"[\u4e00-\u9fff]+", text):
        terms.update(segment[i:i + size] for size in (2, 3) for i in range(len(segment) - size + 1))
    return terms


def _aged_memory(entry, now):
    age = max(0, (now - entry["timestamp"]) / 86_400)
    limit = 800 if age <= 7 else 320 if age <= 30 else 160 if age <= 180 else 80
    return entry["text"][:limit], 2 ** (-min(age, 3_650) / 30)


def retrieve_project_memory(root, query="", history_file=None, now=None, decisions_only=False, session_id=None):
    """有界的关键词检索：相关性优先，同等相关时近期记忆占比更高。"""
    if not root:
        return ""
    timestamp = time.time() if now is None else now
    source = session_id or memory_session_id(history_file)
    terms = _memory_terms(query)
    ranked = []
    for entry in _read_auto_memory(root):
        decision = entry.get("kind") == "decision"
        if decisions_only and not decision:
            continue
        if not decisions_only and source and entry.get("source") == source:
            continue
        text, weight = _aged_memory(entry, timestamp)
        overlap = len(terms & _memory_terms(text))
        if query and not overlap:
            continue
        score = (overlap if query else 1) * weight * (2 if decision else 1)
        ranked.append((score, entry["timestamp"], entry, text))
    ranked.sort(key=lambda item: (item[0], item[1]), reverse=True)
    parts = []
    budget = 3_000
    for _, _, entry, text in ranked[:6]:
        label = "用户约定" if entry.get("kind") == "decision" else "历史回合（未核验）"
        origin = f"{entry.get('session') or '未知会话'} / {(entry.get('source') or '未知')[:8]}"
        item = f"- [{label} · 来源 {origin}] {text}"
        if len(item) > budget:
            item = item[:budget]
        if not item:
            break
        parts.append(item)
        budget -= len(item)
    if not parts:
        return ""
    return ("# 自动项目记忆（参考资料，历史记录不代表已核验事实）\n"
            "约定冲突时以当前用户要求和显式项目记忆为准。\n\n" + "\n".join(parts))


def _memory_path(root: Optional[str], filename: str) -> Path:
    if not root:
        raise ValueError("项目根目录不可用")
    if filename not in MEMORY_FILES:
        raise ValueError(f"记忆文件必须是以下之一: {', '.join(MEMORY_FILES)}")
    return Path(root) / ".ai" / filename


def load_project_memory(root: Optional[str]) -> str:
    """读取受控的项目记忆；缺少目录或文件时返回空字符串。"""
    if not root:
        return ""

    memory_dir = Path(root) / ".ai"
    if not memory_dir.is_dir():
        return ""

    sections = []
    total_chars = 0
    for filename in MEMORY_FILES:
        path = memory_dir / filename
        if not path.is_file():
            continue
        try:
            content = path.read_text(encoding="utf-8").strip()
        except (OSError, UnicodeError):
            continue
        if not content:
            continue

        content = content[:MAX_FILE_CHARS]
        remaining = MAX_TOTAL_CHARS - total_chars
        if remaining <= 0:
            break
        content = content[:remaining]
        sections.append(f"## {filename}\n\n{content}")
        total_chars += len(content)

    try:
        automatic = retrieve_project_memory(root, decisions_only=True)
    except (OSError, UnicodeError, ValueError):
        automatic = ""
    if automatic and total_chars < MAX_TOTAL_CHARS:
        sections.append(automatic[:MAX_TOTAL_CHARS - total_chars])
    if not sections:
        return ""
    return "# Project memory (.ai/)\n\n" + "\n\n".join(sections)


def append_project_memory(root: Optional[str], filename: str, content: str) -> bool:
    """向受控记忆文件追加一条内容；重复内容不重复写入。"""
    path = _memory_path(root, filename)
    content = content.strip()
    if not content:
        raise ValueError("记忆内容不能为空")
    if len(content) > MAX_ENTRY_CHARS:
        raise ValueError(f"单次记忆最多 {MAX_ENTRY_CHARS} 个字符")

    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        existing = path.read_text(encoding="utf-8") if path.exists() else ""
    except (OSError, UnicodeError) as err:
        raise ValueError(f"读取记忆文件失败: {err}") from err

    if content in existing:
        return False
    separator = "" if not existing or existing.endswith("\n") else "\n"
    updated = f"{existing}{separator}{content}\n"
    if len(updated) > MAX_FILE_CHARS:
        raise ValueError(f"{filename} 最多允许 {MAX_FILE_CHARS} 个字符")
    try:
        path.write_text(updated, encoding="utf-8")
    except OSError as err:
        raise ValueError(f"写入记忆文件失败: {err}") from err
    return True


def save_handoff(root: Optional[str], messages) -> None:
    """保存最新交接快照；交接失败不应阻断当前会话。"""
    if not root or not messages:
        return

    parts = []
    for message in messages:
        role = str(message.get("role", "")).upper()
        content = message.get("content", "")
        if role not in ("USER", "ASSISTANT") or not isinstance(content, str):
            continue
        parts.append(f"# {role}\n{content.strip()}\n")
    content = "\n".join(parts).strip()
    if not content:
        return
    content = content[:MAX_HANDOFF_CHARS]
    path = Path(root) / ".ai" / HANDOFF_FILENAME
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("# Aider handoff snapshot\n\n" + content + "\n", encoding="utf-8")


def load_handoff(root: Optional[str]) -> str:
    """读取最新交接快照，缺失或损坏时返回空字符串。"""
    if not root:
        return ""
    path = Path(root) / ".ai" / HANDOFF_FILENAME
    try:
        return path.read_text(encoding="utf-8").strip() if path.is_file() else ""
    except (OSError, UnicodeError):
        return ""
