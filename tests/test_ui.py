"""Headless UI tests (Streamlit AppTest) plus the data helpers the UI relies on. No network."""
from pathlib import Path

import pytest
from sqlalchemy import select
from streamlit.testing.v1 import AppTest

from db.models import Review, Run, Vendor, make_session_factory
from db.reviews import add_review
from db.seed import seed_vendors
from pipeline.runner import run_pipeline
from scripts.make_sample_pdfs import build_all
from ui import presets
from ui.data import kpis, reset_demo, review_queue, run_detail, runs_dataframe, save_upload

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("VENDORFLOW_DB", str(tmp_path / "ui.db"))
    at = AppTest.from_file(str(ROOT / "streamlit_app.py"), default_timeout=60)
    at.session_state["use_ai"] = False  # never call the network from tests
    at.session_state["step_delay"] = 0.0
    return at.run()


def run_scenario(at, key):
    at.selectbox(key="preset_key").select(presets.PRESETS[key]["label"]).run()
    [b for b in at.button if b.label == "Run onboarding pipeline"][0].click().run()
    assert not at.exception
    return at.session_state["last_run_id"]


def test_app_loads_with_every_preset(app):
    assert not app.exception and app.title[0].value == "Run onboarding"
    assert list(app.selectbox(key="preset_key").options) == [p["label"] for p in presets.PRESETS.values()]


@pytest.mark.parametrize("key,alert", [
    ("happy", "success"), ("dba", "warning"), ("wrongco", "warning"), ("dupbank", "warning"),
    ("wrongdoc", "warning"), ("unsigned", "warning"), ("taxmismatch", "warning"), ("blocked", "error"),
])
def test_each_scenario_ends_with_the_expected_banner(app, key, alert):
    run_scenario(app, key)
    assert len(getattr(app, alert)) >= 1
    assert not app.exception


def test_blank_form_lists_everything_missing(app):
    run_scenario(app, "blank")
    assert app.warning
    assert "tax registration document" in app.code[0].value


def test_scanned_scenario_without_ai_reports_a_technical_error(app):
    run_scenario(app, "scanned")
    assert app.warning and "technical" not in app.error  # PENDING, not a crash
    assert not app.exception


def test_dba_resubmission_flow_links_runs_and_approves(app):
    first = run_scenario(app, "dba")
    app.selectbox(key=f"rs_support_{first}").select(presets.PROOF_SAMPLE).run()
    app.button(key=f"rs_go_{first}").click().run()
    assert not app.exception
    second = app.session_state["last_run_id"]
    assert second != first and app.success
    assert f"resubmission of `{first}`" in " ".join(c.value for c in app.caption)


def test_resubmission_shows_what_changed_since_the_parent_run(app):
    first = run_scenario(app, "dba")
    assert "What changed since" not in " ".join(m.value for m in app.markdown)  # an original run has no diff
    app.selectbox(key=f"rs_support_{first}").select(presets.PROOF_SAMPLE).run()
    app.button(key=f"rs_go_{first}").click().run()
    assert not app.exception
    counts = {m.label: m.value for m in app.metric}
    assert (counts["Resolved"], counts["Still open"], counts["New"]) == ("1", "0", "0")
    text = " ".join(m.value for m in app.markdown)
    assert f"What changed since `{first}`" in text and "Resolved: Company vs bank holder name" in text


def test_live_view_shows_the_grounding_stage(app):
    run_scenario(app, "happy")
    text = " ".join(m.value for m in app.markdown)
    assert "S4 Grounding" in text and "S8 Persistence" in text and "Extraction grounding: **pass**" in text


def test_pending_banner_counts_skipped_checks_separately(app):
    run_scenario(app, "blank")
    banner = app.warning[0].value
    assert "need attention" in banner and "skipped because their inputs are missing" in banner


def test_dashboard_history_has_a_skipped_column(app):
    run_scenario(app, "blank")
    app.switch_page("app_pages/dashboard.py").run()
    assert not app.exception
    assert "Skipped checks" in list(app.dataframe[0].value.columns)


def test_resubmit_without_anything_new_shows_an_error(app):
    first = run_scenario(app, "dba")
    app.button(key=f"rs_go_{first}").click().run()
    assert app.error and "Add a supporting document" in app.error[0].value
    assert app.session_state["last_run_id"] == first


def test_resubmit_panel_not_shown_for_approved_or_rejected(app):
    run_scenario(app, "happy")
    assert not [b for b in app.button if b.label == "Resubmit"]


def test_dashboard_shows_history_and_kpis(app):
    run_scenario(app, "happy")
    run_scenario(app, "dupbank")
    app.switch_page("app_pages/dashboard.py").run()
    assert not app.exception
    values = {m.label: m.value for m in app.metric}
    assert values["Runs"] == "2" and values["Approved"] == "1" and values["Pending"] == "1"
    assert values["Awaiting review"] == "1"
    assert len(app.dataframe) == 1  # the run history table


def test_dashboard_run_detail_by_picker_shows_chain_and_findings(app):
    first = run_scenario(app, "dba")
    app.selectbox(key=f"rs_support_{first}").select(presets.PROOF_SAMPLE).run()
    app.button(key=f"rs_go_{first}").click().run()
    second = app.session_state["last_run_id"]
    app.switch_page("app_pages/dashboard.py").run()
    assert "Run detail" not in " ".join(m.value for m in app.markdown) and not app.exception
    app.selectbox(key="dash_pick").select(second).run()
    assert not app.exception
    text = " ".join(c.value for c in app.caption) + " ".join(m.value for m in app.markdown)
    assert f"resubmission of `{first}`" in text and f"Parent run: `{first}`" in text
    assert app.success  # the approved resubmission
    app.selectbox(key="dash_pick").select(first).run()
    assert f"Resubmissions: `{second}`" in " ".join(m.value for m in app.markdown)
    assert app.warning  # the original run is still shown as pending


def test_dashboard_empty_state(app):
    app.switch_page("app_pages/dashboard.py").run()
    assert app.info and "No runs yet" in app.info[0].value


def test_review_queue_records_a_decision(app):
    run_id = run_scenario(app, "dupbank")
    app.switch_page("app_pages/review.py").run()
    assert not app.exception and app.metric[0].value == "1"
    app.text_input(key=f"who_{run_id}").set_value("Priya")
    app.text_area(key=f"note_{run_id}").set_value("Bank letter confirmed by phone.")
    app.get("button_group")[0].set_value("approve")
    app.get("form_submit_button")[0].click().run()
    assert not app.exception
    assert app.session_state["reviewer_name"] == "Priya"
    app.switch_page("app_pages/review.py").run()
    assert app.metric[0].value == "0" and app.success


def test_review_requires_a_decision_and_a_note(app):
    run_id = run_scenario(app, "dupbank")
    app.switch_page("app_pages/review.py").run()
    app.get("form_submit_button")[0].click().run()
    assert app.error and "Choose a decision" in app.error[0].value
    app.get("button_group")[0].set_value("approve")
    app.text_input(key=f"who_{run_id}").set_value("Priya")
    app.get("form_submit_button")[0].click().run()
    assert any("note" in e.value.lower() for e in app.error)


def test_reset_demo_data_clears_history(app):
    run_scenario(app, "happy")
    [b for b in app.button if b.key == "reset_confirm"][0].click().run()
    assert "last_run_id" not in app.session_state
    app.switch_page("app_pages/dashboard.py").run()
    assert app.info


# --- data helpers (plain pytest) ----------------------------------------------------------------

@pytest.fixture
def session(tmp_path):
    with make_session_factory(f"sqlite:///{tmp_path / 'd.db'}")() as s:
        seed_vendors(s)
        yield s


@pytest.fixture(scope="module")
def samples(tmp_path_factory):
    out = tmp_path_factory.mktemp("ui_samples")
    build_all(out)
    return out


def sub(samples, **overrides):
    base = {"company_name": "Orion Components LLC", "country": "United States", "tax_id": "12-3456789",
            "bank_account_name": "Orion Components LLC", "bank_account_num": "000555666777",
            "documents": [str(samples / "w9_valid.pdf")]}
    return {**base, **overrides}


def test_runs_dataframe_effective_status_and_queue(session, samples):
    approved = run_pipeline(session, sub(samples), use_llm=False)
    pending = run_pipeline(session, sub(samples, bank_account_num="000111222333"), use_llm=False)
    df = runs_dataframe(session)
    assert kpis(df) == {"Runs": 2, "Approved": 1, "Pending": 1, "Rejected": 0, "Awaiting review": 1}
    assert [d["run_id"] for d in review_queue(session)] == [pending["run_id"]]
    add_review(session, pending["run_id"], "Priya", "approve", "Confirmed.")
    df = runs_dataframe(session)
    row = df[df["Run"] == pending["run_id"]].iloc[0]
    assert (row["System status"], row["Final status"], row["Decided by"]) == ("PENDING", "APPROVED", "reviewer")
    assert review_queue(session) == [] and approved["status"] == "APPROVED"


def test_request_info_review_leaves_the_queue_but_not_the_pending_state(session, samples):
    pending = run_pipeline(session, sub(samples, bank_account_num="000111222333"), use_llm=False)
    add_review(session, pending["run_id"], "Priya", "request_info", "Asked for ownership proof.")
    df = runs_dataframe(session)
    assert df.iloc[0]["Final status"] == "PENDING" and not df.iloc[0]["Needs review"]


def test_run_detail_has_findings_events_and_chain(session, samples):
    first = run_pipeline(session, sub(samples, bank_account_name="Orion Components Holdings LLC"), use_llm=False)
    second = run_pipeline(session, {"supporting_documents": [str(samples / "parent_entity_proof.pdf")]},
                          parent_run_id=first["run_id"], use_llm=False)
    detail = run_detail(session, first["run_id"])
    assert detail["children"] == [second["run_id"]] and len(detail["findings"]) == 12
    assert detail["events"][0]["stage"] == "S1" and detail["findings"][0]["check"] == "Required fields and document"
    assert run_detail(session, "run-nope") is None


def test_reset_demo_restores_only_the_seed_vendors(session, samples):
    run_pipeline(session, sub(samples), use_llm=False)
    assert len(session.scalars(select(Vendor)).all()) == 5
    reset_demo(session)
    assert session.scalars(select(Run)).all() == [] and session.scalars(select(Review)).all() == []
    assert len(session.scalars(select(Vendor)).all()) == 4


def test_save_upload_validates_and_renames(tmp_path):
    path = save_upload(b"%PDF-1.4 test", tmp_path)
    assert Path(path).parent == tmp_path and Path(path).name != "w9.pdf" and Path(path).read_bytes() == b"%PDF-1.4 test"
    with pytest.raises(ValueError, match="not a PDF"):
        save_upload(b"MZ executable", tmp_path)
    with pytest.raises(ValueError, match="10 MB"):
        save_upload(b"%PDF" + b"0" * (10 * 1024 * 1024), tmp_path)
