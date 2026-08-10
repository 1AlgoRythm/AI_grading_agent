# AI Assignment Grading Agent
## Detailed Project Plan & End-to-End Development Guide
*Three-person team · parallel build · open-source · BYOK*

> **Current status (implementation note).** The repository root folder is `seam/` (not `grading_agent/`). The walking skeleton described in §9 is built and green — it runs the full ingest → solution → rubric → context → grade → feedback → approve path end to end, is version-controlled, and pushed to GitHub. Per the build order in §5, the grader was built solo first; **the independent critic is now built too** — grader + adversarial critic + bounded one-revision reconciliation, escalating to human review on persistent disagreement (`lanes/p2_grader.py`, `lanes/p2_critic.py`, `lanes/p2_engine.py`). P1, P2, and P3 each have a real Streamlit screen backed by a shared database (`P1Store`/`P2Store`/`P3Store`), unified into one deployed app (`app.py`) so the full pipeline — not just review — is reachable once deployed. CI (`.github/workflows/ci.yml`) runs the walking skeleton and the test suite on every push/PR. Where this plan and the code differ on a detail, the code is the source of truth.

---

## Contents
1. Project Overview
2. Instructor Suggestions (Note)
3. Scope
4. System Architecture (End to End)
5. Multi-Agent Grading (Grader + Critic)
6. Context Engineering
7. Technology Stack
8. Data Model & Frozen Contracts
9. Parallel Development Method
10. Work Split — Three Lanes in Detail
11. Integration Sequence & Definition of Done
12. Testing Strategy
13. Evaluation (Label-Free)
14. Deployment
15. Risk Register
16. Open Questions to Confirm with the Instructor
17. Deliverables Checklist

---

## 1. Project Overview

This project builds an AI agent that grades student assignments end to end: it ingests the course material and assignment, develops a model solution and a grading rubric, grades student submissions through an agentic workflow with an independent critic, explains the results in a feedback chat, and routes everything through human approval. It is designed as a general assignment-grading system, not a one-assignment script.

**Assignment-type-agnostic by design.** The pipeline — ingest, develop solution and rubric, grade, critique, give feedback, human-approve — is the same for any assignment type. What changes per type is a small, pluggable *verification tool* and the parsing of the submission. The first supported type is simple math (using a symbolic-math tool as the verifier); other types (short answer, code, etc.) can be added later by implementing the same tool interface, without touching the rest of the system.

**Guiding properties:** grounded (the model grades from the approved solution and rubric, not from memory), agentic (a reasoning-and-tool-using grader plus an independent critic), human-in-the-loop (a person approves the solution, the rubric, and every final grade), lenient (student work is judged generously, with partial credit), open-source (MIT licensed), and BYOK (bring your own key — the model provider and key are configuration, so anyone can run it with their own account or a local model).

---

## 2. Instructor Suggestions (Note)

*The following points came directly from the instructor and shape the whole plan. They are recorded here in spirit, with how the plan honors each.*

> **Instructor's stated points:**
> - Use context ingestion and context engineering.
> - Do not add anything extra — keep to the core pipeline.
> - "Have docs."
> - Build an agentic system with an end-to-end pipeline.
> - Don't be too strict — apply the rubric leniently.
> - BYOK (bring your own key).
> - Make it open source.

**How the plan honors each point**
- **Context ingestion / engineering:** given its own section (§6). P1 owns ingestion and the context-assembly layer.
- **Agentic, end-to-end:** a ReAct grading agent inside a full ingest → solution → rubric → grade → feedback → approval pipeline, with a strict definition of done (§11).
- **Lenient rubric:** a standing grading-policy block instructs the grader to favor partial credit and charitable interpretation (§6). Leniency applies to student work only — never to the reference solution.
- **BYOK + open source:** a provider-agnostic model module reads the key and model from configuration; MIT license; runnable from a clean checkout.

> **Two items to confirm with the instructor (flagged honestly):**
> - **"Have docs" is ambiguous.** It likely means the system ingests documents (course material) as its grading context — which it does. It may also mean project documentation. The plan satisfies both (ingestion + a maintained README), but the intended meaning is worth a one-line confirmation.
> - **The multi-agent critic vs. "don't add anything extra."** The team has chosen to include an independent critic agent in the grading step (§5) because it directly targets the "confident wrong grade" failure mode. This is the one deliberate step beyond a strictly minimal core. It is recommended to clear this with the instructor: framed as "independent verification of grades," it is a safety feature rather than a flourish, but the team should confirm it does not read as 'extra.'

---

## 3. Scope

**Committed (must work end to end)**
- Ingest an assignment and course material; develop a model solution (or ingest a provided sample and cross-check it); draft a lenient rubric; all human-approved before grading.
- Grade a batch of submissions through the agentic grader + independent critic, with human review and approval of final grades.
- A feedback chat that explains grades, grounded in the rubric and the graded evidence.
- Deployable from a clean checkout (Docker Compose), open-source, BYOK.

**Stretch (only after the core is solid)**
- Additional assignment types beyond math via the verification-tool interface.
- Textbook-grounded rubric drafting via retrieval (see §6); feedback chat that cites sources.
- A fine-tuned small-model comparison against the API baseline.

**Out of scope**
- Handwriting / image OCR of submissions (typed or text-PDF submissions only).
- Fully autonomous grade submission — a human always approves. This is a hard rule, not a limitation.
- Validated absolute-accuracy claims — there is no human-graded golden set; evaluation is label-free (§13).

---

## 4. System Architecture (End to End)

The system is a fixed pipeline with agentic components placed only where runtime judgment is needed. Everything else is a predictable workflow.

**The pipeline stages**
1. **Ingest.** Parse the assignment into structured problems; ingest course material / textbook. Sanitize all inputs (submissions are untrusted).
2. **Develop solution.** Generate a model solution grounded in the course material, or ingest a provided sample and cross-check it. The solution is a human-approved artifact before it can grade.
3. **Draft rubric.** Turn the assignment into a lenient, per-problem rubric grounded in the course method. Human-approved.
4. **Grade (agentic + multi-agent).** For each submission: the grader agent verifies the answer with a tool, reads the shown work, assigns lenient partial credit; an independent critic agent challenges the grade; they reconcile or escalate to a human (§5).
5. **Feedback.** Explain each grade; answer student follow-ups in a chat, grounded in the graded evidence.
6. **Human review & finalize.** A person approves or overrides; finalizing writes the grade with a full audit trail.

**Assignment-type abstraction (why it is not assignment-specific)**
The type-specific logic is isolated behind a `VerificationTool` interface — conceptually `check_equivalence(student, reference)` and `verify(answer, problem)`. Math implements it with a symbolic-math library; a future code type would implement it with a test runner; a short-answer type with a semantic comparison. The grader, critic, rubric, context engineering, feedback, and human-loop are all type-agnostic and unchanged across types.

**Where retrieval (RAG) lives — and where it does not**
- **At rubric-design time only.** If a textbook or notes corpus is provided, P1 retrieves the relevant method so the rubric rewards the course's approach. That knowledge is then baked into the approved rubric and solution.
- **Not at grading time.** The grader works from the approved rubric and solution, so it does not re-retrieve per submission — that would add cost, latency, and a wrong-chunk failure mode to the most accuracy-critical step. For self-contained problems with no corpus, there is no RAG at all; context is engineered directly.

**Human-in-the-loop gates**
Three artifacts require human approval before they take effect: the model solution, the rubric, and every final grade. A generated solution is never trusted as the grading oracle until a human signs off — a wrong reference would silently corrupt every grade.

---

## 5. Multi-Agent Grading (Grader + Critic)

Grading is a two-agent step. The value is independence: the model that produced a grade shares the blind spots that produced it, so a separate agent checks it fresh.

**Grader agent.** A ReAct loop (reason → act → observe). It calls the verification tool to check the final answer, reads the shown work, and assigns lenient partial credit against the approved rubric, producing a proposed score with cited evidence and a reasoning trace.

**Critic agent.** An independent agent with a deliberately adversarial prompt ("find what is wrong with this grade"), ideally a different model or temperature. It receives the submission, the rubric criterion, the approved solution, and the grader's proposed score with evidence, and outputs an agreement signal plus reasoning — it does not write a new grade.

**Reconciliation rule.**
- **Agree →** finalize the proposed grade (still pending human approval).
- **Disagree →** the grader revises once given the critique; if they still disagree, the submission is escalated to human review.

**Design guards:** (1) the loop is bounded — one revision round, then escalate, so the agents cannot ping-pong forever; (2) the critic runs on subjective partial-credit judgments, not on objective tool-checked answers, to control cost and latency; (3) critic independence is monitored — if the critic agrees 100% of the time it is not working, so agreement rate is a tracked metric.

**Build order:** build the single grader first and get it working end to end, then add the critic as a layer. The solo grader is the baseline that lets P3 measure what the critic actually adds (do escalated cases catch grades the solo grader got wrong?).

---

## 6. Context Engineering

Context engineering is the deliberate construction of what goes into the model's context window — as opposed to dumping text or blindly retrieving. For grading, the relevant context is known in advance, so the task is assembling it well and within a token budget.

For each problem, the grader's context is assembled by `build_context(problem, submission, rubric)` and contains:
- The problem statement.
- The approved model solution and reference answer.
- The approved rubric criterion for that problem.
- **A standing grading-policy block** — course conventions and the leniency instruction (favor partial credit, reward correct method even with arithmetic slips, interpret charitably). This is injected on every grade, not retrieved.
- The student's shown work and final answer for that problem, sanitized.

**Retrieve vs. inject:** the principle is "retrieve when you don't know what's relevant; inject when you do." The grading-policy block and the problem's own solution/rubric are always relevant, so they are injected directly. A textbook method is a needle in a haystack, so it is retrieved — but only at rubric-design time, after which it lives in the approved rubric.

---

## 7. Technology Stack

| Layer | Choice | Why |
|---|---|---|
| Language | Python | First-class libraries for parsing, math, and LLM SDKs. |
| Backend | Streamlit only, no separate API tier | See note below — the three lane screens share one database directly. |
| System of record | PostgreSQL + SQLAlchemy | Durable, auditable rubrics, grades, and override log. |
| Retrieval (if corpus) | Chroma | Used only by P1 at rubric-design time. See note below on why not pgvector. |
| Verification tool | SymPy (math, first type) | Deterministic equivalence checking behind the tool interface. |
| Model access | BYOK provider module | Key + model from config; provider-agnostic; supports local models. |
| Frontend | Streamlit, unified into one multi-page app (`app.py`) | One deployed service reaches the whole pipeline, not just review. |
| Packaging | Docker Compose | One command brings up the app + Postgres. |
| License | MIT | Open source, as directed. |

> **Amended from the original plan (was: FastAPI + Chroma-or-pgvector).** Two deliberate mismatches between this plan's original ambition and the shipped code, resolved in the plan's favor of what actually runs end to end:
> - **No FastAPI.** P1/P2/P3 don't need to call each other over HTTP — they already share one Postgres instance (`P1Store`/`P2Store`/`P3Store`), which is a working integration layer on its own. Standing up a separate API tier (and, worse, splitting the three screens into separately-deployed services) would have meant three deployments, inter-service config, and network calls to solve a problem a shared database already solves — real complexity added for no corresponding benefit at this project's scale, and the opposite of the instructor's "don't add anything extra." Async batch grading (the original justification for FastAPI) is handled inside P2's own grading engine directly, not via an HTTP layer.
> - **Chroma, not pgvector.** Chroma is simpler to stand up for a P1-only, rubric-design-time concern (no Postgres extension to enable, works fully offline), and the retrieval corpus here is small enough that pgvector's "keep it in one DB" advantage doesn't outweigh that simplicity.
>
> A Streamlit-only stack that actually runs end to end beats a half-built API layer.

---

## 8. Data Model & Frozen Contracts

These are agreed on day one and frozen. Once frozen, each person builds against them using stubs, so no lane waits on another's real code.

**Core objects**
- `Assignment` — problems[]: { id, statement, reference_answer, reference_solution, solution_source (sample|generated), solution_status (proposed|approved), points }, type.
- `Rubric` — per-problem criteria[] { criterion, description, points, failure_signals }, leniency_note, version, status (proposed|approved).
- `Submission` — student_id, per-problem { work_text, final_answer }, sanitized_flag.
- `Grade` — a gate `status` (proposed|approved), an `escalated` flag, and provenance (`resolution` + approver); holds per-problem entries (`ProblemGrade`: outcome, points_awarded/points_possible, answer_matched, partial_credit_reason, evidence, critic_agreement).
- `Trace` — grader and critic tool calls, observations, revisions, stop reason.

**Function seams (who owns what)**
- `retrieve_method(problem)` — P1; returns textbook context, or nothing when no corpus is loaded.
- `draft_rubric(assignment, context)`, `develop_solution(problem)`, `verify_solution(problem)` — P1.
- `check_equivalence(a, b)` / `verify(answer, problem)` — P2 (the verification tool; P1 also calls it during solution verification).
- `build_context(problem, submission, rubric)` — P1 (per problem; takes the approved `Problem` for its statement and solution, plus `points_possible` on the returned context).
- `build_submission_context(assignment, submission, rubric)` → `SubmissionContext` — P1 → consumed by P2's `grade()`.
- `grade(submission, rubric, context)` → { Grade, Trace } — P2 (grader + critic) → consumed by P3.
- `generate_feedback(grade, rubric)`, `answer_followup(question, submission_id)` — P3.

**Database tables (owned by feature)**
- P1: assignments, problems, solutions (with status), rubrics (with version + status), textbook_index.
- P2: grades, grade_traces, critic_results.
- P3: reviews / approvals, override_audit_log, evaluation_runs.

---

## 9. Parallel Development Method

The whole plan hinges on building in a way that keeps three people unblocked and keeps the system runnable at all times.

**Day one, together**
1. Agree and freeze the data contracts (§8).
2. Build the BYOK provider module: one function that returns a model client from config, used identically by all three.
3. Scaffold the repo, FastAPI app, Docker Compose, and CI.
4. Commit a fixtures file: one realistic Assignment, Rubric, Submission, Grade, Trace — the fake data every lane builds against.
5. Build a walking skeleton: the thinnest end-to-end run (one hardcoded problem → stub rubric → real grade → real feedback → human-approve click) that actually executes.

**After that**
Each person deepens their own lane against the skeleton, replacing stubs with real modules one at a time. The skeleton must always stay green. Rule: if a task needs another person's code (not their contract) to start, it is decomposed wrong — split it so it can start against a stub.

---

## 10. Work Split — Three Lanes in Detail

Each person owns one hard component plus a proportional share of API, database, and UI — no glue-only role, no infrastructure bottleneck. Backend and frontend are split by feature.

| Lane | Owns | Primary learning |
|---|---|---|
| P1 — Ingestion, context & rubric | Parsing, textbook RAG, solution development + verification, rubric drafting, context engineering, rubric/solution editor UI | Context engineering (instructor-suggested) + RAG + prompt engineering |
| P2 — Grading (multi-agent) | Grader agent, critic agent, reconciliation, verification tool, batch orchestration, grade/override UI | Agents, multi-agent design, tool use |
| P3 — Feedback, review & deploy | Feedback + feedback chat, human-review/approval workflow, correctness check, evaluation, deployment & docs | Agentic RAG + evaluation + systems/deployment |

### P1 — Ingestion, Context & Rubric
*Learning: context engineering, RAG, prompt engineering. This is an instructor-suggested focus, so it is appropriately meaty.*
- Parse assignments into structured problems (text / text-PDF via a PDF text extractor; notebook via a notebook parser). Attach metadata (source, problem id).
- Input sanitization on the ingestion path (treat submission text as untrusted; never as instructions).
- Textbook ingestion + index (chunk, embed, store) — committed since the textbook matters for grading; retrieve the relevant method at rubric-design time.
- **Solution development:** generate a model solution grounded in the retrieved method, OR ingest a provided sample and cross-check the two; disagreements flag for human review.
- **Solution verification (separate function):** self-consistency + substitution via P2's tool. Kept as its own function so it can move to P2 later without a rewrite.
- Rubric drafting: LLM turns the spec + retrieved method into a lenient per-problem rubric with structured output.
- `build_context`: assemble problem + approved solution + rubric + grading-policy block + student work, within a token budget.
- UI: upload screen, rubric editor, solution-review screen (mirrors the rubric editor). DB: assignments, problems, solutions, rubrics, textbook_index.
- **Exposes:** `retrieve_method`, `develop_solution`, `verify_solution`, `draft_rubric`, `build_context`. Mocks nothing downstream (it is the source).

### P2 — Grading Agent + Critic (Multi-Agent)
*Learning: agents, multi-agent design, tool use.*
- The verification tool behind the type interface (SymPy for math): equivalence checking + substitution verification. Owned here; P1 calls it as a contract.
- Grader agent: ReAct loop — call the tool to check the answer, read the work, assign lenient partial credit, produce a proposed score + evidence + trace.
- Critic agent: independent adversarial review of the proposed grade; agreement signal + reasoning.
- Reconciliation: bounded one-round revision, then escalate to human review; track critic-agreement rate.
- Batch orchestration: grade many submissions concurrently (async, capped concurrency + backoff) against the same approved rubric; surface low-confidence / escalated grades to the top of the review queue.
- UI: grade panel with the trace and the override control. DB: grades, grade_traces, critic_results.
- **Exposes:** `grade(submission, rubric, context) -> {Grade, Trace}` and the verification tool. Mocks `build_context` with a fixture and consumes a Rubric fixture while building. No RAG.

### P3 — Feedback, Human Review & Deployment
*Learning: agentic RAG, evaluation, systems/deployment. Owns "it actually ships."*
- Feedback generation: per-problem explanation grounded in the grade's evidence and the rubric.
- Feedback chat: student follow-ups (agentic RAG; may optionally cite the textbook — the one place P3 touches retrieval, and it is polish).
- Human-review workflow: the approval/override screen that gates the solution, the rubric, and final grades; the finalize logic; the override audit log.
- Correctness check: final-answer match rate against the approved answer (cheap objective signal) + the label-free evaluation harness (§13).
- Deployment: Docker Compose, README + project documentation, MIT license, clean-checkout run, and a deploy target (Render / Railway / Fly.io / VM).
- **Exposes:** `generate_feedback`, `answer_followup`, evaluation reports. Consumes a Grade fixture while building.

### Shared by all three
- Own a lane + a report section + a demo segment; contribute to a small set of crafted exemplars (strong / middling / flawed) for evaluation; rotating pull-request review.
- One cross-cutting task each — P1: the end-to-end demo path; P2: the adversarial injection set; P3: the override logging / audit.

### Balance & the release valve
P1 and P2 are both heavy (P1: ingestion + RAG + solution + rubric + context; P2: grader + critic + tool + batch); P3 owns feedback + review + the whole deployment. If P1 runs hot in practice, the drawn-and-ready release valve is to move solution verification (already a separate function) to P2, whose tool it uses. Check the balance right after the walking skeleton runs — not at the end.

---

## 11. Integration Sequence & Definition of Done

Build in this order, not feature-by-feature: (1) the walking skeleton runs; (2) each lane swaps its stub for the real module, integrating continuously so the skeleton stays green; (3) polish only once the full path grades a real submission correctly. Resist deepening any lane before the skeleton connects — that is the exact trap that produces half-baked demos.

> **Definition of Done (agreed now, so "works end to end" is not fuzzy):**
> A fresh `docker compose up` on a clean machine: you upload an assignment (and, if provided, course material / a textbook); the system develops a model solution and drafts a lenient rubric, both of which you review and approve; you upload a batch of student submissions; the grader-plus-critic grades each with a visible trace, escalating low-confidence cases; you review, override where needed, and finalize; a student can open the feedback chat and ask why they lost a point; every grade and override is persisted in Postgres with an audit trail; and the whole thing runs from a clean checkout with your own API key (BYOK).

---

## 12. Testing Strategy

- **Unit:** the verification tool (equivalence edge cases), parsers, context assembly, sanitization.
- **Contract tests:** each lane's exposed functions validated against the frozen fixtures — this is what makes stub-swapping safe.
- **Integration:** the walking-skeleton path runs in CI on every commit, so end-to-end never silently breaks.
- **Adversarial:** the prompt-injection submission set must not change grades.
- **Grading sanity:** crafted strong/medium/flawed exemplars must be ranked in the correct order.

---

## 13. Evaluation (Label-Free)

There is no human-graded golden set, so absolute accuracy is unvalidated and the report says so plainly. The human-in-the-loop design also means every override is logged, so a golden set accumulates over time for later validation.

- **Reliability:** score variance across repeated runs on the same submission (low temperature).
- **Final-answer match rate:** objective ground truth for the answer portion, from the verification tool.
- **Grounding:** fraction of grades citing the approved solution / rubric.
- **Critic agreement rate:** sanity check that the critic is actually independent (never 100%).
- **Feedback quality:** LLM-as-judge — specific, consistent with the score, actionable.
- **Cost / latency and injection robustness.**

---

## 14. Deployment

Docker Compose brings up the FastAPI service, Postgres, and the vector store (if used) together, so the system runs from a clean checkout. Configuration — the API key and model — is supplied by the user (BYOK) via environment variables, never committed. The stack deploys to any container host (Render, Railway, Fly.io) or a plain VM. The repository is MIT-licensed with a README that documents setup, configuration, and the end-to-end run.

---

## 15. Risk Register

| Risk | Owner | Mitigation |
|---|---|---|
| A wrong model solution silently corrupts every grade | P1 | Human approval gate on the solution; cross-check generated vs. sample; self-consistency + substitution. |
| Grader confidently wrong | P2 | Independent critic; bounded revision then escalate; human approval on all grades. |
| Critic just agrees (no independence) | P2 | Adversarial prompt, different model/temperature; track agreement rate. |
| No labels → accuracy unvalidated | P3 | Reliability, grounding, answer-match rate; state limitation in report. |
| Submissions are hostile input | P1 & P2 | Sanitize on ingestion; never treat submission text as instructions. |
| One lane blocks another | All | Contract-first + stubs + fixtures + walking skeleton on day one. |
| Batch cost / rate limits | P2 | Spend cap, capped concurrency + backoff; run critic only on subjective criteria. |
| P1 overloaded | Team lead | Move solution verification to P2 (release valve); check after skeleton runs. |
| Half-baked demo | All | Build skeleton first; DoD in §11; CI runs the end-to-end path. |

---

## 16. Open Questions — Resolved with the Team

- **"Have docs"** — means project/code documentation (README + docstrings), not ingested course documents. Already largely true (README, docstrings throughout); a dedicated completeness pass is a fair follow-up, not yet done.
- **AI-developed model solutions** — acceptable as the grading basis when no sample exists, gated on human approval (as already built). The generated solution should be checked for missing/wrong points before that approval: `verify_solution` does this via self-consistency (independently re-derive, compare) and substitution (plug the answer back into the equation, check it holds). Note: self-consistency's comparison is SymPy-based, so it only meaningfully applies to short symbolic answers — for free-form answers (a proof, a written explanation), it now explicitly defers to human review rather than reporting a false "disagreement" (fixed; see `_looks_symbolic` in `lanes/p1_solution.py`). A full second-agent adversarial critic for solution-checking (mirroring P2's grader/critic) was considered and deliberately not built — the honest-skip design above was judged sufficient for the committed "simple math" scope, and proof-type verification is a different, larger problem (would need an LLM-as-judge for logical validity, not equivalence checking) that's out of scope unless the project adds a proof assignment type.
- **The independent critic (grading)** — confirmed a critical feature, not "extra." Built as designed: grader + critic + one bounded revision + escalation on persistent disagreement.
- **Textbook grounding** — hybrid, not all-or-nothing: the question takes precedence (self-contained correctness by default — any valid method earns full credit). But if the question is specifically testing a taught concept or method, grading should follow that method, and check against the textbook if it prescribes something specific for that concept.

---

## 17. Deliverables Checklist

- [ ] Demo — full run from a clean checkout; each member presents their segment.
- [ ] Report — one section per lane + shared scope / context-engineering / evaluation / ethics; states the no-golden-set limitation.
- [ ] Code — clean repo, README + docs, `docker compose up` runs it, tests pass, fixtures included, MIT license.
- [ ] Evaluation artifact — reliability, answer-match, grounding, critic-agreement, feedback-quality, injection results.
