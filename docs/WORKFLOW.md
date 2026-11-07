# Reproducible workflow and script map

The folders reflect the order of research activities, **not** a guaranteed end-to-end runnable pipeline. Scripts were collected from multiple iterations and may have different input schemas or default working-directory assumptions. Each stage has its own original output datasets under `data/`. Execute from a working directory containing the required inputs, or supply CLI paths where supported.

| Stage | Folder | Purpose | Historical milestone |
|---|---|---|---|
| 1 | `scripts/01_initial_discovery/` | GitHub repository and Java code searches | 7,203 distinct repositories in the initial combined dataset |
| 2 | `scripts/02_merge_and_filter/` | Merge search results; deduplication and filtered datasets | 2,519 repositories with at least one star, then 312 recently active |
| 3 | `scripts/03_initial_code_screening/` | Initial inspection of Java test files and parallel filtering | First candidate triage of the 312 |
| 4 | `scripts/04_refinement/` | Refined detection and a separate 730-day lookback | Narrower executable GWT candidates |
| 5 | `scripts/05_complementary_discovery/` | Supplementary search for JGiven and executable GWT conventions | 3,406 additional distinct repositories; 10,609 cumulative |
| 6 | `scripts/06_complementary_audit/` | Code audit of additional repositories | New audit CSV and separate exclusion CSV |
| 7 | `scripts/07_candidate_review/` | Prepare candidate ZIP downloads for human review | Reviewed repositories and manual decisions |

## Data and manual decisions

- `data/raw/`: raw GitHub API discovery exports.
- `data/intermediate/`: search results, filters, deduplication, and downloads.
- `data/audits/`: automated test-code inspections.
- `data/exclusions/`: exclusion records for audit eligibility.
- `data/candidates/`: candidate shortlists and working reviews.
- `data/manual/manual_decisions.csv`: **researcher labels**, not automated labels. `APPROVED + VERIFY` and other pending categories are not automatically final approvals. Raw label values remain in Portuguese for fidelity.

## Reproducibility considerations

The Python filenames and ordinary project documentation are in English. Existing CSV contents (including field names and labels), Portuguese script internals, and Portuguese original notes remain unaltered where translating them might alter historical evidence or executable behavior. This means English file names are not a claim that raw research data was translated. Paths appearing in *historical output manifest columns* may refer to earlier source names; these are evidence of provenance, not current paths.

Do not treat comments-only GWT matches as confirmed executable scenarios. Do not treat inspection errors as negatives. Review actual Java test methods and step implementations before admitting a repository into the final sample.
