from fastapi import (
    FastAPI,
    Depends,
    HTTPException,
    Request
)

import edge_tts
import uuid

from fastapi.responses import FileResponse, StreamingResponse, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from authlib.integrations.starlette_client import OAuth
from starlette.middleware.sessions import SessionMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import RedirectResponse
from starlette.background import BackgroundTask

from pydantic import BaseModel, EmailStr

from google import genai
from dotenv import load_dotenv
from pathlib import Path
import os
import tempfile
from contextlib import asynccontextmanager

# Load .env before importing database.py so DATABASE_URL is available.
if not os.getenv("VERCEL"):
    load_dotenv(Path(__file__).with_name(".env"))

from sqlalchemy.orm import Session
from sqlalchemy import inspect, text, Column, Integer, String, DateTime, Float, Boolean, Text
from sqlalchemy.exc import IntegrityError

if __package__:
    from .database import SessionLocal, engine, Base, get_db
    from .models import User, ChatMessage, ChatSession, LiveSession, LiveMessage
    from .auth_store import AuthChallenge, store_challenge, read_challenge, consume_challenge
else:
    from database import SessionLocal, engine, Base, get_db
    from models import User, ChatMessage, ChatSession, LiveSession, LiveMessage
    from auth_store import AuthChallenge, store_challenge, read_challenge, consume_challenge

from pwdlib import PasswordHash

import base64
from typing import Optional, Any
import os
import threading
import webbrowser
import socket
import uvicorn
import secrets
import time
import random
import qrcode
import io
import json
import urllib.request
import urllib.error
from cryptography.fernet import Fernet, InvalidToken

from datetime import datetime, timedelta

from fastapi_mail import FastMail, MessageSchema, ConnectionConfig

import jwt


# =====================================================
# LOAD ENVIRONMENT VARIABLES
# =====================================================

# .env was already loaded above before importing database.py.


# =====================================================
# EMAIL CONFIGURATION
# =====================================================

mail_conf = ConnectionConfig(
    MAIL_USERNAME=os.getenv("MAIL_USERNAME"),
    MAIL_PASSWORD=os.getenv("MAIL_PASSWORD"),
    MAIL_FROM=os.getenv("MAIL_FROM"),
    MAIL_PORT=587,
    MAIL_SERVER="smtp.gmail.com",
    MAIL_STARTTLS=True,
    MAIL_SSL_TLS=False,
    USE_CREDENTIALS=True
)


# =====================================================
# GEMINI API KEY
# =====================================================

API_KEY = os.getenv("GEMINI_API_KEY")

if not API_KEY:
    raise ValueError("GEMINI_API_KEY not found in .env")


# =====================================================
# PASSWORD HASHING
# =====================================================

password_hasher = PasswordHash.recommended()


# =====================================================
# JARVIS DEFAULTS / MODEL CONFIGURATION
# =====================================================

DEFAULT_SYSTEM_PROMPT = (
    "You are MAXIMUS, a helpful general-purpose AI assistant. "
    "Answer the user’s actual question directly and naturally. "
    "Be clear, useful, and concise without unnecessary technical jargon. "
    "Do not invent classifications, anomaly reports, target payloads, iterations, "
    "execution pipeline warnings, or engineering diagnostics unless the user explicitly asks for them. "
    "If the user asks a general knowledge question, simply answer it. "
    "If you are unsure, say so briefly and give the best supported answer."
)
DEFAULT_VOICE = os.getenv("JARVIS_DEFAULT_VOICE", "en-US-AriaNeural")
DEFAULT_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.6-flash")
MODEL_MAP = {
    "flash": DEFAULT_MODEL,
}


# =====================================================
# GEMINI CLIENT
# =====================================================

client = genai.Client(
    api_key=API_KEY
)


# =====================================================
# FASTAPI
# =====================================================

@asynccontextmanager
async def lifespan(application: FastAPI):
    # Database DDL belongs to startup, not module import / Vercel inspection.
    initialize_database()
    yield
    engine.dispose()


app = FastAPI(lifespan=lifespan)


# =====================================================
# SETTINGS + DEVICE MODELS
# =====================================================

class UserSettings(Base):
    __tablename__ = "user_settings"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, unique=True, index=True, nullable=False)
    model = Column(String, nullable=False, default="flash")
    temperature = Column(Float, nullable=False, default=0.25)
    context_limit = Column(Integer, nullable=False, default=128)
    system_prompt = Column(
        Text,
        nullable=False,
        default=DEFAULT_SYSTEM_PROMPT
    )
    voice = Column(String, nullable=False, default="en-US-AriaNeural")
    input_language = Column(String, nullable=False, default="en-IN")
    output_language = Column(String, nullable=False, default="en-IN")
    voice_gender = Column(String, nullable=False, default="female")
    duplex = Column(Boolean, nullable=False, default=True)
    auto_mute = Column(Boolean, nullable=False, default=True)
    zdr = Column(Boolean, nullable=False, default=False)
    vision_sampling = Column(String, nullable=False, default="1 Frame / Sec (Standard Duplex)")
    vision_fidelity = Column(String, nullable=False, default="Native 4K (High Precision)")
    auto_ocr = Column(Boolean, nullable=False, default=True)
    spatial_grounding = Column(Boolean, nullable=False, default=True)
    network_access = Column(Boolean, nullable=False, default=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class Provider(Base):
    __tablename__ = "providers"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, index=True, nullable=False)
    provider = Column(String, nullable=False, default="custom")
    label = Column(String, nullable=False)
    base_url = Column(String, nullable=False)
    api_key = Column(Text, nullable=True)  # encrypted with Fernet
    organization = Column(String, nullable=True)
    api_format = Column(String, nullable=False, default="chat_completions")
    model_id = Column(String, nullable=True)
    models_json = Column(Text, nullable=True)
    is_active = Column(Boolean, nullable=False, default=True)
    is_valid = Column(Boolean, nullable=False, default=False)
    last_error = Column(Text, nullable=True)
    last_latency_ms = Column(Float, nullable=True)
    last_checked_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class GenerationMetric(Base):
    __tablename__ = "generation_metrics"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, index=True, nullable=False)
    provider_id = Column(Integer, index=True, nullable=False)
    latency_ms = Column(Float, nullable=False)
    success = Column(Boolean, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)


class ProviderTestRequest(BaseModel):
    provider: str = "custom"
    label: str
    base_url: str
    api_key: Optional[str] = None
    organization: Optional[str] = None
    api_format: str = "chat_completions"
    model_id: Optional[str] = None


class ProviderCreateRequest(ProviderTestRequest):
    is_active: Optional[bool] = True


class ProviderUpdateRequest(BaseModel):
    label: Optional[str] = None
    base_url: Optional[str] = None
    api_key: Optional[str] = None
    organization: Optional[str] = None
    api_format: Optional[str] = None
    is_active: Optional[bool] = None


class ModelRequest(BaseModel):
    provider_id: int
    name: Optional[str] = None
    model_name: Optional[str] = None
    model_id: Optional[str] = None
    replace_model: Optional[str] = None
    context_window: Optional[int] = None


class AuthDevice(Base):
    __tablename__ = "auth_devices"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, index=True, nullable=False)
    token = Column(String, unique=True, index=True, nullable=False)
    name = Column(String, nullable=False, default="Unknown Device")
    user_agent = Column(Text, nullable=True)
    ip_address = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    last_seen_at = Column(DateTime, default=datetime.utcnow)
    revoked = Column(Boolean, nullable=False, default=False)


class AccountProfile(Base):
    __tablename__ = "account_profiles"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, unique=True, index=True, nullable=False)
    role = Column(String(160), nullable=False, default="")
    bio = Column(Text, nullable=False, default="")
    timezone = Column(String(100), nullable=False, default="Asia/Kolkata")
    locale = Column(String(40), nullable=False, default="en-IN")
    avatar_data = Column(Text, nullable=True)
    auditory_feedback = Column(Boolean, nullable=False, default=True)
    weekly_digest = Column(Boolean, nullable=False, default=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


# =====================================================
# TOKEN STORES
# =====================================================

# One-time codes and QR transfers are stored in the shared database so that
# verification can land on a different Vercel instance than creation.


# =====================================================
# MIDDLEWARE
# =====================================================

# DeviceSessionMiddleware must run INSIDE SessionMiddleware so that
# request.session is available when device validation executes.
class DeviceSessionMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        user_id = request.session.get("user_id")
        device_id = request.session.get("device_id")

        if user_id and device_id:
            db = SessionLocal()
            try:
                device = (
                    db.query(AuthDevice)
                    .filter(
                        AuthDevice.id == device_id,
                        AuthDevice.user_id == user_id
                    )
                    .first()
                )

                if not device or device.revoked:
                    request.session.clear()
            finally:
                db.close()

        return await call_next(request)


# IMPORTANT: add DeviceSessionMiddleware FIRST and SessionMiddleware SECOND.
# Starlette makes the last-added middleware the outermost middleware.
app.add_middleware(DeviceSessionMiddleware)
app.add_middleware(
    SessionMiddleware,
    secret_key=os.getenv("SESSION_SECRET"),
    https_only=bool(os.getenv("VERCEL")),
    same_site="lax",
)


# =====================================================
# CREATE DATABASE TABLES
# =====================================================

def initialize_database():
    if engine.dialect.name == "postgresql":
        # Serialize cold-start migrations across serverless instances.
        with engine.begin() as lock_connection:
            lock_connection.execute(text("SELECT pg_advisory_xact_lock(748312967)"))
            _initialize_schema()
    else:
        _initialize_schema()


def _initialize_schema():
    Base.metadata.create_all(bind=engine)

    # Account profile storage is part of the profile/settings feature.
    Base.metadata.create_all(bind=engine, tables=[AccountProfile.__table__])

    # Provider table migration for databases created before BYOK support.
    provider_columns = {column["name"] for column in inspect(engine).get_columns("providers")}
    with engine.begin() as connection:
        provider_migrations = {
            "organization": "ALTER TABLE providers ADD COLUMN organization VARCHAR",
            "api_format": "ALTER TABLE providers ADD COLUMN api_format VARCHAR DEFAULT 'chat_completions' NOT NULL",
            "model_id": "ALTER TABLE providers ADD COLUMN model_id VARCHAR",
            "models_json": "ALTER TABLE providers ADD COLUMN models_json TEXT",
            "is_active": "ALTER TABLE providers ADD COLUMN is_active BOOLEAN DEFAULT TRUE NOT NULL",
            "is_valid": "ALTER TABLE providers ADD COLUMN is_valid BOOLEAN DEFAULT FALSE NOT NULL",
            "last_error": "ALTER TABLE providers ADD COLUMN last_error TEXT",
            "last_latency_ms": "ALTER TABLE providers ADD COLUMN last_latency_ms FLOAT",
            "last_checked_at": "ALTER TABLE providers ADD COLUMN last_checked_at TIMESTAMP",
            "created_at": "ALTER TABLE providers ADD COLUMN created_at TIMESTAMP",
            "updated_at": "ALTER TABLE providers ADD COLUMN updated_at TIMESTAMP",
        }
        for column_name, statement in provider_migrations.items():
            if column_name not in provider_columns:
                connection.execute(text(statement))



    # =====================================================
    # VOICE PREFERENCES MIGRATION
    # =====================================================
    voice_columns = {column["name"] for column in inspect(engine).get_columns("user_settings")}
    with engine.begin() as connection:
        if "input_language" not in voice_columns:
            connection.execute(text("ALTER TABLE user_settings ADD COLUMN input_language VARCHAR DEFAULT 'en-IN' NOT NULL"))
            print("✅ Added input_language to user_settings")
        if "output_language" not in voice_columns:
            connection.execute(text("ALTER TABLE user_settings ADD COLUMN output_language VARCHAR DEFAULT 'en-IN' NOT NULL"))
            print("✅ Added output_language to user_settings")
        if "voice_gender" not in voice_columns:
            connection.execute(text("ALTER TABLE user_settings ADD COLUMN voice_gender VARCHAR DEFAULT 'female' NOT NULL"))
            print("✅ Added voice_gender to user_settings")
        if "network_access" not in voice_columns:
            connection.execute(text("ALTER TABLE user_settings ADD COLUMN network_access BOOLEAN DEFAULT FALSE NOT NULL"))
            print("✅ Added network_access to user_settings")


    # =====================================================
    # DATABASE MIGRATION
    # =====================================================

    inspector = inspect(engine)


    # -------------------------
    # CHAT MESSAGES — session_id
    # -------------------------

    chat_columns_for_migration = [
        column["name"]
        for column in inspector.get_columns("chat_messages")
    ]

    if "session_id" not in chat_columns_for_migration:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "ALTER TABLE chat_messages ADD COLUMN session_id INTEGER"
                )
            )

        print("✅ Added session_id to chat_messages table")


    inspector = inspect(engine)


    # -------------------------
    # CHAT SESSIONS — mode
    # -------------------------

    chat_session_columns_for_migration = [
        column["name"]
        for column in inspector.get_columns("chat_sessions")
    ]

    if "mode" not in chat_session_columns_for_migration:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "ALTER TABLE chat_sessions "
                    "ADD COLUMN mode VARCHAR(20) DEFAULT 'chat' NOT NULL"
                )
            )

        print("✅ Added mode to chat_sessions table")


    inspector = inspect(engine)


    # -------------------------
    # CONVERSATION SESSIONS — pinned
    # -------------------------

    chat_session_columns_for_migration = {
        column["name"] for column in inspect(engine).get_columns("chat_sessions")
    }

    if "pinned" not in chat_session_columns_for_migration:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "ALTER TABLE chat_sessions ADD COLUMN pinned BOOLEAN DEFAULT FALSE NOT NULL"
                )
            )
        print("✅ Added pinned to chat_sessions table")

    live_session_columns_for_migration = {
        column["name"] for column in inspect(engine).get_columns("live_sessions")
    }

    if "pinned" not in live_session_columns_for_migration:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "ALTER TABLE live_sessions ADD COLUMN pinned BOOLEAN DEFAULT FALSE NOT NULL"
                )
            )
        print("✅ Added pinned to live_sessions table")


    # -------------------------
    # USERS TABLE
    # -------------------------

    user_columns = [
        column["name"]
        for column in inspector.get_columns("users")
    ]


    if "google_id" not in user_columns:
        with engine.begin() as connection:
            connection.execute(
                text("ALTER TABLE users ADD COLUMN google_id VARCHAR")
            )

        print("✅ Added google_id to users table")


    if "apple_id" not in user_columns:
        with engine.begin() as connection:
            connection.execute(
                text("ALTER TABLE users ADD COLUMN apple_id VARCHAR")
            )

        print("✅ Added apple_id to users table")


    if "reset_token" not in user_columns:
        with engine.begin() as connection:
            connection.execute(
                text("ALTER TABLE users ADD COLUMN reset_token VARCHAR")
            )

        print("✅ Added reset_token to users table")


    if "reset_token_expires" not in user_columns:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "ALTER TABLE users ADD COLUMN reset_token_expires TIMESTAMP"
                )
            )

        print("✅ Added reset_token_expires to users table")


    if "phone" not in user_columns:
        with engine.begin() as connection:
            connection.execute(
                text("ALTER TABLE users ADD COLUMN phone VARCHAR")
            )

        print("✅ Added phone to users table")


    if "is_verified" not in user_columns:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "ALTER TABLE users ADD COLUMN is_verified BOOLEAN DEFAULT FALSE"
                )
            )

        print("✅ Added is_verified to users table")


    # -------------------------
    # CHAT MESSAGES TABLE
    # -------------------------

    chat_columns = [
        column["name"]
        for column in inspector.get_columns("chat_messages")
    ]

    if "pinned" not in chat_columns:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "ALTER TABLE chat_messages ADD COLUMN pinned BOOLEAN DEFAULT FALSE"
                )
            )

        print("✅ Added pinned to chat_messages table")


# =====================================================
# CORS
# =====================================================

app.add_middleware(
    CORSMiddleware,
    allow_origins=[origin.strip() for origin in os.getenv("ALLOWED_ORIGINS", "").split(",") if origin.strip()],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# =====================================================
# FRONTEND PATHS
# Project/
# ├── backend/app.py
# └── Frontend/
#     ├── pc/
#     ├── mobile/
#     └── static/
# =====================================================

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

FRONTEND_DIR = os.path.abspath(
    os.path.join(BASE_DIR, "..", "Frontend")
)
PC_DIR = os.path.join(FRONTEND_DIR, "pc")
MOBILE_DIR = os.path.join(FRONTEND_DIR, "mobile")
STATIC_DIR = os.path.join(FRONTEND_DIR, "static")


def _frontend_page(directory: str, filename: str) -> FileResponse:
    # Explicit mobile routes must use their dedicated mobile templates.
    # Fall back only when that particular template has not been created yet.
    path = os.path.join(directory, filename)
    if directory == MOBILE_DIR and not os.path.isfile(path):
        path = os.path.join(PC_DIR, filename)
    if not os.path.isfile(path):
        raise HTTPException(status_code=404, detail=f"Frontend page not found: {filename}")
    return FileResponse(path)


# Backward compatibility for older PC links.
@app.get("/static/apipro.html", include_in_schema=False)
def legacy_static_apipro_page():
    return _frontend_page(PC_DIR, "apipro.html")


@app.get("/static/profile-settings.html", include_in_schema=False)
def legacy_static_profile_settings_page():
    return _frontend_page(PC_DIR, "profile-settings.html")


# =====================================================
# SHARED STATIC FILES
# =====================================================

app.mount(
    "/static",
    StaticFiles(directory=STATIC_DIR),
    name="static"
)


# Canonical Live AI output languages. The frontend sends only these codes.
LIVE_OUTPUT_LANGUAGES = {
    "en-IN": "English (India)",
    "ru-RU": "Russian",
    "fr-FR": "French",
    "ja-JP": "Japanese",
    "hi-IN": "Hindi",
    "te-IN": "Telugu",
    "ta-IN": "Tamil",
    "kn-IN": "Kannada",
    "ml-IN": "Malayalam",
}

# =====================================================
# REQUEST MODELS
# =====================================================

class Prompt(BaseModel):
    text: str
    model: Optional[str] = None


class NewChatRequest(BaseModel):
    title: str = "New Conversation"
    mode: str = "chat"


class NewLiveSessionRequest(BaseModel):
    title: str = "New Live Session"


class ImageRequest(BaseModel):
    image: str
    mime_type: str
    text: Optional[str] = (
        "What is in this image? Describe it in detail."
    )
    output_language: Optional[str] = None
    model: Optional[str] = None
    live_session_id: Optional[int] = None


class LiveAskRequest(BaseModel):
    text: str
    output_language: Optional[str] = None
    # Display/transcript language and spoken-audio language are intentionally separate.
    # Example: show English text while MAXIMUS speaks Telugu.
    speech_language: Optional[str] = None


class SignupRequest(BaseModel):
    name: str
    email: str
    password: str


class LoginRequest(BaseModel):
    email: str
    password: str
    remember: bool = False


# ---- Phone + Email OTP Models ----

class PhoneOTPRequest(BaseModel):
    phone: str
    email: str
    name: str


class VerifyPhoneOTPRequest(BaseModel):
    phone: str
    otp: str


class EmailOTPRequest(BaseModel):
    email: str
    name: str


class SignupCompleteRequest(BaseModel):
    name: str
    email: str
    phone: str
    password: str
    otp: str


class LoginOTPRequest(BaseModel):
    phone: str


class VerifyLoginOTPRequest(BaseModel):
    phone: str
    otp: str


class SettingsUpdateRequest(BaseModel):
    model: Optional[str] = None
    temperature: Optional[float] = None
    context_limit: Optional[int] = None
    system_prompt: Optional[str] = None
    voice: Optional[str] = None
    input_language: Optional[str] = None
    output_language: Optional[str] = None
    voice_gender: Optional[str] = None
    duplex: Optional[bool] = None
    auto_mute: Optional[bool] = None
    zdr: Optional[bool] = None
    vision_sampling: Optional[str] = None
    vision_fidelity: Optional[str] = None
    auto_ocr: Optional[bool] = None
    spatial_grounding: Optional[bool] = None
    network_access: Optional[bool] = None


class ProfileUpdateRequest(BaseModel):
    name: Optional[str] = None
    role: Optional[str] = None
    bio: Optional[str] = None
    timezone: Optional[str] = None
    locale: Optional[str] = None
    auditory_feedback: Optional[bool] = None
    weekly_digest: Optional[bool] = None
    avatar_data: Optional[str] = None


class PasswordUpdateRequest(BaseModel):
    current_password: str
    new_password: str


class EmailChangeRequest(BaseModel):
    new_email: EmailStr


class EmailChangeVerifyRequest(BaseModel):
    new_email: EmailStr
    otp: str


class APIKeyVerifyRequest(BaseModel):
    api_key: str


# =====================================================
# OTP HELPERS
# =====================================================

def generate_otp() -> str:
    """Generate a random 6-digit OTP."""
    return str(secrets.randbelow(900000) + 100000)


def store_otp(
    key: str,
    otp: str,
    data: Optional[dict] = None
):
    """Store OTP with 10-minute expiry."""

    store_challenge("otp", key, data or {}, 600, verification=otp)


def verify_otp(
    key: str,
    otp: str
):
    """Verify OTP."""

    return consume_challenge("otp", key, verification=otp)


async def send_otp_email(
    to_email: str,
    name: str,
    otp: str
):
    """Send OTP code via email."""

    message = MessageSchema(
        subject="JARVIS AI — Your verification code",
        recipients=[to_email],
        body=f"""Hello {name},

Your JARVIS AI verification code is:

    {otp}

This code expires in 10 minutes.

If you did not request this, please ignore this email.

— JARVIS AI
""",
        subtype="plain"
    )

    fm = FastMail(mail_conf)

    await fm.send_message(message)


# =====================================================
# SETTINGS / DEVICE HELPERS
# =====================================================

def _get_settings(db: Session, user_id: int) -> UserSettings:
    settings = (
        db.query(UserSettings)
        .filter(UserSettings.user_id == user_id)
        .first()
    )

    legacy_system_prompt = (
        "Act as a high-bandwidth engineering research companion. "
        "Maintain absolute conciseness. Forfeit introductory pleasantries. "
        "Highlight deterministic anomalies first."
    )

    if settings is None:
        new_settings = UserSettings(
            user_id=user_id,
            model="flash",
            temperature=0.25,
            context_limit=128,
            system_prompt=DEFAULT_SYSTEM_PROMPT,
            voice=DEFAULT_VOICE,
            input_language="en-IN",
            output_language="en-IN",
            voice_gender="female",
            duplex=True,
            auto_mute=True,
            zdr=False,
            vision_sampling="1 Frame / Sec (Standard Duplex)",
            vision_fidelity="Native 4K (High Precision)",
            auto_ocr=True,
            spatial_grounding=True,
            network_access=False,
        )

        # Multiple page requests can call _get_settings() at the same time
        # (for example /api/settings and /api/network/status). Both requests
        # may initially see no row and then race to INSERT the same user_id.
        # Use a SAVEPOINT so a losing request can safely recover the row
        # created by the winning request instead of returning a 500.
        try:
            with db.begin_nested():
                db.add(new_settings)
                db.flush()
            db.commit()
            db.refresh(new_settings)
            settings = new_settings
        except IntegrityError:
            db.rollback()
            settings = (
                db.query(UserSettings)
                .filter(UserSettings.user_id == user_id)
                .first()
            )
            if settings is None:
                raise
    elif settings.system_prompt.strip() == legacy_system_prompt:
        settings.system_prompt = DEFAULT_SYSTEM_PROMPT
        settings.updated_at = datetime.utcnow()
        db.commit()
        db.refresh(settings)

    return settings


def _create_device_session(request: Request, db: Session, user_id: int) -> str:
    old_device_id = request.session.get("device_id")
    if old_device_id:
        old = db.query(AuthDevice).filter(AuthDevice.id == old_device_id, AuthDevice.user_id == user_id).first()
        if old and not old.revoked:
            old.last_seen_at = datetime.utcnow()
            db.commit()
            return old.token

    token = secrets.token_urlsafe(32)
    device = AuthDevice(
        user_id=user_id,
        token=token,
        name=_device_name(request),
        user_agent=request.headers.get("user-agent"),
        ip_address=request.client.host if request.client else None,
        last_seen_at=datetime.utcnow(),
    )
    db.add(device)
    db.commit()
    db.refresh(device)
    request.session["device_id"] = device.id
    return token


def _device_name(request: Request) -> str:
    ua = (request.headers.get("user-agent") or "").lower()
    if "iphone" in ua:
        return "iPhone • JARVIS Mobile"
    if "android" in ua:
        return "Android • JARVIS Mobile"
    if "macintosh" in ua:
        return "Mac • Browser"
    if "windows" in ua:
        return "Windows • Browser"
    if "linux" in ua:
        return "Linux • Browser"
    return "Browser Device"


def _is_mobile_client(request: Request) -> bool:
    """Best-effort mobile browser detection for device-specific page redirects."""
    ua = (request.headers.get("user-agent") or "").lower()
    mobile_markers = (
        "android",
        "iphone",
        "ipad",
        "ipod",
        "mobile",
        "windows phone",
    )
    return any(marker in ua for marker in mobile_markers)


MOBILE_PAGE_PATHS = frozenset({
    "/", "/home", "/login", "/signup", "/settings", "/history",
    "/speakwithai", "/overview", "/profile-settings", "/api-pro",
    "/qr-login", "/qr-confirm", "/forgot-password", "/reset-password",
})


@app.middleware("http")
async def mobile_page_routing(request: Request, call_next):
    # Only page GETs: never redirect API requests, form POSTs, or OAuth callbacks.
    if request.method in {"GET", "HEAD"} and request.url.path in MOBILE_PAGE_PATHS:
        if _is_mobile_client(request):
            path = "/m/" if request.url.path == "/" else "/m" + request.url.path
            if request.url.query:
                path += "?" + request.url.query
            return RedirectResponse(path, status_code=307, headers={
                "Vary": "User-Agent", "Cache-Control": "private, no-store",
            })
        response = await call_next(request)
        response.headers["Vary"] = ", ".join(filter(None, [response.headers.get("Vary"), "User-Agent"]))
        response.headers["Cache-Control"] = "private, no-store"
        return response
    return await call_next(request)


def _get_current_user_id(request: Request) -> int:
    user_id = request.session.get("user_id")
    if not user_id:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return int(user_id)


def _delete_unused_chat_sessions(db: Session, user_id: int, keep_id: Optional[int] = None) -> int:
    """Delete chat sessions that never received a user/assistant message."""
    deleted = 0
    sessions = db.query(ChatSession).filter(ChatSession.user_id == user_id).all()
    for session in sessions:
        if keep_id is not None and int(session.id) == int(keep_id):
            continue
        if not session.messages:
            db.delete(session)
            deleted += 1
    if deleted:
        db.commit()
    return deleted


def _delete_unused_live_sessions(db: Session, user_id: int, keep_id: Optional[int] = None) -> int:
    """Delete Live AI sessions that never received a user/assistant message."""
    deleted = 0
    sessions = db.query(LiveSession).filter(LiveSession.user_id == user_id).all()
    for session in sessions:
        if keep_id is not None and int(session.id) == int(keep_id):
            continue
        if not session.messages:
            db.delete(session)
            deleted += 1
    if deleted:
        db.commit()
    return deleted


def _start_authenticated_conversation_session(request: Request, db: Session, user_id: int) -> ChatSession:
    """Start exactly one fresh chat for a newly authenticated browser session.

    Live AI is intentionally created lazily on the first visit to /speakwithai.
    Empty abandoned sessions are cleaned up so they never accumulate in history.
    """
    request.session.pop("chat_id", None)
    request.session.pop("live_session_id", None)
    _delete_unused_chat_sessions(db, user_id)
    _delete_unused_live_sessions(db, user_id)

    chat = ChatSession(user_id=user_id, title="New Chat", mode="chat")
    db.add(chat)
    db.commit()
    db.refresh(chat)
    request.session["chat_id"] = chat.id
    return chat


def _provider_cipher() -> Fernet:
    raw = os.getenv("MAXIMUS_PROVIDER_ENCRYPTION_KEY") or os.getenv("SESSION_SECRET")
    if not raw:
        raise RuntimeError("MAXIMUS_PROVIDER_ENCRYPTION_KEY or SESSION_SECRET is required for provider credentials.")
    # Accept a Fernet key directly, otherwise derive a stable Fernet key from the configured secret.
    try:
        return Fernet(raw.encode() if isinstance(raw, str) else raw)
    except Exception:
        import hashlib
        derived = base64.urlsafe_b64encode(hashlib.sha256(raw.encode()).digest())
        return Fernet(derived)


def _encrypt_provider_key(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    return _provider_cipher().encrypt(value.encode()).decode()


def _decrypt_provider_key(value: Optional[str]) -> str:
    if not value:
        return ""
    try:
        return _provider_cipher().decrypt(value.encode()).decode()
    except (InvalidToken, ValueError):
        # Compatibility with any pre-BYOK local rows that may contain plaintext.
        return value


def _normalise_provider_url(base_url: str, provider: str = "custom") -> str:
    url = (base_url or "").strip().rstrip("/")
    if not url:
        raise ValueError("Base URL is required.")
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    if provider.lower() == "ollama" and url.endswith("/v1"):
        url = url[:-3].rstrip("/")
    return url


def _is_local_ollama(base_url: str, provider: str = "custom") -> bool:
    low = (base_url or "").lower()
    return provider.lower() == "ollama" or "localhost:11434" in low or "127.0.0.1:11434" in low


def _provider_request(url: str, *, method: str = "GET", api_key: str = "", body: Optional[dict] = None, organization: str = "", timeout: float = 12.0) -> tuple[int, dict, float]:
    headers = {"Accept": "application/json", "Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    if organization:
        headers["OpenAI-Organization"] = organization
    payload = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(url, data=payload, headers=headers, method=method)
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8", errors="replace")
            data = json.loads(raw) if raw else {}
            return response.status, data, round((time.perf_counter() - started) * 1000, 1)
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            data = json.loads(raw) if raw else {}
        except Exception:
            data = {"error": {"message": raw[:500]}}
        return exc.code, data, round((time.perf_counter() - started) * 1000, 1)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        # Convert connection/read timeouts into a normal provider error response
        # so MAXIMUS returns a useful 502 instead of an unhandled server exception.
        message = str(getattr(exc, "reason", exc)) or "Provider request timed out."
        return 504, {"error": {"message": message[:500]}}, round((time.perf_counter() - started) * 1000, 1)


def _provider_error(data: Any, status: int) -> str:
    if isinstance(data, dict):
        err = data.get("error")
        if isinstance(err, dict) and err.get("message"):
            return str(err["message"])[:500]
        if isinstance(err, str):
            return err[:500]
        if data.get("message"):
            return str(data["message"])[:500]
    return f"Provider returned HTTP {status}."


def _provider_models(provider: Provider) -> list[str]:
    key = _decrypt_provider_key(provider.api_key)
    if _is_local_ollama(provider.base_url, provider.provider):
        url = _normalise_provider_url(provider.base_url, provider.provider) + "/api/tags"
        status, data, _ = _provider_request(url, api_key="", timeout=8)
        if status >= 400:
            raise RuntimeError(_provider_error(data, status))
        return [str(m.get("name")) for m in data.get("models", []) if isinstance(m, dict) and m.get("name")]

    base = _normalise_provider_url(provider.base_url, provider.provider)
    if base.endswith("/v1"):
        url = base + "/models"
    else:
        url = base + "/v1/models"
    status, data, _ = _provider_request(url, api_key=key, organization=provider.organization or "", timeout=12)
    if status >= 400:
        raise RuntimeError(_provider_error(data, status))
    return [str(m.get("id")) for m in data.get("data", []) if isinstance(m, dict) and m.get("id")]


def _test_provider_config(data: ProviderTestRequest) -> dict:
    """Verify provider credentials and, when supplied, verify model visibility for that credential.

    The model check is deliberately non-generative: it uses the provider's model
    catalogue, so testing a key does not spend inference quota/tokens.
    """
    base = _normalise_provider_url(data.base_url, data.provider)
    key = data.api_key or ""
    requested_model = (data.model_id or "").strip()

    if _is_local_ollama(base, data.provider):
        status, payload, latency = _provider_request(base + "/api/tags", timeout=8)
        if status >= 400:
            return {"valid": False, "message": _provider_error(payload, status), "latency_ms": latency, "models": [], "model_available": False}
        models = [str(m.get("name")) for m in payload.get("models", []) if isinstance(m, dict) and m.get("name")]
        if requested_model and requested_model not in models:
            return {
                "valid": False,
                "message": f'Model "{requested_model}" is not installed/available in this Ollama instance.',
                "latency_ms": latency, "models": models, "model_available": False, "provider": "ollama"
            }
        return {"valid": True, "message": "Ollama is reachable and the requested model is available." if requested_model else "Ollama is reachable.", "latency_ms": latency, "models": models, "model_available": bool(requested_model and requested_model in models) if requested_model else None, "provider": "ollama"}

    if not key:
        return {"valid": False, "message": "API key is required for this provider.", "latency_ms": None, "models": [], "model_available": False}
    probe = base + "/models" if base.endswith("/v1") else base + "/v1/models"
    status, payload, latency = _provider_request(probe, api_key=key, organization=data.organization or "", timeout=12)
    if status >= 400:
        return {"valid": False, "message": _provider_error(payload, status), "latency_ms": latency, "models": [], "model_available": False, "provider": data.provider}
    models = [str(m.get("id")) for m in payload.get("data", []) if isinstance(m, dict) and m.get("id")]
    model_available = (requested_model in models) if requested_model else None
    if requested_model and not model_available:
        return {
            "valid": False,
            "message": f'Model "{requested_model}" is not available to this API key.',
            "latency_ms": latency, "models": models, "model_available": False, "provider": data.provider
        }
    return {
        "valid": True,
        "message": "Provider authentication succeeded and the requested model is available." if requested_model else "Provider authentication succeeded.",
        "latency_ms": latency,
        "models": models,
        "model_available": model_available,
        "provider": data.provider
    }


def _provider_to_dict(provider: Provider) -> dict:
    key = _decrypt_provider_key(provider.api_key)
    masked = None
    if key:
        masked = (key[:4] + "••••" + key[-4:]) if len(key) > 8 else "••••••••"
    try:
        stored_models = json.loads(provider.models_json or "[]")
        if not isinstance(stored_models, list):
            stored_models = []
    except Exception:
        stored_models = []
    models = [str(m) for m in stored_models if m]
    if provider.model_id and provider.model_id not in models:
        models.insert(0, provider.model_id)
    return {
        "id": provider.id, "provider": provider.provider, "label": provider.label,
        "base_url": provider.base_url, "organization": provider.organization or "",
        "api_format": provider.api_format, "model_id": provider.model_id, "models": models,
        "is_active": bool(provider.is_active), "is_valid": bool(provider.is_valid),
        "masked_key": masked, "last_error": provider.last_error,
        "last_latency_ms": provider.last_latency_ms,
        "last_checked_at": provider.last_checked_at.isoformat() if provider.last_checked_at else None,
    }


def _model_name(settings: UserSettings) -> str:
    return MODEL_MAP.get(settings.model, DEFAULT_MODEL)


def _generation_config(settings: UserSettings):
    return genai.types.GenerateContentConfig(
        temperature=float(max(0.0, min(1.0, settings.temperature)))
    )


def _provider_chat_impl(
    provider: Provider,
    model_id: str,
    prompt_text: str,
    settings: UserSettings,
    provider_messages: Optional[list[dict]] = None,
) -> str:
    """Run one text request through a saved BYOK provider.

    Ollama uses its native /api/chat endpoint. Other providers that expose an
    OpenAI-compatible chat API use /v1/chat/completions.

    For local Ollama, ``provider_messages`` lets MAXIMUS send real chat turns
    instead of wrapping the whole conversation inside one giant user prompt.
    This substantially reduces prompt-processing overhead on CPU laptops.
    """
    key = _decrypt_provider_key(provider.api_key)
    system_prompt = (settings.system_prompt or DEFAULT_SYSTEM_PROMPT).strip()
    messages = provider_messages or [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": prompt_text},
    ]
    body = {
        "model": model_id,
        "messages": messages,
        "stream": False,
        "temperature": float(max(0.0, min(1.0, settings.temperature))),
    }

    base = _normalise_provider_url(provider.base_url, provider.provider)
    is_ollama = _is_local_ollama(base, provider.provider)
    if is_ollama:
        url = base + "/api/chat"
        # Keep the local model warm and cap the context used by MAXIMUS. The
        # latter is especially important on CPU laptops where prompt-eval time
        # can dominate an otherwise fast local generation.
        body["keep_alive"] = "5m"
        body["options"] = {
            "num_ctx": 4096,
            "temperature": float(max(0.0, min(1.0, settings.temperature))),
        }
    else:
        url = base + "/chat/completions" if base.endswith("/v1") else base + "/v1/chat/completions"

    # This is only the HTTP read deadline, not an artificial generation delay.
    request_timeout = 45 if os.getenv("VERCEL") else (300 if is_ollama else 120)
    status, data, latency = _provider_request(
        url, method="POST", api_key=key, organization=provider.organization or "",
        body=body, timeout=request_timeout
    )
    provider.last_latency_ms = latency
    provider.last_checked_at = datetime.utcnow()
    if status >= 400:
        provider.last_error = _provider_error(data, status)
        raise HTTPException(status_code=502, detail=f"{provider.label}: {provider.last_error}")

    reply = ""
    if is_ollama:
        reply = str(((data.get("message") or {}).get("content") or "")).strip()
    else:
        choices = data.get("choices") or []
        if choices and isinstance(choices[0], dict):
            message = choices[0].get("message") or {}
            reply = str(message.get("content") or "").strip()

    if not reply:
        provider.last_error = "Provider returned an empty response."
        raise HTTPException(status_code=502, detail=f"{provider.label}: empty response.")

    provider.last_error = None
    return reply


def _provider_chat(provider, model_id, prompt_text, settings, provider_messages=None):
    started = time.perf_counter()
    success = False
    try:
        reply = _provider_chat_impl(provider, model_id, prompt_text, settings, provider_messages)
        success = True
        return reply
    finally:
        # Persist failures too, independently of the chat transaction rollback.
        with SessionLocal() as metrics_db:
            metrics_db.add(GenerationMetric(
                user_id=provider.user_id, provider_id=provider.id,
                latency_ms=(time.perf_counter() - started) * 1000,
                success=success,
            ))
            metrics_db.commit()


def _build_chat_prompt(
    db: Session,
    chat_id: int,
    settings: UserSettings,
    current_text: str,
    *,
    max_messages: int = 200,
    max_chars: Optional[int] = None,
    include_system_directive: bool = True,
) -> str:
    # context_limit is the application's history budget in thousands of tokens,
    # not a claim about the model's maximum context window.
    default_budget = max(8_000, min(256_000, int(settings.context_limit) * 1000 * 4))
    budget_chars = max_chars if max_chars is not None else default_budget
    budget_chars = max(4_000, min(256_000, int(budget_chars)))

    messages = (
        db.query(ChatMessage)
        .filter(ChatMessage.session_id == chat_id)
        .order_by(ChatMessage.id.desc())
        .limit(max(1, int(max_messages)))
        .all()
    )
    messages.reverse()

    history_parts = []
    used = 0
    for msg in messages:
        block = f"{msg.role.upper()}: {msg.content}\n"
        if used + len(block) > budget_chars:
            continue
        history_parts.append(block)
        used += len(block)

    system_part = f"SYSTEM DIRECTIVE:\n{settings.system_prompt.strip()}\n\n" if include_system_directive else ""
    return (
        system_part
        + "CONVERSATION HISTORY:\n"
        + ("".join(history_parts) if history_parts else "No previous conversation.")
        + f"\nUSER: {current_text}\nASSISTANT:"
    )


# =====================================================
# GOOGLE AUTHENTICATION
# =====================================================

oauth = OAuth()

oauth.register(
    name="google",
    client_id=os.getenv("GOOGLE_CLIENT_ID"),
    client_secret=os.getenv("GOOGLE_CLIENT_SECRET"),
    # Use Google's explicit OAuth/OIDC endpoints instead of fetching
    # /.well-known/openid-configuration on every login attempt.
    # This avoids Authlib failing at the metadata-discovery step when
    # the local machine cannot reach Google's discovery URL reliably.
    authorize_url="https://accounts.google.com/o/oauth2/v2/auth",
    access_token_url="https://oauth2.googleapis.com/token",
    userinfo_endpoint="https://openidconnect.googleapis.com/v1/userinfo",
    jwks_uri="https://www.googleapis.com/oauth2/v3/certs",
    client_kwargs={
        "scope": "openid email profile"
    }
)


@app.get("/auth/google")
async def google_login(request: Request):

    redirect_uri = os.getenv("GOOGLE_REDIRECT_URI") or (_public_base_url(request) + "/auth/google/callback")

    return await oauth.google.authorize_redirect(
        request,
        redirect_uri
    )


@app.get("/auth/google/callback")
async def google_callback(
    request: Request,
    db: Session = Depends(get_db)
):

    from authlib.integrations.base_client.errors import OAuthError
    try:
        token = await oauth.google.authorize_access_token(request)
    except OAuthError:
        # Expired state, cancelled consent, and cross-domain session loss must
        # not become an unhandled 500. Never bypass OAuth state verification.
        raise HTTPException(status_code=400, detail="Google sign-in expired or could not be verified. Open the main MAXIMUS website and start sign-in again.")

    # Authlib may not place userinfo in the token response when we use
    # explicit endpoints, so fetch it from Google's OIDC userinfo endpoint.
    try:
        user_info = token.get("userinfo")
        if not user_info:
            user_info = await oauth.google.userinfo(token=token)
    except Exception as e:
        print("GOOGLE USERINFO ERROR:", e)
        raise HTTPException(
            status_code=400,
            detail="Could not get Google account information"
        )

    if not user_info:
        raise HTTPException(
            status_code=400,
            detail="Could not get Google account information"
        )

    google_id = user_info["sub"]

    email = user_info["email"].lower()

    name = user_info.get(
        "name",
        "Google User"
    )

    user = (
        db.query(User)
        .filter(User.google_id == google_id)
        .first()
    )

    if not user:

        user = (
            db.query(User)
            .filter(User.email == email)
            .first()
        )

    if user:

        user.google_id = google_id

        db.commit()

        db.refresh(user)

    else:

        user = User(
            name=name,
            email=email,
            google_id=google_id,
            password_hash=None,
            is_verified=True
        )

        db.add(user)

        db.commit()

        db.refresh(user)

    request.session["user_id"] = user.id
    request.session["user_name"] = user.name
    request.session["user_email"] = user.email
    _create_device_session(request, db, user.id)
    _start_authenticated_conversation_session(request, db, user.id)

    return RedirectResponse(url="/m/home" if _is_mobile_client(request) else "/home", status_code=303)


# =====================================================
# USER PROFILE
# =====================================================

@app.get("/profile-settings")
def profile_settings_page():
    return _frontend_page(PC_DIR, "profile-settings.html")


def _get_account_profile(db: Session, user_id: int) -> AccountProfile:
    profile = db.query(AccountProfile).filter(AccountProfile.user_id == user_id).first()
    if profile is None:
        profile = AccountProfile(user_id=user_id)
        db.add(profile)
        db.commit()
        db.refresh(profile)
    return profile


def _profile_payload(db: Session, user: User) -> dict:
    profile = _get_account_profile(db, user.id)
    settings = _get_settings(db, user.id)
    return {
        "id": user.id,
        "name": user.name or "",
        "email": user.email or "",
        "phone": getattr(user, "phone", None),
        "icon_url": profile.avatar_data,
        "is_verified": bool(getattr(user, "is_verified", False)),
        "has_password": bool(getattr(user, "password_hash", None)),
        "role": profile.role or "",
        "bio": profile.bio or "",
        "timezone": profile.timezone or "Asia/Kolkata",
        "locale": profile.locale or "en-IN",
        "auditory_feedback": bool(profile.auditory_feedback),
        "weekly_digest": bool(profile.weekly_digest),
        "settings": {
            "model": settings.model,
            "temperature": settings.temperature,
            "context_limit": settings.context_limit,
            "system_prompt": settings.system_prompt,
            "voice": settings.voice,
            "duplex": settings.duplex,
            "auto_mute": settings.auto_mute,
            "zdr": settings.zdr,
            "vision_sampling": settings.vision_sampling,
            "vision_fidelity": settings.vision_fidelity,
            "auto_ocr": settings.auto_ocr,
            "spatial_grounding": settings.spatial_grounding,
        }
    }


@app.get("/api/user/profile")
def get_user_profile(request: Request, db: Session = Depends(get_db)):
    user_id = _get_current_user_id(request)
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found.")
    return _profile_payload(db, user)


@app.patch("/api/user/profile")
def update_user_profile(data: ProfileUpdateRequest, request: Request, db: Session = Depends(get_db)):
    user_id = _get_current_user_id(request)
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found.")

    profile = _get_account_profile(db, user_id)

    if data.name is not None:
        name = data.name.strip()
        if not name:
            raise HTTPException(status_code=400, detail="Name cannot be empty.")
        if len(name) > 120:
            raise HTTPException(status_code=400, detail="Name is too long.")
        user.name = name
        request.session["user_name"] = name

    if data.role is not None:
        profile.role = data.role.strip()[:160]
    if data.bio is not None:
        if len(data.bio) > 240:
            raise HTTPException(status_code=400, detail="Bio must be 240 characters or less.")
        profile.bio = data.bio.strip()
    if data.timezone is not None:
        profile.timezone = data.timezone.strip()[:100] or "Asia/Kolkata"
    if data.locale is not None:
        profile.locale = data.locale.strip()[:40] or "en-IN"
    if data.auditory_feedback is not None:
        profile.auditory_feedback = data.auditory_feedback
    if data.weekly_digest is not None:
        profile.weekly_digest = data.weekly_digest
    if data.avatar_data is not None:
        if not data.avatar_data.startswith("data:image/"):
            raise HTTPException(status_code=400, detail="Invalid avatar image.")
        if len(data.avatar_data) > 3_000_000:
            raise HTTPException(status_code=400, detail="Avatar image is too large.")
        profile.avatar_data = data.avatar_data

    profile.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(user)
    return {**_profile_payload(db, user), "message": "Profile updated successfully."}


@app.delete("/api/user/profile/avatar")
def delete_profile_avatar(request: Request, db: Session = Depends(get_db)):
    user_id = _get_current_user_id(request)
    profile = _get_account_profile(db, user_id)
    profile.avatar_data = None
    profile.updated_at = datetime.utcnow()
    db.commit()
    return {"message": "Profile photo removed."}


@app.post("/api/user/password")
def update_password(data: PasswordUpdateRequest, request: Request, db: Session = Depends(get_db)):
    user_id = _get_current_user_id(request)
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found.")
    if not user.password_hash:
        raise HTTPException(status_code=400, detail="This social-login account does not have a password yet.")
    try:
        valid = password_hasher.verify(data.current_password, user.password_hash)
    except Exception:
        valid = False
    if not valid:
        raise HTTPException(status_code=400, detail="Current password is incorrect.")
    if len(data.new_password) < 8:
        raise HTTPException(status_code=400, detail="New password must be at least 8 characters.")
    user.password_hash = password_hasher.hash(data.new_password)
    db.commit()
    return {"message": "Password updated successfully."}


@app.post("/api/user/password/set")
def set_password(data: PasswordUpdateRequest, request: Request, db: Session = Depends(get_db)):
    """Set the first MAXIMUS password for an account authenticated through Google/social login."""
    user_id = _get_current_user_id(request)
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found.")
    if user.password_hash:
        raise HTTPException(status_code=400, detail="A password already exists. Use Update Password instead.")
    if len(data.new_password) < 8:
        raise HTTPException(status_code=400, detail="New password must be at least 8 characters.")
    user.password_hash = password_hasher.hash(data.new_password)
    db.commit()
    return {"message": "MAXIMUS password created successfully."}


@app.post("/api/user/email/request")
async def request_email_change(data: EmailChangeRequest, request: Request, db: Session = Depends(get_db)):
    user_id = _get_current_user_id(request)
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found.")
    new_email = str(data.new_email).strip().lower()
    if new_email == (user.email or "").lower():
        raise HTTPException(status_code=400, detail="That is already your current email address.")
    if db.query(User).filter(User.email == new_email).first():
        raise HTTPException(status_code=400, detail="That email address is already registered.")
    otp = generate_otp()
    store_otp(f"email_change:{user_id}:{new_email}", otp)
    print(f"📧 EMAIL CHANGE OTP for {new_email}: {otp}")
    try:
        await send_otp_email(new_email, user.name or "MAXIMUS user", otp)
    except Exception as e:
        print(f"[EMAIL ERROR] {e}")
    return {"message": "Verification code sent to the new email."}


@app.post("/api/user/email/verify")
def verify_email_change(data: EmailChangeVerifyRequest, request: Request, db: Session = Depends(get_db)):
    user_id = _get_current_user_id(request)
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found.")
    new_email = str(data.new_email).strip().lower()
    if db.query(User).filter(User.email == new_email, User.id != user_id).first():
        raise HTTPException(status_code=400, detail="That email address is already registered.")
    if verify_otp(f"email_change:{user_id}:{new_email}", data.otp) is None:
        raise HTTPException(status_code=400, detail="Invalid or expired verification code.")
    user.email = new_email
    user.is_verified = True
    request.session["user_email"] = new_email
    db.commit()
    return {"message": "Email replaced successfully.", "email": new_email}


@app.post("/api/devices/revoke-others")
def revoke_other_devices(request: Request, db: Session = Depends(get_db)):
    user_id = _get_current_user_id(request)
    current_id = request.session.get("device_id")
    devices = db.query(AuthDevice).filter(
        AuthDevice.user_id == user_id,
        AuthDevice.revoked == False,
        AuthDevice.id != current_id
    ).all()
    count = 0
    for device in devices:
        device.revoked = True
        count += 1
    db.commit()
    return {"message": f"{count} other session(s) revoked.", "count": count}


@app.get("/api/user/export")
def export_user_data(request: Request, db: Session = Depends(get_db)):
    user_id = _get_current_user_id(request)
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found.")
    profile = _get_account_profile(db, user_id)
    chats = db.query(ChatSession).filter(ChatSession.user_id == user_id).all()
    lives = db.query(LiveSession).filter(LiveSession.user_id == user_id).all()
    devices = db.query(AuthDevice).filter(AuthDevice.user_id == user_id).all()
    payload = {
        "exported_at": datetime.utcnow().isoformat() + "Z",
        "user": {"id": user.id, "name": user.name, "email": user.email, "phone": getattr(user, "phone", None)},
        "profile": {
            "role": profile.role, "bio": profile.bio, "timezone": profile.timezone, "locale": profile.locale,
            "auditory_feedback": bool(profile.auditory_feedback), "weekly_digest": bool(profile.weekly_digest),
            "avatar_present": bool(profile.avatar_data),
        },
        "chats": [
            {"id": c.id, "title": c.title, "created_at": c.created_at.isoformat() if c.created_at else None,
             "updated_at": c.updated_at.isoformat() if c.updated_at else None,
             "messages": [{"role": m.role, "content": m.content, "created_at": m.created_at.isoformat() if m.created_at else None} for m in c.messages]}
            for c in chats
        ],
        "live_sessions": [
            {"id": c.id, "title": c.title, "created_at": c.created_at.isoformat() if c.created_at else None,
             "updated_at": c.updated_at.isoformat() if c.updated_at else None,
             "messages": [{"role": m.role, "content": m.content, "created_at": m.created_at.isoformat() if m.created_at else None} for m in c.messages]}
            for c in lives
        ],
        "devices": [
            {"id": d.id, "name": d.name, "created_at": d.created_at.isoformat() if d.created_at else None,
             "last_seen_at": d.last_seen_at.isoformat() if d.last_seen_at else None, "revoked": d.revoked}
            for d in devices
        ],
    }
    return Response(
        content=json.dumps(payload, ensure_ascii=False, indent=2),
        media_type="application/json",
        headers={"Content-Disposition": 'attachment; filename="maximus-account-export.json"'}
    )


@app.delete("/api/account")
def deactivate_account(request: Request, db: Session = Depends(get_db)):
    user_id = _get_current_user_id(request)
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found.")

    db.query(Provider).filter(Provider.user_id == user_id).delete(synchronize_session=False)
    db.query(GenerationMetric).filter(GenerationMetric.user_id == user_id).delete(synchronize_session=False)
    db.query(AuthDevice).filter(AuthDevice.user_id == user_id).delete(synchronize_session=False)
    db.query(AccountProfile).filter(AccountProfile.user_id == user_id).delete(synchronize_session=False)
    db.query(UserSettings).filter(UserSettings.user_id == user_id).delete(synchronize_session=False)
    db.query(ChatMessage).filter(ChatMessage.user_id == user_id).delete(synchronize_session=False)
    db.query(ChatSession).filter(ChatSession.user_id == user_id).delete(synchronize_session=False)
    db.query(LiveMessage).filter(LiveMessage.user_id == user_id).delete(synchronize_session=False)
    db.query(LiveSession).filter(LiveSession.user_id == user_id).delete(synchronize_session=False)
    db.delete(user)
    db.commit()
    request.session.clear()
    return {"message": "MAXIMUS account deleted successfully."}


# =====================================================
# SETTINGS API
# =====================================================

@app.get("/api/settings")
def get_settings(request: Request, db: Session = Depends(get_db)):
    user_id = _get_current_user_id(request)
    settings = _get_settings(db, user_id)
    return {
        "model": settings.model,
        "model_id": settings.model.split(":", 2)[-1] if settings.model.startswith("provider:") else _model_name(settings),
        "temperature": settings.temperature,
        "context_limit": settings.context_limit,
        "system_prompt": settings.system_prompt,
        "voice": settings.voice,
        "input_language": settings.input_language,
        "output_language": settings.output_language,
        "voice_gender": settings.voice_gender,
        "duplex": settings.duplex,
        "auto_mute": settings.auto_mute,
        "zdr": False,
        "vision_sampling": "On Demand",
        "vision_fidelity": settings.vision_fidelity,
        "auto_ocr": settings.auto_ocr,
        "spatial_grounding": settings.spatial_grounding,
        "network_access": settings.network_access,
    }


@app.put("/api/settings")
@app.post("/api/settings")
def update_settings(data: SettingsUpdateRequest, request: Request, db: Session = Depends(get_db)):
    user_id = _get_current_user_id(request)
    settings = _get_settings(db, user_id)

    if data.model is not None:
        selected_model = data.model.strip()
        if selected_model in MODEL_MAP:
            settings.model = selected_model
        elif selected_model.startswith("provider:"):
            try:
                _, provider_id_text, model_id = selected_model.split(":", 2)
                provider_id = int(provider_id_text)
            except (ValueError, TypeError):
                raise HTTPException(status_code=400, detail="Invalid BYOK model selection.")
            provider = (
                db.query(Provider)
                .filter(
                    Provider.id == provider_id,
                    Provider.user_id == user_id,
                    Provider.is_active == True,
                    Provider.is_valid == True,
                )
                .first()
            )
            if not provider:
                raise HTTPException(status_code=404, detail="Selected BYOK provider is not available.")
            try:
                stored_models = json.loads(provider.models_json or "[]")
                if not isinstance(stored_models, list):
                    stored_models = []
            except Exception:
                stored_models = []
            known_models = {str(m) for m in stored_models if m}
            if provider.model_id:
                known_models.add(str(provider.model_id))
            if known_models and model_id not in known_models:
                raise HTTPException(status_code=400, detail=f'Model "{model_id}" is not registered for this provider.')
            settings.model = selected_model
        else:
            raise HTTPException(status_code=400, detail="Unsupported model selection.")

    if data.temperature is not None:
        if not 0 <= data.temperature <= 1:
            raise HTTPException(status_code=400, detail="Temperature must be between 0 and 1.")
        settings.temperature = data.temperature

    if data.context_limit is not None:
        if not 8 <= data.context_limit <= 256 or data.context_limit % 8 != 0:
            raise HTTPException(status_code=400, detail="Context limit must be 8–256 in steps of 8.")
        settings.context_limit = data.context_limit

    if data.system_prompt is not None:
        if len(data.system_prompt) > 12000:
            raise HTTPException(status_code=400, detail="System prompt is too long.")
        settings.system_prompt = data.system_prompt.strip() or DEFAULT_SYSTEM_PROMPT

    if data.voice is not None:
        settings.voice = data.voice.strip() or DEFAULT_VOICE

    allowed_languages = {"en-IN", "ru-RU", "fr-FR", "ja-JP", "hi-IN", "te-IN", "ta-IN", "kn-IN", "ml-IN"}
    if data.input_language is not None:
        if data.input_language not in allowed_languages:
            raise HTTPException(status_code=400, detail="Unsupported input language.")
        settings.input_language = data.input_language
    if data.output_language is not None:
        if data.output_language not in allowed_languages:
            raise HTTPException(status_code=400, detail="Unsupported output text language.")
        settings.output_language = data.output_language
    if data.voice_gender is not None:
        if data.voice_gender not in {"female", "male"}:
            raise HTTPException(status_code=400, detail="Unsupported voice gender.")
        settings.voice_gender = data.voice_gender

    if data.duplex is not None:
        settings.duplex = data.duplex
    if data.auto_mute is not None:
        settings.auto_mute = data.auto_mute
    # Do not advertise unsupported no-storage or continuous-screen modes.
    settings.zdr = False
    settings.vision_sampling = "On Demand"
    if data.vision_fidelity is not None:
        settings.vision_fidelity = data.vision_fidelity
    if data.auto_ocr is not None:
        settings.auto_ocr = data.auto_ocr
    if data.spatial_grounding is not None:
        settings.spatial_grounding = data.spatial_grounding
    if data.network_access is not None:
        settings.network_access = data.network_access

    settings.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(settings)

    return {
        "message": "Settings saved successfully.",
        "settings": {
            "model": settings.model,
            "model_id": _model_name(settings),
            "temperature": settings.temperature,
            "context_limit": settings.context_limit,
            "system_prompt": settings.system_prompt,
            "voice": settings.voice,
            "input_language": settings.input_language,
            "output_language": settings.output_language,
            "voice_gender": settings.voice_gender,
            "duplex": settings.duplex,
            "auto_mute": settings.auto_mute,
            "zdr": settings.zdr,
            "vision_sampling": settings.vision_sampling,
            "vision_fidelity": settings.vision_fidelity,
            "auto_ocr": settings.auto_ocr,
            "spatial_grounding": settings.spatial_grounding,
            "network_access": settings.network_access,
        }
    }


@app.post("/api/settings/reset")
def reset_settings(request: Request, db: Session = Depends(get_db)):
    user_id = _get_current_user_id(request)
    settings = _get_settings(db, user_id)
    settings.model = "flash"
    settings.temperature = 0.25
    settings.context_limit = 128
    settings.system_prompt = DEFAULT_SYSTEM_PROMPT
    settings.voice = DEFAULT_VOICE
    settings.input_language = "en-IN"
    settings.output_language = "en-IN"
    settings.voice_gender = "female"
    settings.duplex = True
    settings.auto_mute = True
    settings.zdr = False
    settings.vision_sampling = "1 Frame / Sec (Standard Duplex)"
    settings.vision_fidelity = "Native 4K (High Precision)"
    settings.auto_ocr = True
    settings.spatial_grounding = True
    settings.network_access = False
    settings.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(settings)
    return {"message": "JARVIS settings restored to defaults."}


@app.post("/api/settings/verify-api-key")
async def verify_api_key(data: APIKeyVerifyRequest, request: Request, db: Session = Depends(get_db)):
    _get_current_user_id(request)
    key = data.api_key.strip()
    if not key or key == "YOUR_GEMINI_API_KEY":
        raise HTTPException(status_code=400, detail="Enter a Gemini API key first.")

    try:
        test_client = genai.Client(api_key=key)
        test_model = DEFAULT_MODEL
        response = test_client.models.generate_content(
            model=test_model,
            contents="Reply with exactly: OK"
        )
        if not response or not response.text:
            raise ValueError("The API returned no response.")
        return {"verified": True, "message": "Gemini API key verified successfully."}
    except Exception as e:
        print("API KEY VERIFY ERROR:", e)
        raise HTTPException(status_code=400, detail="Gemini API key could not be verified.")


# =====================================================
# NETWORK ACCESS / WEBSITE ACTIONS
# =====================================================

class NetworkOpenRequest(BaseModel):
    target: str


def _resolve_website_target(target: str) -> str:
    """Resolve a spoken/site-name target into a normal HTTPS URL.

    This endpoint does not fetch the remote site on the server. It authorizes
    the browser action; the frontend can open the returned URL in the user's
    browser after the permission check succeeds.
    """
    raw = (target or "").strip()
    if not raw:
        raise HTTPException(status_code=400, detail="Website name or URL is required.")

    aliases = {
        "youtube": "https://www.youtube.com",
        "youtube.com": "https://www.youtube.com",
        "google": "https://www.google.com",
        "google.com": "https://www.google.com",
        "github": "https://github.com",
        "github.com": "https://github.com",
        "gmail": "https://mail.google.com",
        "gmail.com": "https://mail.google.com",
        "instagram": "https://www.instagram.com",
        "instagram.com": "https://www.instagram.com",
        "facebook": "https://www.facebook.com",
        "facebook.com": "https://www.facebook.com",
        "reddit": "https://www.reddit.com",
        "reddit.com": "https://www.reddit.com",
        "linkedin": "https://www.linkedin.com",
        "linkedin.com": "https://www.linkedin.com",
        "spotify": "https://open.spotify.com",
        "spotify.com": "https://open.spotify.com",
        "whatsapp": "https://web.whatsapp.com",
        "whatsapp.com": "https://web.whatsapp.com",
        "chatgpt": "https://chatgpt.com",
        "chatgpt.com": "https://chatgpt.com",
    }

    key = raw.lower().rstrip("/")
    if key in aliases:
        return aliases[key]

    candidate = raw
    if not candidate.lower().startswith(("http://", "https://")):
        candidate = "https://" + candidate

    from urllib.parse import urlparse
    parsed = urlparse(candidate)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise HTTPException(status_code=400, detail="Please provide a valid website name or URL.")

    # Browser navigation only: reject local/private host targets so this
    # endpoint cannot be turned into a server-side network/SSRF primitive.
    hostname = (parsed.hostname or "").lower().rstrip(".")
    if hostname in {"localhost", "127.0.0.1", "::1"} or hostname.endswith(".local"):
        raise HTTPException(status_code=400, detail="Local network addresses are not allowed.")

    return candidate


@app.get("/api/network/status")
def network_access_status(request: Request, db: Session = Depends(get_db)):
    user_id = _get_current_user_id(request)
    settings = _get_settings(db, user_id)
    return {"network_access": bool(settings.network_access)}


@app.post("/api/network/open")
def authorize_website_open(
    data: NetworkOpenRequest,
    request: Request,
    db: Session = Depends(get_db)
):
    user_id = _get_current_user_id(request)
    settings = _get_settings(db, user_id)

    if not settings.network_access:
        raise HTTPException(
            status_code=403,
            detail="Network access is disabled. Enable it in Settings first."
        )

    url = _resolve_website_target(data.target)
    return {
        "allowed": True,
        "action": "open_website",
        "url": url,
        "message": "Website action authorized."
    }


# =====================================================
# DEVICES API
# =====================================================

@app.get("/api/devices")
def list_devices(request: Request, db: Session = Depends(get_db)):
    user_id = _get_current_user_id(request)
    current_id = request.session.get("device_id")
    devices = (
        db.query(AuthDevice)
        .filter(AuthDevice.user_id == user_id, AuthDevice.revoked == False)
        .order_by(AuthDevice.last_seen_at.desc(), AuthDevice.id.desc())
        .all()
    )
    now = datetime.utcnow()
    return [
        {
            "id": d.id,
            "name": d.name,
            "user_agent": d.user_agent,
            "ip_address": d.ip_address,
            "created_at": d.created_at.isoformat() if d.created_at else None,
            "last_seen_at": d.last_seen_at.isoformat() if d.last_seen_at else None,
            "current": d.id == current_id,
            "online": bool(d.last_seen_at and (now - d.last_seen_at).total_seconds() < 300),
        }
        for d in devices
    ]


@app.post("/api/devices/{device_id}/revoke")
def revoke_device(device_id: int, request: Request, db: Session = Depends(get_db)):
    user_id = _get_current_user_id(request)
    device = (
        db.query(AuthDevice)
        .filter(AuthDevice.id == device_id, AuthDevice.user_id == user_id)
        .first()
    )
    if not device:
        raise HTTPException(status_code=404, detail="Device not found.")

    device.revoked = True
    db.commit()

    if request.session.get("device_id") == device.id:
        request.session.clear()
        return {"message": "Current device revoked.", "logged_out": True}

    return {"message": "Device revoked.", "logged_out": False}


@app.delete("/api/user/data")
def purge_user_data(request: Request, db: Session = Depends(get_db)):
    user_id = _get_current_user_id(request)
    db.query(GenerationMetric).filter(GenerationMetric.user_id == user_id).delete(synchronize_session=False)
    db.query(ChatMessage).filter(ChatMessage.user_id == user_id).delete(synchronize_session=False)
    db.query(ChatSession).filter(ChatSession.user_id == user_id).delete(synchronize_session=False)

    # Live AI data is stored separately from normal Chat.
    db.query(LiveMessage).filter(LiveMessage.user_id == user_id).delete(synchronize_session=False)
    db.query(LiveSession).filter(LiveSession.user_id == user_id).delete(synchronize_session=False)

    db.commit()
    request.session.pop("chat_id", None)
    request.session.pop("live_session_id", None)
    return {"message": "Your JARVIS chat and Live AI data have been permanently purged."}


@app.get("/api-pro")
def api_provider_page():
    return _frontend_page(PC_DIR, "apipro.html")


@app.get("/overview")
def overview_page():
    return _frontend_page(PC_DIR, "overview.html")


# =====================================================
# PAGES
# =====================================================

@app.get("/")
def intro():

    return _frontend_page(PC_DIR, "intro.html")


@app.get("/home")
def home():

    return _frontend_page(PC_DIR, "home.html")


@app.get("/settings")
def settings():

    return _frontend_page(PC_DIR, "settings.html")


@app.get("/history")
def history():

    return _frontend_page(PC_DIR, "history.html")


@app.get("/speakwithai")
def speak_with_ai():

    return _frontend_page(PC_DIR, "speakWithAI.html")


@app.get("/qr-login")
def qr_login():

    return _frontend_page(PC_DIR, "qr-login.html")


@app.get("/qr-confirm")
def qr_confirm():

    return _frontend_page(PC_DIR, "qr-confirm.html")




# =====================================================
# MOBILE PAGES
# =====================================================

@app.get("/m")
@app.get("/m/")
def mobile_intro():
    return _frontend_page(MOBILE_DIR, "intro.html")


@app.get("/m/home")
def mobile_home():
    return _frontend_page(MOBILE_DIR, "home.html")


@app.get("/m/settings")
def mobile_settings():
    return _frontend_page(MOBILE_DIR, "settings.html")


@app.get("/m/history")
def mobile_history():
    return _frontend_page(MOBILE_DIR, "history.html")


@app.get("/m/speakwithai")
def mobile_speak_with_ai():
    return _frontend_page(MOBILE_DIR, "speakWithAI.html")


@app.get("/m/qr-login")
def mobile_qr_login():
    return _frontend_page(MOBILE_DIR, "qr-login.html")


@app.get("/m/qr-confirm")
def mobile_qr_confirm():
    return _frontend_page(MOBILE_DIR, "qr-confirm.html")


@app.get("/m/login")
def mobile_login_page():
    return _frontend_page(MOBILE_DIR, "login.html")


@app.get("/m/signup")
def mobile_signup_page():
    return _frontend_page(MOBILE_DIR, "signup.html")


@app.get("/m/profile-settings")
def mobile_profile_settings_page():
    return _frontend_page(MOBILE_DIR, "profile-settings.html")


@app.get("/m/api-pro")
@app.get("/m/apipro")
def mobile_api_provider_page():
    return _frontend_page(MOBILE_DIR, "apipro.html")


@app.get("/m/overview")
def mobile_overview_page():
    return _frontend_page(MOBILE_DIR, "overview.html")


@app.get("/m/forgot-password")
def mobile_forgot_password_page():
    return _frontend_page(MOBILE_DIR, "forgot-password.html")


@app.get("/m/reset-password")
def mobile_reset_password_page():
    return _frontend_page(MOBILE_DIR, "reset-password.html")


# =====================================================
# LOGIN — PAGE
# =====================================================

@app.get("/logout")
def logout(request: Request, db: Session = Depends(get_db)):
    user_id = request.session.get("user_id")
    if user_id:
        _delete_unused_chat_sessions(db, int(user_id))
        _delete_unused_live_sessions(db, int(user_id))
    request.session.clear()
    return RedirectResponse(url="/login", status_code=303)


@app.get("/login")
def login_page():

    return _frontend_page(PC_DIR, "login.html")


# =====================================================
# LOGIN — EMAIL + PASSWORD
# =====================================================

@app.post("/login")
async def login_user(
    request: Request,
    user: LoginRequest,
    db: Session = Depends(get_db)
):

    existing_user = (
        db.query(User)
        .filter(User.email == user.email)
        .first()
    )

    if not existing_user:
        raise HTTPException(
            status_code=401,
            detail="Invalid email or password"
        )

    if existing_user.password_hash is None:

        raise HTTPException(
            status_code=401,
            detail=(
                "This account uses social login only. "
                "Please sign in with Google."
            )
        )

    password_valid = password_hasher.verify(
        user.password,
        existing_user.password_hash
    )

    if not password_valid:

        raise HTTPException(
            status_code=401,
            detail="Invalid email or password"
        )

    request.session["user_id"] = existing_user.id
    request.session["user_name"] = existing_user.name
    request.session["user_email"] = existing_user.email
    _create_device_session(request, db, existing_user.id)
    _start_authenticated_conversation_session(request, db, existing_user.id)

    return {
        "message": "Login successful",
        "user_id": existing_user.id,
        "name": existing_user.name,
        "email": existing_user.email
    }


# =====================================================
# LOGIN — PHONE OTP
# =====================================================

@app.post("/login/send-otp")
async def login_send_otp(
    req: LoginOTPRequest,
    db: Session = Depends(get_db)
):

    user = (
        db.query(User)
        .filter(User.phone == req.phone)
        .first()
    )

    if not user:

        raise HTTPException(
            status_code=404,
            detail="No account found with this phone number."
        )

    otp = generate_otp()

    store_otp(
        f"phone_login:{req.phone}",
        otp,
        {"user_id": user.id}
    )

    print("\n" + "=" * 40)
    print(f"📱 LOGIN OTP for {req.phone}: {otp}")
    print("=" * 40 + "\n")

    return {
        "message": "OTP sent to phone"
    }


@app.post("/login/verify-otp")
def login_verify_otp(
    req: VerifyLoginOTPRequest,
    request: Request,
    db: Session = Depends(get_db)
):

    data = verify_otp(
        f"phone_login:{req.phone}",
        req.otp
    )

    if data is None:

        raise HTTPException(
            status_code=400,
            detail="Invalid or expired OTP."
        )

    user = (
        db.query(User)
        .filter(User.id == data["user_id"])
        .first()
    )

    if not user:

        raise HTTPException(
            status_code=404,
            detail="User not found."
        )

    request.session["user_id"] = user.id

    request.session["user_name"] = user.name

    request.session["user_email"] = user.email
    _create_device_session(request, db, user.id)
    _start_authenticated_conversation_session(request, db, user.id)

    return {
        "message": "Login successful",
        "user_id": user.id,
        "name": user.name
    }


# =====================================================
# SIGNUP
# =====================================================

@app.get("/signup")
def signup_page():

    return _frontend_page(PC_DIR, "signup.html")


# =====================================================
# SIGNUP — PHONE OTP
# =====================================================

@app.post("/signup/send-phone-otp")
async def signup_send_phone_otp(
    req: PhoneOTPRequest,
    db: Session = Depends(get_db)
):

    if (
        db.query(User)
        .filter(User.email == req.email)
        .first()
    ):

        raise HTTPException(
            status_code=400,
            detail="Email already registered. Please login."
        )

    if (
        db.query(User)
        .filter(User.phone == req.phone)
        .first()
    ):

        raise HTTPException(
            status_code=400,
            detail="Phone number already registered."
        )

    otp = generate_otp()

    store_otp(
        f"phone_signup:{req.phone}",
        otp,
        {
            "email": req.email,
            "name": req.name
        }
    )

    print("\n" + "=" * 40)
    print(f"📱 SIGNUP PHONE OTP for {req.phone}: {otp}")
    print("=" * 40 + "\n")

    return {
        "message": "OTP sent to phone"
    }


# =====================================================
# SIGNUP — VERIFY PHONE OTP
# =====================================================

@app.post("/signup/verify-phone-otp")
def signup_verify_phone_otp(
    req: VerifyPhoneOTPRequest
):

    data = verify_otp(
        f"phone_signup:{req.phone}",
        req.otp
    )

    if data is None:

        raise HTTPException(
            status_code=400,
            detail="Invalid or expired OTP. Please try again."
        )

    store_otp(
        f"phone_verified:{req.phone}",
        "ok",
        data
    )

    return {
        "message": "Phone verified"
    }


# =====================================================
# SIGNUP — EMAIL OTP
# =====================================================

@app.post("/signup/send-email-otp")
async def signup_send_email_otp(
    req: EmailOTPRequest
):

    otp = generate_otp()

    store_otp(
        f"email_signup:{req.email}",
        otp
    )

    print("\n" + "=" * 40)
    print(f"📧 SIGNUP EMAIL OTP for {req.email}: {otp}")
    print("=" * 40 + "\n")

    try:

        await send_otp_email(
            req.email,
            req.name,
            otp
        )

    except Exception as e:

        print(f"[EMAIL ERROR] {e}")

    return {
        "message": "OTP sent to email"
    }


# =====================================================
# SIGNUP — COMPLETE
# =====================================================

@app.post("/signup/complete")
def signup_complete(
    req: SignupCompleteRequest,
    request: Request,
    db: Session = Depends(get_db)
):

    data = verify_otp(
        f"email_signup:{req.email}",
        req.otp
    )

    if data is None:

        raise HTTPException(
            status_code=400,
            detail="Invalid or expired code. Please try again."
        )

    if (
        db.query(User)
        .filter(User.email == req.email)
        .first()
    ):

        raise HTTPException(
            status_code=400,
            detail="Email already registered."
        )

    new_user = User(
        name=req.name,
        email=req.email,
        phone=req.phone,
        password_hash=password_hasher.hash(req.password),
        is_verified=True
    )

    db.add(new_user)

    db.commit()

    db.refresh(new_user)

    request.session["user_id"] = new_user.id
    request.session["user_name"] = new_user.name
    request.session["user_email"] = new_user.email
    _create_device_session(request, db, new_user.id)
    _start_authenticated_conversation_session(request, db, new_user.id)

    return {
        "message": "Account created successfully",
        "user_id": new_user.id,
        "name": new_user.name,
        "email": new_user.email
    }


# =====================================================
# QR LOGIN
# =====================================================

def _public_base_url(request: Request) -> str:
    configured = os.getenv("APP_BASE_URL", "").strip().rstrip("/")
    if configured:
        return configured
    if os.getenv("VERCEL"):
        hostname = os.getenv("VERCEL_PROJECT_PRODUCTION_URL") or os.getenv("VERCEL_URL")
        if hostname:
            return "https://" + hostname
        raise HTTPException(status_code=503, detail="Configure APP_BASE_URL for public login links.")
    return str(request.base_url).rstrip("/")


@app.get("/auth/qr/generate")
def generate_qr(request: Request, db: Session = Depends(get_db)):
    """Create a short-lived QR session that transfers the current PC account to a new device."""
    user_id = request.session.get("user_id")
    if not user_id:
        raise HTTPException(status_code=401, detail="Sign in on this device before generating a QR login.")

    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        request.session.clear()
        raise HTTPException(status_code=401, detail="Your login session is no longer valid.")

    target_id = secrets.token_urlsafe(24)
    token = secrets.token_urlsafe(32)
    now = time.time()
    request.session["qr_target_id"] = target_id
    request.session["qr_token"] = token
    store_challenge("qr", token, {"target_id": target_id, "source_user_id": user.id}, 60)

    lan_host = os.getenv("MAXIMUS_LAN_HOST", "").strip()
    if not lan_host and not os.getenv("VERCEL") and not os.getenv("APP_BASE_URL"):
        try:
            probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            probe.connect(("8.8.8.8", 80))
            lan_host = probe.getsockname()[0]
            probe.close()
        except Exception:
            lan_host = request.url.hostname or "127.0.0.1"

    scheme = request.url.scheme
    port = request.url.port
    host_part = lan_host if not port else f"{lan_host}:{port}"
    base_url = _public_base_url(request) if os.getenv("VERCEL") or os.getenv("APP_BASE_URL") else f"{scheme}://{host_part}"
    qr_url = f"{base_url}/auth/qr-confirm?token={token}"

    qr = qrcode.QRCode(version=1, box_size=10, border=4)
    qr.add_data(qr_url); qr.make(fit=True)
    image = qr.make_image()
    buffer = io.BytesIO(); image.save(buffer, format="PNG"); buffer.seek(0)
    response = StreamingResponse(buffer, media_type="image/png")
    response.headers["X-MAXIMUS-QR-Token"] = token
    response.headers["X-MAXIMUS-QR-Mode"] = "account_transfer"
    response.headers["Cache-Control"] = "no-store"
    return response


@app.get("/auth/qr-confirm")
def scan_qr(token: str, request: Request, mobile: bool = False, db: Session = Depends(get_db)):
    data = consume_challenge("qr", token)
    if not data:
        raise HTTPException(status_code=400, detail="QR code is invalid, expired, or already used.")
    user = db.query(User).filter(User.id == data["source_user_id"]).first()
    if not user:
        raise HTTPException(status_code=404, detail="The MAXIMUS account could not be found.")
    request.session["user_id"] = user.id
    request.session["user_name"] = user.name
    request.session["user_email"] = user.email
    _create_device_session(request, db, user.id)
    _start_authenticated_conversation_session(request, db, user.id)
    destination = "/m/home" if mobile or _is_mobile_client(request) else "/home"
    return RedirectResponse(url=destination, status_code=303)


def _qr_status(request: Request, token: str):
    entry = read_challenge("qr", token)
    target_id = request.session.get("qr_target_id")
    if not entry or not target_id or entry["data"].get("target_id") != target_id:
        raise HTTPException(status_code=404, detail="Invalid or expired QR session.")
    if entry["used"]:
        return {"status": "completed"}
    if time.time() > entry["expires"]:
        raise HTTPException(status_code=400, detail="QR code expired. Please generate a new one.")
    return {"status": "waiting"}


@app.get("/auth/qr/pending")
def qr_pending(request: Request):
    token = request.session.get("qr_token")
    if not token:
        raise HTTPException(status_code=400, detail="No active QR login session.")
    return _qr_status(request, token)


@app.get("/auth/qr/status")
def qr_status(request: Request, token: str):
    return _qr_status(request, token)


@app.post("/auth/qr/approve")
def approve_qr_legacy():
    raise HTTPException(status_code=410, detail="QR approval is no longer used. Scanning the QR signs the new device in directly.")


@app.post("/auth/qr/exchange")
def exchange_qr_legacy():
    raise HTTPException(status_code=410, detail="QR exchange is no longer used. Scanning the QR signs the new device in directly.")


# =====================================================
# FORGOT PASSWORD
# =====================================================

@app.get("/forgot-password")
async def forgot_password_page():

    return _frontend_page(PC_DIR, "forgot-password.html")


@app.post("/forgot-password")
async def forgot_password(
    data: dict,
    request: Request,
):

    email = data.get(
        "email",
        ""
    ).strip().lower()

    if not email:

        raise HTTPException(
            status_code=400,
            detail="Email is required."
        )

    db = SessionLocal()

    try:

        user = (
            db.query(User)
            .filter(User.email == email)
            .first()
        )

        if not user:

            return {
                "message": (
                    "If that email is registered, "
                    "a password reset link has been sent."
                )
            }

        if (
            (user.google_id or user.apple_id)
            and not user.password_hash
        ):

            return {
                "message": (
                    "This account uses Google or "
                    "Apple Sign-In. Please continue "
                    "with your social login."
                )
            }

        token = secrets.token_urlsafe(32)

        user.reset_token = token

        user.reset_token_expires = (
            datetime.utcnow() +
            timedelta(minutes=15)
        )

        db.commit()

        reset_link = f"{_public_base_url(request)}/reset-password?token={token}"

        message = MessageSchema(
            subject="JARVIS AI - Password Reset",
            recipients=[user.email],
            body=f"""Hello {user.name},

You requested a password reset for your JARVIS AI account.

Click the link below to create a new password:

{reset_link}

This link will expire in 15 minutes.

If you did not request this password reset, you can safely ignore this email.

Regards,
JARVIS AI
""",
            subtype="plain"
        )

        fm = FastMail(mail_conf)

        await fm.send_message(message)

        print(
            "\n" +
            "=" * 40 +
            f"\nPASSWORD RESET EMAIL SENT TO: {user.email}" +
            "\n" +
            "=" * 40 +
            "\n"
        )

        return {
            "message": (
                "If that email is registered, "
                "a password reset link has been sent."
            )
        }

    except Exception as e:

        db.rollback()

        print(
            "\n" +
            "=" * 40 +
            f"\nEMAIL ERROR: {e}" +
            "\n" +
            "=" * 40 +
            "\n"
        )

        raise HTTPException(
            status_code=500,
            detail="Could not send reset email."
        )

    finally:

        db.close()


# =====================================================
# RESET PASSWORD
# =====================================================

@app.get("/reset-password")
async def reset_password_page():

    return _frontend_page(PC_DIR, "reset-password.html")


@app.post("/reset-password")
async def reset_password(
    data: dict
):

    token = data.get(
        "token",
        ""
    ).strip()

    password = data.get(
        "password",
        ""
    )

    if not token:

        raise HTTPException(
            status_code=400,
            detail="Invalid reset token."
        )

    if len(password) < 8:

        raise HTTPException(
            status_code=400,
            detail="Password must be at least 8 characters."
        )

    db = SessionLocal()

    try:

        user = (
            db.query(User)
            .filter(User.reset_token == token)
            .first()
        )

        if not user:

            raise HTTPException(
                status_code=400,
                detail="Invalid or expired reset link."
            )

        if (
            not user.reset_token_expires
            or
            user.reset_token_expires < datetime.utcnow()
        ):

            raise HTTPException(
                status_code=400,
                detail="This reset link has expired."
            )

        user.password_hash = password_hasher.hash(
            password
        )

        user.reset_token = None

        user.reset_token_expires = None

        db.commit()

        return {
            "message": (
                "Password reset successfully. "
                "You can now log in."
            )
        }

    except HTTPException:
        raise

    except Exception as e:

        db.rollback()

        print(
            "\n" +
            "=" * 40 +
            f"\nRESET PASSWORD ERROR: {e}" +
            "\n" +
            "=" * 40 +
            "\n"
        )

        raise HTTPException(
            status_code=500,
            detail="Could not reset password."
        )

    finally:

        db.close()


# =====================================================
# CREATE NEW CHAT
# =====================================================

@app.post("/api/chat/new")
def create_chat(
    payload: NewChatRequest,
    request: Request,
    db: Session = Depends(get_db)
):

    user_id = request.session.get(
        "user_id"
    )

    if not user_id:

        raise HTTPException(
            status_code=401,
            detail="Not authenticated"
        )

    title = (payload.title or "New Conversation").strip()[:200]
    mode = (payload.mode or "chat").strip().lower()

    if mode not in {"chat", "live"}:
        raise HTTPException(
            status_code=400,
            detail="Invalid conversation mode. Use chat or live."
        )

    # A manually-created chat replaces an unused placeholder chat instead of
    # leaving another 0-message entry behind.
    _delete_unused_chat_sessions(db, int(user_id))

    chat = ChatSession(
        user_id=user_id,
        title=title or "New Conversation",
        mode=mode
    )

    db.add(chat)

    db.commit()

    db.refresh(chat)

    request.session["chat_id"] = chat.id

    return {
        "id": chat.id,
        "title": chat.title,
        "mode": chat.mode
    }


# =====================================================
# OPEN A FRESH CHAT WHEN HOME IS ENTERED
# =====================================================

@app.post("/api/chat/open-new")
def open_new_chat(
    request: Request,
    db: Session = Depends(get_db)
):
    """Return this login session's current chat, creating it only once."""
    user_id = _get_current_user_id(request)
    chat_id = request.session.get("chat_id")

    if chat_id:
        chat = (
            db.query(ChatSession)
            .filter(ChatSession.id == chat_id, ChatSession.user_id == user_id)
            .first()
        )
        if chat is not None:
            return {"id": chat.id, "title": chat.title, "mode": getattr(chat, "mode", "chat") or "chat"}

    _delete_unused_chat_sessions(db, user_id)
    chat = ChatSession(user_id=user_id, title="New Chat", mode="chat")
    db.add(chat)
    db.commit()
    db.refresh(chat)
    request.session["chat_id"] = chat.id
    return {"id": chat.id, "title": chat.title, "mode": "chat"}


# =====================================================
# GET / CREATE CURRENT CHAT
# =====================================================

def _get_or_create_current_chat(
    request: Request,
    db: Session
) -> ChatSession:

    user_id = request.session.get(
        "user_id"
    )

    if not user_id:

        raise HTTPException(
            status_code=401,
            detail="Not authenticated"
        )

    chat_id = request.session.get(
        "chat_id"
    )

    chat = None

    if chat_id:

        chat = (
            db.query(ChatSession)
            .filter(
                ChatSession.id == chat_id,
                ChatSession.user_id == user_id
            )
            .first()
        )

    if chat is None:

        chat = ChatSession(
            user_id=user_id,
            title="New Chat",
            mode="chat"
        )

        db.add(chat)

        db.commit()

        db.refresh(chat)

    request.session["chat_id"] = chat.id

    return chat


# =====================================================
# CHAT LIST
# =====================================================

@app.get("/api/chat/list")
def list_chats(
    request: Request,
    db: Session = Depends(get_db)
):

    user_id = request.session.get(
        "user_id"
    )

    if not user_id:

        raise HTTPException(
            status_code=401,
            detail="Not authenticated"
        )

    # Remove abandoned 0-message chats from older/navigation-created sessions,
    # but keep this browser session's current empty draft alive.
    current_chat_id = request.session.get("chat_id")
    _delete_unused_chat_sessions(db, int(user_id), keep_id=current_chat_id)

    chats = (
        db.query(ChatSession)
        .filter(
            ChatSession.user_id == user_id
        )
        .order_by(
            ChatSession.updated_at.desc(),
            ChatSession.id.desc()
        )
        .all()
    )
    pinned_rows = db.execute(
        text("SELECT id, pinned FROM chat_sessions WHERE user_id = :uid"),
        {"uid": user_id}
    ).fetchall()
    pinned_map = {int(row[0]): bool(row[1]) for row in pinned_rows}

    return [
        {
            "id": chat.id,
            "title": chat.title,
            "mode": getattr(chat, "mode", "chat") or "chat",
            "created_at": (
                chat.created_at.isoformat()
                if chat.created_at
                else None
            ),
            "updated_at": (
                chat.updated_at.isoformat()
                if chat.updated_at
                else None
            ),
            "message_count": len(chat.messages),
            "pinned": pinned_map.get(chat.id, False)
        }
        for chat in chats
        if len(chat.messages) > 0
    ]


# =====================================================
# OPEN CHAT
# =====================================================

@app.get("/api/chat/{chat_id:int}")
def get_chat(
    chat_id: int,
    request: Request,
    db: Session = Depends(get_db)
):

    user_id = request.session.get(
        "user_id"
    )

    if not user_id:

        raise HTTPException(
            status_code=401,
            detail="Not authenticated"
        )

    chat = (
        db.query(ChatSession)
        .filter(
            ChatSession.id == chat_id,
            ChatSession.user_id == user_id
        )
        .first()
    )

    if not chat:

        raise HTTPException(
            status_code=404,
            detail="Chat not found"
        )

    request.session["chat_id"] = chat.id

    return {
        "id": chat.id,
        "title": chat.title,
        "mode": getattr(chat, "mode", "chat") or "chat",
        "messages": [
            {
                "id": msg.id,
                "role": msg.role,
                "content": msg.content,
                "created_at": (
                    msg.created_at.isoformat()
                    if msg.created_at
                    else None
                ),
                "pinned": msg.pinned
            }
            for msg in chat.messages
        ]
    }


# =====================================================
# DELETE CHAT
# =====================================================

@app.delete("/api/chat/{chat_id:int}")
def delete_chat(
    chat_id: int,
    request: Request,
    db: Session = Depends(get_db)
):

    user_id = request.session.get(
        "user_id"
    )

    if not user_id:

        raise HTTPException(
            status_code=401,
            detail="Not authenticated"
        )

    chat = (
        db.query(ChatSession)
        .filter(
            ChatSession.id == chat_id,
            ChatSession.user_id == user_id
        )
        .first()
    )

    if not chat:

        raise HTTPException(
            status_code=404,
            detail="Chat not found"
        )

    was_current = (
        request.session.get("chat_id")
        == chat.id
    )

    db.delete(chat)

    db.commit()

    if was_current:

        request.session.pop(
            "chat_id",
            None
        )

    return {
        "message": "Chat deleted",
        "id": chat_id
    }


# =====================================================
# BYOK PROVIDERS
# =====================================================

@app.get("/api/providers")
def list_providers(request: Request, db: Session = Depends(get_db)):
    user_id = _get_current_user_id(request)
    return [_provider_to_dict(p) for p in db.query(Provider).filter(Provider.user_id == user_id).order_by(Provider.id.asc()).all()]


@app.post("/api/providers/test")
def test_provider(data: ProviderTestRequest, request: Request):
    _get_current_user_id(request)
    try:
        return _test_provider_config(data)
    except Exception as exc:
        return {"valid": False, "message": str(exc), "latency_ms": None, "models": []}


@app.post("/api/providers")
def create_provider(data: ProviderCreateRequest, request: Request, db: Session = Depends(get_db)):
    user_id = _get_current_user_id(request)
    test = _test_provider_config(ProviderTestRequest(**data.model_dump()))
    if not test.get("valid"):
        raise HTTPException(status_code=400, detail=test.get("message") or "Provider validation failed.")
    provider_name = "ollama" if _is_local_ollama(data.base_url, data.provider) else data.provider
    provider = Provider(
        user_id=user_id, provider=provider_name, label=data.label.strip(),
        base_url=_normalise_provider_url(data.base_url, provider_name),
        api_key=_encrypt_provider_key(data.api_key), organization=data.organization or None,
        api_format=data.api_format, is_active=data.is_active if data.is_active is not None else True,
        is_valid=True, last_latency_ms=test.get("latency_ms"), last_checked_at=datetime.utcnow()
    )
    models = [str(m) for m in (test.get("models") or []) if m]
    provider.models_json = json.dumps(models)
    if models:
        provider.model_id = models[0]
    db.add(provider); db.commit(); db.refresh(provider)
    return {"message": "Provider connected.", **_provider_to_dict(provider)}


@app.patch("/api/providers/{provider_id}")
def update_provider(provider_id: int, data: ProviderUpdateRequest, request: Request, db: Session = Depends(get_db)):
    user_id = _get_current_user_id(request)
    provider = db.query(Provider).filter(Provider.id == provider_id, Provider.user_id == user_id).first()
    if not provider:
        raise HTTPException(status_code=404, detail="Provider not found.")
    payload = data.model_dump(exclude_unset=True)
    if "label" in payload and payload["label"]:
        provider.label = payload["label"].strip()
    if "base_url" in payload and payload["base_url"]:
        provider.base_url = _normalise_provider_url(payload["base_url"], provider.provider)
    if "organization" in payload:
        provider.organization = payload["organization"] or None
    if "api_format" in payload and payload["api_format"]:
        provider.api_format = payload["api_format"]
    if "api_key" in payload and payload["api_key"]:
        provider.api_key = _encrypt_provider_key(payload["api_key"])
    if "is_active" in payload and payload["is_active"] is not None:
        provider.is_active = bool(payload["is_active"])

    test = _test_provider_config(ProviderTestRequest(
        provider=provider.provider, label=provider.label, base_url=provider.base_url,
        api_key=_decrypt_provider_key(provider.api_key), organization=provider.organization, api_format=provider.api_format
    ))
    provider.is_valid = bool(test.get("valid")); provider.last_error = None if provider.is_valid else test.get("message")
    provider.last_latency_ms = test.get("latency_ms"); provider.last_checked_at = datetime.utcnow(); provider.updated_at = datetime.utcnow()
    discovered = [str(m) for m in (test.get("models") or []) if m]
    if provider.is_valid and discovered:
        provider.models_json = json.dumps(discovered)
        if not provider.model_id or provider.model_id not in discovered:
            provider.model_id = discovered[0]
    db.commit(); db.refresh(provider)
    if not provider.is_valid:
        raise HTTPException(status_code=400, detail=provider.last_error or "Provider validation failed.")
    return {"message": "Provider updated.", **_provider_to_dict(provider)}


@app.delete("/api/providers/{provider_id}")
def delete_provider(provider_id: int, request: Request, db: Session = Depends(get_db)):
    user_id = _get_current_user_id(request)
    provider = db.query(Provider).filter(Provider.id == provider_id, Provider.user_id == user_id).first()
    if not provider:
        raise HTTPException(status_code=404, detail="Provider not found.")
    db.delete(provider); db.commit()
    return {"message": "Provider removed.", "id": provider_id}


@app.get("/api/providers/{provider_id}/models")
def discover_provider_models(provider_id: int, request: Request, db: Session = Depends(get_db)):
    user_id = _get_current_user_id(request)
    provider = db.query(Provider).filter(Provider.id == provider_id, Provider.user_id == user_id).first()
    if not provider:
        raise HTTPException(status_code=404, detail="Provider not found.")
    try:
        models = _provider_models(provider)
        provider.models_json = json.dumps(models)
        selected = (provider.model_id or "").strip()
        model_available = (selected in models) if selected else None
        if selected and not model_available:
            provider.is_valid = False
            provider.last_error = f'Model "{selected}" is no longer available to this API key.'
        else:
            provider.is_valid = True
            provider.last_error = None
            if not selected and models:
                provider.model_id = models[0]
        provider.last_checked_at = datetime.utcnow()
        provider.updated_at = datetime.utcnow()
        db.commit()
        return {"models": models, "model_available": model_available, "provider": _provider_to_dict(provider)}
    except Exception as exc:
        provider.is_valid = False; provider.last_error = str(exc)[:500]; provider.last_checked_at = datetime.utcnow(); db.commit()
        raise HTTPException(status_code=400, detail=provider.last_error)


@app.post("/api/models")
def register_provider_model(data: ModelRequest, request: Request, db: Session = Depends(get_db)):
    user_id = _get_current_user_id(request)
    provider = db.query(Provider).filter(Provider.id == data.provider_id, Provider.user_id == user_id).first()
    if not provider:
        raise HTTPException(status_code=404, detail="Provider not found.")
    name = (data.name or data.model_name or data.model_id or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="Model ID is required.")
    if provider.model_id and provider.model_id != data.replace_model and provider.model_id != name:
        raise HTTPException(status_code=409, detail="Only one selected model is stored per provider.")
    try:
        stored_models = json.loads(provider.models_json or "[]")
        if not isinstance(stored_models, list): stored_models = []
    except Exception:
        stored_models = []
    if name not in stored_models:
        stored_models.append(name)
    provider.models_json = json.dumps(stored_models)
    provider.model_id = name; provider.updated_at = datetime.utcnow(); db.commit(); db.refresh(provider)
    return {"message": "Model ID saved.", "provider": _provider_to_dict(provider)}


@app.delete("/api/models")
def delete_provider_model(data: ModelRequest, request: Request, db: Session = Depends(get_db)):
    user_id = _get_current_user_id(request)
    provider = db.query(Provider).filter(Provider.id == data.provider_id, Provider.user_id == user_id).first()
    if not provider:
        raise HTTPException(status_code=404, detail="Provider not found.")
    name = (data.name or data.model_name or data.model_id or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="Model ID is required.")
    try:
        stored_models = json.loads(provider.models_json or "[]")
        if not isinstance(stored_models, list): stored_models = []
    except Exception:
        stored_models = []
    if name not in [str(m) for m in stored_models] and provider.model_id != name:
        raise HTTPException(status_code=404, detail="Model ID not found.")
    stored_models = [str(m) for m in stored_models if str(m) != name]
    provider.models_json = json.dumps(stored_models)
    provider.model_id = stored_models[0] if stored_models else None
    provider.updated_at = datetime.utcnow(); db.commit()
    return {"message": "Model ID removed."}


@app.get("/api/providers/telemetry")
def provider_telemetry(request: Request, db: Session = Depends(get_db)):
    user_id = _get_current_user_id(request)
    providers = db.query(Provider).filter(Provider.user_id == user_id).all()
    valid = [p for p in providers if p.is_active and p.is_valid]
    metrics = db.query(GenerationMetric).filter(
        GenerationMetric.user_id == user_id,
        GenerationMetric.created_at >= datetime.utcnow() - timedelta(hours=24),
    ).all()
    latencies = sorted(m.latency_ms for m in metrics)
    import math
    p95 = round(latencies[math.ceil(.95 * len(latencies)) - 1], 1) if latencies else None
    model_count = 0
    for provider in providers:
        try:
            models = json.loads(provider.models_json or "[]")
        except (ValueError, TypeError):
            models = []
        model_count += len({str(m) for m in models if m} | ({provider.model_id} if provider.model_id else set()))
    return {"active_gateways": len(valid), "registered_models": model_count,
            "providers_total": len(providers), "requests": len(metrics),
            "errors": sum(not m.success for m in metrics), "p95_latency_ms": p95,
            "window": "24h", "scope": "custom provider text requests"}


# =====================================================
# TEXT CHAT
# =====================================================

@app.post("/ask")
def ask(
    prompt: Prompt,
    request: Request,
    db: Session = Depends(get_db)
):

    user_text = prompt.text.strip()

    if not user_text:

        raise HTTPException(
            status_code=400,
            detail="Message cannot be empty"
        )

    try:

        chat = _get_or_create_current_chat(
            request,
            db
        )

        if chat.title == "New Chat":

            chat.title = (
                user_text[:60]
                +
                ("..." if len(user_text) > 60 else "")
            )

        settings = _get_settings(db, request.session["user_id"])
        selected_model = (prompt.model or "flash").strip()

        # Build history before inserting the current user message. For local
        # providers, keep the history small and structured so Ollama does not
        # spend most of the request re-processing a large text transcript.
        provider_messages = None
        if selected_model.startswith("provider:"):
            recent_messages = (
                db.query(ChatMessage)
                .filter(ChatMessage.session_id == chat.id)
                .order_by(ChatMessage.id.desc())
                .limit(8)
                .all()
            )
            recent_messages.reverse()
            provider_messages = [
                {"role": "system", "content": (settings.system_prompt or DEFAULT_SYSTEM_PROMPT).strip()}
            ]
            for msg in recent_messages:
                role = "assistant" if msg.role == "assistant" else "user"
                content = str(msg.content or "").strip()
                if content:
                    provider_messages.append({"role": role, "content": content[:4000]})
            provider_messages.append({"role": "user", "content": user_text})
            chat_prompt = user_text
        else:
            chat_prompt = _build_chat_prompt(db, chat.id, settings, user_text)

        # Finish session metadata before the external call. Persist the user
        # message together with the reply, so failed requests are safe to retry.
        db.commit()

        if selected_model.startswith("provider:"):
            try:
                _, provider_id_text, model_id = selected_model.split(":", 2)
                provider_id = int(provider_id_text)
            except (ValueError, TypeError):
                raise HTTPException(status_code=400, detail="Invalid provider model selection.")

            provider = (
                db.query(Provider)
                .filter(
                    Provider.id == provider_id,
                    Provider.user_id == request.session["user_id"],
                    Provider.is_active == True,
                    Provider.is_valid == True,
                )
                .first()
            )
            if not provider:
                raise HTTPException(status_code=404, detail="Selected provider is not available.")

            # Do not call Ollama /api/tags on every chat message. Model discovery
            # belongs to the Models & Providers page; repeating it here adds an
            # unnecessary round-trip before every generation.
            try:
                stored_models = json.loads(provider.models_json or "[]")
                if not isinstance(stored_models, list):
                    stored_models = []
            except Exception:
                stored_models = []

            known_models = {str(m) for m in stored_models if m}
            if provider.model_id:
                known_models.add(str(provider.model_id))
            if known_models and model_id not in known_models:
                raise HTTPException(status_code=400, detail=f'Model "{model_id}" is no longer registered for this provider. Refresh Models & Providers to update the model list.')

            reply = _provider_chat(
                provider, model_id, chat_prompt, settings,
                provider_messages=provider_messages,
            )
            db.commit()
        else:
            response = client.models.generate_content(
                model=_model_name(settings),
                contents=chat_prompt,
                config=_generation_config(settings)
            )
            reply = response.text or "I couldn't generate a response."

        db.add(
            ChatMessage(
                user_id=request.session["user_id"],
                session_id=chat.id,
                role="user",
                content=user_text
            )
        )
        db.add(
            ChatMessage(
                user_id=request.session["user_id"],
                session_id=chat.id,
                role="assistant",
                content=reply
            )
        )

        chat.updated_at = datetime.utcnow()

        db.commit()

        return {
            "reply": reply,
            "chat_id": chat.id,
            "title": chat.title
        }

    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        raise HTTPException(status_code=502, detail="The AI provider could not complete this request. Please try again.")

# =====================================================
# OPEN A FRESH LIVE SESSION WHEN LIVE AI IS ENTERED
# =====================================================

@app.post("/api/live/open-new")
def open_new_live_session(
    request: Request,
    db: Session = Depends(get_db)
):
    """Return this login session's Live AI session, creating it only once."""
    user_id = _get_current_user_id(request)
    live_session_id = request.session.get("live_session_id")

    if live_session_id:
        live = (
            db.query(LiveSession)
            .filter(LiveSession.id == live_session_id, LiveSession.user_id == user_id)
            .first()
        )
        if live is not None:
            return {"id": live.id, "title": live.title, "mode": "live"}

    _delete_unused_live_sessions(db, user_id)
    live = LiveSession(user_id=user_id, title="New Live Session")
    db.add(live)
    db.commit()
    db.refresh(live)
    request.session["live_session_id"] = live.id
    return {"id": live.id, "title": live.title, "mode": "live"}


# =====================================================
# LIVE AI HELPERS
# =====================================================

def _get_or_create_current_live_session(
    request: Request,
    db: Session
) -> LiveSession:
    user_id = _get_current_user_id(request)

    live_session_id = request.session.get("live_session_id")
    live = None

    if live_session_id:
        live = (
            db.query(LiveSession)
            .filter(
                LiveSession.id == live_session_id,
                LiveSession.user_id == user_id
            )
            .first()
        )

    if live is None:
        live = LiveSession(
            user_id=user_id,
            title="New Live Session"
        )
        db.add(live)
        db.commit()
        db.refresh(live)

    request.session["live_session_id"] = live.id

    return live


def _build_live_prompt(
    db: Session,
    live_id: int,
    settings: UserSettings,
    current_text: str,
    user_id: int,
    output_language: Optional[str] = None
) -> str:
    # Keep Live AI conversational while still using recent Live history.
    budget_chars = max(
        8_000,
        min(128_000, int(settings.context_limit) * 1000 * 4)
    )

    messages = (
        db.query(LiveMessage)
        .filter(
            LiveMessage.session_id == live_id,
            LiveMessage.user_id == user_id
        )
        .order_by(LiveMessage.id.desc())
        .limit(100)
        .all()
    )
    messages.reverse()

    history_parts = []
    used = 0

    for msg in messages:
        block = f"{msg.role.upper()}: {msg.content}\n"
        if used + len(block) > budget_chars:
            continue
        history_parts.append(block)
        used += len(block)

    language_code = output_language if output_language in LIVE_OUTPUT_LANGUAGES else settings.output_language
    language_name = LIVE_OUTPUT_LANGUAGES.get(language_code, "English (India)")

    return (
        f"SYSTEM DIRECTIVE:\n{settings.system_prompt.strip()}\n\n"
        "You are MAXIMUS, a fast conversational voice assistant. "
        "Answer naturally and concisely. "
        "Usually one or two short sentences. "
        "No markdown or bullet points. "
        f"IMPORTANT LANGUAGE RULE: Write your entire answer in {language_name} ({language_code}). "
        "Do not answer in English unless English (India) is the selected output language. "
        "Do not translate the user's message unless the user asks for a translation. "
        "The spoken input language and answer text language may be different.\n\n"
        "CONVERSATION HISTORY:\n"
        + ("".join(history_parts) if history_parts else "No previous conversation.")
        + f"\nUSER: {current_text}\nASSISTANT:"
    )



def _translate_live_speech_text(
    text_value: str,
    target_language: str,
    settings: UserSettings,
    provider: Optional[Provider] = None,
    provider_model_id: Optional[str] = None,
) -> str:
    """Create a spoken-language copy without changing the saved/displayed reply."""
    source = (text_value or "").strip()
    if not source:
        return source

    language_name = LIVE_OUTPUT_LANGUAGES.get(target_language)
    if not language_name:
        return source

    translation_prompt = (
        f"Translate the following assistant reply into {language_name} ({target_language}) "
        "for natural spoken audio. Preserve the meaning and tone. "
        "Return only the translated text, with no notes, labels, markdown, or quotation marks.\n\n"
        f"{source}"
    )

    if provider is not None and provider_model_id:
        try:
            translated = _provider_chat(
                provider,
                provider_model_id,
                translation_prompt,
                settings,
                provider_messages=[
                    {
                        "role": "system",
                        "content": (
                            "You are a precise translation engine for spoken assistant audio. "
                            "Return only the translation requested by the user."
                        ),
                    },
                    {"role": "user", "content": translation_prompt},
                ],
            )
            if translated and translated.strip():
                return translated.strip()
        except Exception as provider_translation_error:
            # A BYOK model can fail translation independently of the main turn.
            # Fall back to MAXIMUS Flash so the requested spoken language still works.
            print("BYOK speech translation fallback:", provider_translation_error)

    response = client.models.generate_content(
        model=_model_name(settings),
        contents=translation_prompt,
        config=genai.types.GenerateContentConfig(temperature=0.1),
    )
    translated = (response.text or "").strip() if response else ""
    return translated or source


# =====================================================
# CREATE NEW LIVE SESSION
# =====================================================

@app.post("/api/live/new")
def create_live_session(
    payload: NewLiveSessionRequest,
    request: Request,
    db: Session = Depends(get_db)
):
    user_id = _get_current_user_id(request)

    title = (
        (payload.title or "New Live Session").strip()[:200]
        or "New Live Session"
    )

    # A manually-created Live session replaces an unused placeholder Live
    # session instead of leaving another 0-message entry behind.
    _delete_unused_live_sessions(db, user_id)

    live = LiveSession(
        user_id=user_id,
        title=title
    )

    db.add(live)
    db.commit()
    db.refresh(live)

    request.session["live_session_id"] = live.id

    return {
        "id": live.id,
        "title": live.title,
        "mode": "live"
    }


# =====================================================
# LIVE AI
# =====================================================

@app.post("/api/live-ask")
def live_ask(
    prompt: LiveAskRequest,
    request: Request,
    db: Session = Depends(get_db)
):
    user_id = _get_current_user_id(request)
    user_text = prompt.text.strip()

    if not user_text:
        raise HTTPException(
            status_code=400,
            detail="Message cannot be empty"
        )

    try:
        live = _get_or_create_current_live_session(request, db)

        if live.title == "New Live Session":
            live.title = (
                user_text[:60]
                + ("..." if len(user_text) > 60 else "")
            )

        # Save metadata now; persist a complete turn only after generation.
        # This also keeps the current utterance out of history until it is
        # appended once by the prompt builder below.
        db.commit()

        settings = _get_settings(db, user_id)

        output_language = prompt.output_language or settings.output_language
        if output_language not in LIVE_OUTPUT_LANGUAGES:
            raise HTTPException(
                status_code=400,
                detail="Unsupported output language."
            )

        # Keep the user's persisted preference synchronized with the language
        # explicitly selected on the Live page. This also makes direct API
        # calls behave exactly like the website.
        if prompt.output_language and settings.output_language != output_language:
            settings.output_language = output_language
            db.commit()

        selected_model = str(settings.model or "flash")
        speech_provider = None
        speech_provider_model_id = None

        chat_prompt = _build_live_prompt(
            db,
            live.id,
            settings,
            user_text,
            user_id,
            output_language
        )

        if selected_model.startswith("provider:"):
            try:
                _, provider_id_text, model_id = selected_model.split(":", 2)
                provider_id = int(provider_id_text)
            except (ValueError, TypeError):
                raise HTTPException(status_code=400, detail="Invalid BYOK model selection.")

            provider = (
                db.query(Provider)
                .filter(
                    Provider.id == provider_id,
                    Provider.user_id == user_id,
                    Provider.is_active == True,
                    Provider.is_valid == True,
                )
                .first()
            )
            if not provider:
                raise HTTPException(status_code=404, detail="Selected BYOK provider is not available.")

            speech_provider = provider
            speech_provider_model_id = model_id

            try:
                stored_models = json.loads(provider.models_json or "[]")
                if not isinstance(stored_models, list):
                    stored_models = []
            except Exception:
                stored_models = []
            known_models = {str(m) for m in stored_models if m}
            if provider.model_id:
                known_models.add(str(provider.model_id))
            if known_models and model_id not in known_models:
                raise HTTPException(
                    status_code=400,
                    detail=f'Model "{model_id}" is no longer registered for this provider. Refresh Models & Providers.'
                )

            recent_messages = (
                db.query(LiveMessage)
                .filter(LiveMessage.session_id == live.id)
                .order_by(LiveMessage.id.desc())
                .limit(8)
                .all()
            )
            recent_messages.reverse()
            provider_messages = [
                {"role": "system", "content": chat_prompt[:12000]}
            ]
            for msg in recent_messages:
                content = str(msg.content or "").strip()
                if not content:
                    continue
                role = "assistant" if msg.role == "assistant" else "user"
                provider_messages.append({"role": role, "content": content[:4000]})
            provider_messages.append({"role": "user", "content": user_text[:4000]})
            reply = _provider_chat(
                provider,
                model_id,
                user_text,
                settings,
                provider_messages=provider_messages,
            )
        else:
            response = None
            for attempt in range(3):
                try:
                    response = client.models.generate_content(
                        model=_model_name(settings),
                        contents=chat_prompt,
                        config=_generation_config(settings)
                    )
                    break
                except Exception as e:
                    if "503" in str(e) and attempt < 2:
                        time.sleep(2)
                        continue
                    raise

            reply = (
                response.text.strip()
                if response and response.text
                else "I couldn't generate a response."
            )

        speech_language = prompt.speech_language or output_language
        if speech_language not in LIVE_OUTPUT_LANGUAGES:
            raise HTTPException(
                status_code=400,
                detail="Unsupported speech language."
            )

        # Keep the transcript/display reply in output_language. Only create a
        # translated copy for TTS when the requested spoken language differs.
        speech_text = reply
        if speech_language != output_language:
            try:
                speech_text = _translate_live_speech_text(
                    reply,
                    speech_language,
                    settings,
                    provider=speech_provider,
                    provider_model_id=speech_provider_model_id,
                )
            except Exception as speech_error:
                # Do not fail the whole Live turn if speech translation fails.
                print("Live speech translation warning:", speech_error)
                speech_text = reply

        # Save both sides together so a failed live request can be retried.
        db.add(LiveMessage(user_id=user_id, session_id=live.id, role="user", content=user_text))
        # Save the displayed assistant response separately from normal Chat.
        db.add(
            LiveMessage(
                user_id=user_id,
                session_id=live.id,
                role="assistant",
                content=reply
            )
        )

        live.updated_at = datetime.utcnow()
        db.commit()

        return {
            "reply": reply,
            "speech_text": speech_text,
            "output_language": output_language,
            "speech_language": speech_language,
            "live_session_id": live.id,
            "title": live.title
        }

    except HTTPException:
        db.rollback()
        raise

    except Exception as e:
        db.rollback()
        print("Live AI error:", e)

        raise HTTPException(
            status_code=503,
            detail="JARVIS is temporarily unavailable. Please try again."
        )


# =====================================================
# LIVE AI SESSIONS — LIST / OPEN / DELETE
# =====================================================

@app.get("/api/live/list")
def list_live_sessions(
    request: Request,
    db: Session = Depends(get_db)
):
    user_id = _get_current_user_id(request)

    # Remove abandoned 0-message Live sessions while preserving the current
    # empty Live draft for this browser session.
    current_live_id = request.session.get("live_session_id")
    _delete_unused_live_sessions(db, user_id, keep_id=current_live_id)

    sessions = (
        db.query(LiveSession)
        .filter(LiveSession.user_id == user_id)
        .order_by(
            LiveSession.updated_at.desc(),
            LiveSession.id.desc()
        )
        .all()
    )
    pinned_rows = db.execute(
        text("SELECT id, pinned FROM live_sessions WHERE user_id = :uid"),
        {"uid": user_id}
    ).fetchall()
    pinned_map = {int(row[0]): bool(row[1]) for row in pinned_rows}

    return [
        {
            "id": session.id,
            "title": session.title,
            "created_at": (
                session.created_at.isoformat()
                if session.created_at else None
            ),
            "updated_at": (
                session.updated_at.isoformat()
                if session.updated_at else None
            ),
            "message_count": len(session.messages),
            "pinned": pinned_map.get(session.id, False)
        }
        for session in sessions
        if len(session.messages) > 0
    ]


@app.get("/api/live/{live_session_id:int}")
def get_live_session(
    live_session_id: int,
    request: Request,
    db: Session = Depends(get_db)
):
    user_id = _get_current_user_id(request)

    live = (
        db.query(LiveSession)
        .filter(
            LiveSession.id == live_session_id,
            LiveSession.user_id == user_id
        )
        .first()
    )

    if not live:
        raise HTTPException(
            status_code=404,
            detail="Live AI session not found"
        )

    request.session["live_session_id"] = live.id

    return {
        "id": live.id,
        "title": live.title,
        "messages": [
            {
                "id": msg.id,
                "role": msg.role,
                "content": msg.content,
                "created_at": (
                    msg.created_at.isoformat()
                    if msg.created_at else None
                )
            }
            for msg in live.messages
        ]
    }


@app.delete("/api/live/{live_session_id:int}")
def delete_live_session(
    live_session_id: int,
    request: Request,
    db: Session = Depends(get_db)
):
    user_id = _get_current_user_id(request)

    live = (
        db.query(LiveSession)
        .filter(
            LiveSession.id == live_session_id,
            LiveSession.user_id == user_id
        )
        .first()
    )

    if not live:
        raise HTTPException(
            status_code=404,
            detail="Live AI session not found"
        )

    was_current = (
        request.session.get("live_session_id")
        == live.id
    )

    # Explicitly delete messages first so this also works on SQLite
    # without relying on database-level ON DELETE CASCADE.
    db.query(LiveMessage).filter(
        LiveMessage.session_id == live.id,
        LiveMessage.user_id == user_id
    ).delete(synchronize_session=False)

    db.delete(live)
    db.commit()

    if was_current:
        request.session.pop("live_session_id", None)

    return {
        "message": "Live AI session deleted",
        "id": live_session_id
    }


@app.delete("/api/live/history")
def clear_live_history(
    request: Request,
    db: Session = Depends(get_db)
):
    user_id = _get_current_user_id(request)

    db.query(LiveMessage).filter(
        LiveMessage.user_id == user_id
    ).delete(synchronize_session=False)

    db.query(LiveSession).filter(
        LiveSession.user_id == user_id
    ).delete(synchronize_session=False)

    db.commit()

    request.session.pop("live_session_id", None)

    return {
        "message": "Live AI history cleared"
    }


# =====================================================
# IMAGE + TEXT AI
# =====================================================

@app.post("/ask-image")
def ask_image(
    request_data: ImageRequest,
    request: Request,
    db: Session = Depends(get_db)
):

    user_id = _get_current_user_id(request)
    settings = _get_settings(db, user_id)
    selected_model = request_data.model or settings.model
    if selected_model.startswith("provider:"):
        raise HTTPException(status_code=400, detail="Image analysis currently requires the built-in Flash model. Select Flash before attaching an image or sharing your screen.")
    if request_data.mime_type not in {"image/png", "image/jpeg", "image/webp"}:
        raise HTTPException(status_code=400, detail="Use a PNG, JPEG or WebP image.")
    if len(request_data.image) > 14_000_000:
        raise HTTPException(status_code=413, detail="Image is too large. Use an image smaller than 10 MB.")
    live = None
    if request_data.live_session_id is not None:
        live = db.query(LiveSession).filter(LiveSession.id == request_data.live_session_id, LiveSession.user_id == user_id).first()
        if not live:
            raise HTTPException(status_code=404, detail="Live conversation not found.")

    try:

        try:
            image_bytes = base64.b64decode(request_data.image, validate=True)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid image encoding.")
        if not image_bytes:
            raise HTTPException(status_code=400, detail="Image is empty.")

        image_part = genai.types.Part.from_bytes(
            data=image_bytes,
            mime_type=request_data.mime_type
        )

        settings = _get_settings(db, user_id) if user_id else None
        output_language = request_data.output_language or (settings.output_language if settings else "en-IN")
        if output_language not in LIVE_OUTPUT_LANGUAGES:
            output_language = "en-IN"
        language_name = LIVE_OUTPUT_LANGUAGES[output_language]

        image_prompt = request_data.text or "Describe this image."
        language_instruction = (
            f"IMPORTANT LANGUAGE RULE: Answer entirely in {language_name} ({output_language}). "
            "Do not answer in English unless English (India) is selected. "
            "Keep the answer natural and concise for spoken conversation."
        )
        if settings and settings.system_prompt:
            image_prompt = (
                f"SYSTEM DIRECTIVE:\n{settings.system_prompt}\n\n"
                f"{language_instruction}\n\n"
                f"USER REQUEST:\n{request_data.text}"
            )
        else:
            image_prompt = f"{language_instruction}\n\nUSER REQUEST:\n{request_data.text}"

        if settings.auto_ocr:
            image_prompt += "\nRead and quote relevant visible text when useful."
        if settings.spatial_grounding:
            image_prompt += "\nDescribe the relative positions of relevant visible elements when useful. Do not invent coordinates."
        response = client.models.generate_content(
            model=_model_name(settings) if settings else DEFAULT_MODEL,
            contents=[
                image_prompt,
                image_part
            ],
            config=_generation_config(settings) if settings else None
        )

        reply = (
            response.text
            or
            "Could not analyze image."
        )

        if live:
            db.add_all([
                LiveMessage(user_id=user_id, session_id=live.id, role="user", content="[Screen] " + (request_data.text or "Analyze screen")),
                LiveMessage(user_id=user_id, session_id=live.id, role="assistant", content=reply),
            ])
            live.updated_at = datetime.utcnow()
            db.commit()
            return {"reply": reply, "live_session_id": live.id}

        if user_id:

            chat = _get_or_create_current_chat(
                request,
                db
            )

            title_text = (
                (request_data.text or "Image analysis").strip()
            )

            if (
                chat.title == "New Chat"
                and title_text
            ):

                chat.title = (
                    title_text[:60]
                    +
                    (
                        "..."
                        if len(title_text) > 60
                        else ""
                    )
                )

            db.add(
                ChatMessage(
                    user_id=user_id,
                    session_id=chat.id,
                    role="user",
                    content=(
                        f"[Image] "
                        f"{request_data.text}"
                    )
                )
            )

            db.add(
                ChatMessage(
                    user_id=user_id,
                    session_id=chat.id,
                    role="assistant",
                    content=reply
                )
            )

            chat.updated_at = datetime.utcnow()

            db.commit()

        return {
            "reply": reply,
            "chat_id": request.session.get(
                "chat_id"
            )
        }

    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        raise HTTPException(status_code=502, detail="Image analysis failed. Please try again.")


# =====================================================
# CHAT HISTORY
# =====================================================

@app.get("/api/chat/history")
def get_chat_history(
    request: Request,
    db: Session = Depends(get_db)
):

    user_id = request.session.get(
        "user_id"
    )

    if not user_id:

        raise HTTPException(
            status_code=401,
            detail="Please login to view chat history"
        )

    chat_id = request.session.get(
        "chat_id"
    )

    if chat_id:

        messages = (
            db.query(ChatMessage)
            .filter(
                ChatMessage.user_id == user_id,
                ChatMessage.session_id == chat_id
            )
            .order_by(
                ChatMessage.id.asc()
            )
            .all()
        )

    else:

        messages = (
            db.query(ChatMessage)
            .filter(
                ChatMessage.user_id == user_id
            )
            .order_by(
                ChatMessage.created_at.asc(),
                ChatMessage.id.asc()
            )
            .all()
        )

    return [
        {
            "id": msg.id,
            "role": msg.role,
            "content": msg.content,
            "created_at": msg.created_at.isoformat(),
            "pinned": msg.pinned
        }
        for msg in messages
    ]


# =====================================================
# PIN CHAT MESSAGE
# =====================================================

@app.post("/api/chat/pin")
def pin_chat(
    data: dict,
    request: Request,
    db: Session = Depends(get_db)
):

    user_id = request.session.get(
        "user_id"
    )

    if not user_id:

        raise HTTPException(
            status_code=401,
            detail="Not authenticated"
        )

    message = (
        db.query(ChatMessage)
        .filter(
            ChatMessage.id == data.get("message_id"),
            ChatMessage.user_id == user_id
        )
        .first()
    )

    if not message:

        raise HTTPException(
            status_code=404,
            detail="Chat message not found"
        )

    message.pinned = True

    db.commit()

    return {
        "message": "Chat pinned",
        "id": message.id,
        "pinned": True
    }


# =====================================================
# UNPIN CHAT MESSAGE
# =====================================================

@app.post("/api/chat/unpin")
def unpin_chat(
    data: dict,
    request: Request,
    db: Session = Depends(get_db)
):

    user_id = request.session.get(
        "user_id"
    )

    if not user_id:

        raise HTTPException(
            status_code=401,
            detail="Not authenticated"
        )

    message = (
        db.query(ChatMessage)
        .filter(
            ChatMessage.id == data.get("message_id"),
            ChatMessage.user_id == user_id
        )
        .first()
    )

    if not message:

        raise HTTPException(
            status_code=404,
            detail="Chat message not found"
        )

    message.pinned = False

    db.commit()

    return {
        "message": "Chat unpinned",
        "id": message.id,
        "pinned": False
    }


# =====================================================
# PIN / UNPIN CONVERSATIONS
# =====================================================

@app.post("/api/chat/{chat_id}/pin")
def pin_chat_conversation(chat_id: int, request: Request, db: Session = Depends(get_db)):
    user_id = _get_current_user_id(request)
    result = db.execute(
        text("UPDATE chat_sessions SET pinned = TRUE WHERE id = :id AND user_id = :uid"),
        {"id": chat_id, "uid": user_id}
    )
    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="Chat conversation not found")
    db.commit()
    return {"message": "Conversation pinned", "id": chat_id, "pinned": True}


@app.post("/api/chat/{chat_id}/unpin")
def unpin_chat_conversation(chat_id: int, request: Request, db: Session = Depends(get_db)):
    user_id = _get_current_user_id(request)
    result = db.execute(
        text("UPDATE chat_sessions SET pinned = FALSE WHERE id = :id AND user_id = :uid"),
        {"id": chat_id, "uid": user_id}
    )
    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="Chat conversation not found")
    db.commit()
    return {"message": "Conversation unpinned", "id": chat_id, "pinned": False}


@app.post("/api/live/{live_session_id}/pin")
def pin_live_conversation(live_session_id: int, request: Request, db: Session = Depends(get_db)):
    user_id = _get_current_user_id(request)
    result = db.execute(
        text("UPDATE live_sessions SET pinned = TRUE WHERE id = :id AND user_id = :uid"),
        {"id": live_session_id, "uid": user_id}
    )
    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="Live AI conversation not found")
    db.commit()
    return {"message": "Conversation pinned", "id": live_session_id, "pinned": True}


@app.post("/api/live/{live_session_id}/unpin")
def unpin_live_conversation(live_session_id: int, request: Request, db: Session = Depends(get_db)):
    user_id = _get_current_user_id(request)
    result = db.execute(
        text("UPDATE live_sessions SET pinned = FALSE WHERE id = :id AND user_id = :uid"),
        {"id": live_session_id, "uid": user_id}
    )
    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="Live AI conversation not found")
    db.commit()
    return {"message": "Conversation unpinned", "id": live_session_id, "pinned": False}


# =====================================================
# CLEAR CHAT HISTORY
# =====================================================

@app.delete("/api/chat/history")
def clear_chat_history(
    request: Request,
    db: Session = Depends(get_db)
):

    user_id = request.session.get(
        "user_id"
    )

    if not user_id:

        raise HTTPException(
            status_code=401,
            detail="Not authenticated"
        )

    db.query(ChatMessage).filter(
        ChatMessage.user_id == user_id
    ).delete(
        synchronize_session=False
    )

    db.query(ChatSession).filter(
        ChatSession.user_id == user_id
    ).delete(
        synchronize_session=False
    )

    db.commit()

    request.session.pop(
        "chat_id",
        None
    )

    return {
        "message": "Chat history cleared"
    }


# =====================================================
# TTS — GET AVAILABLE NEURAL VOICES
# =====================================================

@app.get("/api/tts/voices")
async def get_tts_voices():

    try:

        voices = await edge_tts.list_voices()

        selected = []

        for voice in voices:

            name = voice.get(
                "ShortName",
                ""
            )

            locale = voice.get(
                "Locale",
                ""
            )

            if (
                locale in {"en-IN", "en-US", "en-GB", "en-AU", "ru-RU", "fr-FR", "ja-JP", "hi-IN", "te-IN", "ta-IN", "kn-IN", "ml-IN"}
                and
                name.endswith("Neural")
            ):

                selected.append(
                    {
                        "name": name,
                        "display_name": voice.get(
                            "FriendlyName",
                            name
                        ),
                        "gender": voice.get(
                            "Gender",
                            "Unknown"
                        ),
                        "locale": locale
                    }
                )

        return selected

    except Exception as e:

        print(
            "VOICE LIST ERROR:",
            e
        )

        return {
            "error": str(e)
        }


# =====================================================
# TTS — GENERATE SPEECH
# =====================================================

@app.post("/api/tts")
async def text_to_speech(
    request: Request,
    db: Session = Depends(get_db)
):

    _get_current_user_id(request)
    output_path = None
    try:

        data = await request.json()

        text = data.get(
            "text",
            ""
        ).strip()

        voice = data.get("voice")
        output_language = data.get("output_language")
        if not voice:
            user_id = request.session.get("user_id")
            if user_id:
                voice = _get_settings(db, user_id).voice
            else:
                voice = DEFAULT_VOICE

        # Never let a stale/mismatched voice override the selected response
        # language. Edge TTS voices are language-specific.
        if output_language in LIVE_OUTPUT_LANGUAGES:
            voice_language = str(voice or "").split("-", 2)
            current_voice_lang = "-".join(voice_language[:2]) if len(voice_language) >= 2 else ""
            if current_voice_lang != output_language:
                language_voices = {
                    "en-IN": "en-IN-NeerjaNeural",
                    "ru-RU": "ru-RU-SvetlanaNeural",
                    "fr-FR": "fr-FR-DeniseNeural",
                    "ja-JP": "ja-JP-NanamiNeural",
                    "hi-IN": "hi-IN-SwaraNeural",
                    "te-IN": "te-IN-ShrutiNeural",
                    "ta-IN": "ta-IN-PallaviNeural",
                    "kn-IN": "kn-IN-SapnaNeural",
                    "ml-IN": "ml-IN-SobhanaNeural",
                }
                voice = language_voices.get(output_language, voice)

        if not text:

            raise HTTPException(
                status_code=400,
                detail="No text provided"
            )
        if len(text) > 20000:
            raise HTTPException(status_code=413, detail="Speech text must be at most 20,000 characters.")

        filename = (
            f"jarvis_"
            f"{uuid.uuid4().hex}.mp3"
        )

        output_path = os.path.join(
            tempfile.gettempdir(),
            filename
        )

        communicate = edge_tts.Communicate(
            text,
            voice
        )

        await communicate.save(
            output_path
        )

        return FileResponse(
            output_path,
            media_type="audio/mpeg",
            filename=filename,
            background=BackgroundTask(os.remove, output_path)
        )

    except HTTPException:
        raise

    except Exception as e:
        if output_path and os.path.exists(output_path):
            os.remove(output_path)
        print(
            "TTS ERROR:",
            e
        )

        raise HTTPException(
            status_code=500,
            detail="Speech generation failed. Please try again."
        )


# =====================================================
# OPEN BROWSER
# =====================================================

def open_browser():

    webbrowser.open(
        "http://127.0.0.1:8000"
    )


# =====================================================
# START SERVER
# =====================================================

if __name__ == "__main__":

    threading.Timer(
        1.5,
        open_browser
    ).start()

    uvicorn.run(
        app,
        host="0.0.0.0",
        port=8000
    )
