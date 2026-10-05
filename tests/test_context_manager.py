import io
from types import SimpleNamespace
from unittest.mock import patch

from aider.coders import Coder
from aider.core.context_manager import ContextManager
from aider.io import InputOutput
from aider.models import Model


def test_full_request_threshold_and_reserve():
    model = SimpleNamespace(info={"max_input_tokens": 512_000}, extra_params={},
                            token_count=lambda value: value if isinstance(value, int) else 0)
    manager = ContextManager()
    assert manager.threshold(model) == 384_000
    assert not manager.should_compact(model, 383_999)
    assert manager.should_compact(model, 384_000)
    model.info["max_input_tokens"] = 32_000
    assert 0 < manager.threshold(model) <= 24_000
    model.info["max_input_tokens"] = 1_000_000
    assert manager.threshold(model) == 750_000


def make_coder(tmp_path, monkeypatch, messages=None, restore=False):
    monkeypatch.chdir(tmp_path)
    model = Model("openai/responses/gpt-test", native_config={
        "model": "gpt-test", "protocol": "openai-responses", "api_key": "fake",
        "api_base": "http://127.0.0.1:1", "options": {"context_window": 16_000},
    })
    terminal = InputOutput(pretty=False, output=io.StringIO(), chat_history_file=None)
    terminal.chat_history_file = tmp_path / "33.md"
    return Coder.create(model, io=terminal, done_messages=messages, restore_chat_history=restore,
                        use_git=False, map_tokens=0, auto_commits=False)


def test_cli_compacts_without_model_and_restores_original_history(tmp_path, monkeypatch):
    messages = []
    for index in range(12):
        messages.extend([{"role": "user", "content": f"修复第 {index} 个接口 " * 900},
                         {"role": "assistant", "content": "已检查并修复接口，请继续验证。 " * 900}])
    current = {"role": "user", "content": "接着完成当前请求，保留这个唯一目标"}
    original_history = "".join(f"#### {m['content']}\n\n" if m["role"] == "user"
                               else m["content"] + "\n\n" for m in messages)
    coder = make_coder(tmp_path, monkeypatch, messages)
    coder.io.chat_history_file.write_text(original_history, encoding="utf-8")
    coder.cur_messages = [current]
    with patch.object(coder.main_model, "simple_send_with_retries", side_effect=AssertionError("不应调用摘要模型")), \
            patch.object(coder.summarizer, "summarize", side_effect=AssertionError("不应调用旧摘要")):
        chunks = coder.format_messages()
        assert coder.context_manager.compactions == 1
        assert coder.cur_messages[-1] == current
        assert len(coder.done_messages) < len(messages)
        assert "# 上下文交接" in chunks.system[0]["content"]
        assert "# 会话史书" in chunks.system[0]["content"]
        assert coder.io.chat_history_file.read_text(encoding="utf-8").startswith(original_history)
    restored = make_coder(tmp_path, monkeypatch, restore=True)
    assert len(restored.restored_messages) == len(messages)
    assert len(restored.done_messages) == len(coder.done_messages)
    assert restored.context_manager.prompt() == coder.context_manager.prompt()
    clone = Coder.create(main_model=coder.main_model, from_coder=coder)
    assert clone.context_manager is coder.context_manager


def test_turn_recording_is_local_bounded_and_does_not_change_prompt(tmp_path, monkeypatch):
    coder = make_coder(tmp_path, monkeypatch)
    def answer(message):
        coder.partial_response_content = "已处理，请验证"
        coder.cur_messages.extend([{"role": "user", "content": message},
                                   {"role": "assistant", "content": coder.partial_response_content}])
        yield None
    with patch.object(coder, "send_message", side_effect=answer):
        coder.run_one("处理接口", preproc=False)
        coder.run_one("处理接口", preproc=False)
    assert coder.context_manager.chronicle.turn_counter == 2
    snapshot = coder.context_manager.prompt()
    for index in range(200):
        coder.context_manager.record_turn(f"目标 {index}", "已完成部分修改 api_key=private-test")
    stats = coder.context_manager.chronicle.get_stats()
    assert stats["total_entries"] <= 22 and stats["within_budget"]
    assert stats["recent_entries"] <= 12 and stats["earlier_entries"] <= 6 and stats["oldest_entries"] <= 4
    assert coder.context_manager.prompt() == snapshot
    assert "private-test" not in coder.context_manager.path.read_text(encoding="utf-8")
    other = ContextManager(tmp_path / "other.md", restore=True)
    assert other.chronicle.turn_counter == 0


def test_checkpoint_mismatch_and_clear_preserve_history(tmp_path):
    model = SimpleNamespace(info={"max_input_tokens": 16_000}, extra_params={},
                            token_count=lambda value: len(str(value)))
    messages = [{"role": role, "content": f"{index}:" + "内容 " * 200}
                for index in range(30) for role in ("user", "assistant")]
    manager = ContextManager(tmp_path / "chat.md")
    remaining, changed = manager.compact(model, messages)
    assert changed and len(remaining) < len(messages)
    restored = ContextManager(tmp_path / "chat.md", restore=True)
    assert restored.restore_messages(messages) == remaining
    continuation = [{"role": role, "content": f"新增 {index}:" + "原文 " * 200}
                    for index in range(30, 45) for role in ("user", "assistant")]
    remaining, changed = manager.compact(model, remaining + continuation)
    assert changed
    restored = ContextManager(tmp_path / "chat.md", restore=True)
    assert restored.restore_messages(messages + continuation) == remaining
    changed_history = [{"role": "user", "content": "其他分支的历史"}]
    assert restored.restore_messages(changed_history) == changed_history
    assert not restored.prompt()
    manager.clear(messages + continuation)
    restored = ContextManager(tmp_path / "chat.md", restore=True)
    assert restored.restore_messages(messages + continuation) == []
    assert not restored.prompt() and restored.chronicle.turn_counter == 0


def test_corrupt_state_does_not_replace_original(tmp_path):
    path = tmp_path / "chat.md.context.json"
    path.write_text("invalid-json", encoding="utf-8")
    manager = ContextManager(tmp_path / "chat.md", restore=True)
    manager.record_turn("目标", "已检查")
    assert path.read_text(encoding="utf-8") == "invalid-json"
