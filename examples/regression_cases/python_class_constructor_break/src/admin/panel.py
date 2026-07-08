from src.billing.models import BillingPlan


def create_default_plan(user_id):
    return BillingPlan(user_id)
