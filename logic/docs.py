"""Content hashing for submitted files."""
import hashlib
from pathlib import Path


def file_sha256(path):
    """SHA-256 of the file's bytes, or None if it cannot be read."""
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError:
        return None
