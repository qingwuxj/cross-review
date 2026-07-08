class Router:
    def get(self, path):
        def wrap(fn):
            return fn
        return wrap


router = Router()


@router.get("/orders/{order_id}")
def get_order(order_id: str):
    return {"id": order_id}
