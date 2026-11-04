import streamlit as st

from ui import app_state
from ui.data import reset_demo

st.set_page_config(page_title="VendorFlow AI", page_icon=":material/fact_check:", layout="wide")
app_state.init_session()

with st.sidebar:
    st.markdown("### VendorFlow AI")
    st.caption("Vendor onboarding, from submission to approval")
    st.toggle(
        "Use Gemini AI", key="use_ai", disabled=not app_state.ai_available(),
        help="On: documents are read by Gemini (scans via vision). Off: the offline parser reads text PDFs only.",
    )
    st.slider("Slow down stages (seconds)", 0.0, 1.0, step=0.1, key="step_delay",
              help="A short pause between pipeline events so each stage is easy to follow on screen.")
    with st.popover("Reset demo data", icon=":material/restart_alt:"):
        st.warning("This deletes every run, finding, review and onboarded vendor, then restores the seed vendors.")
        if st.button("Delete everything", type="primary", key="reset_confirm"):
            with app_state.factory()() as session:
                reset_demo(session)
            st.session_state.pop("last_run_id", None)
            st.toast("Demo data reset.", icon=":material/check_circle:")
            st.rerun()

page = st.navigation(
    [
        st.Page("app_pages/run.py", title="Run onboarding", icon=":material/play_circle:", default=True),
        st.Page("app_pages/dashboard.py", title="Audit dashboard", icon=":material/dashboard:"),
        st.Page("app_pages/review.py", title="Review queue", icon=":material/rate_review:"),
    ],
    position="top",
)
st.title(page.title, icon=page.icon)
page.run()
