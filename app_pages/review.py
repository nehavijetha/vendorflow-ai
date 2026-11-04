import streamlit as st

from db.reviews import add_review
from logic.review import ReviewError
from ui import app_state, components
from ui.data import review_queue, reviews_dataframe

factory = app_state.factory()
DECISIONS = {"approve": "Approve", "reject": "Reject", "request_info": "Request more information"}

with factory() as session:
    queue = review_queue(session)
    history = reviews_dataframe(session)

st.metric("Awaiting review", len(queue), border=True)
st.caption("Runs that stopped at PENDING with a discrepancy a person should judge. A review is stored beside the "
           "system result and never changes it.")

if not queue:
    st.success(title="Queue is empty", body="Nothing needs a human decision right now.")

for detail in queue:
    run_id = detail["run_id"]
    company = detail["input"].get("company_name") or "Unnamed vendor"
    with st.container(border=True):
        top, badge = st.columns([5, 1], vertical_alignment="center")
        top.subheader(company)
        top.caption(f"Run `{run_id}`" + (f" · resubmission of `{detail['parent_run_id']}`" if detail["parent_run_id"] else ""))
        with badge:
            components.status_badge(detail["system_status"])
        for finding in detail["findings"]:
            if finding["result"] not in ("pass", "skipped"):
                st.markdown(f"- **{finding['check']}** ({finding['result']}): {finding['evidence']}")
        if any(f["result"] == "error" for f in detail["findings"]):
            st.info("This run has a technical error, so it cannot be approved. Reject it or re-run the submission.")
        with st.form(f"review_form_{run_id}"):
            who = st.text_input("Your name", value=st.session_state.get("reviewer_name", ""), key=f"who_{run_id}")
            choice = st.segmented_control("Decision", list(DECISIONS), format_func=DECISIONS.get,
                                          key=f"decision_{run_id}", selection_mode="single")
            note = st.text_area("Note (required)", key=f"note_{run_id}", height=80,
                                placeholder="What did you check, and why this decision?")
            submitted = st.form_submit_button("Record decision", icon=":material/gavel:")
        if submitted:
            if not choice:
                st.error("Choose a decision.")
            else:
                try:
                    with factory() as session:
                        add_review(session, run_id, who, choice, note)
                except ReviewError as exc:
                    st.error(str(exc))
                else:
                    st.session_state["reviewer_name"] = who.strip()
                    st.toast(f"Recorded: {DECISIONS[choice]}", icon=":material/check_circle:")
                    st.rerun()

with st.container(border=True):
    st.subheader("Decision history")
    if history.empty:
        st.caption("No reviewer decisions yet.")
    else:
        st.dataframe(history, hide_index=True, alt="Reviewer decisions, newest first",
                     column_config={"Time": st.column_config.DatetimeColumn(format="YYYY-MM-DD HH:mm:ss"),
                                    "Note": st.column_config.TextColumn(width="large")})
