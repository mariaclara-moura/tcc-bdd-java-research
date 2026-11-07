
import csv
import os
import time
import requests
from collections import defaultdict

# ============================================================
# CONFIGURATION
# ============================================================

TOKEN = os.getenv("GITHUB_TOKEN")

if not TOKEN:
    raise RuntimeError(
        "GITHUB_TOKEN não encontrado. Configure sua variável de ambiente."
    )

API_URL = "https://api.github.com/search/code"

HEADERS = {
    "Accept": "application/vnd.github+json",
    "Authorization": f"Bearer {TOKEN}",
    "X-GitHub-Api-Version": "2022-11-28",
}

PER_PAGE = 100
MAX_PAGES_PER_QUERY = 10
SLEEP_SECONDS = 7

OUTPUT_FILES = "github_code_search_js_swift_files.csv"
OUTPUT_REPOS = "github_code_search_js_swift_repositories.csv"

# ============================================================
# SEARCH QUERIES
# ============================================================

QUERIES = {
    "JavaScript": [
        '"defineFeature" language:JavaScript',
        '"createBdd" language:JavaScript',
        '"Given" "When" "Then" language:JavaScript',
        '"Scenario" "Given" "When" "Then" language:JavaScript',
    ],

    "Swift": [
        '"Scenario" "Given" "When" "Then" language:Swift',
        '"Given" "When" "Then" language:Swift',
        '"describe" "context" "it" language:Swift',
    ],
}

# ============================================================
# TEST FILE FILTER
# ============================================================

def is_test_file(path, language):
    path_lower = path.lower()
    filename = path_lower.split("/")[-1]

    if language == "JavaScript":
        test_directories = [
            "/test/",
            "/tests/",
            "/__tests__/",
            "/spec/",
            "/step_definitions/",
        ]

        test_suffixes = (
            ".test.js",
            ".spec.js",
            ".test.jsx",
            ".spec.jsx",
        )

        return (
            any(directory in f"/{path_lower}" for directory in test_directories)
            or filename.endswith(test_suffixes)
        )

    if language == "Swift":
        test_directories = [
            "/test/",
            "/tests/",
            "/spec/",
        ]

        return (
            any(directory in f"/{path_lower}" for directory in test_directories)
            or filename.endswith("tests.swift")
            or filename.endswith("spec.swift")
        )

    return False


# ============================================================
# GITHUB CODE SEARCH
# ============================================================

def search_code(query, language):
    collected = {}

    for page in range(1, MAX_PAGES_PER_QUERY + 1):
        params = {
            "q": query,
            "per_page": PER_PAGE,
            "page": page,
        }

        print(f"  Página {page}: pesquisando...")

        response = requests.get(
            API_URL,
            headers=HEADERS,
            params=params,
            timeout=30,
        )

        if response.status_code != 200:
            print(
                f"  Erro HTTP {response.status_code}: "
                f"{response.text[:300]}"
            )
            break

        data = response.json()
        items = data.get("items", [])

        print(
            f"  Página {page}: {len(items)} resultados "
            f"(total informado: {data.get('total_count', 0)})"
        )

        if not items:
            break

        for item in items:
            repository = item["repository"]
            full_name = repository["full_name"]
            file_path = item["path"]

            if not is_test_file(file_path, language):
                continue

            key = f"{full_name.lower()}:{file_path.lower()}"

            if key not in collected:
                collected[key] = {
                    "language": language,
                    "full_name": full_name,
                    "repository_url": repository["html_url"],
                    "repository_description": (
                        repository.get("description") or ""
                    ),
                    "stars": repository.get("stargazers_count", 0),
                    "forks": repository.get("forks_count", 0),
                    "file_name": item["name"],
                    "file_path": file_path,
                    "file_url": item["html_url"],
                    "sha": item["sha"],
                    "matched_queries": set(),
                }

            collected[key]["matched_queries"].add(query)

        if len(items) < PER_PAGE:
            break

        time.sleep(SLEEP_SECONDS)

    return collected


# ============================================================
# MAIN
# ============================================================

all_files = {}

for language, queries in QUERIES.items():
    print(f"\n{'=' * 60}")
    print(f"LINGUAGEM: {language}")
    print(f"{'=' * 60}")

    for index, query in enumerate(queries, start=1):
        print(f"\n[{index}/{len(queries)}] Consulta: {query}")

        results = search_code(query, language)

        for key, item in results.items():
            if key not in all_files:
                all_files[key] = item
            else:
                all_files[key]["matched_queries"].update(
                    item["matched_queries"]
                )

# ============================================================
# SAVE FILE RESULTS
# ============================================================

file_fields = [
    "language",
    "full_name",
    "repository_url",
    "repository_description",
    "stars",
    "forks",
    "file_name",
    "file_path",
    "file_url",
    "sha",
    "matched_queries",
]

with open(OUTPUT_FILES, "w", newline="", encoding="utf-8-sig") as file:
    writer = csv.DictWriter(file, fieldnames=file_fields)
    writer.writeheader()

    for item in all_files.values():
        row = item.copy()
        row["matched_queries"] = "; ".join(
            sorted(item["matched_queries"])
        )
        writer.writerow(row)

# ============================================================
# SAVE UNIQUE REPOSITORIES
# ============================================================

repositories = {}

for item in all_files.values():
    key = item["full_name"].lower()

    if key not in repositories:
        repositories[key] = {
            "language": item["language"],
            "full_name": item["full_name"],
            "repository_url": item["repository_url"],
            "repository_description": item["repository_description"],
            "stars": item["stars"],
            "forks": item["forks"],
        }

repo_fields = [
    "language",
    "full_name",
    "repository_url",
    "repository_description",
    "stars",
    "forks",
]

with open(OUTPUT_REPOS, "w", newline="", encoding="utf-8-sig") as file:
    writer = csv.DictWriter(file, fieldnames=repo_fields)
    writer.writeheader()
    writer.writerows(repositories.values())

# ============================================================
# SUMMARY
# ============================================================

print("\n" + "=" * 60)
print("SEARCH SUMMARY")
print("=" * 60)

for language in QUERIES:
    files_count = sum(
        1 for item in all_files.values()
        if item["language"] == language
    )

    repos_count = len({
        item["full_name"].lower()
        for item in all_files.values()
        if item["language"] == language
    })

    print(f"\n{language}:")
    print(f"  Unique test files: {files_count}")
    print(f"  Unique repositories: {repos_count}")

print(f"\nTotal unique files: {len(all_files)}")
print(f"Total unique repositories: {len(repositories)}")

print(f"\nGenerated: {OUTPUT_FILES}")
print(f"Generated: {OUTPUT_REPOS}")
