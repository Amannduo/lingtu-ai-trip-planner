# INTEGRATION DECISION — local `main` × `origin/main`

**Date:** 2026-07-27
**Local snapshot reviewed:** `fce297e`
**Upstream:** `origin/main` @ `a4358ef`
**Merge base:** `f7d0b44 fix: isolate trip generation rate limits`
**Pushed for review:** `origin/feat/semantic-contract-quality-gate-integration`

Companion to the upstream `MERGE_REVIEW.md`, which documents the *other* side of
this divergence. Neither branch can be merged until the decision in §1 is made.

---

## 1. The blocking decision: two incompatible gate designs

Both lines re-implemented "may this trip plan be delivered?" with different data
models. This is not a textual conflict; one design has to win.

| | local `main` | `origin/main` (scheme 1) |
| --- | --- | --- |
| Decision field | `quality_status` — `blocked` / `needs_review` / `publishable` | `publishable` + `review_required` |
| `quality_status` on `TripPlanQualityResult` | present | **absent by design** |
| `review_required` | absent | present |
| Single authority | `resolve_plan_quality_status()` | `publishable` + `issue_disposition()` |
| Gate recomputation | `refresh_quality_gate()` keeps the triple coherent | blocking codes + severity |

`MERGE_REVIEW.md` states scheme 1 explicitly: *"No independent `quality_status`
field on `TripPlanQualityResult`. Delivery truth is `publishable` +
`review_required` + `status` + `issues`."*

**Decision required:** adopt `quality_status`, adopt `review_required`, or define
a mapping. Every other conflict below is downstream of this.

---

## 2. Conflict surface

`git merge-tree main origin/main` → **36 conflicting files**.

Content conflicts span nearly every core backend module:

```
api/routes/trip.py            services/trip_plan_quality_service.py
models/schemas.py             services/travel_plan_data_service.py
agents/trip_planner_agent.py  services/destination_feasibility_service.py
agents/graph/trip_planning_graph.py
api/main.py                   api/routes/{agent,auth,map,poi,push}.py
services/{amap,auth_service,email_quota,schema,transport_budget}.py
tools/{analytics_context,chart,llm_sql_agent,send_email}.py
frontend: Result.vue, AgentAssistantModal.vue, AgentChart.vue
```

Add/add conflicts (both sides created the same test file independently):

```
test_api_trust_boundary.py       test_backend_hardening.py
test_trip_planner_reliability.py test_trip_quality_and_jobs.py
test_weather_fallback.py         test_zhipu_search_service.py
```

Scale: **103 files differ, +7731 / −14576**. `git cherry origin/main main`
reports **0 of 19** local commits as already-upstream — despite `b8e2fa7` and
upstream `89b850d` sharing the subject *"harden trip planning validation and
trust boundaries"*. The two lines are genuinely independent implementations.

---

## 3. Alembic revision collision (must be resolved by hand)

```
local     revision = "20260727_0005"   down_revision = "20260712_0004"
upstream  revision = "20260725_0005"   down_revision = "20260712_0004"
```

Two different migrations claim `0004` as parent, so a merge produces two Alembic
heads. Local also carries `20260727_0006`, which has to be re-parented with it.

---

## 4. What each side would lose

Dropping either side silently drops its security hardening. Review per item.

**Only upstream has**

- `services/trip_pacing_contract.py`, `frontend/src/types/agentChart.ts`
- migration `20260725_0005_user_token_version.py`
- tests: analytics security, auth session security, chart tool, email delivery
  security, map/POI input boundaries, quality advisory policy, reviewable
  HTTP + jobs, web verification fallback

**Only local has**

- `services/contract_token_service.py`
- migrations `20260727_0005_travel_plan_user_budget`, `20260727_0006_request_contract_snapshots`
- tests: semantic scope/exclusions, recommender routing, quality repair-loop
  state, `quality_status` consistency, job notifications, sync timeout capacity,
  health minimisation, draft-save policy, contract token, budget separation,
  structured handoff, error-response contract

---

## 5. Verification status of the pushed snapshot

- backend suite green at push time (601 passed / 0 failed)
- `vue-tsc --noEmit` clean; `vite build` clean
- Every significant change was verified in a **clean worktree built from HEAD**,
  comparing against that HEAD's own baseline, rather than in the mixed working
  tree. The standard used throughout was *"no new failures relative to HEAD"* —
  not *"the whole suite is green"*, because HEAD carried pre-existing failures
  at several points during the day.

---

## 6. Recommended path

1. Decide §1 (gate design). Nothing else can be resolved consistently first.
2. Re-parent the Alembic chain so there is a single head.
3. Resolve the 36 conflicts in the direction the §1 decision implies, using §4
   as the checklist so no security test is dropped from either side.
4. Re-verify in a clean worktree against the post-merge HEAD.

Reviewing via the pushed branch keeps `origin/main` and local history untouched,
so this is reversible at every step: deleting the remote branch undoes it.
