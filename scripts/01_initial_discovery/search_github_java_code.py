
import csv
import os
import time
import requests
from collections import defaultdict

# ============================================================
# CONFIGURATION
# ============================================================

GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")

if not GITHUB_TOKEN:
    raise SystemExit(
        "GITHUB_TOKEN não encontrado. Configure a variável de ambiente antes de executar."
    )

API_URL = "https://api.github.com/search/code"

# GitHub Code Search: cada consulta pode retornar até 1.000 resultados.
# Limitamos a 3 páginas por consulta nesta primeira execução.
PER_PAGE = 100
MAX_PAGES_PER_QUERY = 10

OUTPUT_FILES = "github_code_search_files3.csv"
OUTPUT_REPOS = "github_code_search_repositories3.csv"

# Buscas independentes para encontrar diferentes partes do padrão GWT.
QUERIES = [
    "jgiven language:Java path:src/test",
    # "when() language:Java path:src/test",
    # "then() language:Java path:src/test",
    "jgiven language:Java path:test/java",
    # "when() language:Java path:test/java",
    # "then() language:Java path:test/java",
]

HEADERS = {
    "Accept": "application/vnd.github+json",
    "Authorization": f"Bearer {GITHUB_TOKEN}",
    "X-GitHub-Api-Version": "2022-11-28",
}

# ============================================================
# GITHUB CODE SEARCH
# ============================================================

def search_code(query, page):
    params = {
        "q": query,
        "per_page": PER_PAGE,
        "page": page,
    }

    response = requests.get(
        API_URL,
        headers=HEADERS,
        params=params,
        timeout=30,
    )

    if response.status_code == 401:
        raise RuntimeError("Token inválido ou sem autenticação adequada.")

    if response.status_code == 403:
        raise RuntimeError(
            "Acesso negado ou limite de requisições atingido. "
            f"Resposta: {response.text[:500]}"
        )

    if response.status_code == 422:
        raise RuntimeError(
            f"Consulta não aceita pelo GitHub: {query}\n{response.text[:500]}"
        )

    response.raise_for_status()
    return response.json()


def collect_results():
    collected = {}
    query_summary = []

    for query_index, query in enumerate(QUERIES, start=1):
        print(f"\n[{query_index}/{len(QUERIES)}] Consulta: {query}")

        total_found = 0

        for page in range(1, MAX_PAGES_PER_QUERY + 1):
            try:
                data = search_code(query, page)
            except Exception as error:
                print(f"Erro na consulta: {error}")
                break

            items = data.get("items", [])
            total_count = data.get("total_count", 0)

            if not items:
                break

            for item in items:
                repository = item.get("repository", {})

                full_name = repository.get("full_name", "")
                path = item.get("path", "")

                # Mantém apenas arquivos que parecem pertencer
                # a diretórios de testes.
                normalized_path = path.lower()

                if not any(
                    marker in normalized_path
                    for marker in (
                        "/test/",
                        "/tests/",
                        "/src/test/",
                        "/src/tests/",
                    )
                ):
                    continue

                key = f"{full_name}:{path}"

                if key not in collected:
                    collected[key] = {
                        "full_name": full_name,
                        "repository_url": repository.get("html_url", ""),
                        "repository_description": repository.get("description", ""),
                        "stars": repository.get("stargazers_count", 0),
                        "forks": repository.get("forks_count", 0),
                        "file_name": item.get("name", ""),
                        "file_path": path,
                        "file_url": item.get("html_url", ""),
                        "sha": item.get("sha", ""),
                        "matched_queries": set(),
                    }

                collected[key]["matched_queries"].add(query)
                total_found += 1

            print(
                f"  Página {page}: {len(items)} resultados "
                f"(total informado pela API: {total_count})"
            )

            if page * PER_PAGE >= min(total_count, 1000):
                break

            # Search Code possui limite de requisições próprio.
            time.sleep(7)

        query_summary.append({
            "query": query,
            "results": total_found,
        })

    return collected, query_summary


# ============================================================
# EXPORT FILE-LEVEL RESULTS
# ============================================================

def save_file_results(collected):
    columns = [
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
        writer = csv.DictWriter(file, fieldnames=columns)
        writer.writeheader()

        for item in collected.values():
            row = item.copy()
            row["matched_queries"] = "; ".join(
                sorted(row["matched_queries"])
            )
            writer.writerow(row)


# ============================================================
# EXPORT REPOSITORY-LEVEL RESULTS
# ============================================================

def save_repository_results(collected):
    repositories = defaultdict(list)

    for item in collected.values():
        repositories[item["full_name"]].append(item)

    columns = [
        "full_name",
        "repository_url",
        "repository_description",
        "stars",
        "forks",
        "matched_file_count",
        "java_test_files",
        "matched_queries",
    ]

    with open(OUTPUT_REPOS, "w", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(file, fieldnames=columns)
        writer.writeheader()

        for full_name, files in repositories.items():
            first = files[0]

            writer.writerow({
                "full_name": full_name,
                "repository_url": first["repository_url"],
                "repository_description": first["repository_description"],
                "stars": first["stars"],
                "forks": first["forks"],
                "matched_file_count": len(files),
                "java_test_files": "; ".join(
                    sorted(item["file_path"] for item in files)
                ),
                "matched_queries": "; ".join(sorted({
                    query
                    for item in files
                    for query in item["matched_queries"]
                })),
            })


# ============================================================
# MAIN
# ============================================================

if __name__ == "__main__":
    print("=" * 60)
    print("GITHUB CODE SEARCH - BDD-STYLE JAVA TESTS")
    print("=" * 60)

    collected, summary = collect_results()

    save_file_results(collected)
    save_repository_results(collected)

    print("\n" + "=" * 60)
    print("SEARCH FINISHED")
    print("=" * 60)

    print(f"Unique Java test files: {len(collected)}")
    print(f"Unique repositories: {len(set(
        item['full_name'] for item in collected.values()
    ))}")

    print("\nResults by query:")

    for item in summary:
        print(f"  {item['query']}: {item['results']} files")

    print(f"\nFiles saved:")
    print(f"  - {OUTPUT_FILES}")
    print(f"  - {OUTPUT_REPOS}")
