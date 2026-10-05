"""
ComeCode 精致配色方案
使用低对比中性表面，温暖的主色调和清晰的代码高亮
"""

from dataclasses import dataclass
from typing import Dict

from rich.theme import Theme


@dataclass
class ThemeTokens:
    """主题色彩标记"""

    # 主要颜色
    primary: str
    secondary: str
    accent: str
    error: str
    warning: str
    success: str
    info: str

    # 文本颜色
    text: str
    text_muted: str
    text_bright: str

    # 背景颜色
    background: str
    background_panel: str
    background_element: str

    # 边框颜色
    border: str
    border_active: str

    # Diff 颜色
    diff_added: str
    diff_removed: str
    diff_context: str

    # Markdown 颜色
    markdown_heading: str
    markdown_link: str
    markdown_code: str
    markdown_strong: str

    # 语法高亮
    syntax_comment: str
    syntax_keyword: str
    syntax_function: str
    syntax_string: str
    syntax_number: str
    syntax_type: str


# ComeCode 深色主题
DARK_THEME_TOKENS = ThemeTokens(
    primary="#cc9b7a",
    secondary="#b9a4d0",
    accent="#cc9b7a",
    error="#e0785f",
    warning="#d9b26a",
    success="#96b088",
    info="#a8a294",
    text="#e8e2d8",
    text_muted="#9a938a",
    text_bright="#f5f0e8",
    background="#141414",
    background_panel="#1b1a18",
    background_element="#26241f",
    border="#3d3a35",
    border_active="#cc9b7a",
    diff_added="#96b088",
    diff_removed="#e0785f",
    diff_context="#e8e2d8",
    markdown_heading="#f0ebe1",
    markdown_link="#cc9b7a",
    markdown_code="#d9b26a",
    markdown_strong="#f5f0e8",
    syntax_comment="#8a8379",
    syntax_keyword="#cc9b7a",
    syntax_function="#c8b088",
    syntax_string="#96b088",
    syntax_number="#d9b26a",
    syntax_type="#b9a4d0",
)

# ComeCode 浅色主题
LIGHT_THEME_TOKENS = ThemeTokens(
    primary="#a3532c",
    secondary="#6b5b8f",
    accent="#a3532c",
    error="#a8382a",
    warning="#8a6417",
    success="#4a6b3d",
    info="#6b6459",
    text="#2b2823",
    text_muted="#6e675d",
    text_bright="#1f1d19",
    background="#faf8f4",
    background_panel="#ffffff",
    background_element="#efeae1",
    border="#d8d1c5",
    border_active="#a3532c",
    diff_added="#4a6b3d",
    diff_removed="#a8382a",
    diff_context="#2b2823",
    markdown_heading="#1f1d19",
    markdown_link="#a3532c",
    markdown_code="#8a6417",
    markdown_strong="#1f1d19",
    syntax_comment="#7d766b",
    syntax_keyword="#a3532c",
    syntax_function="#8a6a2f",
    syntax_string="#4a6b3d",
    syntax_number="#8a6417",
    syntax_type="#6b5b8f",
)


def create_rich_theme(tokens: ThemeTokens) -> Theme:
    """
    从主题标记创建 Rich Theme
    """
    styles = {
        # 基础样式
        "info": tokens.text,
        "warning": f"bold {tokens.warning}",
        "error": f"bold {tokens.error}",
        "success": f"bold {tokens.success}",
        # 文本样式
        "text": tokens.text,
        "muted": tokens.text_muted,
        "bright": f"bold {tokens.text_bright}",
        "primary": f"bold {tokens.primary}",
        "secondary": tokens.secondary,
        # Markdown 样式
        "markdown.h1": f"bold {tokens.markdown_heading}",
        "markdown.h2": f"bold {tokens.markdown_heading}",
        "markdown.h3": f"bold {tokens.markdown_heading}",
        "markdown.link": f"underline {tokens.markdown_link}",
        "markdown.code": tokens.markdown_code,
        "markdown.code_block": tokens.markdown_code,
        "markdown.strong": f"bold {tokens.markdown_strong}",
        # 代码语法高亮
        "code.comment": f"italic {tokens.syntax_comment}",
        "code.keyword": f"bold {tokens.syntax_keyword}",
        "code.function": tokens.syntax_function,
        "code.string": tokens.syntax_string,
        "code.number": tokens.syntax_number,
        "code.type": tokens.syntax_type,
        # Diff 样式
        "diff.added": tokens.diff_added,
        "diff.removed": tokens.diff_removed,
        "diff.context": tokens.text_muted,
        # 提示符样式
        "prompt": tokens.primary,
        "prompt.choices": tokens.text_muted,
        # 表格样式
        "table.header": f"bold {tokens.text_bright}",
        "table.cell": tokens.text,
        # 进度条样式
        "progress.description": tokens.text,
        "progress.percentage": tokens.primary,
        "bar.complete": tokens.success,
        "bar.finished": tokens.success,
        "bar.pulse": tokens.primary,
    }

    return Theme(styles)


# 预定义主题
DARK_THEME = create_rich_theme(DARK_THEME_TOKENS)
LIGHT_THEME = create_rich_theme(LIGHT_THEME_TOKENS)

# 默认使用深色主题
DEFAULT_THEME = DARK_THEME


def get_theme(mode: str = "dark") -> Theme:
    """
    获取指定模式的主题

    Args:
        mode: "dark" 或 "light"

    Returns:
        Rich Theme 对象
    """
    return DARK_THEME if mode == "dark" else LIGHT_THEME
