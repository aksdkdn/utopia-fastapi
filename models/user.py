import uuid
from datetime import datetime

from sqlalchemy import String, DateTime, Boolean, Numeric, ForeignKey, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from core.database import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    password_hash: Mapped[str | None] = mapped_column(String(255))
    nickname: Mapped[str] = mapped_column(String(50), unique=True, nullable=False, index=True)

    profile_image_key: Mapped[str | None] = mapped_column(String(255))
    phone: Mapped[str | None] = mapped_column(String(50))

    provider: Mapped[str] = mapped_column(String(30), nullable=False, server_default="local")
    provider_id: Mapped[str | None] = mapped_column(String(255))

    role: Mapped[str] = mapped_column(String(20), nullable=False, server_default="user")
    trust_score: Mapped[float] = mapped_column(Numeric, nullable=False, server_default="36.5")

    referred_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )

    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")

    banned_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    hosted_parties: Mapped[list["Party"]] = relationship("Party", back_populates="host")  # noqa
    party_members: Mapped[list["PartyMember"]] = relationship("PartyMember", back_populates="user")  # noqa
    notifications: Mapped[list["Notification"]] = relationship(
        "Notification",
        back_populates="user",
        foreign_keys="Notification.user_id",
    )

    created_notifications: Mapped[list["Notification"]] = relationship(
        "Notification",
        foreign_keys="Notification.created_by",
    )