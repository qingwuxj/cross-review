# admin/panel.py
from src.billing.client import charge_user

def trigger_billing_override():
    """
    故意有坑：传入了 userId=123 而不是 user_id=123！
    """
    charge_user(userId=123, amount=99.0)
