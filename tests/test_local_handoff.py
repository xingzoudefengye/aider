"""测试本地交接式压缩"""

import pytest
from aider.core.local_handoff import (
    build_local_handoff,
    _sanitize_text,
    _extract_latest_user_goal,
    _extract_progress,
    _extract_tool_calls,
    MAX_LOCAL_HANDOFF_CHARS,
)


def test_sanitize_text_removes_secrets():
    """测试敏感信息脱敏"""
    text = "API_KEY=sk-1234567890abcdef"
    result = _sanitize_text(text)
    assert "sk-1234567890abcdef" not in result
    assert "[已脱敏]" in result


def test_sanitize_text_limits_length():
    """测试长度限制"""
    text = "a" * 1000
    result = _sanitize_text(text, max_chars=100)
    assert len(result) <= 100


def test_sanitize_text_removes_private_keys():
    """测试私钥脱敏"""
    text = """
    -----BEGIN RSA PRIVATE KEY-----
    MIIEpAIBAAKCAQEA...
    -----END RSA PRIVATE KEY-----
    """
    result = _sanitize_text(text)
    assert "BEGIN RSA PRIVATE KEY" not in result
    assert "[已脱敏]" in result


def test_sanitize_text_simplifies_code_blocks():
    """测试代码块简化"""
    text = """
    Here is some code:
    ```python
    def hello():
        return "world"
    ```
    Done.
    """
    result = _sanitize_text(text, max_chars=200)
    assert "def hello()" not in result
    assert "[代码省略]" in result


def test_extract_latest_user_goal():
    """测试提取用户目标"""
    messages = [
        {"role": "user", "content": "帮我实现登录功能"},
        {"role": "assistant", "content": "好的,我来实现"},
        {"role": "user", "content": "还要加上验证码"},
    ]
    goal = _extract_latest_user_goal(messages)
    assert "验证码" in goal
    assert len(goal) > 0


def test_extract_latest_user_goal_skips_synthetic():
    """测试跳过系统生成消息"""
    messages = [
        {"role": "user", "content": "真实用户消息"},
        {"role": "user", "content": "系统消息", "metadata": {"source": "synthetic"}},
    ]
    goal = _extract_latest_user_goal(messages)
    assert "真实用户消息" in goal
    assert "系统消息" not in goal


def test_extract_progress():
    """测试提取进展"""
    messages = [
        {"role": "assistant", "content": "我已经创建了 login.py 文件"},
        {"role": "assistant", "content": "正在添加验证逻辑"},
    ]
    progress = _extract_progress(messages)
    assert len(progress) > 0
    # 应该包含行动关键词
    assert any(keyword in progress for keyword in ["已经", "正在"])


def test_extract_tool_calls():
    """测试提取工具调用"""
    messages = [
        {
            "role": "assistant",
            "tool_calls": [
                {
                    "id": "call_123",
                    "function": {"name": "read_file", "arguments": '{"path": "test.py"}'}
                }
            ]
        },
        {
            "role": "tool",
            "tool_call_id": "call_123",
            "content": "File content here"
        }
    ]
    tools = _extract_tool_calls(messages)
    assert "read_file" in tools
    assert len(tools) > 0


def test_build_local_handoff_basic():
    """测试基础交接构建"""
    messages = [
        {"role": "user", "content": "帮我修复 bug"},
        {"role": "assistant", "content": "我来帮你修复"},
    ]
    handoff = build_local_handoff(messages)

    assert isinstance(handoff.guide, str)
    assert isinstance(handoff.summary, str)
    assert len(handoff.guide) + len(handoff.summary) <= MAX_LOCAL_HANDOFF_CHARS


def test_build_local_handoff_with_custom_instructions():
    """测试包含自定义指令"""
    messages = [{"role": "user", "content": "测试"}]
    custom = "请使用 Python 3.10"

    handoff = build_local_handoff(messages, custom_instructions=custom)
    assert "Python 3.10" in handoff.guide


def test_build_local_handoff_respects_budget():
    """测试容量限制"""
    # 创建大量消息
    messages = []
    for i in range(100):
        messages.append({
            "role": "user",
            "content": f"这是一条很长的用户消息 {i} " + "x" * 500
        })
        messages.append({
            "role": "assistant",
            "content": f"这是一条很长的回复 {i} " + "y" * 500
        })

    handoff = build_local_handoff(messages, max_chars=2000)
    total_len = len(handoff.guide) + len(handoff.summary)
    assert total_len <= 2000


def test_build_local_handoff_empty_messages():
    """测试空消息列表"""
    handoff = build_local_handoff([])
    assert handoff.guide == ""
    assert handoff.summary == ""


def test_format_handoff_for_prompt():
    """测试交接格式化"""
    from aider.core.local_handoff import format_handoff_for_prompt, LocalHandoff

    handoff = LocalHandoff(
        guide="用户目标: 实现功能",
        summary="进展: 已完成"
    )

    formatted = format_handoff_for_prompt(handoff)
    assert "上下文交接" in formatted
    assert "用户目标" in formatted
    assert "已完成" in formatted


def test_tool_calls_with_complex_structure():
    """测试复杂工具调用结构"""
    messages = [
        {
            "role": "assistant",
            "tool_calls": [
                {
                    "id": "call_1",
                    "name": "search_files",  # 不同的结构
                    "arguments": {"pattern": "*.py", "path": "/src"}
                },
                {
                    "id": "call_2",
                    "function": {"name": "edit_file"},  # 标准结构
                    "arguments": '{"file": "test.py"}'
                }
            ]
        }
    ]

    tools = _extract_tool_calls(messages)
    assert "search_files" in tools or "edit_file" in tools


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
