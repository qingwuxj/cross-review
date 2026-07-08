import src.billing.client as billing_client


def trigger_billing_override(user_id):
    return billing_client.charge_user(user_id, 100)
