from fastapi import APIRouter

router = APIRouter()


@router.post("/subscriptions")
def create_subscription_route(user_id: str):
    return {"user_id": user_id}
