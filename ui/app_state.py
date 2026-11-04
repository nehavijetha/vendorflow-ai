"""Shared app resources and session defaults."""
import streamlit as st

from db.models import make_session_factory
from db.seed import seed_vendors
from logic import settings
from ui import presets

UPLOAD_DIR = presets.ROOT / "uploads"
CACHE_PATH = presets.ROOT / ".cache" / "extractions.json"


@st.cache_resource
def get_factory(db_path=""):
    factory = make_session_factory(f"sqlite:///{db_path}") if db_path else make_session_factory()
    with factory() as session:
        seed_vendors(session)
    return factory


def factory():
    return get_factory(settings.get("VENDORFLOW_DB") or "")


def ai_available():
    return bool(settings.get("GEMINI_API_KEY"))


def init_session():
    st.session_state.setdefault("use_ai", ai_available())
    st.session_state.setdefault("step_delay", 0.3)
    CACHE_PATH.parent.mkdir(exist_ok=True)
