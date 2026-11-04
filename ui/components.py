"""Shared Streamlit pieces: the live run view, the run summary, and the resubmit panel."""
import time

import pandas as pd
import streamlit as st

from pipeline.runner import STAGE_NAMES, STAGES, PipelineError, run_pipeline
from ui import presets
from ui.app_state import cache_path
from ui.data import check_label

RESULT_COLORS = {
    "pass": "rgba(34, 197, 94, 0.18)",
    "warn": "rgba(245, 158, 11, 0.25)",
    "fail": "rgba(239, 68, 68, 0.22)",
    "block": "rgba(185, 28, 28, 0.35)",
    "error": "rgba(147, 51, 234, 0.25)",
    "skipped": "rgba(148, 163, 184, 0.28)",
}
STAGE_ICON = {
    "pending": ":gray[:material/radio_button_unchecked:]",
    "running": ":blue[:material/hourglass_top:]",
    "done": ":green[:material/check_circle:]",
    "failed": ":red[:material/error:]",
}
STATUS_BADGE = {"APPROVED": "green", "PENDING": "orange", "REJECTED": "red"}


def status_badge(status):
    st.badge(status or "UNKNOWN", color=STATUS_BADGE.get(status, "gray"))


# --- live run view ---------------------------------------------------------------------------

def run_with_live_view(factory, submission, parent_run_id=None):
    """Run the pipeline, painting each stage as its event arrives. Returns the run id or None."""
    state = {stage: "pending" for stage in STAGES}
    text = {stage: "" for stage in STAGES}
    lines = {stage: [] for stage in STAGES}
    delay = float(st.session_state.get("step_delay", 0))
    use_ai = bool(st.session_state.get("use_ai"))

    with st.status("Running onboarding pipeline", expanded=True) as box:
        slots = {stage: st.empty() for stage in STAGES}

        def paint(stage):
            body = f"{STAGE_ICON[state[stage]]} **{stage} {STAGES[stage]}**"
            if text[stage]:
                body += f" &nbsp; {text[stage]}"
            for line in lines[stage]:
                body += f"\n\n&emsp;:gray[:material/subdirectory_arrow_right:] {line}"
            slots[stage].markdown(body)

        def on_event(event):
            stage = event["stage"]
            if event["status"] == "started":
                state[stage] = "running"
            elif event["status"] == "progress":
                check_id, _, result = (event["detail"] or "").partition(": ")
                lines[stage].append(f"{check_label(check_id)}: **{result}**")
            else:
                state[stage] = "done" if event["status"] == "completed" else "failed"
                text[stage] = f":gray[{event['detail']}]" if event["detail"] else ""
            paint(stage)
            if delay:
                time.sleep(delay)

        for stage in STAGES:
            paint(stage)
        try:
            with factory() as session:
                result = run_pipeline(
                    session, submission, parent_run_id=parent_run_id, use_llm=use_ai,
                    cache_path=cache_path(), on_event=on_event,
                )
        except PipelineError as exc:
            box.update(label=str(exc), state="error", expanded=True)
            return None
        failed = result["error"] is not None
        box.update(label=f"Finished: {result['status']}" + (" (pipeline error)" if failed else ""),
                   state="error" if failed else "complete", expanded=True)
    return result["run_id"]


# --- run summary -----------------------------------------------------------------------------

def _findings_table(findings, key):
    df = pd.DataFrame([{
        "Check": f["check"], "Result": f["result"], "Evidence": f["evidence"],
        "Asked of the vendor": f["vendor_request"] or "", "Human review": f["human_review"],
    } for f in findings])
    styled = df.style.map(lambda v: f"background-color: {RESULT_COLORS.get(v, '')}", subset=["Result"])
    st.dataframe(styled, hide_index=True, key=key, alt="Result and evidence for each verification check",
                 column_config={"Evidence": st.column_config.TextColumn(width="large"),
                                "Asked of the vendor": st.column_config.TextColumn(width="large")})


def render_diff(diff):
    """Resolved / still open / new findings compared with the run this one resubmits."""
    with st.container(border=True):
        top, status = st.columns([4, 2], vertical_alignment="center")
        top.markdown(f"**What changed since `{diff['parent_run_id']}`**")
        with status:
            with st.container(horizontal=True):
                status_badge(diff["parent_status"])
                st.markdown(":material/arrow_forward:")
                status_badge(diff["status"])
        with st.container(horizontal=True):
            st.metric("Resolved", len(diff["resolved"]), border=True)
            st.metric("Still open", len(diff["still_open"]), border=True)
            st.metric("New", len(diff["new"]), border=True)
            st.metric("Unchanged passes", diff["unchanged_pass"], border=True)
        sections = (("resolved", ":green[:material/check_circle:]", "Resolved"),
                    ("still_open", ":orange[:material/hourglass_top:]", "Still open"),
                    ("new", ":red[:material/error:]", "New"))
        for name, icon, title in sections:
            for row in diff[name]:
                st.markdown(f"{icon} **{title}: {row['check']}** ({row['before']} to {row['after']}). :gray[{row['evidence']}]")


def render_run(detail, key="run", diff=None):
    outcome, findings = detail["outcome"], detail["findings"]
    flagged = [f for f in findings if f["result"] not in ("pass", "skipped")]
    skipped = [f for f in findings if f["result"] == "skipped"]
    status = outcome["status"]
    if status == "APPROVED":
        st.success(title="Approved", body=f"All {len(findings)} checks passed." if not (flagged or skipped)
                   else f"Approved by a reviewer after {len(flagged) + len(skipped)} flagged or skipped check(s).")
    elif status == "REJECTED":
        blocking = [f["check"] for f in findings if f["result"] == "block"]
        st.error(title="Rejected", body=("Disqualifying finding: " + ", ".join(blocking)) if blocking
                 else "Rejected by a reviewer.")
    else:
        extra = " A person needs to review this run." if any(f["human_review"] for f in flagged) else ""
        note = f" ({len(skipped)} more skipped because their inputs are missing)" if skipped else ""
        st.warning(title="Pending: more information needed",
                   body=f"{len(flagged)} of {len(findings)} checks need attention{note}.{extra}")
    bits = [f"Run `{detail['run_id']}`"]
    if detail["parent_run_id"]:
        bits.append(f"resubmission of `{detail['parent_run_id']}`")
    if outcome["source"] == "reviewer":
        bits.append(f"final status set by reviewer (system result was {detail['system_status']})")
    st.caption(" · ".join(bits))
    if diff:
        render_diff(diff)

    left, right = st.columns([3, 2])
    with left:
        st.markdown("**Verification checks**")
        _findings_table(findings, key=f"{key}_findings")
    with right:
        st.markdown("**Message to the vendor**")
        if detail["vendor_message"]:
            st.code(detail["vendor_message"], language=None, wrap_lines=True)
        else:
            st.caption("No message was generated.")

    docs, timeline, submitted = st.tabs(["Extracted document data", "Run timeline", "Submission"])
    with docs:
        extracted = detail["extracted"] or {}
        shown = {k: v for k, v in extracted.items() if k not in ("supporting_documents", "sources", "detail")}
        if shown:
            st.dataframe(pd.DataFrame([{"Field": k, "Value": str(v)} for k, v in shown.items()]),
                         hide_index=True, key=f"{key}_extracted", alt="Fields read from the tax document")
            if extracted.get("sources"):
                st.caption("Read by: " + "; ".join(extracted["sources"]))
            for doc in extracted.get("supporting_documents", []):
                st.caption(f"Supporting document ({doc['document_type']}) names: " + ", ".join(doc["entity_names"]))
        else:
            st.caption("No document data.")
    with timeline:
        st.dataframe(pd.DataFrame([{"Time": e["at"].strftime("%H:%M:%S.%f")[:-3], "Stage": f"{e['stage']} {STAGE_NAMES.get(e['stage'], e['stage'])}",
                                    "Status": e["status"], "Detail": e["detail"] or ""} for e in detail["events"]]),
                     hide_index=True, key=f"{key}_timeline", alt="Pipeline stage events with timestamps")
    with submitted:
        st.json(detail["input"], expanded=True)


# --- resubmission -----------------------------------------------------------------------------

def _request_resubmit(run_id):
    st.session_state["resubmit_request"] = {"parent": run_id}


def render_resubmit_panel(detail, sample_files):
    run_id = detail["run_id"]
    with st.container(border=True):
        st.subheader("Resubmit with more evidence", divider="gray")
        asks = [f["vendor_request"] for f in detail["findings"] if f["vendor_request"]]
        st.caption("The vendor was asked for:")
        for ask in dict.fromkeys(asks):
            st.markdown(f"- {ask}")
        support_key = f"rs_support_{run_id}"
        choice = st.selectbox("Supporting document", [presets.NO_SUPPORT, presets.PROOF_SAMPLE, presets.UPLOAD_CHOICE],
                              key=support_key, format_func=lambda v: v if v != presets.PROOF_SAMPLE else "Sample: parent entity certificate")
        if choice == presets.UPLOAD_CHOICE:
            st.file_uploader("Supporting PDF", type=["pdf"], key=f"rs_upload_{run_id}")
        with st.expander("Correct form fields"):
            data = detail["input"]
            for field, label in (("company_name", "Company name"), ("tax_id", "Tax ID"),
                                 ("bank_account_name", "Bank account holder"), ("bank_account_num", "Bank account number")):
                st.text_input(label, value=data.get(field, ""), key=f"rs_{field}_{run_id}")
        st.button("Resubmit", icon=":material/send:", type="primary", key=f"rs_go_{run_id}",
                  on_click=_request_resubmit, args=(run_id,))
