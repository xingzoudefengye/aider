"""测试 SQLite 会话持久化"""

import os
import tempfile
import time
from pathlib import Path

import pytest

from aider.core.session_store import SessionStore, load_session_store


@pytest.fixture
def temp_db():
    """创建临时数据库"""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    yield db_path

    # 清理
    if os.path.exists(db_path):
        os.remove(db_path)


def test_session_store_init(temp_db):
    """测试数据库初始化"""
    store = SessionStore(temp_db)
    assert store.conn is not None
    assert os.path.exists(temp_db)
    store.close()


def test_create_and_get_session(temp_db):
    """测试创建和获取会话"""
    store = SessionStore(temp_db)

    session = store.create_session(
        session_id="test-001",
        model="gpt-4",
        git_root="/path/to/repo",
        metadata={"user": "test_user"},
    )

    assert session["session_id"] == "test-001"
    assert session["model"] == "gpt-4"
    assert session["git_root"] == "/path/to/repo"
    assert session["metadata"]["user"] == "test_user"
    assert session["message_count"] == 0

    # 获取会话
    loaded = store.get_session("test-001")
    assert loaded["session_id"] == "test-001"
    assert loaded["model"] == "gpt-4"

    store.close()


def test_add_and_get_messages(temp_db):
    """测试添加和获取消息"""
    store = SessionStore(temp_db)
    store.create_session("test-002", model="gpt-4")

    # 添加消息
    store.add_message("test-002", "user", "Hello")
    store.add_message("test-002", "assistant", "Hi there!")
    store.add_message(
        "test-002",
        "assistant",
        "Using tool",
        tool_calls=[{"name": "read_file", "args": {"path": "test.py"}}],
    )

    # 获取消息
    messages = store.get_messages("test-002")
    assert len(messages) == 3
    assert messages[0]["role"] == "user"
    assert messages[0]["content"] == "Hello"
    assert messages[1]["role"] == "assistant"
    assert messages[2]["tool_calls"][0]["name"] == "read_file"

    # 检查会话的 message_count
    session = store.get_session("test-002")
    assert session["message_count"] == 3

    store.close()


def test_list_sessions(temp_db):
    """测试列出会话"""
    store = SessionStore(temp_db)

    # 创建多个会话
    store.create_session("s1", "gpt-4")
    time.sleep(0.01)
    store.create_session("s2", "claude-3")
    time.sleep(0.01)
    store.create_session("s3", "gpt-3.5")

    # 列出会话 (按更新时间倒序)
    sessions = store.list_sessions(limit=10)
    assert len(sessions) == 3
    assert sessions[0]["session_id"] == "s3"  # 最新
    assert sessions[1]["session_id"] == "s2"
    assert sessions[2]["session_id"] == "s1"

    # 测试分页
    page1 = store.list_sessions(limit=2, offset=0)
    assert len(page1) == 2
    assert page1[0]["session_id"] == "s3"

    page2 = store.list_sessions(limit=2, offset=2)
    assert len(page2) == 1
    assert page2[0]["session_id"] == "s1"

    store.close()


def test_update_session_stats(temp_db):
    """测试更新会话统计"""
    store = SessionStore(temp_db)
    store.create_session("test-003", model="gpt-4")

    # 第一次更新
    store.update_session_stats(
        "test-003",
        total_cost=0.05,
        total_tokens=1000,
        cache_read_tokens=500,
        cache_creation_tokens=200,
    )

    session = store.get_session("test-003")
    assert session["total_cost"] == 0.05
    assert session["total_tokens"] == 1000
    assert session["cache_read_tokens"] == 500
    assert session["cache_creation_tokens"] == 200

    # 第二次更新 (累加)
    store.update_session_stats(
        "test-003",
        total_cost=0.03,
        total_tokens=500,
        cache_read_tokens=300,
        cache_creation_tokens=100,
    )

    session = store.get_session("test-003")
    assert session["total_cost"] == 0.08
    assert session["total_tokens"] == 1500
    assert session["cache_read_tokens"] == 800
    assert session["cache_creation_tokens"] == 300

    store.close()


def test_delete_session(temp_db):
    """测试删除会话"""
    store = SessionStore(temp_db)
    store.create_session("test-004", model="gpt-4")
    store.add_message("test-004", "user", "Hello")

    # 删除前确认存在
    assert store.get_session("test-004") is not None
    assert len(store.get_messages("test-004")) == 1

    # 删除
    store.delete_session("test-004")

    # 确认已删除
    assert store.get_session("test-004") is None
    assert len(store.get_messages("test-004")) == 0

    store.close()


def test_message_limit(temp_db):
    """测试消息数量限制"""
    store = SessionStore(temp_db)
    store.create_session("test-005", model="gpt-4")

    # 添加 10 条消息
    for i in range(10):
        store.add_message("test-005", "user", f"Message {i}")

    # 获取全部
    all_messages = store.get_messages("test-005")
    assert len(all_messages) == 10

    # 获取最近 5 条
    recent = store.get_messages("test-005", limit=5)
    assert len(recent) == 5
    assert recent[0]["content"] == "Message 0"  # 按时间顺序

    store.close()


def test_session_updated_at(temp_db):
    """测试会话的 updated_at 自动更新"""
    store = SessionStore(temp_db)
    session = store.create_session("test-006", model="gpt-4")
    created_at = session["created_at"]
    updated_at1 = session["updated_at"]

    assert created_at == updated_at1

    time.sleep(0.01)

    # 添加消息应更新 updated_at
    store.add_message("test-006", "user", "Hello")
    session = store.get_session("test-006")
    updated_at2 = session["updated_at"]

    assert updated_at2 > updated_at1

    time.sleep(0.01)

    # 更新统计也应更新 updated_at
    store.update_session_stats("test-006", total_cost=0.01)
    session = store.get_session("test-006")
    updated_at3 = session["updated_at"]

    assert updated_at3 > updated_at2

    store.close()


def test_load_session_store_helper():
    """测试辅助加载函数"""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    try:
        store = load_session_store(db_path)
        assert store.db_path == db_path
        assert store.conn is not None
        store.close()
    finally:
        if os.path.exists(db_path):
            os.remove(db_path)


def test_default_db_path():
    """测试默认数据库路径"""
    store = load_session_store()
    expected_path = Path.home() / ".aider" / "sessions.db"

    assert store.db_path == str(expected_path)
    assert Path(store.db_path).exists()

    store.close()


def test_session_metadata():
    """测试会话元数据"""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    try:
        store = SessionStore(db_path)

        # 创建带复杂元数据的会话
        metadata = {
            "user": "test_user",
            "tags": ["bug-fix", "feature"],
            "priority": 1,
            "nested": {"key": "value"},
        }

        store.create_session("test-007", model="gpt-4", metadata=metadata)
        session = store.get_session("test-007")

        assert session["metadata"]["user"] == "test_user"
        assert session["metadata"]["tags"] == ["bug-fix", "feature"]
        assert session["metadata"]["priority"] == 1
        assert session["metadata"]["nested"]["key"] == "value"

        store.close()
    finally:
        if os.path.exists(db_path):
            os.remove(db_path)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
