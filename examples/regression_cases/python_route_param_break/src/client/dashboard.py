def load_order(order_id):
    return http_get(f"/orders/{order_id}")
