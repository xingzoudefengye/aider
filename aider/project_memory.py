"""读取项目级 .ai/ 记忆文件。"""

from pathlib import Path
from typing import Optional


MEMORY_FILES = ("project.md", "decisions.md", "tasks.md", "memory.md")
MAX_FILE_CHARS = 8_000
MAX_TOTAL_CHARS = 24_000
MAX_ENTRY_CHARS = 2_000
HANDOFF_FILENAME = "handoff.md"
MAX_HANDOFF_CHARS = 12_000


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
