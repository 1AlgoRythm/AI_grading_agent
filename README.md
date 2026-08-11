# AI Assignment Grading Agent

A general, assignment-type-agnostic grading agent: ingest → develop solution →
draft rubric → grade (agentic grader + independent critic) → feedback →
human approval. Open-source, BYOK.

## Layout

```
.
├── contracts.py         # FROZEN: shared models, enums, seams, exceptions
├── fixtures.py          # shared fake data (one math assignment, wired by ID)
├── skeleton.py          # the thin end-to-end path through every seam
├── model_provider.py    # shared BYOK model caller (OpenAI/Anthropic + offline fallback)
├── app.py               # unified entry point -- all three screens, one deployed service
├── p1_app.py            # [P1] upload / solution-review / rubric-editor / grade-a-submission
├── p2_app.py            # [P2] grade + trace review (standalone dev screen)
├── p3_app.py            # [P3] human-review, feedback chat, audit, evaluation
├── conftest.py          # puts the repo root on sys.path for bare `pytest`
├── requirements.txt
├── compose.yaml / Dockerfile
├── textbook/            # sample course material for retrieval (P1)
├── .github/workflows/ci.yml
├── lanes/
│   ├── p1_ingestion.py  # [P1] public facade -> p1_io / p1_rag / p1_solution / p1_context / p1_storage
│   ├── p2_grading.py    # [P2] public facade -> p2_engine / p2_grader / p2_critic / p2_verify / p2_batch / p2_storage
│   └── p3_feedback.py   # [P3] feedback + chat (see also p3_review, p3_evaluation, p3_storage)
└── tests/                # contract, lane, grading-sanity, and Streamlit AppTest coverage
```

Each lane's facade module (`p1_ingestion.py`, `p2_grading.py`, `p3_feedback.py`)
re-exports a stable public API while the real implementation lives in
dedicated `lanes/p{1,2,3}_*.py` helper files underneath — see each facade's
docstring for the current split.

## Quick start

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python fixtures.py     # smoke test: builds every object, checks cross-refs
python skeleton.py     # watch a submission flow end to end
pytest -q              # full test suite
```

If `python skeleton.py` prints the nine stages and ends with "end-to-end path
executed", the three lanes connect. CI (`.github/workflows/ci.yml`) runs all
three of the commands above on every push and PR.

## Run the app

```bash
streamlit run app.py
```

One deployed service, sidebar-switches between all three screens (shared
`st.session_state`, so a rubric approved on the upload screen is immediately
visible on the grading/review screens with no extra clicks):

- **Upload & Rubric** — upload/paste an assignment, develop and review a
  proposed solution per problem (`verify_solution`'s self-consistency /
  substitution check shown inline), draft and edit the rubric, then upload a
  submission and grade it.
- **Grade & Trace** — the full grader → critic → reconciliation trace (every
  REASON/ACT/CRITIQUE/REVISION step), with a lightweight re-score control for
  inspection. Not the audited approval workflow — final approval happens on
  the review screen.
- **Review & Feedback** — human review, score overrides (with required
  reasons), final approval, the grounded student feedback chat, an override
  audit trail, and label-free evaluation metrics.

All three share one database via `P1Store`/`P2Store`/`P3Store` (`DATABASE_URL`,
defaulting to one local `grading_demo.db` file if unset, so a plain local run
shares data across the whole app automatically). Each lane app can also be run
standalone (`streamlit run p1_app.py`, etc.) against that same database.

## Configuration and BYOK

Copy `.env.example` to `.env` and fill in `MODEL_PROVIDER` (`openai` or
`anthropic`), `MODEL_NAME`, and `MODEL_API_KEY`. Without a key configured,
everything runs on deterministic offline fallbacks (the app's sidebar shows a
loud warning when this is the case, so it's never silent about it).
`CRITIC_MODEL_NAME`/`CRITIC_TEMPERATURE` optionally point the critic at a
different model/temperature than the grader, for genuine independence.

Never commit `.env` or an API key.

## Run with Docker Compose

```bash
cp .env.example .env   # fill in your BYOK key first
docker compose up --build
```

Open <http://localhost:8501>. Compose passes your `.env` BYOK variables
through to the container and starts Postgres alongside the app (health-checked
before the app starts; records persist in the `grading_data` volume). Stop
with `docker compose down`; add `--volumes` only when you intentionally want
to remove stored data.

## Evaluation limitations

The evaluation report contains label-free indicators: answer-match rate,
evidence-grounding rate, critic agreement, repeated-run score variance,
latency, and token usage. These signals do **not** prove grading accuracy
because the project does not yet have a human-graded golden dataset. Human
overrides are recorded so the team can build that dataset over time.

## Rules

- `contracts.py` is frozen. Changing a shared model or signature needs a quick
  team conversation, because it can silently break another lane.
- BYOK: the model provider + key come from environment config — never commit
  a key.


https://drive.google.com/drive/folders/1vmOi_DnN_q8dmZZsy1dbTqFFfDZ70EAI?usp=share_link
