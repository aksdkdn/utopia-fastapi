import uuid
from pydantic import BaseModel


class UserResponse(BaseModel):
    id: uuid.UUID
    email: str
    nickname: str

    model_config = {"from_attributes": True}


# 마이페이지 - 프로필
class MyPageProfileResponse(BaseModel):
    nickname: str

    model_config = {"from_attributes": True}