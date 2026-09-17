"""Shared, expiring one-time challenges for multi-instance deployments."""
import hashlib
import hmac
import json
import os
import time

from sqlalchemy import Column, String, Text, Float, delete
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

if __package__:
    from .database import Base, SessionLocal
else:
    from database import Base, SessionLocal


class AuthChallenge(Base):
    __tablename__ = "auth_challenges"
    key = Column(String(64), primary_key=True)
    verifier = Column(String(64), nullable=True)
    payload = Column(Text, nullable=False)
    expires_at = Column(Float, nullable=False, index=True)
    consumed_at = Column(Float, nullable=True)


def _key(namespace, key):
    return hashlib.sha256(f"{namespace}:{key}".encode()).hexdigest()


def _verifier(key, value):
    secret = os.environ.get("SESSION_SECRET", "")
    if not secret:
        raise RuntimeError("SESSION_SECRET must be configured.")
    return hmac.new(secret.encode(), f"{key}:{value}".encode(), hashlib.sha256).hexdigest()


def store_challenge(namespace, key, payload, ttl_seconds, verification=None):
    identifier = _key(namespace, key)
    with SessionLocal() as db:
        # Retain completed transfers briefly so the originating device can poll.
        db.execute(delete(AuthChallenge).where(AuthChallenge.expires_at < time.time() - 300))
        values = dict(key=identifier, payload=json.dumps(payload), expires_at=time.time()+ttl_seconds,
                      verifier=_verifier(identifier, verification) if verification is not None else None,
                      consumed_at=None)
        insert = pg_insert if db.bind.dialect.name == "postgresql" else sqlite_insert
        statement = insert(AuthChallenge).values(**values)
        db.execute(statement.on_conflict_do_update(index_elements=["key"], set_={k:v for k,v in values.items() if k != "key"}))
        db.commit()


def read_challenge(namespace, key):
    with SessionLocal() as db:
        row = db.get(AuthChallenge, _key(namespace, key))
        if row is None:
            return None
        return {"data": json.loads(row.payload), "expires": row.expires_at, "used": row.consumed_at is not None}


def consume_challenge(namespace, key, verification=None):
    """One conditional UPDATE: exactly one worker can claim a live challenge."""
    identifier = _key(namespace, key)
    with SessionLocal() as db:
        query = db.query(AuthChallenge).filter(AuthChallenge.key == identifier,
                    AuthChallenge.expires_at > time.time(), AuthChallenge.consumed_at.is_(None))
        if verification is not None:
            query = query.filter(AuthChallenge.verifier == _verifier(identifier, verification))
        if query.update({AuthChallenge.consumed_at: time.time()}, synchronize_session=False) != 1:
            db.rollback()
            return None
        row = db.get(AuthChallenge, identifier)
        payload = json.loads(row.payload)
        db.commit()
        return payload
