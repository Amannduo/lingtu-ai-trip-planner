# Quality-audit reviewer findings

Read-only review of the quality-audit fix round for Lingtu AI trip planner.

**Reviewed sources (not only the diff):**
- `backend/app/api/routes/trip.py`
- `backend/app/services/destination_feasibility_service.py`
- `backend/pytest.ini`
- `backend/app/services/trip_generation_job_service.py` (cancellation / capacity)
- `backend/app/agents/trip_planner_agent.py` (public quality gate)
- `backend/app/services/trip_plan_quality_service.py` (`quality_status` derivation)
- Related tests: `test_trip_quality_and_jobs.py`, `test_api_trust_boundary.py`, `test_backend_hardening.py`, `test_semantic_contract_http_gate.py`, `test_trip_trusted_fields.py`, `test_trip_plan_jobs.py`, `test_destination_recommender.py`

**Intended fixes in scope:**
1. `_validate_generation_request` — past dates, semantic hard-block, infeasible recommendation destinations
2. `_restore_verified_plan_facts` + `UntrustedTripEditError` on PUT history
3. `_generate_sync_with_deadline` — `asyncio.wait_for` + cancellation token
4. Destination feasibility — county-name normalize for short-trip circle match
5. plan-jobs `quality_status` vs publishable gate
6. `pytest.ini` `testpaths=tests`

---

## Summary

Core trust and preflight work is largely correct and well-aligned with the stated bugs:

- Past-date / semantic / recommendation feasibility preflight is shared by `/plan` and `/plan-jobs`, runs before rate-limit spend, and is covered by HTTP + unit tests.
- History restore preserves user-allowed **attraction** `description` / `visit_duration`, restores server-owned narrative/POI/hotel/budget/weather, and rejects forged new POIs. Identity immutability and post-edit quality recompute are coherent with BUG-003.
- County short-trip matching for 扶风 → 麟游(县) works via bilateral `normalize_location_for_matching`.
- Sync timeout cancels the token and blocks finalization/persistence in the covered cases.

**However there is one serious behavioral bug:** plan-jobs `needs_review` handling is effectively dead because `_plan_is_publishable` rejects every real `needs_review` plan before that branch runs. That makes `/plan` and `/plan-jobs` inconsistent for mid-score plans, and leaves the new response fields untested.

**Also material:** after sync `/plan` wall-clock timeout, the threadpool worker is not stopped; generation capacity can remain held until the planner cooperatively exits. Tests assert no late **persistence**, not capacity release after timeout.

No evidence of pure “hardcode only to pass tests” in restore/preflight logic. The `quality_status = "publishable"` remap after a successful publishable gate is defensive for stubs (`TripPlanQualityResult` defaults `quality_status="blocked"`) and matches BUG-005 intent; it is slightly test-fixture-shaped but not a fake pass of a real blocked plan.

If the only goals are preflight + trust restore + county match + pytest collection, those are in good shape. Ship risk is concentrated in **quality gate consistency** and **post-timeout resource hold**.

---

## Priority answers (must-cover)

| Priority | Finding |
|---|---|
| Beyond task scope? | Mildly. ETag/If-Match, identity immutability, edit-time quality recompute go beyond the six bullets but match BUG-003. Broader location normalization (province/station stripping) is reasonable for feasibility. |
| Hardcoded just to pass tests? | No for restore/preflight. `quality_status` remap after `_plan_is_publishable` is fixture-aware (default `blocked` + `publishable=True` in `_ok_plan`) — see Issue 2. |
| Impact on normal history editing? | User can still edit attraction description and visit_duration; day narrative / hotel / meals / routes / weather / budget / POI identity are intentionally not user-editable. |
| Restore overwrites user-allowed fields? | **No** for attraction `description` / `visit_duration` (`_merge_trusted_attraction`). Day-level `description` is server-owned and correctly restored. |
| Deadline thread/task leak? | **Yes, residual hold risk** — see Issue 3. Not an infinite leak if the worker eventually finishes and checks cancel, but capacity/thread stay occupied after HTTP 504. |
| Sync vs async consistency? | Preflight consistent. **Quality/needs_review and response shape are not** — see Issue 1. |
| SQLite vs PostgreSQL? | No new dialect-sensitive logic introduced by these fixes. History list SQL and optimistic `plan_json` compare pre-exist; both engines handle the patterns used. Not a blocker for this round. |
| Tests truly cover fixes? | Strong for past dates, restore trust, POI identity, hotel restore, sync timeout no-save, semantic HTTP gate, 扶风/麟游 feasibility. **Weak/absent for `needs_review` path and post-timeout capacity release.** |

---

## Issues

### Issue 1 -- Severity: bug
- File: `backend/app/api/routes/trip.py:751-763` (interacts with `trip_planner_agent.py:189-217` and `trip_plan_quality_service.py:1290-1297`)
- Description:
  plan-jobs worker does:

  1. `if not _plan_is_publishable(trip_plan): raise TripPlanQualityRejectedError`
  2. then derives `needs_review` from `quality_status == "needs_review"`

  `_plan_is_publishable` requires `quality.publishable is True` (and score ≥ 75, no error issues, not `map_fallback`).

  Real quality service sets:
  - `publishable = not has_blocking and score >= 75`
  - `quality_status = "needs_review"` only when **not** blocking **and** `score < 75` → therefore `publishable` is always `False` for `needs_review`

  So any genuine `needs_review` plan is rejected at step 1 and never reaches the “skip save / return needs_review” branch. That branch is dead for production quality objects.

  Meanwhile the agent public gate **allows** `needs_review` through (`quality_status == "blocked"` only raises). Sync `POST /plan` then **always saves** authenticated plans without a second publishable check and without setting `TripPlanResponse.needs_review` / `quality_status`.

  Net:
  - `/plan`: mid-score non-blocking plan → 200 + may persist
  - `/plan-jobs`: same plan → quality rejection error event, no result payload with `needs_review=true`
- Suggestion:
  Align both endpoints on one policy:
  - Prefer agent’s unified `quality_status`: reject only `blocked`; allow `needs_review` with no auto-persist; allow `publishable` with persist; **or**
  - If product wants jobs to require `publishable`, remove the dead `needs_review` response path and make the agent gate match (also reject `needs_review`).
  - Apply the same response fields (`needs_review`, `quality_status`) on sync `/plan` if that path remains public.
- Status: open

### Issue 2 -- Severity: suggestion
- File: `backend/app/api/routes/trip.py:757-762`
- Description:
  After `_plan_is_publishable` succeeds, any `quality_status` outside `{publishable, needs_review}` is forced to `"publishable"`. Comment cites default `quality_status="blocked"` on `TripPlanQualityResult`.

  Real `evaluate()` always sets a coherent status; the remap primarily rescues **test/stub** plans like `_ok_plan()` (`publishable=True`, default `quality_status="blocked"`). It does not falsely publish a plan that failed `_plan_is_publishable`, so it is not a security hole, but it papers over incomplete quality objects instead of requiring callers to set `quality_status` correctly.
- Suggestion:
  Prefer fixing stubs/fixtures to set `quality_status="publishable"` when `publishable=True`, and/or treat missing/default status as a hard failure in the worker. Keep remap only if intentionally supporting partial quality payloads from older agents.
- Status: open

### Issue 3 -- Severity: bug
- File: `backend/app/api/routes/trip.py:219-255` (with `run_with_generation_capacity` in `trip_generation_job_service.py`)
- Description:
  `_generate_sync_with_deadline` uses `asyncio.wait_for(run_in_threadpool(worker), timeout=max_runtime)`. On timeout it cancels the **awaitable** and calls `progress.cancel("generation_timeout")`, then returns HTTP 504.

  Threads cannot be cancelled. The worker continues inside the thread pool until `plan_trip` finishes or hits `raise_if_cancelled` / progress checks. Capacity is acquired inside `worker()` and only released when that function returns — so after client-visible timeout, a generation slot can remain held for the remainder of a long planner run.

  `test_sync_generation_timeout_never_starts_persistence` correctly asserts no save and waits for the thread to finish after releasing a block — it documents continuation, not capacity hygiene.
- Suggestion:
  - Document that cancellation is cooperative.
  - After timeout, ensure progress cancel is set **before** any further work (already done) and that planner checkpoints are frequent enough.
  - Optionally track orphaned threadpool generations and count them against capacity until join; or run sync generation on the same job service machinery that already models deadline/finalization.
  - Add a regression test: after `/plan` 504, `generation_capacity_snapshot()["held"]` returns to 0 within a bounded time once the worker observes cancel (or assert held remains 1 only while the blocking worker still runs, then 0).
- Status: open

### Issue 4 -- Severity: suggestion
- File: `backend/app/api/routes/trip.py:114-165`
- Description:
  `_restore_verified_plan_facts` restores `generation_mode`, `overall_suggestions`, `weather_info`, `budget`, `agent_audit`, `web_references`, and per-day server fields, but **not** `web_guide` or `map_context`. Clients can still overwrite those on PUT and persist them if the post-edit quality gate does not flag them.

  Attraction identity merge is correct; user `description` / `visit_duration` are preserved.
- Suggestion:
  Restore `web_guide` and `map_context` from `existing` (same trust class as `web_references` / narrative facts), or explicitly document them as client-writable and validate length/content.
- Status: open

### Issue 5 -- Severity: suggestion
- File: `backend/app/api/routes/trip.py:145-161`
- Description:
  Extra days beyond `len(existing.days)` are not stripped; the loop `break`s and leaves client-supplied extra days intact (attraction keys must still exist in the global trusted set). Day-count / schedule issues are deferred to the quality gate.

  That is OK **when** `get_trip_request` succeeds and `_quality_blocks_edit_save` runs. If request rebuild fails (`get_trip_request` → `None`), the quality recompute is skipped and a structurally odd plan could be saved with only partial restore.
- Suggestion:
  Fail closed when `get_trip_request` is missing (422) rather than skipping the gate; and/or reject `len(edited.days) != len(existing.days)` inside restore.
- Status: open

### Issue 6 -- Severity: suggestion
- File: tests (coverage gap)
- Description:
  Coverage that **does** exercise the fixes well:
  - Past dates before planner: `test_past_trip_is_rejected_before_planner_call`, `test_past_dates_never_reach_planner_factory`, `test_generation_request_rejects_past_dates_before_agent_work`
  - Semantic hard-block HTTP: `test_semantic_contract_http_gate.py`
  - Infeasible recommendation preflight: `test_preflight_rejects_stale_infeasible_recommendation`
  - County circle: `test_fufeng_inherits_baoji_short_trip_feasibility_circle` (`assess("宝鸡扶风","麟游县",2).allowed is True`)
  - Restore + user fields: `test_update_restores_poi_identity_not_just_coordinates`, `test_edit_restores_all_server_owned_narrative_and_route_facts`, `test_trip_trusted_fields`
  - Sync timeout no persistence: `test_sync_generation_timeout_never_starts_persistence`, `test_sync_finalization_boundary_returns_timeout_without_persistence`
  - Publishable defense on jobs: `test_unpublishable_plan_never_leases_or_triggers_delivery`
  - Stub with default `quality_status`: `test_trip_plan_jobs._ok_plan` + stream result (BUG-005)

  Coverage that is **missing**:
  - No test references `needs_review` at all under `backend/tests/`
  - No test that a score&lt;75 non-blocking plan returns success with `needs_review=true` and `plan_no is None` on plan-jobs
  - No sync/async parity test for the same quality fixture
  - No assertion that recommendation feasibility preflight is hit on both HTTP paths with a live TestClient (unit-only for `_validate_generation_request` on 乌鲁木齐 case)
- Suggestion:
  Add explicit tests for the intended `needs_review` product behavior on both endpoints; add HTTP dual-path test for infeasible `destination_source=recommendation`.
- Status: open

### Issue 7 -- Severity: nit
- File: `backend/app/services/destination_feasibility_service.py:6`
- Description:
  `import re` is unused after the normalization additions.
- Suggestion:
  Remove unused import.
- Status: open

### Issue 8 -- Severity: nit
- File: `backend/app/api/routes/trip.py:69-73`
- Description:
  Past-date check uses server-local `date.today()`. Users near midnight in other timezones could see surprising 422s. Acceptable for a CN-focused product; not introduced as a correctness bug relative to prior intent.
- Suggestion:
  Optionally document “server calendar date” or use a configured timezone (e.g. `Asia/Shanghai`).
- Status: open

---

## What looks solid (no issue invented)

1. **`_merge_trusted_attraction`** correctly starts from trusted identity/map facts and only copies user presentation fields — does **not** overwrite user-allowed description/visit_duration with server values.
2. **Day description / overall_suggestions / routes / meals / hotels** treated as server-owned matches BUG-003 (forging those was the original vulnerability).
3. **`_validate_generation_request`** order (validate → rate limit → agent) is right; recommendation feasibility uses `explicit_destination=False` appropriately.
4. **County normalize**: graph entry `麟游县` vs destination `麟游` matches via `nearby_normalized`; verified interactively allowed for 宝鸡扶风 + 麟游县 / 麟游.
5. **Finalization boundary** on sync path (`_begin_generation_finalization` after wait_for) prevents late save when the deadline elapses between generation return and persist — tested.
6. **`pytest.ini` `testpaths=tests`** correctly scopes collection (addresses e2e script pollution class of problem without renaming scripts).
7. **SQLite/PG**: no change in this round that would pass on SQLite and fail on PostgreSQL (or vice versa) in an obvious way.

---

## Overall verdict

**Not clean enough to call “no serious issues.”**
Issue 1 (dead `needs_review` + sync/async quality divergence) and Issue 3 (post-timeout thread/capacity hold) are the open serious items. Trust restore and generation preflight meet their primary goals and are adequately tested for the happy/forgery paths.
)
