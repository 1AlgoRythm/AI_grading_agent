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
├── requirements.txt
├── lanes/
│   ├── p1_ingestion.py  # [P1] parsing, solution, rubric, context
│   ├── p2_grading.py    # [P2] grader + critic + verification tool
│   └── p3_feedback.py   # [P3] feedback, chat, review/finalize
└── tests/
    └── test_contracts.py
```

## Run it (day one, together)

```bash
cd grading_agent
pip install -r requirements.txt
python fixtures.py     # smoke test: builds every object, checks cross-refs
python skeleton.py     # watch a submission flow end to end
pytest -q              # contract tests (fixtures + skeleton path)
```

If `python skeleton.py` prints the eight stages and ends with "end-to-end path
executed", the three lanes connect. That is the foundation everything else is
built on.

## P3 review demo

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
