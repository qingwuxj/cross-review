def publish_paid(bus, order):
    bus.publish("OrderPaid", {"order_id": order.id, "total_cents": order.total})
