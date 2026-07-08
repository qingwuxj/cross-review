# notification/listener.py
def on_order_paid(event):
    """
    订阅 OrderPaid。
    故意有坑：依然读取 event['status'] 导致 KeyError 崩溃！
    """
    order_id = event["order_id"]
    status = event["status"]
    print(f"Order {order_id} is paid. Status is {status}")
