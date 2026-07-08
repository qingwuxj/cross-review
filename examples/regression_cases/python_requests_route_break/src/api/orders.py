class Router:
    def get(self, path):
        def decorator(func):
            return func
        return decorator


router = Router()


@router.get("/orders/{order_id}")
def get_order(order_id: str):
    return {"id": order_id}
