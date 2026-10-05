"""
SQLite 会话持久化存储

参考 ComeCode 的永续会话设计，实现:
- 完整会话历史存储
- 跨终端加载恢复
- 元数据和统计
"""

import json
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, List, Optional
from datetime import datetime


class SessionStore:
    """SQLite 会话存储"""

    def __init__(self, db_path: Optional[str] = None):
        """
        初始化会话存储

        Args:
            db_path: 数据库路径，默认 ~/.aider/sessions.db
        """
        if db_path is None:
            aider_dir = Path.home() / ".aider"
            aider_dir.mkdir(parents=True, exist_ok=True)
            db_path = str(aider_dir / "sessions.db")

        self.db_path = db_path
        self.conn: Optional[sqlite3.Connection] = None
        self._init_db()

    def _init_db(self):
        """初始化数据库表结构"""
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row  # 支持字典访问

        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS sessions (
                session_id TEXT PRIMARY KEY,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                model TEXT,
                git_root TEXT,
                metadata TEXT,
                total_cost REAL DEFAULT 0,
                total_tokens INTEGER DEFAULT 0,
                cache_read_tokens INTEGER DEFAULT 0,
                cache_creation_tokens INTEGER DEFAULT 0,
                message_count INTEGER DEFAULT 0
            )
        """)

        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                timestamp REAL NOT NULL,
                tool_calls TEXT,
                metadata TEXT,
                FOREIGN KEY (session_id) REFERENCES sessions(session_id)
            )
        """)

        self.conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_messages_session
            ON messages(session_id, timestamp)
        """)

        self.conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_sessions_updated
            ON sessions(updated_at DESC)
        """)

        self.conn.commit()

    def create_session(
        self,
        session_id: str,
        model: str,
        git_root: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        创建新会话

        Args:
            session_id: 会话 ID
            model: 模型名称
            git_root: Git 根目录
            metadata: 额外元数据

        Returns:
            会话记录
        """
        now = time.time()
        metadata_json = json.dumps(metadata or {})

        self.conn.execute(
            """
            INSERT OR REPLACE INTO sessions
            (session_id, created_at, updated_at, model, git_root, metadata)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (session_id, now, now, model, git_root, metadata_json),
        )
        self.conn.commit()

        return self.get_session(session_id)

    def get_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        """获取会话信息"""
        cursor = self.conn.execute(
            "SELECT * FROM sessions WHERE session_id = ?", (session_id,)
        )
        row = cursor.fetchone()

        if not row:
            return None

        return {
            "session_id": row["session_id"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "model": row["model"],
            "git_root": row["git_root"],
            "metadata": json.loads(row["metadata"]) if row["metadata"] else {},
            "total_cost": row["total_cost"],
            "total_tokens": row["total_tokens"],
            "cache_read_tokens": row["cache_read_tokens"],
            "cache_creation_tokens": row["cache_creation_tokens"],
            "message_count": row["message_count"],
        }

    def list_sessions(
        self, limit: int = 50, offset: int = 0
    ) -> List[Dict[str, Any]]:
        """
        列出最近会话

        Args:
            limit: 返回数量
            offset: 偏移量

        Returns:
            会话列表 (按更新时间倒序)
        """
        cursor = self.conn.execute(
            """
            SELECT * FROM sessions
            ORDER BY updated_at DESC
            LIMIT ? OFFSET ?
            """,
            (limit, offset),
        )

        sessions = []
        for row in cursor.fetchall():
            sessions.append({
                "session_id": row["session_id"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
                "model": row["model"],
                "git_root": row["git_root"],
                "metadata": json.loads(row["metadata"]) if row["metadata"] else {},
                "total_cost": row["total_cost"],
                "total_tokens": row["total_tokens"],
                "message_count": row["message_count"],
            })

        return sessions

    def add_message(
        self,
        session_id: str,
        role: str,
        content: str,
        tool_calls: Optional[List[Dict[str, Any]]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ):
        """
        添加消息到会话

        Args:
            session_id: 会话 ID
            role: 角色 (user/assistant/system)
            content: 消息内容
            tool_calls: 工具调用列表
            metadata: 额外元数据
        """
        now = time.time()
        tool_calls_json = json.dumps(tool_calls) if tool_calls else None
        metadata_json = json.dumps(metadata or {})

        self.conn.execute(
            """
            INSERT INTO messages
            (session_id, role, content, timestamp, tool_calls, metadata)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (session_id, role, content, now, tool_calls_json, metadata_json),
        )

        # 更新会话的 updated_at 和 message_count
        self.conn.execute(
            """
            UPDATE sessions
            SET updated_at = ?,
                message_count = message_count + 1
            WHERE session_id = ?
            """,
            (now, session_id),
        )

        self.conn.commit()

    def get_messages(
        self, session_id: str, limit: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """
        获取会话的所有消息

        Args:
            session_id: 会话 ID
            limit: 限制返回数量 (None = 全部)

        Returns:
            消息列表 (按时间顺序)
        """
        if limit:
            cursor = self.conn.execute(
                """
                SELECT * FROM messages
                WHERE session_id = ?
                ORDER BY timestamp ASC
                LIMIT ?
                """,
                (session_id, limit),
            )
        else:
            cursor = self.conn.execute(
                """
                SELECT * FROM messages
                WHERE session_id = ?
                ORDER BY timestamp ASC
                """,
                (session_id,),
            )

        messages = []
        for row in cursor.fetchall():
            msg = {
                "role": row["role"],
                "content": row["content"],
                "timestamp": row["timestamp"],
            }

            if row["tool_calls"]:
                msg["tool_calls"] = json.loads(row["tool_calls"])

            if row["metadata"]:
                msg["metadata"] = json.loads(row["metadata"])

            messages.append(msg)

        return messages

    def update_session_stats(
        self,
        session_id: str,
        total_cost: float = 0,
        total_tokens: int = 0,
        cache_read_tokens: int = 0,
        cache_creation_tokens: int = 0,
    ):
        """
        更新会话统计信息

        Args:
            session_id: 会话 ID
            total_cost: 增量成本
            total_tokens: 增量 token 数
            cache_read_tokens: 增量缓存读取 token 数
            cache_creation_tokens: 增量缓存创建 token 数
        """
        self.conn.execute(
            """
            UPDATE sessions
            SET total_cost = total_cost + ?,
                total_tokens = total_tokens + ?,
                cache_read_tokens = cache_read_tokens + ?,
                cache_creation_tokens = cache_creation_tokens + ?,
                updated_at = ?
            WHERE session_id = ?
            """,
            (
                total_cost,
                total_tokens,
                cache_read_tokens,
                cache_creation_tokens,
                time.time(),
                session_id,
            ),
        )
        self.conn.commit()

    def delete_session(self, session_id: str):
        """删除会话及其所有消息"""
        self.conn.execute("DELETE FROM messages WHERE session_id = ?", (session_id,))
        self.conn.execute("DELETE FROM sessions WHERE session_id = ?", (session_id,))
        self.conn.commit()

    def close(self):
        """关闭数据库连接"""
        if self.conn:
            self.conn.close()
            self.conn = None

    def __del__(self):
        """析构时自动关闭连接"""
        self.close()


def load_session_store(db_path: Optional[str] = None) -> SessionStore:
    """
    辅助函数: 加载会话存储

    Args:
        db_path: 数据库路径，默认 ~/.aider/sessions.db

    Returns:
        SessionStore 实例
    """
    return SessionStore(db_path)
