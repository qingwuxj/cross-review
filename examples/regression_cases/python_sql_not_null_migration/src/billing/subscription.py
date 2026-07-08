def create_subscription(db, user_id):
    db.execute("INSERT INTO subscriptions (user_id) VALUES (?)", [user_id])
