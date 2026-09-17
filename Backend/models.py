# =====================================================
# models.py — Updated with phone + is_verified
# =====================================================

from sqlalchemy import (
    Column, Integer, String,
    DateTime, Boolean, Text, ForeignKey
)
from sqlalchemy.orm import relationship
from database import Base
from datetime import datetime


class User(Base):
    __tablename__ = "users"

    id            = Column(Integer, primary_key=True, index=True)
    name          = Column(String,  nullable=False)
    email         = Column(String,  unique=True, index=True, nullable=False)
    password_hash = Column(String,  nullable=True)
    google_id     = Column(String,  unique=True, nullable=True)
    apple_id      = Column(String,  unique=True, nullable=True)

    # ✅ NEW columns
    phone         = Column(String,  unique=True, nullable=True)
    is_verified   = Column(Boolean, default=False, nullable=False)

    reset_token         = Column(String,   nullable=True)
    reset_token_expires = Column(DateTime, nullable=True)

    created_at    = Column(DateTime, default=datetime.utcnow)

    messages = relationship("ChatMessage", back_populates="user")
    chat_sessions = relationship("ChatSession", back_populates="user", cascade="all, delete-orphan")
    live_sessions = relationship("LiveSession", back_populates="user", cascade="all, delete-orphan")


class ChatSession(Base):
    __tablename__ = "chat_sessions"

    id         = Column(Integer, primary_key=True, index=True)
    user_id    = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    title      = Column(String(200), nullable=False, default="New Chat")
    mode       = Column(String(20), nullable=False, default="chat")
    pinned     = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    user = relationship("User", back_populates="chat_sessions")
    messages = relationship(
        "ChatMessage",
        back_populates="session",
        cascade="all, delete-orphan",
        order_by="ChatMessage.id"
    )


class ChatMessage(Base):
    __tablename__ = "chat_messages"

    id         = Column(Integer,  primary_key=True, index=True)
    user_id    = Column(Integer,  ForeignKey("users.id"), nullable=False)
    session_id = Column(Integer, ForeignKey("chat_sessions.id"), nullable=True, index=True)
    role       = Column(String,   nullable=False)
    content    = Column(Text,     nullable=False)
    pinned     = Column(Boolean,  default=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    user = relationship("User", back_populates="messages")
    session = relationship("ChatSession", back_populates="messages")

# =====================================================
# LIVE AI SESSIONS
# =====================================================

class LiveSession(Base):
    __tablename__ = "live_sessions"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    title = Column(String(200), nullable=False, default="New Live Session")
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    user = relationship("User", back_populates="live_sessions")
    messages = relationship(
        "LiveMessage",
        back_populates="session",
        cascade="all, delete-orphan",
        order_by="LiveMessage.id"
    )


class LiveMessage(Base):
    __tablename__ = "live_messages"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    session_id = Column(Integer, ForeignKey("live_sessions.id"), nullable=False, index=True)
    role = Column(String, nullable=False)
    content = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    user = relationship("User")
    session = relationship("LiveSession", back_populates="messages")

# =====================================================
# BYOK PROVIDERS + TELEMETRY
# =====================================================

class UserProvider(Base):
    __tablename__ = "user_providers"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)

    provider = Column(String(50), nullable=False)
    label = Column(String(160), nullable=False)
    base_url = Column(String(500), nullable=False)
    api_key_encrypted = Column(Text, nullable=False)
    organization = Column(String(200), nullable=True)

    is_active = Column(Boolean, nullable=False, default=True)
    is_valid = Column(Boolean, nullable=False, default=False)
    last_checked_at = Column(DateTime, nullable=True)
    last_latency_ms = Column(Integer, nullable=True)
    last_error = Column(Text, nullable=True)
    models_json = Column(Text, nullable=False, default="[]")

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class ProviderTelemetry(Base):
    __tablename__ = "provider_telemetry"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    provider_id = Column(Integer, ForeignKey("user_providers.id"), nullable=False, index=True)

    provider = Column(String(50), nullable=False)
    model = Column(String(200), nullable=True)
    latency_ms = Column(Integer, nullable=True)
    success = Column(Boolean, nullable=False, default=False)
    status_code = Column(Integer, nullable=True)
    error = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)

