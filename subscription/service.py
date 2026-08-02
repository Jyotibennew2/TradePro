import uuid
from datetime import datetime, timedelta
from typing import Optional

from .models import BillingCycle, PLANS, Subscription


class SubscriptionService:
    """Handles plan upgrades, downgrades, renewals, and feature gating."""

    def __init__(self, db):
        # db: any async-compatible repository (SQLAlchemy session, Supabase client, etc.)
        self.db = db

    # ------------------------------------------------------------------
    # Create / Activate
    # ------------------------------------------------------------------

    async def create_subscription(
        self,
        user_id: str,
        plan_id: str,
        billing_cycle: BillingCycle,
        trial_days: int = 0,
    ) -> Subscription:
        if plan_id not in PLANS:
            raise ValueError(f"Unknown plan: {plan_id}")

        now = datetime.utcnow()
        duration = timedelta(days=365 if billing_cycle == BillingCycle.ANNUAL else 30)
        trial_ends_at = now + timedelta(days=trial_days) if trial_days else None
        status = "trial" if trial_days else "active"

        sub = Subscription(
            subscription_id=str(uuid.uuid4()),
            user_id=user_id,
            plan_id=plan_id,
            billing_cycle=billing_cycle,
            status=status,
            started_at=now,
            expires_at=now + duration,
            trial_ends_at=trial_ends_at,
        )
        await self.db.save(sub)
        return sub

    # ------------------------------------------------------------------
    # Upgrade / Downgrade
    # ------------------------------------------------------------------

    async def change_plan(
        self, subscription_id: str, new_plan_id: str
    ) -> Subscription:
        sub: Subscription = await self.db.get(Subscription, subscription_id)
        if not sub:
            raise LookupError("Subscription not found")
        if new_plan_id not in PLANS:
            raise ValueError(f"Unknown plan: {new_plan_id}")
        sub.plan_id = new_plan_id
        await self.db.save(sub)
        return sub

    # ------------------------------------------------------------------
    # Renewal
    # ------------------------------------------------------------------

    async def renew(self, subscription_id: str) -> Subscription:
        sub: Subscription = await self.db.get(Subscription, subscription_id)
        if not sub:
            raise LookupError("Subscription not found")
        duration = timedelta(
            days=365 if sub.billing_cycle == BillingCycle.ANNUAL else 30
        )
        sub.expires_at = max(sub.expires_at, datetime.utcnow()) + duration
        sub.status = "active"
        await self.db.save(sub)
        return sub

    # ------------------------------------------------------------------
    # Cancel
    # ------------------------------------------------------------------

    async def cancel(self, subscription_id: str) -> Subscription:
        sub: Subscription = await self.db.get(Subscription, subscription_id)
        if not sub:
            raise LookupError("Subscription not found")
        sub.status = "cancelled"
        sub.auto_renew = False
        await self.db.save(sub)
        return sub

    # ------------------------------------------------------------------
    # Feature Gate
    # ------------------------------------------------------------------

    async def check_feature(self, user_id: str, feature: str) -> bool:
        sub: Optional[Subscription] = await self.db.get_by_user(Subscription, user_id)
        if not sub or not sub.is_active:
            return False
        return sub.has_feature(feature)
