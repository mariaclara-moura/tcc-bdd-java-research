
import urllib.request
import urllib.parse
import urllib.error
import json
import csv
import time
import os

# ==========================================
# CONFIGURATION
# ==========================================

BASE_URL = "https://api.github.com/search/repositories"

KEYWORDS = [
    "BDD",
    "jgiven",
    '"Given When Then"'
    "given()",
    "when()",
    "then()",
    "Behavior Driven Development",
]

LANGUAGE = "Java"

RESULTS_PER_PAGE = 100

# GitHub Search API allows up to 1000 results
# per query, so at most 10 pages.
MAX_PAGES = 10

OUTPUT_FILE = "github_repositories3.csv"
JSON_FILE = "github_repositories3.json"

# Optional GitHub token.
# If you do not have one, leave it as None.
# You can also set the GITHUB_TOKEN environment variable.
TOKEN = os.getenv("GITHUB_TOKEN")


# ==========================================
# SEARCH FUNCTION
# ==========================================

def search_repositories(keyword):

    repositories = []

    query = f"{keyword} language:{LANGUAGE}"

    print(f"\nSearching for: {query}")

    for page in range(1, MAX_PAGES + 1):

        params = {
            "q": query,
            "per_page": RESULTS_PER_PAGE,
            "page": page
        }

        encoded_params = urllib.parse.urlencode(params)

        url = f"{BASE_URL}?{encoded_params}"

        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28"
        }

        if TOKEN:
            headers["Authorization"] = f"Bearer {TOKEN}"

        request = urllib.request.Request(
            url,
            headers=headers
        )

        try:

            with urllib.request.urlopen(request) as response:

                data = json.loads(response.read())

        except urllib.error.HTTPError as error:

            print(f"HTTP error: {error.code}")

            print(error.read().decode("utf-8"))

            break

        except urllib.error.URLError as error:

            print(f"Connection error: {error.reason}")

            break

        total_count = data.get("total_count", 0)

        items = data.get("items", [])

        print(
            f"Page {page}: "
            f"{len(items)} repositories "
            f"(total reported: {total_count})"
        )

        repositories.extend(items)

        # Stop when there are no more results.
        if len(items) < RESULTS_PER_PAGE:
            break

        # Search API rate limit:
        # Wait before requesting the next page.
        if page < MAX_PAGES:

            if TOKEN:
                time.sleep(2)
            else:
                time.sleep(7)

    return repositories


# ==========================================
# MAIN
# ==========================================

def main():

    all_repositories = {}

    for keyword in KEYWORDS:

        results = search_repositories(keyword)

        for repo in results:

            # Keep only repositories whose
            # primary language is Java.
            if repo.get("language") != LANGUAGE:
                continue

            full_name = repo["full_name"]

            # Avoid duplicates across keywords.
            if full_name not in all_repositories:

                all_repositories[full_name] = {

                    "name": repo["name"],

                    "full_name": full_name,

                    "url": repo["html_url"],

                    "description": repo.get("description"),

                    "language": repo.get("language"),

                    "stars": repo.get("stargazers_count"),

                    "forks": repo.get("forks_count"),

                    "created_at": repo.get("created_at"),

                    "updated_at": repo.get("updated_at"),

                    "default_branch": repo.get("default_branch"),

                    "keyword": keyword

                }

    # ==========================================
    # SAVE JSON
    # ==========================================

    repositories = list(all_repositories.values())

    with open(
        JSON_FILE,
        "w",
        encoding="utf-8"
    ) as file:

        json.dump(
            repositories,
            file,
            indent=4,
            ensure_ascii=False
        )

    # ==========================================
    # SAVE CSV
    # ==========================================

    if repositories:

        fieldnames = repositories[0].keys()

        with open(
            OUTPUT_FILE,
            "w",
            newline="",
            encoding="utf-8"
        ) as file:

            writer = csv.DictWriter(
                file,
                fieldnames=fieldnames
            )

            writer.writeheader()

            writer.writerows(repositories)

    # ==========================================
    # SUMMARY
    # ==========================================

    print("\n==========================================")

    print("SEARCH COMPLETED")

    print("==========================================")

    print(
        "Unique Java repositories:",
        len(repositories)
    )

    print("CSV file:", OUTPUT_FILE)

    print("JSON file:", JSON_FILE)


if __name__ == "__main__":

    main()
