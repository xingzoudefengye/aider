"""固定容量分层史书系统

参考 ComeCode 的实现:
E:\Projects\ComeCode\engine\apps\zcode-cli\packages\core\src\compact\chronicle.ts

核心思想:
- 每回合结束后本地摘要(不调用模型)
- 分层存储: 近期细致、旧历史粗略
- 容量不足时自动合并衰减
- 万轮对话稳定在 6000 字符以内
"""

import json
import time
from dataclasses import dataclass, asdict, replace
from pathlib import Path
from typing import List, Optional, Dict


# 容量限制
SESSION_CHRONICLE_MAX_CHARS = 6_000
SESSION_CHRONICLE_MAX_ENTRIES = 22

# 分层配置
RECENT_MAX_ENTRIES = 12
RECENT_MAX_CHARS_PER_ENTRY = 160

EARLIER_MAX_ENTRIES = 6
EARLIER_MAX_CHARS_PER_ENTRY = 100

OLDEST_MAX_ENTRIES = 4
OLDEST_MAX_CHARS_PER_ENTRY = 60


@dataclass
class ChronicleEntry:
    """史书条目"""
    summary: str  # 摘要内容
    status: str  # success/partial/error/interrupted
    timestamp: float  # Unix 时间戳
    turn_index: int  # 回合索引


class SessionChronicle:
    """固定容量分层史书"""

    def __init__(self, storage_path: Optional[str] = None):
        self.recent_entries: List[ChronicleEntry] = []
        self.earlier_entries: List[ChronicleEntry] = []
        self.oldest_entries: List[ChronicleEntry] = []
        self.storage_path = Path(storage_path) if storage_path else None
        self.turn_counter = 0

        if self.storage_path and self.storage_path.exists():
            self._load()

    def add_turn(
        self,
        user_goal: str,
        assistant_response: str,
        status: str = "success",
        tool_calls: Optional[List[str]] = None
    ) -> None:
        """添加一回合记录

        Args:
            user_goal: 用户目标
            assistant_response: 助手回复
            status: 状态 (success/partial/error/interrupted)
            tool_calls: 工具调用列表
        """
        self.turn_counter += 1

        # 本地提取摘要(不调用模型)
        summary = self._extract_turn_summary(
            user_goal,
            assistant_response,
            tool_calls
        )

        entry = ChronicleEntry(
            summary=summary[:RECENT_MAX_CHARS_PER_ENTRY],
            status=status,
            timestamp=time.time(),
            turn_index=self.turn_counter
        )

        self.recent_entries.append(entry)
        self._compact_if_needed()

        # 自动保存
        if self.storage_path:
            self._save()

    def _extract_turn_summary(
        self,
        user_goal: str,
        assistant_response: str,
        tool_calls: Optional[List[str]] = None
    ) -> str:
        """本地提取回合摘要(规则匹配,不调用模型)"""
        parts = []

        # 1. 提取用户意图关键词
        goal_keywords = self._extract_keywords(user_goal, max_len=40)
        if goal_keywords:
            parts.append(f"目标: {goal_keywords}")

        # 2. 提取助手行动
        action = self._extract_action(assistant_response, max_len=60)
        if action:
            parts.append(f"行动: {action}")

        # 3. 工具调用
        if tool_calls:
            tools_str = ", ".join(tool_calls[:3])
            if len(tool_calls) > 3:
                tools_str += f" +{len(tool_calls) - 3}个"
            parts.append(f"工具: {tools_str}")

        summary = " | ".join(parts)
        return summary[:RECENT_MAX_CHARS_PER_ENTRY]

    def _extract_keywords(self, text: str, max_len: int = 40) -> str:
        """提取关键词"""
        if not text:
            return ""

        # 简单截取前几个词
        words = text.strip().split()[:8]
        result = " ".join(words)
        return result[:max_len]

    def _extract_action(self, text: str, max_len: int = 60) -> str:
        """提取行动描述"""
        if not text:
            return ""

        # 查找行动关键句
        lines = text.split('\n')
        for line in lines[:5]:
            line = line.strip()
            # 匹配行动模式
            if any(line.startswith(prefix) for prefix in
                   ['我', '已', '将', '正在', 'I ', 'I\'', 'Let me', 'I\'ll']):
                return line[:max_len]

        # 回退: 返回第一行
        first_line = lines[0].strip() if lines else ""
        return first_line[:max_len]

    def _compact_if_needed(self) -> None:
        """容量不足时自动分层压缩"""
        now = time.time()
        # 即使没有新增很多回合，超过 7/30 天的记录也会降到更粗的层级。
        recent = []
        for entry in self.recent_entries:
            if now - entry.timestamp > 7 * 86_400:
                self.earlier_entries.append(replace(entry, summary=entry.summary[:EARLIER_MAX_CHARS_PER_ENTRY]))
            else:
                recent.append(entry)
        self.recent_entries = recent
        earlier = []
        for entry in self.earlier_entries:
            if now - entry.timestamp > 30 * 86_400:
                self.oldest_entries.append(replace(entry, summary=entry.summary[:OLDEST_MAX_CHARS_PER_ENTRY]))
            else:
                earlier.append(entry)
        self.earlier_entries = sorted(earlier, key=lambda entry: entry.timestamp)
        self.oldest_entries.sort(key=lambda entry: entry.timestamp)
        # 1. 近期条目超限 → 合并最旧的几条到 earlier
        while len(self.recent_entries) > RECENT_MAX_ENTRIES:
            overflow_count = len(self.recent_entries) - RECENT_MAX_ENTRIES
            merge_count = min(overflow_count + 2, 4)  # 每次合并 2-4 条

            to_merge = self.recent_entries[:merge_count]
            self.recent_entries = self.recent_entries[merge_count:]

            merged = self._merge_entries(to_merge, EARLIER_MAX_CHARS_PER_ENTRY)
            self.earlier_entries.append(merged)

        # 2. 早期条目超限 → 合并最旧的几条到 oldest
        while len(self.earlier_entries) > EARLIER_MAX_ENTRIES:
            overflow_count = len(self.earlier_entries) - EARLIER_MAX_ENTRIES
            merge_count = min(overflow_count + 1, 3)  # 每次合并 1-3 条

            to_merge = self.earlier_entries[:merge_count]
            self.earlier_entries = self.earlier_entries[merge_count:]

            merged = self._merge_entries(to_merge, OLDEST_MAX_CHARS_PER_ENTRY)
            self.oldest_entries.append(merged)

        # 3. 最旧条目超限 → 丢弃最旧的
        if len(self.oldest_entries) > OLDEST_MAX_ENTRIES:
            self.oldest_entries = self.oldest_entries[-OLDEST_MAX_ENTRIES:]

    def _merge_entries(
        self,
        entries: List[ChronicleEntry],
        max_chars: int
    ) -> ChronicleEntry:
        """合并多个条目为一个粗粒度条目"""
        if not entries:
            raise ValueError("无条目可合并")

        # 提取关键信息
        summaries = [e.summary for e in entries]
        statuses = [e.status for e in entries]

        # 统计状态
        success_count = statuses.count("success")
        error_count = statuses.count("error")

        # 组装合并摘要
        merged_summary = " · ".join(summaries)[:max_chars - 20]

        # 添加统计信息
        status_suffix = ""
        if error_count > 0:
            status_suffix = f" [{success_count}✓/{error_count}✗]"
        elif success_count > 0:
            status_suffix = f" [{success_count}✓]"

        merged_summary += status_suffix

        # 使用最新时间戳和索引
        return ChronicleEntry(
            summary=merged_summary[:max_chars],
            status="merged",
            timestamp=entries[-1].timestamp,
            turn_index=entries[-1].turn_index
        )

    def get_chronicle_text(self) -> str:
        """获取完整史书文本"""
        sections = []

        if self.oldest_entries:
            oldest = "\n".join(f"- {e.summary}" for e in self.oldest_entries)
            sections.append(f"## 早期历史\n\n{oldest}")

        if self.earlier_entries:
            earlier = "\n".join(f"- {e.summary}" for e in self.earlier_entries)
            sections.append(f"## 近期历史\n\n{earlier}")

        if self.recent_entries:
            recent = "\n".join(f"- {e.summary}" for e in self.recent_entries)
            sections.append(f"## 最近回合\n\n{recent}")

        if not sections:
            return ""

        return "# 会话史书\n\n" + "\n\n".join(sections)

    def get_stats(self) -> Dict:
        """获取统计信息"""
        total_entries = (
            len(self.recent_entries) +
            len(self.earlier_entries) +
            len(self.oldest_entries)
        )

        chronicle_text = self.get_chronicle_text()

        return {
            "total_turns": self.turn_counter,
            "total_entries": total_entries,
            "recent_entries": len(self.recent_entries),
            "earlier_entries": len(self.earlier_entries),
            "oldest_entries": len(self.oldest_entries),
            "total_chars": len(chronicle_text),
            "within_budget": len(chronicle_text) <= SESSION_CHRONICLE_MAX_CHARS
        }

    def _save(self) -> None:
        """保存到文件"""
        if not self.storage_path:
            return

        data = {
            "turn_counter": self.turn_counter,
            "recent_entries": [asdict(e) for e in self.recent_entries],
            "earlier_entries": [asdict(e) for e in self.earlier_entries],
            "oldest_entries": [asdict(e) for e in self.oldest_entries],
            "saved_at": time.time()
        }

        self.storage_path.parent.mkdir(parents=True, exist_ok=True)
        self.storage_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )

    def _load(self) -> None:
        """从文件加载"""
        if not self.storage_path or not self.storage_path.exists():
            return

        try:
            data = json.loads(self.storage_path.read_text(encoding="utf-8"))

            self.turn_counter = data.get("turn_counter", 0)

            self.recent_entries = [
                ChronicleEntry(**e) for e in data.get("recent_entries", [])
            ]
            self.earlier_entries = [
                ChronicleEntry(**e) for e in data.get("earlier_entries", [])
            ]
            self.oldest_entries = [
                ChronicleEntry(**e) for e in data.get("oldest_entries", [])
            ]
            self._compact_if_needed()
        except (json.JSONDecodeError, TypeError, KeyError):
            # 损坏时重置
            pass

    def clear(self) -> None:
        """清空史书"""
        self.recent_entries.clear()
        self.earlier_entries.clear()
        self.oldest_entries.clear()
        self.turn_counter = 0

        if self.storage_path and self.storage_path.exists():
            self.storage_path.unlink()


def load_chronicle(project_root: str) -> SessionChronicle:
    """加载项目的会话史书"""
    storage_path = Path(project_root) / ".aider" / "session_chronicle.json"
    return SessionChronicle(storage_path=str(storage_path))
