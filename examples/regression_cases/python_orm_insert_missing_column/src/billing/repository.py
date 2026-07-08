from sqlalchemy import insert


def create_subscription(user_id):
    return insert("subscriptions").values(user_id=user_id)
