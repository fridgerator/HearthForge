"""
Simple user authentication for the web server.

Storage: ~/.hearthforge/users.json
Passwords: bcrypt hashed
Sessions: JWT tokens (stateless, no server-side session store)

This is deliberately simple for a home lab setup.
For production you'd swap this for a real auth provider.
"""

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

import bcrypt
import jwt

from config import AUTH_DB_PATH, JWT_SECRET, JWT_EXPIRY_HOURS

logger = logging.getLogger(__name__)


class AuthManager:
    def __init__(self):
        self.db_path = AUTH_DB_PATH
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._users: dict[str, dict] = {}
        self._load()

    def _load(self) -> None:
        if self.db_path.exists():
            try:
                self._users = json.loads(self.db_path.read_text())
            except (json.JSONDecodeError, Exception) as e:
                logger.warning(f"Failed to load user DB, starting fresh: {e}")
                self._users = {}
        else:
            self._users = {}

    def _save(self) -> None:
        self.db_path.write_text(json.dumps(self._users, indent=2))

    def create_user(self, username: str, password: str, display_name: str | None = None) -> bool:
        """
        Create a new user. Returns False if username already exists.
        """
        if username in self._users:
            return False

        hashed = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt())
        self._users[username] = {
            "password_hash": hashed.decode("utf-8"),
            "display_name": display_name or username,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        self._save()
        logger.info(f"Created user: {username}")
        return True

    def verify_password(self, username: str, password: str) -> bool:
        """Check if username/password combination is valid."""
        user = self._users.get(username)
        if not user:
            return False
        return bcrypt.checkpw(
            password.encode("utf-8"),
            user["password_hash"].encode("utf-8"),
        )

    def create_token(self, username: str) -> str:
        """Create a JWT token for an authenticated user."""
        payload = {
            "sub": username,
            "display_name": self._users[username].get("display_name", username),
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
            if payload.get("sub") not in self._users:
                return None
            return payload
        except jwt.ExpiredSignatureError:
            return None
        except jwt.InvalidTokenError:
            return None

    def get_user(self, username: str) -> dict | None:
        """Get user info (without password hash)."""
        user = self._users.get(username)
        if not user:
            return None
        return {
            "username": username,
            "display_name": user.get("display_name", username),
            "created_at": user.get("created_at"),
        }

    def list_users(self) -> list[dict]:
        """List all users (without password hashes)."""
        return [
            {
                "username": uname,
                "display_name": data.get("display_name", uname),
                "created_at": data.get("created_at"),
            }
            for uname, data in self._users.items()
        ]

    def user_exists(self, username: str) -> bool:
        return username in self._users

    def has_users(self) -> bool:
        return len(self._users) > 0
