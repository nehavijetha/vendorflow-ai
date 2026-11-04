# VendorFlow AI

Vendor onboarding (Zamp ASA case study, PS-2): a vendor submission goes in, an APPROVED / PENDING /
REJECTED decision with every reason comes out, and the vendor is told exactly what is still needed.

## Run it

```bash
pip install -r requirements.txt
python scripts/make_sample_pdfs.py     # synthetic test documents (also created on first launch)
python scripts/warm_cache.py           # optional: read the samples with Gemini once so demos are instant
streamlit run streamlit_app.py
```

Put your key in `.env` (never commit it):

```
GEMINI_API_KEY=...
GEMINI_MODELS=gemini-3.1-flash-lite,gemini-3.5-flash-lite,gemini-3.5-flash
```

The Gemini free tier allows about 20 requests per day per model, so the client tries the models in
order and skips one whose quota is used up. With no key, or with "Use Gemini AI" switched off, a
regex parser reads text PDFs (scans need the model).

## Pages

- **Run onboarding**: pick a scenario, watch stages S1 to S8 execute live, read the result, resubmit with evidence and see what changed.
- **Audit dashboard**: KPIs, charts, run history, per-run findings, timeline, extracted data, run chain.
- **Review queue**: runs that need a person; decisions are stored beside the system result.

## The pipeline

| Stage | Job | Done by |
|---|---|---|
| S1 Intake | create the run, store the input, hash every uploaded file | code |
| S2 Completeness | required fields and documents, every gap listed together | code |
| S3 Extraction | read the document into fields, and report its layout against the official W-9 (the model never sees the form) | pypdf, Gemini |
| S4 Grounding | is what the model read really in the document? | code |
| S5 Verification | eleven independent checks, each with evidence | code |
| S6 Decision | APPROVED / PENDING / REJECTED from the findings alone | code |
| S7 Communication | vendor message; the model writes only the opening sentence | template + Gemini |
| S8 Persistence | everything saved; each stage writes an event as it finishes | SQLite |
| S9 Human review | outside the pipeline; stored beside the system result | person |

**Findings** are `pass`, `warn`, `fail`, `block`, `error` (technical failure) or `skipped` (inputs
missing). Only `block` rejects. Anything that is not an explicit `pass` keeps the run PENDING, so a run
can never be approved while a check is skipped or has errored.

**Grounding.** A hallucination only matters if it produces a wrong pass. For a PDF with a text layer the
value must appear in that text. For a scan there is no text, so the value must equal what the vendor
typed. That second rule is valid only because the extraction request never contains the form values;
`tests/test_extract_independence.py` captures the real request bodies to prove it.

**Genuine form check.** A page that says "Form W-9" is not a W-9. `reference/fw9.pdf` is the official IRS
form (downloaded unmodified from irs.gov). Two independent signals decide, and neither is the model's
opinion of the document's type:

1. *Text signature (plain code).* Eighteen distinctive phrases of the official page (title, "Give form to the
   requester", Part I and II wording, "Cat. No. 10231X", the footer) must be in the PDF's text, 15 or more.
   A typed fake scores 0 to 2.
2. *Layout (model observations, code decides).* The model is shown the official page beside the submitted
   pages and answers eleven yes/no questions (is there a Line 1 box? SSN and EIN cells? a Sign Here block?),
   never "is this genuine?". A fixed rule judges the answers. This catches a *lookalike* that retypes every
   word of the form on a plain page: the text test passes, the layout does not.

Anything that fails goes PENDING asking for the official form, flagged for a person, never rejected. With no
model available the check is a technical error, so a text match alone can never approve. This proves the
document uses the official form; it does not prove the vendor is real or that the number is theirs, and a
forgery that copies the real layout would still pass. The reference image goes only with the W-9 for a US
submission, not with supporting documents or other countries.

**Tax ID check digits.** US EIN prefix against the IRS published list, UK VAT mod 97 or the 9755 variant,
Canadian BN Luhn. A failure is unresolved (a likely typo), never a rejection. Cross-checked against
`python-stdnum` (see the notes in `logic/taxid.py` for the two known differences).

**Document reuse.** The same file (by content hash) submitted for a different tax ID in an unrelated run
goes to manual review. Runs in the same resubmission chain are excluded.

**Audit trail.** Events and findings are append-only. The `runs` row is updated in place, so every status
change also writes an event, and `status_from_events` rebuilds a run's status from events alone.

## Sample documents

`python scripts/make_sample_pdfs.py` builds them from the official form. Genuine forms are the real IRS page
with values written on it (and a drawn signature); the attacks are a typed fake, a lookalike with every word
retyped, a letter that only mentions a W-9, and the blank template. Each scenario that ends APPROVED has its
own vendor (Meridian, Cobalt, Orion), so approving one never makes another look like a duplicate and the demo
needs no reset between scenarios.

## Deploy (Streamlit Community Cloud)

1. Push this folder to a GitHub repository (the `.env` file and the database are git-ignored).
2. At share.streamlit.io choose **Create app**, pick the repository, branch `main`, main file `streamlit_app.py`.
3. Under **Advanced settings > Secrets** paste (this is where the key lives, never in the code):

```toml
GEMINI_API_KEY = "your-key"
GEMINI_MODELS = "gemini-3.1-flash-lite,gemini-3.5-flash-lite,gemini-3.5-flash"
```

4. Deploy. Pushing a change to the repo redeploys the app automatically.

On a free host the database and uploads are temporary and reset when the app restarts, so the dashboard
starts empty after a restart. The sample PDFs and their pre-read answers (`.cache/extractions.json`) are
committed, so the preset scenarios work instantly and use no Gemini quota. Anyone with the link can
use the app and spend the free-tier quota, so share the link only with the people who need it.

## Tests

```bash
python -m pytest
```
