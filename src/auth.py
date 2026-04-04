"""
Simple user authentication for the web server.

Storage: SQLite (~/.hearthforge/hearthforge.db)
Passwords: bcrypt hashed
Sessions: JWT tokens (stateless, no server-side session store)

This is deliberately simple for a home lab setup.
For production you'd swap this for a real auth provider.
"""

import logging
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt

import database as db
from config import JWT_SECRET, JWT_EXPIRY_HOURS

logger = logging.getLogger(__name__)


class AuthManager:
    def __init__(self):
        pass  # No file loading needed — SQLite handles storage

    def create_user(self, username: str, password: str, display_name: str | None = None) -> bool:
        """
        Create a new user. Returns False if username already exists.
        """
        hashed = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt())
        created = db.create_user(
            username=username,
            password_hash=hashed.decode("utf-8"),
            display_name=display_name or username,
        )
        if created:
            logger.info(f"Created user: {username}")
        return created

    def verify_password(self, username: str, password: str) -> bool:
        """Check if username/password combination is valid."""
        user = db.get_user(username)
        if not user:
            return False
        return bcrypt.checkpw(
            password.encode("utf-8"),
            user["password_hash"].encode("utf-8"),
        )

    def create_token(self, username: str) -> str:
        """Create a JWT token for an authenticated user."""
        user = db.get_user(username)
        payload = {
            "sub": username,
            "display_name": user["display_name"] if user else username,
            "exp": datetime.now(timezone.utc) + timedelta(hours=JWT_EXPIRY_HOURS),
            "iat": datetime.now(timezone.utc),
        }
        return jwt.encode(payload, JWT_SECRET, algorithm="HS256")

    def verify_token(self, token: str) -> dict | None:
        """
        Verify a JWT token. Returns the payload if valid, None if not.
        """
        try:
            payload = jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
            # Verify user still exists
            if not db.get_user(payload.get("sub", "")):
                return None
            return payload
        except jwt.ExpiredSignatureError:
            return None
        except jwt.InvalidTokenError:
            return None

    def get_user(self, username: str) -> dict | None:
        """Get user info (without password hash)."""
        user = db.get_user(username)
        if not user:
            return None
        return {
            "username": username,
            "display_name": user.get("display_name", username),
            "created_at": user.get("created_at"),
        }

    def list_users(self) -> list[dict]:
        """List all users (without password hashes)."""
        return db.list_users()

    def user_exists(self, username: str) -> bool:
        return db.get_user(username) is not None

    def has_users(self) -> bool:
        return db.has_users()
