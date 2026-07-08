def register(bus):
    bus.subscribe("OrderPaid", handle_order_paid)


def handle_order_paid(event):
    return event["amount_cents"]
