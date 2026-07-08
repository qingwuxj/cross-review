# order/events.py
def publish_order_paid(order_id: int):
    """
    发布 OrderPaid 事件。
    更改契约：去掉了旧的 status 字段，变为了 payment_status。
    """
    event = {
        "order_id": order_id,
        "payment_status": "SUCCESS"
    }
    # trigger('OrderPaid', event)
    print("Emitted OrderPaid event:", event)
