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
- ✅ 三级存储：recent 最多 12 条 / earlier 最多 6 条 / oldest 最多 4 条
- ✅ 每条容量依次为 160 / 100 / 60 字符；越旧越粗略
- ✅ 智能压缩：保留关键信息，丢弃冗余内容
- ✅ 关键词提取和动作总结
- ✅ 持久化存储（JSON）
- ✅ 固定内存占用

**测试**: 17/17 通过

**用法**:
```python
from aider.core import SessionChronicle

chronicle = SessionChronicle(storage_path="session.json")
chronicle.add_turn(user_msg, assistant_msg, tool_calls=tools_used)

# 获取史书文本
text = chronicle.get_chronicle_text()

# 配置 storage_path 后每次 add_turn 自动持久化
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

## CLI 接入

- `aider admin` 中保存的模型在 CLI 启动和 `/model` 选择后使用原生
  OpenAI Chat、OpenAI Responses 或 Anthropic SDK。其他模型保持原有接入方式。
- 自动读取项目 `.ai/project.md`、`decisions.md`、`tasks.md`、`memory.md`，
  作为稳定提示词前缀。`/memory` 查看，`/memory add 内容` 追加，
  `/memory reload` 在文件修改后重新加载。
- 每次回复展示本次和累计缓存命中率，`/cache` 查看会话统计；
  网关未返回缓存字段时显示“未返回统计”，不当作零命中。
- 深色主题默认启用，`--light-mode` 切换浅色。手动颜色配置保留。
- 缓存标记默认启用，`--no-cache-prompts` 可关闭 Anthropic 缓存标记。
- 发送前检查完整请求（系统提示、项目资料、历史及当前输入），默认达到上下文
  容量的 75% 时执行本地交接；小窗口还需满足输出预留及安全余量。
  512,000 上下文的默认触发值为 384,000 tokens。
- 自动交接复用 `local_handoff.py`，保留近期原文和当前请求，不调用摘要模型。
  `/compact` 手动交接，`/compact status` 查看阈值与史书分层统计。
  显式 `--max-chat-history-tokens` 仍可额外限制原文历史预算。
- 每回合复用 `session_chronicle.py` 生成有界的分层记忆；史书每回合保存，
  交接提示词仅在压缩时更新，以保持前缀缓存稳定。
- 状态保存在聊天历史旁的 `.context.json` 文件，恢复时依据历史锚点续接，
  不覆盖原始聊天日志；模型切换保留状态，`/clear`、`/reset` 同时清除活动记忆。
- `.ai/project.md`、`decisions.md`、`tasks.md`、`memory.md` 保留显式维护方式。
  CLI 每回合另写 `.ai/memory.json`：提取用户明确表达的持久约定，保留有来源
  的回合摘要；助手结论标为未核验资料，不覆盖人工决策文件。
- 新会话加载自动约定，每次用户请求按关键词检索同项目其他会话的相关记忆；
  检索资料放在请求尾部，避免改变稳定系统前缀。
  每个新会话使用独立标识，恢复时沿用原标识，共用历史文件也能区分记忆来源。
  `/memory search 关键词` 手动检索，`/memory reload` 更新当前项目记忆快照。
- 自动项目记忆采用 30 天半衰期排名；7 天内最多展示 800 字符、30 天内 320、
  180 天内 160、更早 80。同等相关时，近期更详细、权重更高。
  容量上限 500 条，其中最多 100 条约定；同项目并行会话通过文件锁更新。
- 会话史书同时按时间衰减：超过 7 天降为较早层，超过 30 天降为最早层。
  每条上限分别为 160/100/60 字符，启动、恢复及回合记录时应用分层；
  已注入的交接提示词在下一次交接时更新。
- 检索是本地关键词匹配，不依赖嵌入服务或额外模型请求；历史记录不表示目标已核验。

## License

与 Aider 主项目保持一致。
