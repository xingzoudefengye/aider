"""本地零成本交接式压缩

参考 ComeCode 的实现:
E:\Projects\ComeCode\engine\apps\zcode-cli\packages\core\src\compact\local-handoff.ts

核心思想:
- 不调用摘要模型,完全本地提取
- 固定容量上限 6000 字符
- 提取用户目标、进展、工具调用、状态
"""

import re
from dataclasses import dataclass
from typing import List, Dict, Optional

# 容量限制
MAX_LOCAL_HANDOFF_CHARS = 6_000
MAX_USER_CHARS = 2_000
MAX_PROGRESS_CHARS = 900
MAX_TOOL_CHARS = 280
MAX_RECENT_TOOLS = 4
MAX_RECENT_MESSAGES = 80


@dataclass
class LocalHandoff:
    """本地交接内容"""
    guide: str  # 用户目标和约束
    summary: str  # 进展和工具记录


def _sanitize_text(text: str, max_chars: int = 160) -> str:
    """清理敏感信息并限制长度"""
    if not text:
        return ""

    text = text[:16_384]  # 输入限制

    # 脱敏私钥
    text = re.sub(
        r'-----BEGIN [^-]*PRIVATE KEY-----[\s\S]*?(?:-----END [^-]*PRIVATE KEY-----|$)',
        '[已脱敏]',
        text,
        flags=re.IGNORECASE
    )

    # 简化代码块
    text = re.sub(r'```[\s\S]*?(?:```|$)', '[代码省略]', text)

    # 脱敏认证头
    text = re.sub(
        r'\b(?:authorization|cookie|set-cookie)\s*[:=][^\r\n]*',
        '[已脱敏]',
        text,
        flags=re.IGNORECASE
    )

    # 脱敏密钥/令牌
    text = re.sub(
        r'(?:["\']?(?:api[_-]?key|access[_-]?token|refresh[_-]?token|token|secret|password|passwd|密码|密钥|令牌)["\']?\s*[:=：]\s*)(?:"[^"\r\n]*"|\'[^\'\r\n]*\'|[^\s,;，；}\r\n]+)',
        '[已脱敏]',
        text,
        flags=re.IGNORECASE
    )

    return text[:max_chars].strip()


def _extract_latest_user_goal(messages: List[Dict]) -> str:
    """提取最新用户目标"""
    # 从后往前找最近的真实用户消息
    for msg in reversed(messages):
        if msg.get('role') != 'user':
            continue
        content = msg.get('content', '')
        if not content or not isinstance(content, str):
            continue

        # 跳过系统生成的消息
        metadata = msg.get('metadata', {})
        if metadata.get('source') in ('synthetic', 'system'):
            continue

        # 清理并限制长度
        goal = _sanitize_text(content, MAX_USER_CHARS)
        if goal:
            return goal

    return ""


def _extract_progress(messages: List[Dict]) -> str:
    """提取最近进展"""
    recent_assistant = []

    # 收集最近的 assistant 消息
    for msg in reversed(messages):
        if msg.get('role') == 'assistant':
            content = msg.get('content', '')
            if content and isinstance(content, str):
                recent_assistant.append(content)
            if len(recent_assistant) >= 2:
                break

    if not recent_assistant:
        return ""

    # 提取关键句子 (不调用模型,简单规则)
    progress_parts = []
    for content in reversed(recent_assistant):
        # 提取行动语句 (我、已、将、正在等开头)
        lines = content.split('\n')
        for line in lines[:10]:  # 只看前10行
            line = line.strip()
            if not line:
                continue

            # 简单模式匹配
            if any(line.startswith(prefix) for prefix in
                   ['我', '已', '将', '正在', 'I ', 'I\'', 'Let me', 'I\'ll']):
                progress_parts.append(_sanitize_text(line, 120))
                if len(progress_parts) >= 3:
                    break

        if len(progress_parts) >= 3:
            break

    progress = '\n'.join(progress_parts)
    return progress[:MAX_PROGRESS_CHARS]


def _extract_tool_calls(messages: List[Dict]) -> str:
    """提取工具调用记录"""
    tool_records = []

    # 从后往前收集工具调用
    for msg in reversed(messages[-MAX_RECENT_MESSAGES:]):
        if msg.get('role') != 'assistant':
            continue

        tool_calls = msg.get('tool_calls', [])
        if not tool_calls:
            continue

        for call in tool_calls:
            if not isinstance(call, dict):
                continue

            tool_name = call.get('function', {}).get('name', '') or call.get('name', '')
            if not tool_name:
                continue

            # 简化参数
            args = call.get('function', {}).get('arguments', '') or call.get('arguments', '')
            if isinstance(args, str) and len(args) > 80:
                args = args[:80] + '...'

            # 查找对应结果
            tool_id = call.get('id', '')
            result_text = ""
            if tool_id:
                for result_msg in messages:
                    if result_msg.get('tool_call_id') == tool_id:
                        result_text = result_msg.get('content', '')[:60]
                        break

            record = f"• {tool_name}"
            if args and isinstance(args, str):
                record += f": {args[:40]}"
            if result_text:
                record += f" → {_sanitize_text(result_text, 40)}"

            tool_records.append(record)

            if len(tool_records) >= MAX_RECENT_TOOLS:
                break

        if len(tool_records) >= MAX_RECENT_TOOLS:
            break

    if not tool_records:
        return ""

    # 反转顺序(时间正序)
    tool_records.reverse()
    tools = '\n'.join(tool_records)
    return tools[:MAX_TOOL_CHARS]


def _extract_state(messages: List[Dict]) -> str:
    """提取当前状态标记"""
    state_sources = {
        'todo_reminder', 'incoming_message', 'goal_state_change',
        'resume_goal_state', 'target_continuation', 'plan_file_reference',
        'queued_system_notification'
    }

    state_parts = []
    for msg in reversed(messages[-20:]):
        metadata = msg.get('metadata', {})
        if metadata.get('source') not in state_sources:
            continue

        content = msg.get('content', '')
        if content and isinstance(content, str):
            state_parts.append(_sanitize_text(content, 100))
            if len(state_parts) >= 3:
                break

    if not state_parts:
        return ""

    state_parts.reverse()
    return '\n'.join(state_parts)[:200]


def build_local_handoff(
    messages: List[Dict],
    custom_instructions: Optional[str] = None,
    max_chars: int = MAX_LOCAL_HANDOFF_CHARS
) -> LocalHandoff:
    """构建本地交接内容

    Args:
        messages: 消息历史 (List[Dict])
        custom_instructions: 自定义指令
        max_chars: 最大字符数限制

    Returns:
        LocalHandoff: 包含 guide 和 summary
    """
    if not messages:
        return LocalHandoff(guide="", summary="")

    budget = max(256, min(MAX_LOCAL_HANDOFF_CHARS, max_chars))

    # 1. 提取用户目标
    user_goal = _extract_latest_user_goal(messages)

    # 2. 提取进展
    progress = _extract_progress(messages)

    # 3. 提取工具调用
    tools = _extract_tool_calls(messages)

    # 4. 提取状态
    state = _extract_state(messages)

    # 5. 组装 guide (用户目标和约束)
    guide_parts = []
    if user_goal:
        guide_parts.append(f"## 最新用户目标\n\n{user_goal}")

    if custom_instructions:
        guide_parts.append(f"## 自定义指令\n\n{custom_instructions[:500]}")

    if state:
        guide_parts.append(f"## 当前状态\n\n{state}")

    guide = '\n\n'.join(guide_parts)
    guide = guide[:budget // 2]  # guide 最多占一半

    # 6. 组装 summary (进展和工具)
    summary_parts = []
    if progress:
        summary_parts.append(f"## 近期进展\n\n{progress}")

    if tools:
        summary_parts.append(f"## 工具调用\n\n{tools}")

    summary = '\n\n'.join(summary_parts)
    remaining = budget - len(guide)
    summary = summary[:max(0, remaining)]

    return LocalHandoff(guide=guide, summary=summary)


def format_handoff_for_prompt(handoff: LocalHandoff) -> str:
    """将交接内容格式化为 prompt

    用于替代原始消息历史注入到上下文
    """
    if not handoff.guide and not handoff.summary:
        return ""

    parts = []
    if handoff.guide:
        parts.append(handoff.guide)
    if handoff.summary:
        parts.append(handoff.summary)

    content = '\n\n'.join(parts)
    return f"# 上下文交接\n\n{content}"
