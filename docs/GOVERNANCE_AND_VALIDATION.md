# Governance and validation

## Human in the loop

- Agents assess, propose from an allowlist, flag conflicts and explain. They never approve, schedule work, change permissions, compute physics or act on real systems.
- Only the **owner** can approve, reject or request a revision, and only plans that passed every verifier check are eligible. A reason is required and recorded.
- An approval binds the scenario version, the run's input hash and the plan hash. Editing the scenario invalidates the approval and blocks the run; simulating is a separate owner action that re-checks the binding first.
- Blocked, failed, rejected and cancelled runs are shown as such. A failed local-model run is never relabelled as Mock output.

## Permissions

| Capability | Owner | Operator | Demo viewer |
| --- | --- | --- | --- |
| Manage users and workspaces | ✓ | | |
| Edit scenarios, start and cancel runs | ✓ | assigned workspaces | |
| Approve, reject, request revision, simulate | ✓ | | |
| Export reports | ✓ | ✓ | seeded runs only |
| Evidence library and re-embedding | ✓ | | |
| Operations status | ✓ | | |
| Read | all non-demo workspaces | assigned workspaces | seeded completed/blocked demo runs |

Enforced in `app/auth/policy.py` on every request; API tests and Playwright journeys call the API directly to confirm refusals do not depend on the UI.

## Provenance

- District, scenarios and corpus are fictional and authored for this project; each scenario and document carries a provenance label. Corpus documents also carry version, status and licence ("project-internal demonstration text"). No third-party data is included, so no external licence review was needed; any future source requires one before ingestion.
- Every run stores mode, seed, simulator/planner/policy/workflow versions, model and embedding model identifiers, input hash, immutable inputs, timestamps and failure details. Every agent output stores provider, model, attempts, latency, retrieved chunk IDs and validation errors.
- The execution trace records events that happened (calls, validations, rejections, conflicts, solver results, verifier checks, decisions). It does not contain model reasoning.
- Audit events (logins, user changes, scenario edits, decisions, exports) are application records in the same database. They are not tamper-evident.

## Validation record

Recorded on 26 October 2026 on the test machine: Windows 11, 8 logical CPUs, 16 GB RAM, no GPU, Docker 29.9, Ollama 0.40.6.

### Automated tests

| Suite | Result |
| --- | --- |
| Backend: simulator, planner/solver/verifier, agent contracts, API + PostgreSQL, migrations (`pytest`) | 72 passed; the real-Ollama module is skipped unless enabled |
| Backend real-Ollama tests (`TS_RUN_OLLAMA_TESTS=1`) | 3 passed: readiness, real embedding pipeline with pgvector retrieval, schema-valid specialist call |
| Frontend component tests (`vitest`) | 8 passed |
| Playwright journeys against the Docker stack | 9 desktop journeys + responsive journey at desktop, tablet (820 px) and mobile (Pixel 7) widths passed |
| Lint/format/types | ruff, ruff format, mypy, eslint, prettier, tsc clean |

Covered behaviours include: mass balance and determinism, monotonic checks (more protection, pumping or less rain never worsens the isolated case), CP-SAT equal to exhaustive enumeration on four tiny instances, infeasible and empty-clause handling, double-booked crews, blocked routes in the plan's own simulation, wind windows, invalid JSON, unknown evidence IDs, fabricated assets, prohibited actions, extra authority fields, a provider that obeys injected scenario notes (rejected), bounded revision rounds, model timeouts, owner/operator/demo permissions, cross-workspace isolation, approval invalidation, immutable inputs, idempotent run creation, cancellation races, worker crash recovery with lease fencing, exhausted retries, migration round trip with no drift, re-seeding that keeps other models' embeddings, and re-seeding after a prior report was added to a workspace library (a crash found during validation and fixed).

### Docker and operations

- Clean-clone rehearsal: fresh `git clone`, no `.env`, empty volumes, `bootstrap.sh` → ready in 36 s with a warm Docker layer cache (about 90 s when images are built from scratch). All 11 Playwright journeys then passed against it, followed by a backup/restore round trip.
- Containers run as non-root; the database has no published port; the web port binds to 127.0.0.1.
- Backup → restore round trip (Bash and PowerShell): pgvector 0.8.1, 40 embeddings across two models and all 16 runs restored; health ready afterwards.

### Security checks

| Check | Result |
| --- | --- |
| Secret scan (gitleaks 8.28, full history) | no leaks |
| Python dependencies (pip-audit, locked set) | no known vulnerabilities |
| npm production dependencies (`npm audit`) | 0 vulnerabilities |
| Backend image (Trivy, HIGH/CRITICAL, fixed only) | 0 |
| Web image (Trivy) | 47 HIGH in the nginx 1.29-alpine base; after moving to nginx-unprivileged 1.30-alpine with `apk upgrade`: 0 |

These results are a point in time. New advisories will appear; CI runs the scans and reports findings rather than claiming none exist.

### Planning evaluation

Twenty unseen variants (five seeded perturbations of each scenario, evaluation seed 97531; scenarios were tuned only on their base parameters). `python -m app.cli evaluate-planning --per-scenario 5`.

| Method | Feasible-plan rate | Mean disruption when feasible | Mean reduction vs no action | Mean runtime |
| --- | --- | --- | --- | --- |
| No action | 0.50 | 245.7 | 0 % | – |
| Greedy heuristic | 0.40 | 167.7 | 37.7 % | (inside planner run) |
| Constrained CP-SAT, full catalog | 0.75 | 81.1 | 56.2 % | 3.0 s |
| Agent team (Mock), coordinator-selected actions + restrictions | 0.75 | 91.9 | 42.3 % | 0.8 s |
| Single coordinator (Mock), no specialists or restrictions | 0.75 | 86.4 | 49.9 % | 0.4 s |

- **Accepted hard-constraint violations: 0** (every plan offered for approval re-checked by the independent verifier). Release gate met.
- Invalid Mock outputs: 0.
- "Feasible" means a verified plan that also meets the scenario's must-keep requirements. The no-action rate is 0.5 because half the variants carry requirements that no action violates.
- Greedy is infeasible more often because it ignores requirements; the constrained planner encodes them as clauses.
- **Multi-agent result:** in this experiment the specialist team did not improve outcomes. Against the single-coordinator variant it was better on 5 variants and worse on 10, and it averaged 10.8 disruption units above the full-catalog planner. Two reasons are visible in the data: the team's candidate set is narrower than the full catalog, and the ground role's precautionary restrictions remove routes that the other methods may still use. The restrictions are a deliberate safety choice, so the comparison is not purely about plan quality. The value the team adds here is structure: explicit conflicts, cited evidence and role-scoped authority, not lower disruption.

### Retrieval evaluation

Held-out set: 14 relevant questions, 4 irrelevant, 1 stale-source probe, 1 conflicting-source probe, plus a cross-workspace probe (`scenarios/eval/retrieval_questions.json`). k = 4.

| Model | Recall@4 | MRR | Top-1 | Irrelevant → insufficient evidence | Stale excluded | Both conflicting sources retrieved | Cross-workspace denied |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `fixture-hash-256-v1` (Mock, lexical, not semantic) | 0.857 | 0.821 | 0.786 | 3 of 4 | yes | yes | yes |
| `ollama:nomic-embed-text` (768-d) | 1.0 | 1.0 | 1.0 | 4 of 4 | yes | yes | yes |

The corpus has 20 chunks, so these numbers show the pipeline works; they are not evidence of performance on a realistic corpus. The fixture's figures describe a deterministic test fixture, not semantic model quality. Similarity indicates relevance, not truth: retrieved text cannot override simulation data, hard constraints or authorization.

### Local-model journey

One complete real journey through the Docker stack in Local mode (`ollama:qwen2.5:3b`, `ollama:nomic-embed-text`) on *Cascading infrastructure failure*:

- First attempt **failed** honestly: the air specialist emitted a road restriction, which only the ground role may issue; the validator rejected it twice and the run ended `agent_output_invalid`. Fix: water and air now receive an output schema without the restrictions field (validation unchanged).
- Retry (recorded with `retry_of`): all seven model calls validated (water, air with one retry, ground, coordinator, water revision, coordinator re-consolidation, explanation). Analysis 781 s; single calls 75–180 s.
- Owner approved the top verified plan; the simulation completed and the report records mode `local` and both model IDs.
- The local plan reduced simulated disruption by only 3.2 % (309.1 vs 319.3); the Mock team on the same scenario reaches about 73 %. A 3B model selected a much weaker candidate set. This is the measured outcome, not a tuned one.

## Scientific limitations

- The simulator is a simplified, uncalibrated, deterministic model of a fictional district. It is not a hydrodynamic, geotechnical, wind-engineering or forecast model, and nothing here has been validated against observations.
- Disruption is a demonstrative indicator. Sensitivity ranges come from seeded perturbations and are not probabilities or confidence intervals.
- Planner benefits are single-action estimates; interactions are handled by re-simulation and verification, not by the objective, so plans are good within the model rather than proven optimal for the true combined effect (the solver is optimal only for its linearised objective).
- Agent quality depends on the model; the evaluated local model is small and slow on CPU.
- Evaluation sets are small and authored by the same project; they should be replaced by independent data before any stronger claim.

## Future experiments (not dependencies)

- Compare a trained energy-based or learned world-model scorer of candidate states with the deterministic baselines on held-out scenarios. The current simulator is not a learned JEPA/world model and the solver is not an EBM.
- Larger local models or GPU inference, and a team versus single-agent study across many more variants.
- Historical replay only with licensed data, timestamps, preprocessing records and an evaluation before any predictive claim.
- An ANN index once the corpus is large enough for exact search to be measurably slow.
