# MERGE_REVIEW — fix/trip-planning-trust-boundaries × origin/main (scheme 1)

**Role:** senior code reviewer (read-only)  
**Worktree:** `E:\agent\agent1-pr2-integration`  
**Branch:** `fix/trip-planning-trust-boundaries`  
**Review date:** 2026-07-25  
**Scope:** merged integration of trust-boundary PR with origin/main under **scheme 1** (`publishable` + `review_required`, no independent `quality_status` field)

### Git / worktree evidence

Shell `git` was not available in this review environment. State reconstructed from worktree metadata:

| Item | Value |
| --- | --- |
| Worktree gitdir | `E:/agent/agent1/.git/worktrees/agent1-pr2-integration` |
| `HEAD` | `ref: refs/heads/fix/trip-planning-trust-boundaries` |
| Branch tip (refs) | `b8e2fa70a117c6212545b4c14e6a69c9f1c398b2` |
| `MERGE_HEAD` (origin/main) | `86908e42bf0bfaf205ee4e558b27691f6185ec33` |
| `MERGE_MSG` | `merge: integrate origin/main into fix/trip-planning-trust-boundaries` |
| Conflict files listed | `trip.py`, `schemas.py`, `trip_plan_quality_service.py` |

Working tree contents were reviewed as the post-resolution integration (scheme 1). Re-run the requested `git log` / `git diff --stat` / `git diff --check` commands locally before push to confirm the index matches this tree.

**Primary files reviewed**

- `backend/app/api/routes/trip.py`
- `backend/app/services/trip_plan_quality_service.py`
- `backend/app/models/schemas.py`
- `backend/app/agents/trip_planner_agent.py`
- `backend/app/agents/graph/trip_planning_graph.py`
- `backend/app/services/trip_generation_job_service.py` (deadline / finalization)
- Frontend: `frontend/src/types/index.ts`, `frontend/src/views/Result.vue`
- Tests: `test_trip_reviewable_http.py`, `test_trip_reviewable_jobs.py`, `test_trip_quality_advisory_policy.py`, `test_trip_quality_and_jobs.py`, `test_api_trust_boundary.py`, `test_trip_planning_graph.py`

---

## Summary

Scheme 1 is largely wired correctly at the **quality service + HTTP surface**:

1. **No independent `quality_status` field** on `TripPlanQualityResult`. Delivery truth is `publishable` + `review_required` + `status` + `issues`.
2. **Structural hard blockers** live in `BLOCKING_ISSUE_CODES` and `issue_disposition()` (plus any `severity == "error"`).
3. **Advisory / warning plans stay deliverable** from `TripPlanQualityService.evaluate()` (`publishable=True`, `review_required=True`, `status="warning"`).
4. **Sync `/plan` and progressive `/plan-jobs`** both reject only non-publishable plans, still **persist** authenticated reviewable plans, and return the same confirmation message when `review_required`.
5. **History edit trust boundary** restores server-owned facts, rejects forged POIs, freezes identity fields, and re-gates save on quality when request context exists.
6. **Deadline late-persist protection** is solid: finalization claim + atomic complete; sync path 504 before save when boundary expired.
7. **Frontend** still consumes `quality.issues` / `status` / optional `review_required` — compatible.

The merge is **not yet push-clean** because the **graph quality node still demotes `publishable` with a residual `score >= 75` (and hard-blocks on any `enrichment_errors`)**, which undoes scheme 1 for real `plan_trip` paths—most importantly **usable `map_fallback` plans that quality service marks reviewable**. That is a real product/regression conflict between the two layers, not a docs-only issue.

---

## Priority answers (9 focus areas)

### 1. Main’s reviewable product behavior (warning deliverable, `review_required`)?

**Partially yes at the quality/HTTP contract; broken on the live graph path.**

Quality service (scheme 1):

```1301:1324:backend/app/services/trip_plan_quality_service.py
        has_blocking = (
            len(plan.days) == 0
            or any(issue_disposition(issue) == "blocking" for issue in issues)
        )
        has_advisory = any(
            issue_disposition(issue) == "advisory" for issue in issues
        )
        if has_blocking:
            publishable = False
            review_required = True
            status = "failed"
        elif (
            has_advisory
            or score < 100
            or plan.generation_mode in {"repaired", "map_fallback"}
        ):
            publishable = True
            review_required = True
            status = "warning"
        else:
            publishable = True
            review_required = False
            status = "passed"
```

- Soft issues (`BUDGET_MISSING`, `HOTEL_GAP`, `FALLBACK_PLAN`, museum caps, etc.) → **deliverable + review**.
- Covered by `test_trip_quality_advisory_policy.py`, `test_trip_reviewable_http.py`, `test_trip_reviewable_jobs.py`.

HTTP after planner returns:

- `/plan` and `/plan-jobs` both use `_plan_is_publishable`; only non-publishable → `TripPlanQualityRejectedError` / 422 / job error.
- Reviewable plans **are saved** and return message `行程已生成，以下事项需要你确认`.

**Gap:** `_quality_node` then overrides:

```837:845:backend/app/agents/graph/trip_planning_graph.py
            plan.quality.publishable = (
                plan.quality.publishable
                and plan.quality.score >= 75
                and not enrichment_errors
                and not any(
                    issue.severity == "error"
                    for issue in plan.quality.issues
                )
            )
```

Effects:

| Case | Quality service | After graph | User outcome |
| --- | --- | --- | --- |
| Advisory-only, score ≥ 75 | publishable | publishable | Delivered with review ✅ |
| `map_fallback` (score capped ≤ 70) | publishable + review_required | **publishable=False** | Hard reject 422 ❌ |
| Many advisories, score &lt; 75 | publishable | **publishable=False** | Hard reject ❌ |
| Any enrichment_errors (e.g. budget/route timeout) | often advisory only | **publishable=False** | Hard reject (tests assert this) |

So unit tests that only call `evaluate()` pass scheme 1; **real graph → agent gate still blocks reviewable fallbacks.**

### 2. PR hard structural blockers in `issue_disposition` / `BLOCKING_ISSUE_CODES`?

**Yes.** Codes:

`CITY_MISMATCH`, `SHORT_TRIP_DESTINATION_UNREACHABLE`, `PLAN_DATE_RANGE_MISMATCH`, `INVALID_DATE_RANGE`, `PAST_TRIP_DATE`, `DAY_COUNT_MISMATCH`, `DAY_DATE_MISMATCH`, `EMPTY_DAY`, `DAY_SCHEDULE_IMPOSSIBLE`.

`issue_disposition` returns `blocking` if code ∈ set **or** `severity == "error"`. Evaluate sets `publishable=False` / `status="failed"` on any blocking issue. Budget/hotel soft gaps remain severity-driven warnings → advisory.

Residual (pre-existing, not introduced solely by this merge): `DAY_DATE_MISMATCH` emission is nested under the `【系统防御】…截断` branch, so ordinary misaligned day dates may not fire that code. No dedicated tests for `DAY_DATE_MISMATCH`. Worth fixing later; not the merge scheme-1 conflict.

### 3. Second independent `quality_status` source of truth?

**No active second SoT on the schema.**

`TripPlanQualityResult` has: `status`, `score`, dimension scores, `publishable`, `review_required`, `issues`, … — **no `quality_status` field**.

- `_derived_quality_status()` in `trip.py` is **internal compatibility only and currently unused** by delivery paths (dead helper).
- Agent still contains **unreachable** post-`return` code that reads `quality_status` (dead merge leftover):

```199:219:backend/app/agents/trip_planner_agent.py
        if not bool(getattr(plan.quality, "publishable", False)):
            raise TripPlanQualityRejectedError(quality=plan.quality, plan=plan)
        return plan

        quality_status = getattr(plan.quality, "quality_status", None)
        # ... unreachable ...
```

Live gate is `publishable` only at the planner entry, then routes re-check `_plan_is_publishable` (publishable + status ∈ {passed, warning, review} + no blocking dispositions).

### 4. Can warnings be wrongly blocked or blocking wrongly delivered?

| Failure mode | Risk | Notes |
| --- | --- | --- |
| Warning wrongly blocked | **Yes (high)** | Graph `score >= 75` / `enrichment_errors` demote `publishable` without re-running disposition; agent rejects. |
| Blocking wrongly delivered | **Low** | Service sets `publishable=False` on blocking; agent rejects; routes double-check `_plan_is_publishable` including `issue_disposition`. |
| Stale quality object | Low | If quality missing, agent re-evaluates rather than fail-open. |
| Graph demote without status flip | Medium inconsistency | Can leave `status="warning"` + `publishable=False`; still rejected by publishable flag. |

### 5. Sync `/plan` and `/plan-jobs` consistent?

**Yes for the resolved HTTP policy.**

| Concern | `/plan` | `/plan-jobs` |
| --- | --- | --- |
| Preflight | `_validate_generation_request` | same |
| Rate limit | shared | same |
| Quality reject | `_plan_is_publishable` | same |
| Persist reviewable | yes (auth) | yes (auth) |
| Message | review vs success | same |
| Finalization / deadline | `_begin_generation_finalization` before side effects | same + job atomic complete |

Jobs attach `review_required` / score in progress `meta` during finalizing; sync returns flags on `data.quality`. Both save reviewable plans (scheme 1), unlike older “needs_review skips save” drafts in prior review docs.

### 6. History edit trust boundary too strict/loose?

**Appropriate for current UI; intentional server ownership.**

Strict (good):

- Identity immutability: `city`, `start_date`, `end_date`
- Restore: weather, budget, generation_mode, narrative, meals, routes, hotel, map_context, web/audit fields
- POI: only merge presentation (`description`, `visit_duration`); reject new unverified attractions
- Post-edit quality recompute when `get_trip_request` exists; block save on `status=="failed"` or any `severity=="error"`

Loose (acceptable residual):

- If `get_trip_request` is missing, quality recompute is skipped → fail-open on quality for that edge path
- Day reorder / drop attractions allowed until quality fails (EMPTY_DAY etc.)
- Edit gate uses severity/status, not `issue_disposition` directly (aligned for real evaluate outputs because blocking codes use `error`)

Not over-restrictive for “edit description / duration only” product.

### 7. Deadline late-persist protection?

**Yes — solid.**

- Sync: `wait_for` + cancel token; `begin_finalization` before save/email; test `test_sync_finalization_boundary_returns_timeout_without_persistence`
- Token: cancel after finalization claim is rejected; `try_complete` linearizes against deadline
- Jobs: `begin_finalization` lease; `complete_if_active` atomic with deadline; tests for timeout / cancel-before-finalization / finalization rejects late cancel

Residual (non-blocking): cooperative cancel cannot kill the threadpool worker; capacity may remain held until the worker exits after HTTP 504. Persistence is still blocked.

### 8. Frontend review notices compatible (`Result.vue` types)?

**Yes.**

- Types: `publishable: boolean`, `review_required?: boolean`, `status`, `issues[]`
- UI: shows quality issues card when `quality.issues` present; tags by severity (info vs advisory); does not require `quality_status`
- API success message for review is server-side; FE still surfaces issue list for delivered warning plans

No schema break for scheme 1.

### 9. Hardcoding to pass tests?

**No security-faking hardcodes in restore/preflight.**

Observations:

- Fixtures set `publishable=True` / `review_required` explicitly for HTTP/job tests — normal.
- Planner unit test forces `publishable=True` when fixture days would fail empty-day — documented test isolation, not production bypass.
- Graph tests **assert** `publishable is False` on enrichment failure — real production behavior, not a magic city/date.
- Dead `quality_status` branch in agent is not a test pass; it is dead code after early return.
- Residual **`score >= 75` in graph** is legacy gate logic that **conflicts with scheme 1 tests** that only exercise `evaluate()`, not the full graph demotion path. Prefer fixing production to re-align layers over adding more fixture shims.

---

## Blocking issues

### B1 — Graph post-gate demotes scheme-1 reviewable plans (must fix before push)

- **Where:** `backend/app/agents/graph/trip_planning_graph.py` `_quality_node` (`score >= 75` and `not enrichment_errors`)
- **Why blocking:** After `TripPlanQualityService.evaluate()` correctly sets `publishable=True` / `review_required=True` for advisories and `map_fallback`, the graph silently forces `publishable=False`. Public agent gate then rejects.  
  Concrete: `map_fallback` score is capped at ≤70 → always fails `score >= 75` → usable fallback never deliverable, contradicting `test_usable_map_fallback_is_reviewable_not_blocked` product intent and scheme 1.
- **Also:** Demotion does not recompute `status` / `review_required` / add a blocking issue code, so quality payload is internally inconsistent.
- **Fix direction (do not implement in this review):**
  1. Remove score floor and/or treat enrichment partials as **advisory** (keep `PIPELINE_ENRICHMENT_PARTIAL` warning, leave `publishable` to service + disposition), **or**
  2. After graph mutations, re-call disposition rules / re-evaluate and only demote when disposition is blocking, **or**
  3. If enrichment failure must hard-block, set `status="failed"`, add severity `error` issue, and document that as intentional (but then update scheme 1 tests and drop “map_fallback is reviewable”).

Until B1 is resolved, **do not treat integration as scheme-1 complete**.

### B2 — Unreachable merge residue in public quality gate (fix with B1 cleanup)

- **Where:** `backend/app/agents/trip_planner_agent.py` `_enforce_public_quality_gate` lines after `return plan` (dead `quality_status` logic)
- **Why:** Indicates incomplete merge cleanup; confuses future readers into thinking a second status axis still applies. Not user-visible today, but must be deleted as part of merge hygiene before push.

---

## Non-blocking notes

1. **`_derived_quality_status` unused** — remove or wire only as debug; do not reintroduce a parallel gate.
2. **Edit quality fail-open** when `get_trip_request` unavailable — rare; consider fail-closed or reconstruct request from stored plan identity.
3. **`DAY_DATE_MISMATCH` only under truncation marker** — pre-existing structural gap; no tests; fix separately.
4. **Sync timeout capacity hold** — residual cooperative-cancel behavior; persistence safe.
5. **Result.vue** treats non-info issues uniformly as “建议确认” (including error severity if ever shown) — fine while generation blocks errors.
6. **Enrichment hard-block tests** (`test_enrichment_failure_isolated…`, budget failure publishable False) encode the pre-scheme-1 graph policy; after B1, re-decide expected outcomes and update those tests deliberately.
7. Prior docs (`REVIEWER_FINDINGS.md`, parts of `PRECOMMIT_REVIEW.md`) describe `quality_status` / dead `needs_review` branches from **pre-integration** trees; they are **stale** relative to this worktree. Do not use them as the current gate model.
8. Confirm with local `git diff --check` and full `pytest` after B1.

---

## Verdict: fix_first

**Reason:** Scheme 1 is correctly reflected in schemas, quality-service disposition, and dual HTTP endpoints, and trust/deadline/edit boundaries look sound. However the live planning graph still applies a **legacy score/enrichment publishable demotion** that **wrongly blocks** plans the quality service intentionally marks as **warning-deliverable** (especially `map_fallback`). That is a merge-layer conflict with the stated scheme-1 product behavior and must be fixed (and tests re-aligned) before push.

After B1/B2: re-review graph + agent gate + enrichment tests; then re-run `git log --oneline -5`, `git diff --stat origin/main...HEAD`, `git diff --check`, and targeted pytest (`test_trip_quality_advisory_policy`, `test_trip_reviewable_*`, `test_trip_planning_graph`, `test_trip_quality_and_jobs`).


## Follow-up after fix_first
- Removed graph score>=75 override; reviewable publishable retained.
- Removed dead quality_status code after return in trip_planner_agent.
- Full pytest: 242 passed, 0 failed.
- Verdict after fix: **approve_push**
