from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Optional


class PlanType(str, Enum):
    FREE = "free"
    BASIC = "basic"
    PRO = "pro"
    ENTERPRISE = "enterprise"


class BillingCycle(str, Enum):
    MONTHLY = "monthly"
    ANNUAL = "annual"


@dataclass
class Plan:
    plan_id: str
    name: str
    plan_type: PlanType
    price_monthly: float
    price_annual: float
    max_watchlists: int
    max_alerts: int
    backtest_enabled: bool
    signal_engine_enabled: bool
    live_portfolio_enabled: bool
    api_calls_per_day: int


PLANS: dict[str, Plan] = {
    "free": Plan(
        plan_id="free",
        name="Free",
        plan_type=PlanType.FREE,
        price_monthly=0,
        price_annual=0,
        max_watchlists=1,
        max_alerts=5,
        backtest_enabled=False,
        signal_engine_enabled=False,
        live_portfolio_enabled=False,
        api_calls_per_day=100,
    ),
    "basic": Plan(
        plan_id="basic",
        name="Basic",
        plan_type=PlanType.BASIC,
        price_monthly=499,
        price_annual=4999,
        max_watchlists=5,
        max_alerts=25,
        backtest_enabled=True,
        signal_engine_enabled=False,
        live_portfolio_enabled=True,
        api_calls_per_day=1000,
    ),
    "pro": Plan(
        plan_id="pro",
        name="Pro",
        plan_type=PlanType.PRO,
        price_monthly=999,
        price_annual=9999,
        max_watchlists=20,
        max_alerts=100,
        backtest_enabled=True,
        signal_engine_enabled=True,
        live_portfolio_enabled=True,
        api_calls_per_day=10000,
    ),
    "enterprise": Plan(
        plan_id="enterprise",
        name="Enterprise",
        plan_type=PlanType.ENTERPRISE,
        price_monthly=4999,
        price_annual=49999,
        max_watchlists=-1,  # unlimited
        max_alerts=-1,
        backtest_enabled=True,
        signal_engine_enabled=True,
        live_portfolio_enabled=True,
        api_calls_per_day=-1,
    ),
}


@dataclass
class Subscription:
    subscription_id: str
    user_id: str
    plan_id: str
    billing_cycle: BillingCycle
    status: str  # active | cancelled | expired | trial
    started_at: datetime
    expires_at: datetime
    auto_renew: bool = True
    trial_ends_at: Optional[datetime] = None

    @property
    def is_active(self) -> bool:
        return self.status == "active" and self.expires_at > datetime.utcnow()

    @property
    def plan(self) -> Plan:
        return PLANS[self.plan_id]

    def has_feature(self, feature: str) -> bool:
        return getattr(self.plan, feature, False)
