import os

from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker


# Load backend/.env when running locally. In deployment, the host's
# environment variables take precedence.
load_dotenv()

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOCAL_DATABASE_URL = f"sqlite:///{os.path.join(BASE_DIR, 'database.db')}"

DATABASE_URL = (os.getenv("DATABASE_URL") or LOCAL_DATABASE_URL).strip()

# Neon normally provides postgresql://... . SQLAlchemy + psycopg v3
# works best with the explicit postgresql+psycopg:// driver prefix.
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = "postgresql+psycopg://" + DATABASE_URL[len("postgres://"):]
elif DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = "postgresql+psycopg://" + DATABASE_URL[len("postgresql://"):]

engine_kwargs = {"pool_pre_ping": True}

if DATABASE_URL.startswith("sqlite"):
    engine_kwargs["connect_args"] = {"check_same_thread": False}

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
