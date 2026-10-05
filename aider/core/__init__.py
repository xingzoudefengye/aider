"""Aider 核心模块 - ComeCode 移植功能"""

from .local_handoff import build_local_handoff, LocalHandoff, format_handoff_for_prompt
from .session_chronicle import SessionChronicle, load_chronicle

__all__ = [
    "build_local_handoff",
    "LocalHandoff",
    "format_handoff_for_prompt",
    "SessionChronicle",
    "load_chronicle",
]
