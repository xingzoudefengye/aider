#!/usr/bin/env python
"""原生供应商离线检查；失败时返回非零退出码。"""

import os
import sys

# 添加 aider 到 Python 路径
sys.path.insert(0, os.path.dirname(__file__))

from aider.providers import get_provider
from aider.core.cache_optimizer import CacheOptimizer, extract_cache_stats_from_usage
from types import SimpleNamespace


def test_provider_creation():
    """验证三种协议延迟初始化，避免离线检查发出网络请求。"""
    for model, protocol in (("claude-sonnet-4-6", "anthropic"),
                            ("gpt-test", "openai-chat"), ("gpt-test", "openai-responses")):
        provider = get_provider(model, api_key="fake-key", protocol=protocol)
        assert provider.protocol == protocol
        assert provider._client is None


def test_cache_usage():
    optimizer = CacheOptimizer()
    # 缓存 token 已包含在总输入中，不能再次加到分母。
    for details in ("prompt_tokens_details", "input_tokens_details"):
        usage = SimpleNamespace(prompt_tokens=2000, completion_tokens=10,
                                **{details: SimpleNamespace(cached_tokens=1000)})
        optimizer.record(**extract_cache_stats_from_usage(usage))
    assert optimizer.get_session_stats()["cache_hit_rate"] == 0.5
    unknown = CacheOptimizer()
    unknown.record(**extract_cache_stats_from_usage({"input_tokens": 2000, "output_tokens": 10}))
    assert "未返回统计" in unknown.format_stats()


if __name__ == "__main__":
    test_provider_creation()
    test_cache_usage()
    print("PASS: 三协议工厂与统一缓存统计")
