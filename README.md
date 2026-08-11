# AI Assignment Grading Agent

A general, assignment-type-agnostic grading agent: ingest → develop solution →
draft rubric → grade (agentic grader + independent critic) → feedback →
human approval. Open-source, BYOK.

Database-backed accounts gate the whole app: an admin approves instructors,
instructors run courses (enroll students by email, post assignments, review
submissions), and students see only their own enrolled courses, grades, and
feedback through a separate portal.

## Layout

```
.
├── contracts.py         # FROZEN: shared models, enums, seams, exceptions
├── fixtures.py          # shared fake data (one math assignment, wired by ID)
├── skeleton.py          # the thin end-to-end path through every seam
├── model_provider.py    # shared BYOK model caller (OpenAI/Anthropic/Gemini + offline fallback)
├── session_cache.py     # shares one Grade object across P1/P2/P3 tabs so overrides propagate
├── app.py               # unified entry point -- login gate, role routing, session-cookie persistence
├── p1_app.py            # [P1][instructor] upload / solution-review / rubric-editor / submissions roster
├── p2_app.py            # [P2][instructor] grade + trace review (standalone dev screen)
├── p3_app.py            # [P3][instructor] review, publish/reopen a grade, regrade-request queue
├── student_app.py       # [student] own courses/assignments, self-upload, grades, feedback, regrade chat
├── conftest.py          # puts the repo root on sys.path for bare `pytest`
├── requirements.txt
├── compose.yaml / Dockerfile
├── textbook/            # sample course material for retrieval (P1)
├── .github/workflows/ci.yml
├── lanes/
│   ├── p1_ingestion.py     # [P1] public facade -> p1_io / p1_rag / p1_solution / p1_context / p1_storage
│   ├── p2_grading.py       # [P2] public facade -> p2_engine / p2_grader / p2_critic / p2_verify / p2_batch / p2_storage
│   ├── p3_feedback.py      # [P3] feedback + chat (see also p3_review, p3_evaluation, p3_storage)
│   ├── p3_review.py        # [P3] override / finalize / publish / reopen a grade, audit trail
│   ├── active_selection.py # single source of truth for "which submission is active" across screens
│   ├── auth_storage.py     # accounts: admin/instructor/student roles, PBKDF2 password hashing
│   ├── course_storage.py   # courses, email-based enrollment, assignment<->course links, submission ownership
│   └── regrade_storage.py  # student-raised regrade requests + instructor reply thread
└── tests/                  # contract, lane, grading-sanity, and Streamlit AppTest coverage
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

One deployed service. The first thing anyone sees is a login/register screen;
a default admin account is seeded automatically (see `ADMIN_EMAIL`/
`ADMIN_PASSWORD` below). What you see after logging in depends on your role —
`app.py` routes each role to its own page set, so a student has no path to
any instructor screen and vice versa:

- **Admin** — approve or reject pending instructor registrations. Students
  self-register and can log in immediately; instructors need admin approval
  first.
- **Instructor** (after approval) gets three screens, sharing `st.session_state`
  so a rubric approved on one is immediately visible on the others:
  - **Upload & Rubric** — create courses and enroll students by email (works
    even before they've registered — resolved automatically once they do), a
    submissions roster scoped to your own courses (student email, score,
    escalation status, sorted escalated-first), upload/paste an assignment,
    develop and review a proposed solution per problem, draft and edit the
    rubric, then upload a submission on a student's behalf and grade it.
  - **Grade & Trace** — the full grader → critic → reconciliation trace
    (every REASON/ACT/CRITIQUE/REVISION step), with a lightweight re-score
    control for inspection.
  - **Review & Feedback** — human review, score overrides (with required
    reasons), final approval, **publish a grade** so the student sees it
    without locking it (reopen → override → re-publish to change it later),
    the **regrade-request queue** (open requests first; reply, change the
    grade via the same audited path, then resolve/close), an override audit
    trail, and label-free evaluation metrics.
- **Student** gets one portal: every enrolled course/assignment with its
  status (not submitted / submitted-awaiting-grade / graded), a self-upload
  path that's auto-graded on submit, the published score + grounded feedback
  + rubric once an instructor publishes it, a follow-up chat grounded in
  their own grade only, and the ability to open/reply to regrade requests.

Publishing a grade is a softer, editable state than final approval — a
published grade is visible to the student but the instructor can still
reopen and change it (audited); approving locks it.

All screens share one database via `P1Store`/`P2Store`/`P3Store`/`CourseStore`/
etc. (`DATABASE_URL`, defaulting to one local `grading_demo.db` file if unset,
so a plain local run shares data across the whole app automatically). Each
lane app can also be run standalone (`streamlit run p1_app.py`, etc.) against
that same database, with no login gate.

## Configuration and BYOK

Copy `.env.example` to `.env` and fill in `MODEL_PROVIDER` (`openai`,
`anthropic`, or `gemini`), `MODEL_NAME`, and `MODEL_API_KEY`. Without a key
configured, everything runs on deterministic offline fallbacks (the app's
sidebar shows a loud warning when this is the case, so it's never silent
about it). `CRITIC_MODEL_NAME`/`CRITIC_TEMPERATURE` optionally point the
critic at a different model/temperature than the grader, for genuine
independence.

Auth and session config (all optional — every one has a documented default
so a clean local checkout works with zero setup):

- `ADMIN_EMAIL` / `ADMIN_PASSWORD` — the seeded default admin account
  (`admin@local` / `changeme123` if unset). Change these for anything beyond
  local dev.
- `SESSION_SECRET` — signs the login-persistence cookie (HMAC). Falls back to
  a random per-process secret when unset, so a page refresh still stays
  logged in, but a real server restart won't keep old sessions valid unless
  this is set to a fixed value.

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

## Deployment

Live demo: <https://aigradingagent-production.up.railway.app/>

Deployed on [Railway](https://railway.app) as two services: the app (built
from this repo's `Dockerfile`) and a Postgres database. Same shape as the
Docker Compose setup above, just hosted instead of local. A few things that
are easy to get wrong deploying this (or any SQLAlchemy + Streamlit app)
to a fresh Railway project:

- **`DATABASE_URL` needs the `+psycopg` driver prefix.** Railway's
  auto-generated Postgres connection string is plain `postgresql://...`,
  which SQLAlchemy reads as "use `psycopg2`" — not installed here (this repo
  installs `psycopg[binary]`, the newer psycopg3, per `requirements.txt`).
  Change the scheme to `postgresql+psycopg://...` (only that prefix; leave
  the user/password/host/port/database as Railway generated them) or the
  app fails to connect on startup.
- **Set `DATABASE_URL` on the app service, not the database service.**
  Railway's own Postgres service shows a warning if you try to hand-edit its
  variables there — that's the wrong place; it doesn't change the real
  database, it just makes that display misleading. Paste the (corrected)
  connection string into the *app's* `DATABASE_URL` variable instead.
- **`ADMIN_EMAIL`/`ADMIN_PASSWORD`/`SESSION_SECRET` aren't wired through
  `compose.yaml` or set automatically on Railway** — set them explicitly as
  variables on the app service. Without them: the seeded admin account is
  the public default (`admin@local` / `changeme123`, straight out of
  `app.py`'s own fallback — not a secret once this repo is public), and login
  sessions won't survive a container restart.
- `seed_admin()` only creates the admin account if **no admin exists yet**
  in the database — setting `ADMIN_EMAIL`/`ADMIN_PASSWORD` after an admin
  has already been seeded (e.g. the database already has data) won't change
  the existing account.

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
