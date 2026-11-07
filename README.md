# Java Code-Style BDD Repository Research

This repository documents the discovery, filtering, automated code screening, and manual assessment of Java repositories that may contain executable **Given–When–Then (GWT)** tests. The data and Python scripts support a final-year undergraduate research project.

## Overview

The initial GitHub search produced **7,203** unique repositories. Applying the original metadata filters produced **2,519** projects with at least one star and **312** projects in the first recent-activity screen. A complementary search identified **3,406 additional** repositories, giving **10,609 unique repositories discovered**. These discovery counts do not represent confirmed eligible samples.

## Pipeline organization

1. `scripts/01_initial_discovery/` — initial GitHub searches.
2. `scripts/02_merge_and_filter/` — search-result merging and deduplication.
3. `scripts/03_initial_code_screening/` — initial Java test-code inspection.
4. `scripts/04_refinement/` — refined GWT screening and 730-day audit.
5. `scripts/05_complementary_discovery/` — expanded search for additional GWT patterns.
6. `scripts/06_complementary_audit/` — audit of newly discovered repositories.
7. `scripts/07_candidate_review/` — candidate preparation and ZIP download for human review.

Read [`docs/WORKFLOW.md`](docs/WORKFLOW.md) for the phase-to-script map and methodological cautions.

## Data organization

`data/raw/` contains initial discoveries; `data/intermediate/` contains intermediate merges and filters; `data/audits/` contains source-inspection results; `data/exclusions/` documents exclusions; `data/candidates/` holds shortlists; and `data/manual/` holds researcher decisions.

**Source of truth for human labels:** [`data/manual/manual_decisions.csv`](data/manual/manual_decisions.csv). At the time of export, 32 repositories had recorded decisions: 2 `APROVADO`, 1 `APROVADO + VERIFICAR`, 6 `VALIDAR`, 11 `POSSIVEL`, and 12 `REPROVADO`. These original labels are intentionally preserved as source data.

## Eligibility and interpretation

The target is executable Java GWT scenarios (JGiven or method/DSL conventions). Tests do not need high quality to be considered for the study, because quality is the subject of the checklist. Comments-only Given/When/Then matches are **not sufficient** to establish executable GWT. Repositories dedicated to showing how to use a BDD framework are treated separately; demos of unrelated technologies are not automatically excluded. REST Assured and ambiguous DSLs require explicit review.

## Reproducing the work

Install dependencies from `requirements.txt` as appropriate, supply GitHub API credentials via environment variables, and follow the stage notes. **The historical scripts have not been verified as a single executable pipeline after relocation.** Some scripts assume a particular current working directory and preexisting CSVs. Original research data values and script internals have been preserved; do not assume that paths embedded in past output files point to the new layout.

## Publishing

The archive excludes third-party ZIP downloads, local virtual environments, temporary SQLite files, caches, and logs. Before making this repository public, inspect all tracked files for accidental credentials and personal information.

```bash
git init
git branch -M main
git add .
git commit -m "Organize research scripts by workflow stage"
```

Create an empty **private** repository on GitHub, then connect it using your GitHub repository URL:

```bash
git remote add origin https://github.com/YOUR_USERNAME/YOUR_REPOSITORY.git
git push -u origin main
```

Alternatively, with the GitHub CLI installed and authenticated: `gh repo create tcc-bdd-java-research --private --source=. --remote=origin --push`.

## Data provenance

See [`docs/source_file_manifest.csv`](docs/source_file_manifest.csv) and [`docs/source_duplicate_files.csv`](docs/source_duplicate_files.csv). Both are historical manifests and may reference old source paths.
