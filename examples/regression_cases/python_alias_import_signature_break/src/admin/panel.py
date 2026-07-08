from src.billing.client import charge_user as bill_user


def trigger_billing_override(user_id):
    return bill_user(user_id, 100)
