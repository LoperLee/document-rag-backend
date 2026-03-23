import hashlib

def get_password_hash(password: str) -> str:
    """Returns the SHA-256 hash of the given password."""
    return hashlib.sha256(password.encode('utf-8')).hexdigest()

def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verifies a plain password against the hashed password."""
    return get_password_hash(plain_password) == hashed_password
