# Intelligence Layer — Staged Rollout Guide

## Overview

The intelligence layer learns from tenant outcomes to improve recommendations over time. Because it touches ranking, scheduling, and content decisions, it must be rolled out carefully with reversibility at every stage.

---

## Stage 1: Shadow Mode (Week 1–2)

**Goal**: Pipeline runs, data accumulates, but nothing affects user-visible recommendations.

### What to Deploy
- Feature extraction job (nightly)
- Reward computation job (every 4h)
- Model health check (every 6h)
- Intelligence dashboard (read-only)

### What to Verify
- Feature vectors are being created for published posts
- Rewards are being computed from outcomes
- Health check reports "healthy" status
- Dashboard shows data flowing through

### What Stays Off
- Tenant pattern updates (no pattern learning yet)
- Cohort aggregation (no global priors yet)
- Blended recommendations (dashboard only shows raw data)
- Ranking uses static defaults only

### Rollback
- Stop the 3 jobs via scheduler (no user impact — they only write to learning tables)
- Learning tables can be truncated if needed

---

## Stage 2: Pattern Learning — Single Tenant (Week 3–4)

**Goal**: Enable pattern learning for one trusted tenant. Verify patterns are sensible.

### What to Enable
- Tenant pattern update job — pass `{"tenant_id": "UUID"}` for the test tenant only
- Blended recommendations endpoint — returns blended results for test tenant
- Evaluation runs — run for test tenant to establish baseline MAE

### What to Verify
- Patterns discovered match intuition (e.g., "posts at 7pm perform better")
- Confidence scores are reasonable given observation count
- Blending engine correctly ramps tenant weight as observations grow
- Evaluation MAE establishes a baseline (record this number)

### What Stays Off
- Pattern updates for all other tenants
- Cohort aggregation (no cross-tenant data yet)
- Auto-ranking (recommendations are visible but not driving decisions)

### Rollback
- Deactivate test tenant's patterns: `UPDATE tenant_patterns SET is_active = false WHERE tenant_id = 'UUID'`
- No other tenants affected

---

## Stage 3: Global Priors + Cold Start (Week 5–6)

**Goal**: Enable cohort aggregation and cold-start blending for new tenants.

### What to Enable
- Tenant pattern updates for all tenants (remove tenant_id filter)
- Cohort aggregation job (weekly)
- Cold-start blending (onboarding recommendations use global priors)

### What to Verify
- Global priors respect k-anonymity (min 3 tenants per aggregated stat)
- New tenants see "Global Default" labeled recommendations
- As a new tenant accumulates data, recommendations transition from global → blended → tenant-learned
- Existing tenants with sufficient data see mostly tenant-learned recommendations

### What Stays Off
- Auto-ranking (intelligence informs but doesn't drive slot selection)

### Rollback
- Clear global priors: `DELETE FROM global_pattern_stats`
- Disable pattern updates: remove from scheduler
- Cold-start recommendations fall back to empty (safe)

---

## Stage 4: Intelligence-Informed Ranking (Week 7–8)

**Goal**: Ranking decisions use learned patterns and blended recommendations.

### What to Enable
- PostRanker uses tenant patterns + global priors in scoring
- Bandit exploration with Thompson sampling
- Prompt version experiments

### What to Verify
- Evaluation MAE is stable or improving vs Stage 2 baseline
- No evaluation regression >20% (health check alerts on this)
- Ranking decisions are logged in audit trail with full explanations
- Exploration rate is appropriate (not too aggressive)

### Rollback
- Revert Ranker to static scoring (feature flag or code revert)
- Disable bandit exploration
- All learned data is preserved for re-enabling later

---

## Stage 5: Full Production (Week 9+)

**Goal**: All intelligence features active, monitoring stable.

### Steady-State Operations
- All 6 intelligence jobs running on schedule
- Health checks every 6h with alerting
- Weekly evaluation runs per tenant
- Dashboard available for operator inspection

### Ongoing Monitoring
- Watch MAE trends — should be flat or decreasing
- Watch confidence distributions — healthy system has mostly >0.5 confidence
- Watch alert count — should be 0 most of the time
- Watch pattern count per tenant — should stabilize after initial ramp

---

## Emergency Procedures

### Full Intelligence Shutdown
If the intelligence layer is causing issues across multiple tenants:

1. Stop all intelligence jobs (feature extraction, rewards, patterns, cohorts, health check)
2. Revert Ranker to static defaults
3. Intelligence dashboard remains available for diagnosis
4. No data loss — learning tables are preserved
5. Investigate via dashboard → alerts → audit trail → post drill-down

### Single Tenant Isolation
If one tenant is experiencing bad recommendations:

1. Pin their good patterns: `UPDATE tenant_patterns SET is_pinned = true WHERE tenant_id = 'UUID' AND confidence > 0.5`
2. Run manual evaluation: enqueue `run_evaluation` with their tenant_id
3. Check for data quality issues in their post drill-down

### Evaluation Regression
If health check fires `evaluation_regression` alert:

1. Compare the two most recent evaluation runs
2. Check what changed: new patterns? new rewards? code change?
3. If code change: roll back worker deployment
4. If data change: re-run patterns and evaluation
5. Document in incident log
