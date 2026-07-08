from sqlalchemy import insert


def create_subscription(session, user_id: str):
    statement = insert("subscriptions").values(user_id=user_id)
    session.execute(statement)
