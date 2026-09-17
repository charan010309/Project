import os
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker
from sqlalchemy.pool import NullPool


# Load backend/.env when running locally. In deployment, the host's
# environment variables take precedence.
if not os.getenv("VERCEL"):
    load_dotenv(Path(__file__).with_name(".env"))

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOCAL_DATABASE_PATH = Path(BASE_DIR).parent / "database" / "maximus.db"
LOCAL_DATABASE_URL = "sqlite:///" + LOCAL_DATABASE_PATH.as_posix()

DATABASE_URL = (os.getenv("DATABASE_URL") or LOCAL_DATABASE_URL).strip()
if os.getenv("VERCEL") and not DATABASE_URL.startswith(("postgres://", "postgresql://", "postgresql+psycopg://")):
    raise RuntimeError("Set DATABASE_URL to a hosted PostgreSQL connection in Vercel. Local SQLite is for development only.")

# Neon normally provides postgresql://... . SQLAlchemy + psycopg v3
# works best with the explicit postgresql+psycopg:// driver prefix.
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = "postgresql+psycopg://" + DATABASE_URL[len("postgres://"):]
elif DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = "postgresql+psycopg://" + DATABASE_URL[len("postgresql://"):]

engine_kwargs = {"pool_pre_ping": True}

if DATABASE_URL.startswith("sqlite"):
    engine_kwargs["connect_args"] = {"check_same_thread": False}
    if DATABASE_URL == LOCAL_DATABASE_URL:
        LOCAL_DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
else:
    # Use the database provider's pooled URL instead of holding a connection
    # pool in each short-lived Vercel function instance.
    engine_kwargs["poolclass"] = NullPool
    engine_kwargs["connect_args"] = {"connect_timeout": 10}

engine = create_engine(DATABASE_URL, **engine_kwargs)

SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine
)

Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


print(
    "✅ Database: Neon/PostgreSQL"
    if engine.dialect.name == "postgresql"
    else "✅ Database: local SQLite"
)
