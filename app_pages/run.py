import streamlit as st

from ui import app_state, components, presets
from ui.data import run_detail, run_diff, save_upload

factory = app_state.factory()
sample_files = presets.ensure_samples()
FIELDS = ("company_name", "tax_id", "bank_account_name", "bank_account_num")


def apply_preset(key=None):
    key = key or st.session_state["preset_key"]
    fields = presets.PRESETS[key]["fields"]
    st.session_state["f_company"] = fields["company_name"]
    st.session_state["f_country"] = fields["country"]
    st.session_state["f_tax"] = fields["tax_id"]
    st.session_state["f_bank_name"] = fields["bank_account_name"]
    st.session_state["f_bank_num"] = fields["bank_account_num"]
    st.session_state["f_doc"] = fields["document"]


if "f_company" not in st.session_state:
    st.session_state["preset_key"] = next(iter(presets.PRESETS))
    apply_preset(st.session_state["preset_key"])

# Containers fix the on-screen order; they are filled in the order that lets a click
# update the live view and the result in the same run.
form_box = st.container(border=True)
live_box = st.container()
result_box = st.container()

with form_box:
    st.selectbox("Scenario", list(presets.PRESETS), key="preset_key", on_change=apply_preset,
                 format_func=lambda k: presets.PRESETS[k]["label"])
    preset = presets.PRESETS[st.session_state["preset_key"]]
    st.caption(preset["about"])
    st.markdown(f"Expected outcome: **{preset['expect']}**")
    left, right = st.columns(2)
    left.text_input("Company name", key="f_company")
    right.selectbox("Country", presets.COUNTRIES, key="f_country")
    left.text_input("Tax ID", key="f_tax")
    right.text_input("Bank account holder name", key="f_bank_name")
    left.text_input("Bank account number", key="f_bank_num")
    right.selectbox("Tax document", [*sample_files, presets.UPLOAD_CHOICE], key="f_doc")
    if st.session_state["f_doc"] == presets.UPLOAD_CHOICE:
        st.file_uploader("Tax document (PDF)", type=["pdf"], key="f_upload")
    run_clicked = st.button("Run onboarding pipeline", type="primary", icon=":material/play_arrow:")

resubmit = st.session_state.pop("resubmit_request", None)

if run_clicked:
    documents, problem = [], None
    choice = st.session_state["f_doc"]
    if choice == presets.UPLOAD_CHOICE:
        upload = st.session_state.get("f_upload")
        if upload is not None:
            try:
                documents = [save_upload(upload.getvalue(), app_state.UPLOAD_DIR)]
            except ValueError as exc:
                problem = str(exc)
    else:
        documents = [str(presets.SAMPLES_DIR / choice)]
    with live_box:
        if problem:
            st.error(problem)
        else:
            run_id = components.run_with_live_view(factory, {
                "company_name": st.session_state["f_company"], "country": st.session_state["f_country"],
                "tax_id": st.session_state["f_tax"], "bank_account_name": st.session_state["f_bank_name"],
                "bank_account_num": st.session_state["f_bank_num"], "documents": documents,
            })
            if run_id:
                st.session_state["last_run_id"] = run_id
elif resubmit:
    parent = resubmit["parent"]
    with factory() as session:
        parent_detail = run_detail(session, parent)
    support, problem = [], None
    choice = st.session_state.get(f"rs_support_{parent}", presets.NO_SUPPORT)
    if choice == presets.PROOF_SAMPLE:
        support = [str(presets.SAMPLES_DIR / presets.PROOF_SAMPLE)]
    elif choice == presets.UPLOAD_CHOICE:
        upload = st.session_state.get(f"rs_upload_{parent}")
        if upload is None:
            problem = "Choose a PDF to upload, or pick a different option."
        else:
            try:
                support = [save_upload(upload.getvalue(), app_state.UPLOAD_DIR)]
            except ValueError as exc:
                problem = str(exc)
    corrections = {}
    for field in FIELDS:
        value = st.session_state.get(f"rs_{field}_{parent}")
        if value is not None and value != parent_detail["input"].get(field):
            corrections[field] = value
    if not problem and not support and not corrections:
        problem = "Add a supporting document or correct a field before resubmitting."
    with live_box:
        if problem:
            st.error(problem)
        else:
            run_id = components.run_with_live_view(
                factory, {**corrections, "supporting_documents": support}, parent_run_id=parent)
            if run_id:
                st.session_state["last_run_id"] = run_id

with result_box:
    run_id = st.session_state.get("last_run_id")
    detail = None
    if run_id:
        with factory() as session:
            detail = run_detail(session, run_id)
            diff = run_diff(session, run_id)
    if detail is None:
        st.session_state.pop("last_run_id", None)
        st.caption("Pick a scenario and run it. Each stage shows up above the result as it executes.")
    else:
        st.subheader("Result")
        components.render_run(detail, key=f"result_{run_id}", diff=diff)
        if detail["outcome"]["status"] == "PENDING":
            components.render_resubmit_panel(detail, sample_files)
