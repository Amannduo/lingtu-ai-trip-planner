# Pre-commit review — trip planning validation / trust boundaries

**Role:** senior pre-commit code reviewer (read-only)  
**Repo:** `E:\agent\agent1`  
**Date:** 2026-07-25  
**Commit intent:** generation preflight (date + semantic + recommendation feasibility), server-trusted history restore, sync deadline/finalization safeguards, destination feasibility normalize, `pytest.ini` `testpaths=tests`, quality regression tests + quality-audit docs  

**Review method:** inspected the commit-intent surface in the working tree (especially `backend/app/api/routes/trip.py`, `destination_feasibility_service.py`, `backend/pytest.ini`, related tests under `backend/tests/`, and quality-audit docs). Binary `.git/index` could not be text-diffed from this review environment; if the staged set includes unrelated dirty-workspace files, re-check `git diff --cached --name-only` before commit.

**Primary files reviewed:**

| Path | Role |
| --- | --- |
| `backend/app/api/routes/trip.py` | Preflight, sync deadline, trust restore, quality gate for `/plan` and `/plan-jobs` |
| `backend/app/services/destination_feasibility_service.py` | County / short-trip circle normalize |
| `backend/pytest.ini` | Collection scope |
| `backend/app/services/trip_plan_quality_service.py` | `quality_status` derivation |
| `backend/app/agents/trip_planner_agent.py` | Public quality gate |
| Tests: `test_backend_hardening.py`, `test_semantic_contract_http_gate.py`, `test_api_trust_boundary.py`, `test_trip_trusted_fields.py`, `test_trip_quality_and_jobs.py`, `test_trip_plan_jobs.py`, `test_destination_recommender.py`, `test_trip_generation_capacity.py` | Regression coverage |
| `docs/quality-audit/*` | Audit artifacts (appropriate for this commit if intentionally included) |

---

## Summary

This commit intent is **coherent and shippable for a local commit**. The critical trust and preflight fixes are implemented on real code paths (not assertion-only), dual-wired for sync `/plan` and progressive `/plan-jobs`, and backed by solid regression tests for past dates, semantic HTTP gate, restore of server-owned facts, POI identity, hotel restore, sync timeout without persistence, plan-jobs `needs_review` without auto-save, and 扶风/麟游 feasibility.

Earlier review concern that `needs_review` was a dead branch under a pure `_plan_is_publishable` gate appears **addressed** by unified `_resolve_quality_status` used on both endpoints (reject only `blocked`; do not auto-persist `needs_review`; return response flags). Frontend edit UI (attraction `description` / `visit_duration` only) aligns with server restore policy.

Remaining items are **non-blocking residuals**: cooperative cancel after sync wall-clock timeout (capacity held until worker exits), mild stub-friendly remap in `_resolve_quality_status`, optional fail-open when `get_trip_request` is missing on edit, unused `import re`, and no dedicated sync-path `needs_review` HTTP test (jobs path covered).

---

## Priority answers

### 1. Any blocking issues for commit?

**No blocking issues** for the stated local commit intent, assuming the staged set is limited to the files listed above (or equivalent) and does not accidentally include unrelated dirty-workspace churn (frontend dist, rate-limit-only experiments, secrets, etc.).

Must-not-ship as blockers were:

| Former concern | Current status |
| --- | --- |
| `needs_review` dead under publishable-only gate | **Fixed** via `_resolve_quality_status` on both paths |
| Client can forge weather/budget/POI identity on PUT | **Fixed** via `_restore_verified_plan_facts` + tests |
| Past dates / semantic hard-block skip preflight | **Fixed** via `_validate_generation_request` before rate limit / agent |
| County short-trip mismatch (扶风 → 麟游) | **Fixed** via bilateral `normalize_location_for_matching` |

### 2. Hardcoding to pass tests?

**No security- or correctness-faking hardcodes** in restore/preflight.

There is one **fixture-shaped production shim** worth naming (not a blocker):

```371:398:backend/app/api/routes/trip.py
def _resolve_quality_status(plan: TripPlan) -> str:
    ...
    if status in {"publishable", "needs_review", "blocked"}:
        if (
            status == "blocked"
            and bool(getattr(quality, "publishable", False))
            and plan.generation_mode != "map_fallback"
            and not _quality_has_error_issues(quality)
        ):
            # Incomplete stub quality objects used by unit tests.
            return "publishable"
        return status
```

- Real `TripPlanQualityService.evaluate()` never yields `publishable=True` with `quality_status="blocked"` (`publishable = not has_blocking and score >= 75`; `blocked` only when blocking).
- Remap only fires for incomplete stubs that set `publishable=True` while leaving default `quality_status="blocked"`.
- It does **not** promote a truly blocked plan (`publishable=False` or error issues or `map_fallback`).
- Comment explicitly admits unit-test stubs; preferable long-term fix is complete fixtures (`quality_status="publishable"` when `publishable=True`), which `_ok_plan()` in `test_trip_plan_jobs.py` already does.

Past-date and semantic checks use real date/`collect_semantic_hard_block_issues` logic, not magic test cities. Feasibility graph still uses curated short-trip data (product rule, not test hardcode).

### 3. Does `pytest.ini` hide tests that should run? (`testpaths=tests`)

**No.** Current content:

```ini
[pytest]
testpaths = tests
python_files = test_*.py
python_functions = test_*
addopts = -q
```

- When pytest is run from `backend/`, collection is limited to `backend/tests/` — the only pytest suite that should run.
- `backend/scripts/` currently has **no** `test_*.py` / `*_test.py` sources (only leftover `e2e_test*.pyc` under `__pycache__`). So `testpaths` is a belt-and-suspenders fix for the former scripts pollution class (BUG-006), not a silent drop of active tests.
- Does **not** hide tests under `backend/tests/` matching `test_*.py`.
- Caveat: pytest.ini lives under `backend/`; CI must invoke from that directory (or pass `-c backend/pytest.ini`). That is normal for this layout.

### 4. Trip edit trust: over-restrict normal user edits?

**No — policy is intentional and matches current UI.**

Server allow-list for client presentation:

- Attraction `description`, `visit_duration` (via `_merge_trusted_attraction`)
- Attraction **order** / removal of existing attractions (keys must already exist)
- Not allow-listed (restored from server): weather, budget, generation_mode, overall_suggestions, agent_audit, web_references, **web_guide**, **map_context**, day narrative/transport/hotel/meals/routes/dates, POI identity fields, city/start/end identity

Frontend `Result.vue` edit mode only exposes attraction `visit_duration` and `description` — aligned with restore. Day text, hotel, meals, routes are display-only.

Not over-restrictive for the product’s “verified facts” model. Users who need structural changes must regenerate (identity mutation already 422s with a clear message). Residual product note: if a future UI allows meal/hotel edits, restore will silently undo them — document or expand allow-list then.

### 5. Sync deadline background residual risk?

**Yes, residual; not a commit blocker for this intent.**

`_generate_sync_with_deadline`:

- Uses `asyncio.wait_for(run_in_threadpool(worker), timeout=...)`.
- On timeout: cancels the **token**, returns HTTP **504**, does not await thread join.
- Threads are not killable; capacity acquired inside `run_with_generation_capacity` releases only when the worker returns after cooperative cancel checkpoints.
- Docstring correctly states this.

Coverage:

- `test_sync_generation_timeout_never_starts_persistence` — 504, no save; documents thread continuation (`finished.wait`).
- `test_sync_finalization_boundary_returns_timeout_without_persistence` — late finalization blocked.
- Job/capacity suite (`test_trip_generation_capacity.py`) covers release after cancel/timeout **when the worker observes cancel**.

**Residual risk:** after client-visible 504, a generation slot can remain held until the planner hits `raise_if_cancelled`. Under load this can amplify 429 capacity errors. Acceptable for this commit if product accepts cooperative cancel; follow-up would be shared job machinery for sync or capacity accounting for orphaned threadpool work.

### 6. plan vs plan-jobs validation consistency?

**Preflight: consistent. Quality / persist policy: now consistent enough to approve.**

| Concern | `/plan` (sync) | `/plan-jobs` (async) |
| --- | --- | --- |
| Past dates | `_validate_generation_request` | same |
| Semantic hard-block | same | same |
| Recommendation feasibility | same | same |
| Order vs rate limit | validate → rate limit → work | same |
| Quality resolve | `_resolve_quality_status` | same |
| `blocked` | raise quality rejection → HTTP 422 | raise → job error event |
| `needs_review` | no auto-persist; response flags | no auto-persist; SSE result + flags |
| `publishable` | save if authenticated | save if authenticated + finalization lease |

Minor intentional differences (not inconsistencies):

- Sync always claims finalization after successful generation return, then branches on quality; jobs claim finalization only on the publishable save path.
- Email delivery is sync-path only; jobs set `email_delivery=None` in the encoded result (delivery may be elsewhere / deferred — out of this intent).
- Coverage gap: `needs_review` is explicitly tested for **jobs** (`test_needs_review_plan_streams_result_without_auto_save`); sync path shares the same helpers but lacks a dedicated HTTP parity test.

### 7. Scope appropriate for this commit?

**Yes**, if the staged set matches the intent:

- Core product: `trip.py` preflight + trust restore + deadline + quality status alignment  
- Feasibility: `destination_feasibility_service.py` county normalize  
- Tooling: `backend/pytest.ini`  
- Tests that prove the above  
- Optional docs under `docs/quality-audit/` as audit deliverables  

**Scope watch-outs (process, not code bugs):**

- Working tree has historically contained many unrelated uncommitted changes; do **not** stage frontend `dist/`, `.env`, DB files (`qa_audit_*.db`, `travel.db`), server logs, or unrelated rate-limit/auth commits unless they are part of this intent.
- Quality-audit markdown is fine for a docs-inclusive local commit; keep secrets out of reports.

---

## Blocking issues (if any)

_None for the stated commit intent._

---

## Non-blocking notes

1. **Sync timeout capacity hold (RISK-SYNC-TIMEOUT)** — cooperative cancel; capacity may lag HTTP 504. Documented in code; capacity tests cover release after worker observes cancel, not “instant release at wait_for expiry.”
2. **`_resolve_quality_status` stub remap** — comment-admitted test/stub compatibility; prefer fixing incomplete fixtures over long-term production remaps.
3. **Edit quality fail-open if `get_trip_request` missing** — post-edit quality recompute is skipped; extra days beyond `len(existing.days)` are not stripped in restore (rely on quality gate). Prefer fail-closed 422 when request context cannot be loaded.
4. **Unused `import re`** in `destination_feasibility_service.py` (nit).
5. **Coverage gaps** — no dedicated sync `/plan` `needs_review` HTTP test; no unit assertion that `web_guide`/`map_context` restore is covered (code path present; narrative restore tests omit those two fields).
6. **Server-local `date.today()`** for past-date preflight — acceptable for CN-focused deployment; edge cases near midnight/other TZ.
7. **Staged-set hygiene** — re-run `git diff --cached --name-only` and reject accidental broad staging from the dirty workspace.

---

## What looks solid

1. `_validate_generation_request` shared by both create endpoints, before rate-limit spend and planner factory — dual HTTP tests for past dates.
2. `_merge_trusted_attraction` preserves user `description` / `visit_duration` while restoring identity/map facts — covered by `test_update_restores_poi_identity_not_just_coordinates`.
3. `web_guide` / `map_context` restored alongside other server narrative fields (prior gap closed in code).
4. Sync finalization boundary prevents timeout-then-late-persist class bugs.
5. Feasibility bilateral normalize makes 扶风 → 麟游(县) short-trip allowed as intended.
6. Agent public gate and route gate both treat only `blocked` as hard reject; mid-score `needs_review` can reach the client without auto-persist.

---

## Verdict: approve_commit

Approve the planned local commit for trip planning validation / trust boundaries, provided staged files are limited to this intent and do not drag unrelated workspace changes. Address residual timeout capacity and edit fail-open as follow-ups, not as commit blockers.
