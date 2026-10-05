"""会话级本地交接：完整请求预算、分层史书与恢复锚点。"""

import hashlib
import json
from dataclasses import asdict
from pathlib import Path

from .local_handoff import build_local_handoff, format_handoff_for_prompt, _sanitize_text
from .session_chronicle import ChronicleEntry, SessionChronicle


class ContextManager:
    threshold_percent = 75

    def __init__(self, history_file=None, io=None, restore=False, history_limit=None):
        self.path = Path(str(history_file) + ".context.json") if history_file else None
        self.io = io
        self.chronicle = SessionChronicle()
        self.handoff = ""
        self.memory = ""
        self.checkpoint = []
        self.last_turn_digest = None
        self.compactions = 0
        self.last_input_tokens = 0
        self.last_estimated_tokens = 0
        self.persistence_enabled = True
        self.history_limit = history_limit
        if restore and self.path and self.path.is_file():
            self._load()

    def threshold(self, model):
        window = model.info.get("max_input_tokens") or 512_000
        output = (model.extra_params or {}).get("max_tokens") or model.info.get("max_output_tokens") or 32_000
        # 与 ComeCode 一样预留输出和安全余量；小窗口按比例限制预留，避免阈值为零。
        reserve = min(output, 21_000, window // 4)
        buffer = min(13_000, window // 16)
        return min(int(window * self.threshold_percent / 100), max(1, window - reserve - buffer))

    def should_compact(self, model, messages, functions=None, history=None):
        estimate = model.token_count(messages) or 0
        if functions:
            estimate += model.token_count(json.dumps(functions, ensure_ascii=False)) or 0
        # 已有供应商用量时，保守校准本地估算；缓存 token 已包含在输入用量中。
        ratio = max(1, self.last_input_tokens / self.last_estimated_tokens) if self.last_estimated_tokens else 1
        self.last_estimated_tokens = estimate
        return (int(estimate * ratio) >= self.threshold(model)
                or bool(self.history_limit and (model.token_count(history or []) or 0) > self.history_limit))

    @staticmethod
    def _digest(message):
        content = message.get("content", "")
        if isinstance(content, str):
            content = content.strip()
        value = json.dumps([message.get("role"), content], sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(value.encode("utf-8")).hexdigest()

    def restore_messages(self, messages):
        if not self.checkpoint:
            if not self.chronicle.turn_counter:
                self.seed_history(messages)
            return messages
        hashes = [self._digest(message) for message in messages]
        length = len(self.checkpoint)
        for end in range(len(hashes), length - 1, -1):
            if hashes[end - length:end] == self.checkpoint:
                return messages[end:]
        # 历史被编辑或来自另一分支时不删除任何原文，也不注入不匹配的旧交接。
        self.handoff = self.memory = ""
        self.checkpoint = []
        self.chronicle = SessionChronicle()
        self.last_turn_digest = None
        if self.io:
            self.io.tool_warning("交接锚点与当前历史不匹配，保留完整历史重新建立记忆")
        return messages

    def seed_history(self, messages):
        """为尚无史书的旧会话补建本地回合记录，只保存有界的分层摘要。"""
        goal = ""
        for message in messages:
            content = message.get("content", "")
            if not isinstance(content, str):
                continue
            if message.get("role") == "user":
                goal = content if not content.lstrip().startswith("/") else ""
            elif message.get("role") == "assistant" and goal:
                self.chronicle.add_turn(_sanitize_text(goal, 2_000), _sanitize_text(content, 2_000))
                goal = ""
        self._save()

    def record_turn(self, goal, response, status="success"):
        if not goal or goal.lstrip().startswith("/"):
            return
        digest = self._digest({"role": "turn", "content": [goal, response, status]})
        self.chronicle.add_turn(_sanitize_text(goal, 2_000), _sanitize_text(response, 2_000), status)
        self.last_turn_digest = digest
        self._save()

    def compact(self, model, messages):
        """只交接旧历史，保留最后一组用户消息及可容纳的近期原文。"""
        if len(messages) < 3:
            return messages, False
        window = model.info.get("max_input_tokens") or 512_000
        budget = min(16_384, max(256, window // 10))
        if self.history_limit:
            budget = min(budget, max(128, self.history_limit // 2))
        last_user = next((i for i in range(len(messages) - 1, -1, -1)
                          if messages[i].get("role") == "user"), len(messages) - 1)
        split = last_user
        used = model.token_count(messages[split:]) or 0
        # 按完整用户回合选择尾部，不拆开工具调用和对应结果。
        for index in range(last_user - 1, -1, -1):
            if messages[index].get("role") != "user":
                continue
            tokens = model.token_count(messages[index:split]) or 0
            if used + tokens > budget or len(messages) - index > 12:
                break
            split = index
            used += tokens
        if split < 2:
            return messages, False
        archived = messages[:split]
        # 旧会话尚未有逐回合史书时，在首次压缩时补建；不调用摘要模型。
        if not self.chronicle.turn_counter:
            self.seed_history(archived)
        handoff = build_local_handoff(messages, custom_instructions=self.handoff or None)
        self.handoff = format_handoff_for_prompt(handoff)[:6_000]
        self.memory = self.chronicle.get_chronicle_text()[:6_000]
        self.checkpoint = [self._digest(message) for message in archived[-4:]]
        self.compactions += 1
        self._save()
        return messages[split:], True

    def prompt(self):
        return "\n\n".join(text for text in (self.handoff, self.memory) if text)

    def clear(self, messages=None):
        self.handoff = self.memory = ""
        self.checkpoint = [self._digest(message) for message in (messages or [])[-4:]]
        self.last_turn_digest = None
        self.compactions = 0
        self.chronicle = SessionChronicle()
        self.last_input_tokens = self.last_estimated_tokens = 0
        self._save()

    def _save(self):
        if not self.path or not self.persistence_enabled:
            return
        data = {"version": 1, "handoff": self.handoff, "memory": self.memory,
                "checkpoint": self.checkpoint, "compactions": self.compactions,
                "last_turn_digest": self.last_turn_digest,
                "turn_counter": self.chronicle.turn_counter}
        for name in ("recent_entries", "earlier_entries", "oldest_entries"):
            data[name] = [asdict(entry) for entry in getattr(self.chronicle, name)]
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix(self.path.suffix + ".tmp")
            temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            temporary.replace(self.path)
        except OSError as error:
            if self.io:
                self.io.tool_warning(f"保存会话记忆失败：{error}")

    def _load(self):
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if data.get("version") != 1:
                raise ValueError("不支持的记忆版本")
            self.handoff = data["handoff"][:6_000]
            self.memory = data["memory"][:6_000]
            self.checkpoint = data["checkpoint"]
            self.compactions = data["compactions"]
            self.last_turn_digest = data.get("last_turn_digest")
            self.chronicle.turn_counter = data["turn_counter"]
            for name in ("recent_entries", "earlier_entries", "oldest_entries"):
                setattr(self.chronicle, name, [ChronicleEntry(**entry) for entry in data[name]])
            self.chronicle._compact_if_needed()
        except (OSError, UnicodeError, ValueError, TypeError, KeyError, AttributeError) as error:
            self.persistence_enabled = False
            self.handoff = self.memory = ""
            self.checkpoint = []
            self.chronicle = SessionChronicle()
            if self.io:
                self.io.tool_warning(f"无法读取会话记忆，保留历史和原文件：{error}")
