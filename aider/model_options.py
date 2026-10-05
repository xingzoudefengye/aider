"""Web 管理端和 CLI 共用的模型默认设置。"""

DEFAULT_MODEL_OPTIONS = {
    "context_window": 512000,
    "vision": True,
    "reasoning_effort": "medium",
}


def effective_model_options(options):
    # 逐模型的已保存设置优先，未配置的字段采用产品默认值。
    return {
        name: options[name] if options.get(name) not in (None, "") else default
        for name, default in DEFAULT_MODEL_OPTIONS.items()
    }
