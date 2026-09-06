from fastapi import APIRouter, Depends, HTTPException, Request, status

from api.auth import (
    authenticate_user, create_access_token,
    create_user, delete_user, list_users,
)
from api.deps import get_current_user, require_admin
from api.limiter import limiter
from api.models import CreateUserRequest, LoginRequest, Token, UserInfo
from src.observability.audit import record_admin_action
from src.security import UserContext

router = APIRouter()


def _client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


@router.post("/login", response_model=Token)
@limiter.limit("5/minute")
def login(body: LoginRequest, request: Request):
    user = authenticate_user(body.username, body.password)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                            detail="Incorrect username or password")
    return Token(access_token=create_access_token(user), user_id=user.user_id)


@router.get("/me", response_model=UserInfo)
def me(user: UserContext = Depends(get_current_user)):
    return UserInfo(user_id=user.user_id)


@router.get("/users")
def get_users(_: UserContext = Depends(require_admin)):
    return [{"username": u} for u in list_users()]


@router.post("/users", status_code=201)
def add_user(body: CreateUserRequest, request: Request, admin: UserContext = Depends(require_admin)):
    try:
        ok = create_user(body.username, body.password)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    if not ok:
        raise HTTPException(status_code=409, detail="Username already exists")
    record_admin_action(admin.user_id, "user.create", target=body.username, client_ip=_client_ip(request))
    return {"message": f"User '{body.username}' created"}


@router.delete("/users/{username}")
def remove_user(username: str, request: Request, admin: UserContext = Depends(require_admin)):
    ok = delete_user(username)
    if not ok:
        raise HTTPException(status_code=404, detail="User not found or cannot delete admin")
    record_admin_action(admin.user_id, "user.delete", target=username, client_ip=_client_ip(request))
    return {"message": f"User '{username}' deleted"}
