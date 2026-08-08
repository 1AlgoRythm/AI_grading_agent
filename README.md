# AI Assignment Grading Agent — starter repo

A general, assignment-type-agnostic grading agent: ingest → develop solution →
draft rubric → grade (agentic) → feedback → human approval. Open-source, BYOK.
This starter contains the frozen contracts, shared fixtures, a walking skeleton,
and the three lane modules — enough for a three-person team to start in parallel.

## Layout

```
grading_agent/
├── contracts.py         # FROZEN: shared models, enums, seams, exceptions
├── fixtures.py          # shared fake data (one math assignment, wired by ID)
├── skeleton.py          # the thin end-to-end path through every seam
├── model_provider.py    # shared BYOK model caller (+ offline fallback)
├── requirements.txt
├── p1_app.py            # [P1] upload / solution-review / rubric-editor demo
├── p2_app.py            # [P2] grade + trace review demo
├── p3_app.py            # [P3] human-review, feedback chat, audit, evaluation demo
├── textbook/            # sample course material for retrieval (P1)
├── lanes/
│   ├── p1_ingestion.py  # [P1] public facade -> p1_io / p1_rag / p1_solution / p1_context / p1_storage
│   ├── p2_grading.py    # [P2] public facade -> p2_engine / p2_grader / p2_critic / p2_verify / p2_batch / p2_storage
│   └── p3_feedback.py   # [P3] feedback + chat (see also p3_review, p3_evaluation, p3_storage)
└── tests/
    └── test_contracts.py
```

Each lane's facade module (`p1_ingestion.py`, `p2_grading.py`, `p3_feedback.py`)
re-exports a stable public API while the real implementation lives in
dedicated `lanes/p{1,2,3}_*.py` helper files underneath — see each facade's
docstring for the current split.

## Run it (day one, together)

```bash
cd grading_agent
pip install -r requirements.txt
python fixtures.py     # smoke test: builds every object, checks cross-refs
python skeleton.py     # watch a submission flow end to end
pytest -q              # contract tests (fixtures + skeleton path)
pytest -q tests/test_p2.py   # dedicated P2 grading lane coverage
```

If `python skeleton.py` prints the eight stages and ends with "end-to-end path
executed", the three lanes connect. That is the foundation everything else is
built on.

## Demo apps

Each lane has its own Streamlit screen, run independently:

### P1 ingestion & rubric demo

```bash
streamlit run p1_app.py
```

Upload (or paste) an assignment, develop and review a proposed solution per
problem (with `verify_solution`'s self-consistency/substitution check shown
inline), then draft and edit the rubric. A sidebar button syncs the on-disk
`textbook/` corpus into the `textbook_index` table. Everything persists via
`P1Store` (`p1_demo.db` by default; set `P1_DATABASE_URL` for Postgres) so an
in-progress review survives a restart.

### P2 grade + trace review demo

```bash
streamlit run p2_app.py
```

Grades the sample submission and shows the full grader → critic →
reconciliation trace (every REASON/ACT/CRITIQUE/REVISION step), alongside a
lightweight re-score control for inspecting the effect of a different score.
This is deliberately *not* the audited approval workflow — it never touches
`status`/`resolution`/`approver_id` — final approval still happens in the P3
app below. Persists via `P2Store` (`p2_demo.db` by default; set
`DATABASE_URL` for Postgres).

### P3 review demo

Run the human-review, grounded-feedback, audit, and evaluation interface:

```bash
streamlit run p3_app.py
```

The current screen uses the shared fixtures so it remains usable while P1 and
P2 replace their stubs. It demonstrates score overrides, required audit reasons,
final approval, student follow-up explanations, and label-free metrics.

Override audits are stored in `p3_demo.db` by default. Set `DATABASE_URL` to a
SQLAlchemy PostgreSQL URL to use Postgres instead.

## P1 to P2 handoff

P1 is responsible for assignment parsing, textbook retrieval, model solution
and rubric drafting, and building the budget-checked `GradingContext` objects
that P2 consumes directly. P2 should treat the P1 context as the canonical
input for grading and should not need to re-derive the assignment structure.

## Run with Docker Compose

Docker Compose starts the Streamlit application and PostgreSQL:

```bash
docker compose up --build
```

Open <http://localhost:8501>. The database is health-checked before the app
starts and its records are retained in the `grading_data` volume. Stop the stack
with `docker compose down`; add `--volumes` only when you intentionally want to
remove stored audit and evaluation data.

## Configuration and BYOK

Copy `.env.example` to `.env` for local configuration. Never commit `.env` or an
API key. The shared model-provider integration will consume `MODEL_PROVIDER`,
`MODEL_NAME`, and `MODEL_API_KEY`; current P3 feedback is deterministic and does
not require a paid model.

## P3 evaluation limitations

The evaluation report contains label-free indicators: answer-match rate,
evidence-grounding rate, critic agreement, repeated-run score variance, latency,
and token usage. These signals do **not** prove grading accuracy because the
project does not yet have a human-graded golden dataset. Human overrides are
recorded so the team can build that dataset over time.

Generate the reproducible evaluation artifact with:

```bash
python scripts/generate_evaluation_report.py
```

The artifact groups repeated grades by submission ID for reliability and also
reports feedback-quality and injection-robustness signals.

## FastAPI

Start the asynchronous API layer with:

```bash
uvicorn api:app --reload
```

`GET /health`, `POST /feedback`, and `POST /evaluation` are documented at
<http://localhost:8000/docs>. The Streamlit review application remains the
human-facing interface.

## Deployment target

`render.yaml` is a Render Blueprint for the Dockerized application and managed
PostgreSQL database. Connect the GitHub repository in Render, create a Blueprint
from this file, and set the BYOK variables when model-backed behavior is wanted.
The deterministic offline path requires no model key.

## How to work in parallel

1. **Together first:** read `contracts.py`, run the three commands above, and
   agree on the two open choices (half-up rounding; a single `proposed|approved`
   status). Commit `contracts.py` + `fixtures.py` as the frozen foundation.
2. **Then split:** each person owns one lane module and replaces its trivial
   stub bodies with real logic — **without changing the signatures**.
   - P1 → real parsing, textbook retrieval, LLM solution + rubric, budgeted context
   - P2 → the ReAct grader, SymPy tool, and the independent critic
   - P3 → grounded feedback, the agentic-RAG chat, the review UI, evaluation
3. **Stay green:** nobody imports another lane's real code — everyone imports
   `contracts` and `fixtures`. As real modules land, they swap in at the seam and
   `skeleton.py` keeps running. Run `pytest` before every push.

## Rules

- `contracts.py` is frozen. Changing a shared model or signature needs a quick
  team conversation, because it can silently break a teammate.
- Keep the skeleton thin. Real features go in the lane modules, not in
  `skeleton.py`.
- BYOK: the model provider + key come from environment config in the real
  implementations — never commit a key.
