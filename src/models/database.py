"""Database models and session management."""

import os
from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import JSON, DateTime, ForeignKey, String, select
from sqlalchemy.dialects.postgresql import UUID as PostgreSQLUUID
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    """Base model."""


ID_TYPE = String(36).with_variant(PostgreSQLUUID(as_uuid=False), "postgresql")


class Conversation(Base):
    """Conversation model."""

    __tablename__ = "conversations"

    id: Mapped[str] = mapped_column(ID_TYPE, primary_key=True, default=lambda: str(uuid4()))
    title: Mapped[str | None] = mapped_column(String(255))
    user_id: Mapped[str | None] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(20), default="active")
    meta: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )

    messages: Mapped[list["Message"]] = relationship(
        "Message", back_populates="conversation", cascade="all, delete-orphan"
    )


class Message(Base):
    """Message model."""

    __tablename__ = "messages"

    id: Mapped[str] = mapped_column(ID_TYPE, primary_key=True, default=lambda: str(uuid4()))
    conversation_id: Mapped[str] = mapped_column(
        ID_TYPE, ForeignKey("conversations.id", ondelete="CASCADE")
    )
    role: Mapped[str] = mapped_column(String(20))  # user, assistant, tool, system
    content: Mapped[str] = mapped_column(String)
    tool_calls: Mapped[dict | None] = mapped_column(JSON)
    tool_results: Mapped[dict | None] = mapped_column(JSON)
    meta: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )

    conversation: Mapped["Conversation"] = relationship("Conversation", back_populates="messages")


# Database engine
_engine = None
_async_session_maker = None


def get_database_url(url: str | None = None) -> str:
    """Get database URL from environment."""
    url = url or os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./agent.db")
    # Ensure async driver
    if url.startswith("sqlite:///"):
        url = url.replace("sqlite:///", "sqlite+aiosqlite:///")
    elif url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+asyncpg://")
    return url


def init_database() -> None:
    """Initialize database engine."""
    global _engine, _async_session_maker
    url = get_database_url()
    _engine = create_async_engine(url, echo=False)
    _async_session_maker = async_sessionmaker(_engine, expire_on_commit=False)


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """Get database session."""
    if _async_session_maker is None:
        init_database()
    async with _async_session_maker() as session:
        yield session


async def create_tables() -> None:
    """Create all tables."""
    if _engine is None:
        init_database()
    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def save_message(
    session: AsyncSession,
    conversation_id: str,
    role: str,
    content: str,
    tool_calls: dict | None = None,
    tool_results: dict | None = None,
    meta: dict | None = None,
) -> Message:
    """Save a message to database."""
    message = Message(
        conversation_id=conversation_id,
        role=role,
        content=content,
        tool_calls=tool_calls,
        tool_results=tool_results,
        meta=meta or {},
    )
    session.add(message)
    conversation = await session.get(Conversation, conversation_id)
    if conversation:
        conversation.updated_at = datetime.now(UTC)
    await session.commit()
    return message


async def get_conversation_messages(
    session: AsyncSession, conversation_id: str, limit: int = 100
) -> list[Message]:
    """Get messages for a conversation."""
    result = await session.execute(
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .order_by(Message.created_at.asc())
        .limit(limit)
    )
    return result.scalars().all()


async def close_database() -> None:
    """关闭连接池，避免重启与测试之间复用旧连接。"""
    global _engine, _async_session_maker
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _async_session_maker = None


if __name__ == "__main__":
    import asyncio

    async def initialize():
        init_database()
        try:
            await create_tables()
        finally:
            await close_database()

    asyncio.run(initialize())
