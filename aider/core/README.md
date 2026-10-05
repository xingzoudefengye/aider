# Aider Core - ComeCode 移植功能

本模块包含从 ComeCode 项目移植过来的核心功能，已经过完整测试验证。

## 功能模块

### 1. 交接式压缩 (Local Handoff)

**文件**: `local_handoff.py`

零成本的本地上下文压缩方案，完全不依赖摘要模型。

**特性**:
- ✅ 完全本地处理，无需调用 API
- ✅ 固定 6000 字符容量上限
- ✅ 自动提取用户目标、进展、工具调用
- ✅ 敏感信息自动脱敏（API密钥、密码、token）
- ✅ <100ms 响应速度

**测试**: 14/14 通过

**用法**:
```python
from aider.core import build_local_handoff, format_handoff_for_prompt

# 构建交接
handoff = build_local_handoff(messages, custom_instructions)

# 格式化为提示词
prompt = format_handoff_for_prompt(handoff)
```

---

### 2. 分层史书系统 (Session Chronicle)

**文件**: `session_chronicle.py`

固定容量的三级分层历史记录系统。

**特性**:
- ✅ 三级存储：recent (50轮) / earlier (51-150轮) / oldest (151+轮)
- ✅ 智能压缩：保留关键信息，丢弃冗余内容
- ✅ 关键词提取和动作总结
- ✅ 持久化存储（JSON）
- ✅ 固定内存占用

**测试**: 17/17 通过

**用法**:
```python
from aider.core import SessionChronicle

chronicle = SessionChronicle(max_tokens=8000)
chronicle.add_turn(user_msg, assistant_msg, tools_used)

# 获取史书文本
text = chronicle.get_chronicle_text()

# 持久化
chronicle.save("session.json")
```

---

### 3. SQLite 会话存储 (Session Store)

**文件**: `session_store.py`

轻量级的会话和消息持久化方案。

**特性**:
- ✅ SQLite 数据库存储
- ✅ 会话元信息管理
- ✅ 消息历史查询
- ✅ Token 使用统计
- ✅ 自动消息数量限制

**测试**: 11/11 通过

**用法**:
```python
from aider.core import SessionStore

store = SessionStore("aider_sessions.db")

# 创建会话
session_id = store.create_session(
    title="Debug memory leak",
    model="claude-opus-5"
)

# 添加消息
store.add_message(session_id, "user", "帮我修复内存泄漏")
store.add_message(session_id, "assistant", "好的，让我看看...")

# 查询消息
messages = store.get_messages(session_id, limit=50)
```

---

## 测试覆盖

所有功能都有完整的单元测试：

```bash
# 运行所有核心功能测试
pytest tests/test_local_handoff.py -v
pytest tests/test_session_chronicle.py -v
pytest tests/test_session_store.py -v

# 或一次运行全部
pytest tests/test_*.py -v
```

**总计**: 42/42 测试通过 ✅

---

## 设计原则

1. **零成本优先**: 能本地处理的不调用模型
2. **固定容量**: 所有存储都有明确上限
3. **可测试性**: 每个功能都有完整测试覆盖
4. **可追溯性**: 保留关键历史信息

---

## 与 ComeCode 的对应关系

| Aider 模块 | ComeCode 原始文件 |
|-----------|------------------|
| `local_handoff.py` | `local-handoff.ts` |
| `session_chronicle.py` | `session-chronicle.ts` |
| `session_store.py` | `session-store.ts` |

---

## License

与 Aider 主项目保持一致。
