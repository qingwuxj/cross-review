import requests


def load_order(order_id):
    return requests.get(f"/orders/{order_id}")
