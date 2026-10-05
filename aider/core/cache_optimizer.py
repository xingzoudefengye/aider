"""
Prompt Cache 优化器

参考 ComeCode 的多供应商缓存优化:
- 统一的缓存统计接口
- 缓存命中率监控
- 自动优化建议
"""

from dataclasses import dataclass
from typing import Any, Dict, List, Optional
import time


@dataclass
class CacheStats:
    """缓存统计数据"""

    # 基础 token 统计
    prompt_tokens: int = 0
    completion_tokens: int = 0

    # 缓存统计 (多供应商兼容)
    cache_creation_tokens: int = 0  # Anthropic: cache_creation_input_tokens
    cache_read_tokens: int = 0  # Anthropic: cache_read_input_tokens
    cache_hit_tokens: int = 0  # OpenAI: prompt_cache_hit_tokens / DeepSeek
    cache_write_tokens: int = 0  # OpenAI: cache_creation_input_tokens

    # 成本
    total_cost: float = 0.0

    # 时间戳
    timestamp: float = 0.0
    cache_reported: bool = True

    @property
    def total_tokens(self) -> int:
        """总 token 数"""
        return self.prompt_tokens + self.completion_tokens

    @property
    def cache_tokens(self) -> int:
        """所有缓存相关 token (统一)"""
        return max(
            self.cache_creation_tokens + self.cache_read_tokens,
            self.cache_write_tokens + self.cache_hit_tokens,
        )

    @property
    def cache_hit_rate(self) -> float:
        """缓存命中率"""
        total_input = self.prompt_tokens
        cached = max(self.cache_read_tokens, self.cache_hit_tokens)

        if total_input == 0:
            return 0.0

        return cached / total_input


class CacheOptimizer:
    """缓存优化器"""

    def __init__(self):
        self.history: List[CacheStats] = []
        self.session_start = time.time()

    def record(
        self,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        cache_creation_tokens: int = 0,
        cache_read_tokens: int = 0,
        cache_hit_tokens: int = 0,
        cache_write_tokens: int = 0,
        total_cost: float = 0.0,
        cache_reported: bool = True,
    ):
        """
        记录一次请求的缓存统计

        支持多供应商格式:
        - Anthropic: cache_creation_input_tokens, cache_read_input_tokens
        - OpenAI: prompt_cache_hit_tokens
        - DeepSeek: prompt_cache_hit_tokens
        - Gemini: cachedContentTokenCount
        """
        stats = CacheStats(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cache_creation_tokens=cache_creation_tokens,
            cache_read_tokens=cache_read_tokens,
            cache_hit_tokens=cache_hit_tokens,
            cache_write_tokens=cache_write_tokens,
            total_cost=total_cost,
            timestamp=time.time(),
            cache_reported=cache_reported,
        )

        self.history.append(stats)

    def get_session_stats(self) -> Dict[str, Any]:
        """获取会话级统计"""
        if not self.history:
            return {
                "total_requests": 0,
                "total_tokens": 0,
                "total_cost": 0.0,
                "cache_hit_rate": None,
                "cache_savings": 0.0,
                "session_duration": 0,
            }

        total_prompt_tokens = sum(s.prompt_tokens for s in self.history)
        total_completion_tokens = sum(s.completion_tokens for s in self.history)
        total_cache_read = sum(max(s.cache_read_tokens, s.cache_hit_tokens) for s in self.history)
        total_cache_creation = sum(
            max(s.cache_creation_tokens, s.cache_write_tokens) for s in self.history
        )
        total_cost = sum(s.total_cost for s in self.history)

        # 计算缓存命中率
        known = [s for s in self.history if s.cache_reported]
        total_input = sum(s.prompt_tokens for s in known)
        cache_hit_rate = (total_cache_read / total_input if total_input else 0.0) if known else None

        # 估算缓存节省 (假设缓存读取成本是正常的 10%)
        # 各供应商缓存单价不同，没有实际价格时不虚构节省金额。
        cache_savings = 0.0

        return {
            "total_requests": len(self.history),
            "total_tokens": total_prompt_tokens + total_completion_tokens,
            "prompt_tokens": total_prompt_tokens,
            "completion_tokens": total_completion_tokens,
            "cache_creation_tokens": total_cache_creation,
            "cache_read_tokens": total_cache_read,
            "total_cost": total_cost,
            "cache_hit_rate": cache_hit_rate,
            "cache_savings": cache_savings,
            "session_duration": time.time() - self.session_start,
        }

    def get_recent_hit_rate(self, last_n: int = 5) -> float:
        """获取最近 N 次请求的缓存命中率"""
        if not self.history:
            return 0.0

        recent = [s for s in self.history[-last_n:] if s.cache_reported]
        total_input = sum(s.prompt_tokens for s in recent)
        total_cached = sum(max(s.cache_read_tokens, s.cache_hit_tokens) for s in recent)

        if total_input == 0:
            return 0.0

        return total_cached / total_input

    def should_optimize(self) -> bool:
        """判断是否需要优化建议"""
        if len(self.history) < 3:
            return False
        if not any(s.cache_reported for s in self.history[-5:]):
            return False

        # 如果最近 5 次请求的命中率低于 50%，建议优化
        recent_hit_rate = self.get_recent_hit_rate(last_n=5)
        return recent_hit_rate < 0.5

    def get_optimization_suggestions(self) -> List[str]:
        """获取优化建议"""
        suggestions = []
        stats = self.get_session_stats()

        if stats["total_requests"] < 3:
            return []

        hit_rate = stats["cache_hit_rate"]
        if hit_rate is None:
            return []
        recent_hit_rate = self.get_recent_hit_rate()

        # 建议 1: 缓存命中率低
        if hit_rate < 0.3:
            suggestions.append(
                f"缓存命中率较低 ({hit_rate:.1%})。考虑:\n"
                "  - 保持系统提示词、项目记忆和文件前缀稳定\n"
                "  - 确认供应商支持并返回缓存统计"
            )

        # 建议 2: 命中率波动大
        if abs(hit_rate - recent_hit_rate) > 0.3:
            suggestions.append(
                f"缓存命中率波动较大 (会话: {hit_rate:.1%}, 最近: {recent_hit_rate:.1%})。\n"
                "  这可能是因为系统提示词频繁变化。"
            )

        # 建议 3: 有潜在节省空间
        if hit_rate > 0.5 and stats["cache_savings"] > 0.01:
            savings_pct = (stats["cache_savings"] / stats["total_cost"]) * 100
            suggestions.append(
                f"缓存已节省约 ${stats['cache_savings']:.4f} ({savings_pct:.1f}%)。\n"
                f"  继续保持当前配置以获得更多节省。"
            )

        # 建议 4: 长会话优化
        if stats["session_duration"] > 1800:  # 30 分钟
            suggestions.append(
                f"会话已持续 {stats['session_duration'] / 60:.1f} 分钟。\n"
                "  考虑使用固定容量史书来保持缓存稳定。"
            )

        return suggestions

    def format_stats(self, verbose: bool = False) -> str:
        """格式化统计信息"""
        stats = self.get_session_stats()

        if stats["total_requests"] == 0:
            return "无缓存统计数据"

        output = [
            f"📊 缓存统计 ({stats['total_requests']} 次请求):",
            f"  总 tokens: {stats['total_tokens']:,}",
            f"  提示词: {stats['prompt_tokens']:,}",
            f"  完成: {stats['completion_tokens']:,}",
        ]

        output.append(f"  缓存读取: {stats['cache_read_tokens']:,}")
        output.append(f"  缓存创建: {stats['cache_creation_tokens']:,}")
        rate = stats['cache_hit_rate']
        output.append(f"  命中率: {rate:.1%}" if rate is not None else "  命中率: 未返回统计")

        output.append(f"  总成本: ${stats['total_cost']:.4f}")

        if stats["cache_savings"] > 0:
            output.append(f"  缓存节省: ${stats['cache_savings']:.4f}")

        if verbose:
            output.append(f"  会话时长: {stats['session_duration'] / 60:.1f} 分钟")

            # 添加优化建议
            suggestions = self.get_optimization_suggestions()
            if suggestions:
                output.append("\n💡 优化建议:")
                for suggestion in suggestions:
                    output.append(f"  {suggestion}")

        return "\n".join(output)


def extract_cache_stats_from_usage(usage: Any) -> Dict[str, int]:
    """
    从不同供应商的 usage 对象中提取缓存统计

    支持:
    - Anthropic: cache_creation_input_tokens, cache_read_input_tokens
    - OpenAI: prompt_cache_hit_tokens
    - DeepSeek: prompt_cache_hit_tokens
    - Gemini: cachedContentTokenCount
    """
    def field(value, name, default=None):
        return value.get(name, default) if isinstance(value, dict) else getattr(value, name, default)

    candidates = [field(field(usage, "prompt_tokens_details"), "cached_tokens"),
                  field(field(usage, "input_tokens_details"), "cached_tokens"),
                  field(usage, "prompt_cache_hit_tokens"), field(usage, "cache_read_input_tokens"),
                  field(usage, "cachedContentTokenCount")]
    cached = max((v for v in candidates if v is not None), default=0)
    created = field(usage, "cache_creation_input_tokens", 0) or 0
    prompt = field(usage, "prompt_tokens")
    if prompt is None:
        prompt = field(usage, "input_tokens", 0) or 0
        # Anthropic 原始 input_tokens 不含缓存；SDK 适配后的 prompt_tokens 已是总输入。
        if field(usage, "cache_read_input_tokens") is not None:
            prompt += cached + created
    return {"prompt_tokens": prompt,
            "completion_tokens": field(usage, "completion_tokens", field(usage, "output_tokens", 0)) or 0,
            "cache_creation_tokens": created, "cache_read_tokens": cached,
            "cache_hit_tokens": 0, "cache_write_tokens": 0,
            "cache_reported": any(v is not None for v in candidates)}
