"""Planner: tiers may only spend what no unfinished higher-priority tier still needs."""

from pathlib import Path

from nordic_company_agent.context import Planner
from nordic_company_agent.http import BudgetedClient


class _FakeClient(BudgetedClient):
    def __init__(self, limit: int, used: int = 0):
        super().__init__(max_requests=limit)
        self._used = used


def test_lower_tier_cannot_eat_higher_reservation():
    c = _FakeClient(100)
    p = Planner(c, {"registry": 10, "accounts": 60, "website_primary": 20, "extras": 10})
    assert p.allow("extras", 10)          # 100 - (10+60+20) = 10 left for extras
    assert not p.allow("extras", 11)
    c._used = 95                          # someone overspent: only 5 left
    assert p.allow("registry", 5)
    assert not p.allow("accounts", 1)     # registry (unfinished) still needs its 10
    p.done("registry")
    assert p.allow("accounts", 5) and not p.allow("accounts", 6)
    assert not p.allow("extras", 1)       # accounts + website still unfinished


def test_finished_tiers_release_budget():
    c = _FakeClient(50)
    p = Planner(c, {"accounts": 40, "discovery": 10})
    assert not p.allow("discovery", 11)
    p.charge("accounts", 5)
    c._used = 5
    p.done("accounts")
    assert p.allow("discovery", 45)
