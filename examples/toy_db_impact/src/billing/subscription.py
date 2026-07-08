# billing/subscription.py
# db connection reference
def create_subscription(user_id: int):
    """
    故意有坑：未赋值 plan_tier，直接写入数据库将导致 NOT NULL 冲突崩溃！
    """
    sql = f"INSERT INTO subscriptions (user_id) VALUES ({user_id});"
    print(f"Executing: {sql}")
