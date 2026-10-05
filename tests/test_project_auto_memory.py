import json
import time
from concurrent.futures import ThreadPoolExecutor

from aider.core.session_chronicle import ChronicleEntry, SessionChronicle
from aider.core.context_manager import ContextManager
from aider.project_memory import load_project_memory, remember_project_turn, retrieve_project_memory


def test_extract_user_decisions_and_retrieve_other_sessions(tmp_path):
    directory = tmp_path / ".ai"
    directory.mkdir()
    manual = directory / "decisions.md"
    manual.write_text("人工约定：保留现有接口", encoding="utf-8")
    remember_project_turn(tmp_path, tmp_path / "1.md", "以后统一使用 UTF-8 编码。API key=secret-value",
                          "已修好所有接口 password=private-value")
    data = json.loads((directory / "memory.json").read_text(encoding="utf-8"))
    decisions = [entry for entry in data["entries"] if entry["kind"] == "decision"]
    assert len(decisions) == 1 and "UTF-8" in decisions[0]["text"]
    assert "所有接口" not in decisions[0]["text"]
    assert "secret-value" not in str(data) and "private-value" not in str(data)
    assert "UTF-8" in load_project_memory(tmp_path)
    assert "UTF-8" in retrieve_project_memory(tmp_path, "编码", tmp_path / "2.md")
    assert not retrieve_project_memory(tmp_path, "编码", tmp_path / "1.md")
    assert not retrieve_project_memory(tmp_path, "完全不相干", tmp_path / "2.md")
    assert manual.read_text(encoding="utf-8") == "人工约定：保留现有接口"
    remember_project_turn(tmp_path, tmp_path / "2.md", "默认使用 UTF-8 编码吗？", "可以")
    data = json.loads((directory / "memory.json").read_text(encoding="utf-8"))
    assert sum(entry["kind"] == "decision" for entry in data["entries"]) == 1


def test_old_memories_are_coarser_and_rank_after_recent(tmp_path):
    now = time.time()
    remember_project_turn(tmp_path, "old.md", "cache old " + "detail " * 60 + "OLD_DETAIL", "old answer",
                          now=now - 100 * 86_400)
    remember_project_turn(tmp_path, "new.md", "cache recent " + "detail " * 60 + "RECENT_DETAIL", "new answer", now=now)
    found = retrieve_project_memory(tmp_path, "cache", history_file="query.md", now=now)
    assert found.index("cache recent") < found.index("cache old")
    assert "RECENT_DETAIL" in found and "OLD_DETAIL" not in found


def test_parallel_sessions_do_not_lose_memories(tmp_path):
    def write(index):
        remember_project_turn(tmp_path, f"{index}.md", f"处理接口 {index}", "已检查")
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(write, range(12)))
    data = json.loads((tmp_path / ".ai" / "memory.json").read_text(encoding="utf-8"))
    assert len(data["entries"]) == 12


def test_chronicle_demotes_entries_with_time(tmp_path):
    now = time.time()
    chronicle = SessionChronicle(str(tmp_path / "chronicle.json"))
    chronicle.recent_entries = [ChronicleEntry("近期" * 80, "success", now - 2 * 86_400, 1),
                                ChronicleEntry("较早" * 80, "success", now - 10 * 86_400, 2),
                                ChronicleEntry("最早" * 80, "success", now - 40 * 86_400, 3)]
    chronicle._save()
    restored = SessionChronicle(str(tmp_path / "chronicle.json"))
    assert len(restored.recent_entries) == len(restored.earlier_entries) == len(restored.oldest_entries) == 1
    assert len(restored.earlier_entries[0].summary) <= 100
    assert len(restored.oldest_entries[0].summary) <= 60


def test_corrupt_memory_is_not_overwritten(tmp_path):
    import pytest

    directory = tmp_path / ".ai"
    directory.mkdir()
    path = directory / "memory.json"
    path.write_text("invalid", encoding="utf-8")
    with pytest.raises(ValueError):
        remember_project_turn(tmp_path, "chat.md", "记住这个约定", "已收到")
    assert path.read_text(encoding="utf-8") == "invalid"


def test_shared_history_path_does_not_merge_session_identity(tmp_path):
    history = tmp_path / ".aider.chat.history.md"
    first = ContextManager(history)
    first.record_turn("缓存优化", "已检查")
    remember_project_turn(tmp_path, history, "缓存优化", "已检查", session_id=first.session_id)
    restored = ContextManager(history, restore=True)
    assert restored.session_id == first.session_id
    second = ContextManager(history)
    assert second.session_id != first.session_id
    assert "缓存优化" in retrieve_project_memory(tmp_path, "缓存", history, session_id=second.session_id)
    assert not retrieve_project_memory(tmp_path, "缓存", history, session_id=first.session_id)
