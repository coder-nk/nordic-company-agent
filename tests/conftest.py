import pytest


@pytest.fixture(autouse=True)
def _fast_registry_retries(monkeypatch):
    from nordic_company_agent.sources import registry
    monkeypatch.setattr(registry, "BATCH_RETRY_DELAYS", (0.0, 0.0, 0.0))
