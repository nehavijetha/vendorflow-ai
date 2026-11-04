import pandas as pd
import streamlit as st

from ui import app_state, components
from ui.data import flagged_by_check, kpis, run_detail, run_diff, runs_dataframe

factory = app_state.factory()
with factory() as session:
    runs = runs_dataframe(session)
    flagged = flagged_by_check(session)

if runs.empty:
    st.info("No runs yet. Go to Run onboarding and run a scenario; its history shows up here.")
    st.stop()

numbers = kpis(runs)
with st.container(horizontal=True):
    for label, value in numbers.items():
        st.metric(label, value, border=True)

charts_left, charts_right = st.columns(2)
with charts_left, st.container(border=True):
    st.markdown("**Runs by final status**")
    counts = pd.DataFrame({"Status": ["Approved", "Pending", "Rejected"],
                           "Runs": [numbers["Approved"], numbers["Pending"], numbers["Rejected"]]})
    st.bar_chart(counts, x="Status", y="Runs", alt="Number of runs by final status", height=220)
with charts_right, st.container(border=True):
    st.markdown("**Most frequently flagged checks**")
    if flagged.empty:
        st.caption("No check has been flagged yet.")
    else:
        st.bar_chart(flagged, x="Check", y="Flagged runs", horizontal=True, height=220,
                     alt="Number of runs in which each verification check was flagged")

with st.container(border=True):
    st.subheader("Run history")
    filter_left, filter_right = st.columns([2, 1], vertical_alignment="bottom")
    status = filter_left.segmented_control("Final status", ["APPROVED", "PENDING", "REJECTED"], key="dash_status",
                                           selection_mode="single")
    search = filter_right.text_input("Search company or run", key="dash_search")
    view = runs
    if status:
        view = view[view["Final status"] == status]
    if search:
        needle = search.lower()
        view = view[view["Company"].str.lower().str.contains(needle) | view["Run"].str.lower().str.contains(needle)]
    view = view.reset_index(drop=True)
    st.caption(f"{len(view)} of {len(runs)} runs. Select a row to see its full detail.")
    event = st.dataframe(
        view, hide_index=True, on_select="rerun", selection_mode="single-row", key="dash_table",
        alt="All onboarding runs with status, who decided, and how many checks were flagged",
        column_config={
            "Created": st.column_config.DatetimeColumn(format="YYYY-MM-DD HH:mm:ss"),
            "Needs review": st.column_config.CheckboxColumn(),
            "Flagged checks": st.column_config.NumberColumn(width="small"),
        },
    )

labels = {row["Run"]: f"{row['Company'] or 'Unnamed vendor'} ({row['Run']}, {row['Final status']})"
          for _, row in view.iterrows()}
picked = st.selectbox("Or open a run by name", list(labels), index=None, format_func=labels.get,
                      key="dash_pick", placeholder="Choose a run")
selected = event.selection.rows
run_id = view.iloc[selected[0]]["Run"] if selected else picked
if not run_id:
    st.caption("No run selected.")
    st.stop()
with factory() as session:
    detail = run_detail(session, run_id)
    diff = run_diff(session, run_id)

with st.container(border=True):
    heading, badge = st.columns([5, 1], vertical_alignment="center")
    heading.subheader(detail["input"].get("company_name") or "Unnamed vendor")
    with badge:
        components.status_badge(detail["outcome"]["status"])
    components.render_run(detail, key=f"dash_{run_id}", diff=diff)
    chain = st.tabs(["Run chain", "Reviews"])
    with chain[0]:
        st.markdown(f"Parent run: `{detail['parent_run_id']}`" if detail["parent_run_id"] else "This is an original submission.")
        if detail["children"]:
            st.markdown("Resubmissions: " + ", ".join(f"`{c}`" for c in detail["children"]))
        if detail["vendor_id"]:
            st.markdown(f"Onboarded as vendor id `{detail['vendor_id']}`.")
    with chain[1]:
        if detail["reviews"]:
            st.dataframe(pd.DataFrame([{"Reviewer": r["reviewer"], "Decision": r["decision"], "Note": r["note"],
                                        "Time": r["at"]} for r in detail["reviews"]]),
                         hide_index=True, alt="Reviewer decisions for this run")
        else:
            st.caption("No reviewer action on this run.")
