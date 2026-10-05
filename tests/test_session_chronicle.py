"""测试固定容量史书系统"""

import json
import tempfile
from pathlib import Path

import pytest

from aider.core.session_chronicle import (
    SessionChronicle,
    ChronicleEntry,
    load_chronicle,
    SESSION_CHRONICLE_MAX_CHARS,
    RECENT_MAX_ENTRIES,
    EARLIER_MAX_ENTRIES,
    OLDEST_MAX_ENTRIES,
)


def test_add_single_turn():
    """测试添加单个回合"""
    chronicle = SessionChronicle()

    chronicle.add_turn(
        user_goal="修复登录 bug",
        assistant_response="我已经修复了登录逻辑",
        status="success"
    )

    assert len(chronicle.recent_entries) == 1
    assert chronicle.turn_counter == 1
    assert "修复登录" in chronicle.recent_entries[0].summary


def test_add_turn_with_tools():
    """测试包含工具调用的回合"""
    chronicle = SessionChronicle()

    chronicle.add_turn(
        user_goal="读取配置文件",
        assistant_response="我来读取配置",
        status="success",
        tool_calls=["read_file", "edit_file"]
    )

    entry = chronicle.recent_entries[0]
    assert "工具" in entry.summary
    assert "read_file" in entry.summary or "edit_file" in entry.summary


def test_extract_keywords():
    """测试关键词提取"""
    chronicle = SessionChronicle()

    keywords = chronicle._extract_keywords("请帮我实现用户认证和权限管理系统", max_len=20)
    assert len(keywords) <= 20
    assert len(keywords) > 0


def test_extract_action():
    """测试行动提取"""
    chronicle = SessionChronicle()

    action = chronicle._extract_action("我已经创建了 auth.py 文件\n正在添加验证逻辑")
    assert "已经创建" in action or "正在添加" in action


def test_compact_recent_to_earlier():
    """测试近期条目压缩到早期"""
    chronicle = SessionChronicle()

    # 添加超过限制的条目
    for i in range(RECENT_MAX_ENTRIES + 5):
        chronicle.add_turn(
            user_goal=f"任务 {i}",
            assistant_response=f"完成任务 {i}",
            status="success"
        )

    # 应该触发压缩
    assert len(chronicle.recent_entries) <= RECENT_MAX_ENTRIES
    assert len(chronicle.earlier_entries) > 0


def test_compact_earlier_to_oldest():
    """测试早期条目压缩到最旧"""
    chronicle = SessionChronicle()

    # 需要足够多的回合才能触发多级压缩
    # RECENT_MAX_ENTRIES=12, 超出会合并 2-4 条到 earlier
    # EARLIER_MAX_ENTRIES=6, 超出会合并 1-3 条到 oldest
    # 所以需要: 12 + (6+1)*4 = 40+ 回合才能确保触发
    for i in range(50):
        chronicle.add_turn(
            user_goal=f"任务 {i}",
            assistant_response=f"完成 {i}",
            status="success"
        )

    assert len(chronicle.recent_entries) <= RECENT_MAX_ENTRIES
    assert len(chronicle.earlier_entries) <= EARLIER_MAX_ENTRIES
    assert len(chronicle.oldest_entries) > 0


def test_compact_drops_oldest():
    """测试最旧条目丢弃"""
    chronicle = SessionChronicle()

    # 模拟极大量回合
    for i in range(100):
        chronicle.add_turn(
            user_goal=f"任务 {i}",
            assistant_response=f"完成 {i}",
            status="success"
        )

    # 最旧条目应该被限制
    assert len(chronicle.oldest_entries) <= OLDEST_MAX_ENTRIES

    # 总条目数应该稳定
    stats = chronicle.get_stats()
    assert stats["total_entries"] <= (
        RECENT_MAX_ENTRIES + EARLIER_MAX_ENTRIES + OLDEST_MAX_ENTRIES
    )


def test_merge_entries():
    """测试条目合并"""
    chronicle = SessionChronicle()

    entries = [
        ChronicleEntry(summary="任务 1", status="success", timestamp=1.0, turn_index=1),
        ChronicleEntry(summary="任务 2", status="error", timestamp=2.0, turn_index=2),
        ChronicleEntry(summary="任务 3", status="success", timestamp=3.0, turn_index=3),
    ]

    merged = chronicle._merge_entries(entries, max_chars=100)

    assert merged.status == "merged"
    assert merged.timestamp == 3.0  # 最新时间
    assert merged.turn_index == 3  # 最新索引
    assert "✓" in merged.summary  # 包含统计
    assert "✗" in merged.summary  # 包含错误统计


def test_get_chronicle_text():
    """测试获取史书文本"""
    chronicle = SessionChronicle()

    # 添加一些回合
    for i in range(5):
        chronicle.add_turn(
            user_goal=f"任务 {i}",
            assistant_response=f"完成任务 {i}",
            status="success"
        )

    text = chronicle.get_chronicle_text()
    assert "# 会话史书" in text
    assert "最近回合" in text
    assert len(text) > 0


def test_get_stats():
    """测试统计信息"""
    chronicle = SessionChronicle()

    for i in range(10):
        chronicle.add_turn(
            user_goal=f"任务 {i}",
            assistant_response=f"完成 {i}",
            status="success"
        )

    stats = chronicle.get_stats()

    assert stats["total_turns"] == 10
    assert stats["total_entries"] > 0
    assert stats["total_chars"] > 0
    assert isinstance(stats["within_budget"], bool)


def test_chronicle_within_budget():
    """测试万轮对话容量限制"""
    chronicle = SessionChronicle()

    # 模拟大量回合
    for i in range(1000):
        chronicle.add_turn(
            user_goal=f"任务 {i} 这是一个很长的用户目标描述",
            assistant_response=f"我已经完成了任务 {i}，并且做了很多工作",
            status="success",
            tool_calls=["read_file", "edit_file", "run_command"]
        )

    stats = chronicle.get_stats()
    text = chronicle.get_chronicle_text()

    # 核心验证: 史书容量应该稳定
    assert len(text) <= SESSION_CHRONICLE_MAX_CHARS
    assert stats["within_budget"] is True


def test_save_and_load():
    """测试保存和加载"""
    with tempfile.TemporaryDirectory() as tmpdir:
        storage_path = Path(tmpdir) / "chronicle.json"

        # 创建并保存
        chronicle1 = SessionChronicle(storage_path=str(storage_path))
        chronicle1.add_turn("任务 1", "完成 1", "success")
        chronicle1.add_turn("任务 2", "完成 2", "success")

        assert storage_path.exists()

        # 加载到新实例
        chronicle2 = SessionChronicle(storage_path=str(storage_path))

        assert chronicle2.turn_counter == chronicle1.turn_counter
        assert len(chronicle2.recent_entries) == len(chronicle1.recent_entries)
        assert chronicle2.recent_entries[0].summary == chronicle1.recent_entries[0].summary


def test_load_corrupted_file():
    """测试加载损坏文件"""
    with tempfile.TemporaryDirectory() as tmpdir:
        storage_path = Path(tmpdir) / "chronicle.json"

        # 写入损坏的 JSON
        storage_path.write_text("corrupted json data", encoding="utf-8")

        # 应该优雅处理，不崩溃
        chronicle = SessionChronicle(storage_path=str(storage_path))
        assert chronicle.turn_counter == 0
        assert len(chronicle.recent_entries) == 0


def test_clear():
    """测试清空史书"""
    with tempfile.TemporaryDirectory() as tmpdir:
        storage_path = Path(tmpdir) / "chronicle.json"

        chronicle = SessionChronicle(storage_path=str(storage_path))
        chronicle.add_turn("任务 1", "完成 1", "success")

        assert len(chronicle.recent_entries) > 0
        assert storage_path.exists()

        chronicle.clear()

        assert len(chronicle.recent_entries) == 0
        assert chronicle.turn_counter == 0
        assert not storage_path.exists()


def test_load_chronicle_helper():
    """测试加载辅助函数"""
    with tempfile.TemporaryDirectory() as tmpdir:
        chronicle = load_chronicle(tmpdir)

        assert isinstance(chronicle, SessionChronicle)
        assert chronicle.storage_path is not None
        assert ".aider" in str(chronicle.storage_path)


def test_entry_summary_length_limit():
    """测试条目摘要长度限制"""
    chronicle = SessionChronicle()

    # 超长输入
    long_goal = "x" * 1000
    long_response = "y" * 1000

    chronicle.add_turn(long_goal, long_response, "success")

    entry = chronicle.recent_entries[0]
    # 应该被限制在 160 字符内
    assert len(entry.summary) <= 160


def test_status_types():
    """测试不同状态类型"""
    chronicle = SessionChronicle()

    chronicle.add_turn("任务 1", "完成", "success")
    chronicle.add_turn("任务 2", "部分完成", "partial")
    chronicle.add_turn("任务 3", "失败", "error")
    chronicle.add_turn("任务 4", "中断", "interrupted")

    assert len(chronicle.recent_entries) == 4
    assert chronicle.recent_entries[0].status == "success"
    assert chronicle.recent_entries[1].status == "partial"
    assert chronicle.recent_entries[2].status == "error"
    assert chronicle.recent_entries[3].status == "interrupted"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
