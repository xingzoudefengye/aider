"""Aider 核心模块 - ComeCode 移植功能"""

from .local_handoff import build_local_handoff, LocalHandoff, format_handoff_for_prompt

__all__ = [
    "build_local_handoff",
    "LocalHandoff",
    "format_handoff_for_prompt",
]
