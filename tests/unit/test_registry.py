from aistudio._rpc import registry


def test_m1_proved_these_rpcs_work_over_plain_http():
    for spec in (registry.LIST_MODELS, registry.LIST_PROMPTS, registry.COUNT_TOKENS):
        assert spec.transport is registry.Transport.HTTP
        assert spec.idempotent is True
    assert registry.LIST_MODELS.method == "ListModels"
    assert registry.LIST_PROMPTS.method == "ListPrompts"
    assert registry.COUNT_TOKENS.method == "CountTokens"
