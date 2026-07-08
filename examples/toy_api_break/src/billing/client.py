# 1. API Contract Break Example
# billing/client.py
def charge_user(user_id: int, amount: float):
    """
    Simulates charging a user.
    Note: Parameter name is user_id.
    """
    print(f"Charging user {user_id} with ${amount}")
