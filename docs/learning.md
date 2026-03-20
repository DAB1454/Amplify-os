# Learning Subsystem

## Goals

The learning subsystem helps Amplify-OS get smarter over time — recommending
better posting times, content types, and campaign strategies based on observed
outcomes.  It operates under three hard constraints:

1. **Explainable** — every recommendation includes a full breakdown of *why*.
   No opaque scores.  Every component is named, weighted, and traceable to
   source observations.

2. **Auditable** — every learning decision is logged.  Insights link back to
   the observations that produced them.  Evaluation metrics measure whether
   the system is actually helping.

3. **Privacy-safe** — tenant data is isolated by default.  Cross-tenant
   learning only happens through aggregate statistics that meet k-anonymity
   thresholds.  PII fields are stripped before aggregation.

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                        apps/api, apps/worker                     │
│                     (consumers of learning output)               │
└────────┬──────────────────────────────────────────┬─────────────┘
         │                                          │
         ▼                                          ▼
┌─────────────────┐                      ┌─────────────────────┐
│     ranking      │◄────────────────────│    policies          │
│  (score + rank)  │                     │  (advisory rules)    │
└───┬─────────┬───┘                      └──────────────────────┘
    │         │
    ▼         ▼
┌────────┐  ┌──────────────┐
│ tenant │  │ global_priors │
│ memory │  │ (aggregated)  │
└───┬────┘  └──────┬───────┘
    │              │
    │         ┌────┴─────┐
    │         │ privacy   │   ◄── gatekeeper: k-anon, redaction
    │         └────┬─────┘
    │              │
    ▼              ▼
┌──────────────────────────┐
│    rewards               │   ◄── outcome → scalar reward
└────────┬─────────────────┘
         │
         ▼
┌──────────────────────────┐
│  feature_extraction      │   ◄── raw data → feature vectors
└──────────────────────────┘
         ▲
         │
┌──────────────────────────┐
│  schemas                 │   ◄── shared vocabulary (Observation,
│  (Observation, Insight,  │       FeatureVector, Insight, ScoredAction)
│   FeatureVector, Score)  │
└──────────────────────────┘
```

## Data Flow

### Recording an observation

1. A post is published and metrics are collected (via adapters).
2. **feature_extraction** converts the post context into a `FeatureVector`:
   platform, content type, posting hour, campaign phase, etc.
3. **rewards** computes a composite reward score from `OutcomeMetrics`
   (engagement rate, save rate, reach, CTR) using configurable weights.
4. An `Observation` (features + outcomes + reward) is stored in
   **tenant_memory** for the owning tenant.
5. Periodically, **privacy** sanitizes tenant observations (strip PII,
   check cardinality) and feeds them to **global_priors** for aggregation.

### Producing a recommendation

1. A user (or agent) asks "what should I post next?" with a set of
   candidate actions.
2. **ranking** scores each candidate by:
   - Computing feature-based scores from **tenant_memory** observations.
   - Blending with **global_priors** (weighted by how much tenant data
     exists — cold-start tenants lean on priors, established tenants
     lean on their own data).
   - Optionally injecting exploration noise to gather new signal.
3. Each `ScoredAction` includes a `RankingExplanation` with a full
   component breakdown.
4. **policies** provides `PolicyRule` implementations that the core
   policy engine can use — e.g., flagging posts at historically poor
   times for review.

### Evaluating accuracy

**evaluation** periodically compares past predictions against actual
observed rewards:
- Mean Absolute Error (MAE)
- Spearman rank correlation (do higher scores → higher rewards?)
- Top-K precision (did our top picks actually perform best?)

## Submodule Reference

| Module | Purpose | Key types |
|--------|---------|-----------|
| `schemas` | Shared data vocabulary | `Observation`, `FeatureVector`, `Insight`, `ScoredAction` |
| `feature_extraction` | Raw data → feature vectors | `FeatureRegistry`, `extract_*` functions |
| `rewards` | Outcome metrics → scalar reward | `RewardCalculator`, `RewardBreakdown` |
| `tenant_memory` | Per-tenant observation/insight store | `TenantMemoryStore` |
| `global_priors` | Cross-tenant aggregate stats | `PriorAggregator`, `GlobalPrior` |
| `privacy` | Data boundary enforcement | `PrivacyFilter` |
| `ranking` | Score and rank candidate actions | `Ranker`, `ScoredAction` |
| `policies` | Learning-informed policy rules | `TimingAdvisoryRule`, `ContentTypeAdvisoryRule` |
| `evaluation` | Retrospective accuracy metrics | `evaluate_predictions`, `EvaluationReport` |
| `config` | Environment-specific settings | `LearningConfig` with `.local()`, `.production()`, etc. |

## Configuration

`LearningConfig` is a frozen dataclass with environment presets:

```python
from amplify.learning import LearningConfig

# Automatic from deployment mode
config = LearningConfig.for_environment("production")

# Or explicit presets
config = LearningConfig.local()       # relaxed thresholds for dev
config = LearningConfig.staging()     # production-like, lower limits
config = LearningConfig.production()  # full privacy enforcement
config = LearningConfig.disabled()    # all components become no-ops
```

### Key config knobs

| Setting | Default | What it controls |
|---------|---------|------------------|
| `privacy.k_anonymity_threshold` | 5 | Min tenants per global prior bucket |
| `privacy.cross_tenant_enabled` | true | Master switch for cross-tenant learning |
| `rewards.decay_half_life_days` | 30 | How fast old observations lose weight |
| `ranking.cold_start_threshold` | 20 | Observations needed before tenant data dominates |
| `ranking.exploration_rate` | 0.1 | Fraction of exploratory recommendations |
| `memory.max_observations_per_tenant` | 10,000 | Eviction limit per tenant |
| `memory.compaction_age_days` | 90 | When old observations can be dropped |

## Boundaries — What This System Does NOT Do

- **No opaque training jobs.** There are no neural networks, gradient
  descent, or model files.  All "learning" is aggregated statistics and
  weighted scoring.
- **No real-time model serving.** Ranking is a simple in-process
  computation, not an inference call.
- **No A/B testing framework (yet).** Evaluation is retrospective.
  Online experimentation is a future addition.
- **No direct database access.** The initial implementation uses
  in-memory storage.  Persistence via amplify-db models will be added
  when the schema stabilizes.
- **No external data pipelines.** All data flows through the Python
  process — no Kafka, no Spark, no separate ML infrastructure.

## Privacy Model

Cross-tenant learning follows these rules:

1. **Opt-in by default** — `cross_tenant_enabled` defaults to `true` but
   can be turned off per environment.
2. **Field redaction** — `PrivacyFilter.sanitize()` strips
   `artist_name`, `tenant_id`, `campaign_name`, `content_text` before
   any cross-tenant aggregation.
3. **K-anonymity** — global priors are only produced when at least
   `k_anonymity_threshold` distinct tenants contribute to a bucket.
4. **Cardinality capping** — features with too many distinct values
   (> `max_categorical_cardinality`) are excluded from priors to prevent
   re-identification.
5. **Anonymized IDs** — sanitized observations get a zeroed tenant_id
   and a new random observation ID.  No link back to the source.

## Integration Points

### apps/api
- Import `LearningConfig.for_environment(settings.deployment_mode)` in
  the settings/deps layer.
- Policy engine can register `TimingAdvisoryRule` / `ContentTypeAdvisoryRule`
  to get learning-informed approval flags.
- Future: expose `/api/v1/learning/insights` and `/api/v1/learning/rankings`
  endpoints.

### apps/worker
- After collecting post metrics, call `RewardCalculator.compute()` and
  `TenantMemoryStore.record()` to feed the learning loop.
- Periodic job: run `PriorAggregator.aggregate()` to refresh global priors.
- Periodic job: run `evaluate_predictions()` for accuracy monitoring.

### packages/agents
- Agents can query `Ranker.rank()` when generating campaign plans or
  scheduling posts, using the scored + explained output to make decisions.
