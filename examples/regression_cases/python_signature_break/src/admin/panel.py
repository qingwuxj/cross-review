from src.billing.client import charge_user


def trigger_billing_override(user_id):
    return charge_user(user_id, 100)
