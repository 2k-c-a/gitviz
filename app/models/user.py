from sqlalchemy import BigInteger, String, Text, DateTime, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column
import uuid

# auth 서비스는 비동기(asyncpg)를 사용하므로 app.database 의 비동기 Base 사용
from app.database import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    github_id: Mapped[int] = mapped_column(
        BigInteger,
        unique=True,
        nullable=False,
        index=True,
        comment="GitHub numeric user ID; immutable; UNIQUE",
    )
    github_login: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        comment="GitHub username; can change",
    )
    display_name: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
        comment="GitHub full name",
    )
    avatar_url: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="GitHub profile image URL",
    )
    email: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )
    access_token_encrypted: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="OAuth access token; AES-256 encrypted at rest",
    )
    created_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    last_login_at: Mapped[DateTime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
