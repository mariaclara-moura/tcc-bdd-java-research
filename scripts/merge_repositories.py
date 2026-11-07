
# # # # import csv
# # # # from pathlib import Path

# # # # CSV_FILES = [
# # # #     "github_repositories.csv",
# # # #     "github_repositories2.csv",
# # # #     "github_repositories3.csv"
# # # # ]

# # # # OUTPUT_FILE = "github_repositories_merged.csv"

# # # # repositories = {}

# # # # total_read = 0

# # # # for file in CSV_FILES:
# # # #     if not Path(file).exists():
# # # #         raise FileNotFoundError(f"Arquivo não encontrado: {file}")

# # # #     with open(file, "r", encoding="utf-8-sig", newline="") as csvfile:
# # # #         reader = csv.DictReader(csvfile)

# # # #         if "full_name" not in (reader.fieldnames or []):
# # # #             raise ValueError(f"Coluna 'full_name' não encontrada em {file}")

# # # #         for row in reader:
# # # #             total_read += 1

# # # #             repo_name = row["full_name"].strip().lower()

# # # #             if repo_name and repo_name not in repositories:
# # # #                 repositories[repo_name] = row

# # # # # Ordena os repositórios
# # # # sorted_repositories = sorted(
# # # #     repositories.values(),
# # # #     key=lambda repo: repo["full_name"].lower()
# # # # )

# # # # # Salva o CSV consolidado
# # # # with open(OUTPUT_FILE, "w", encoding="utf-8-sig", newline="") as csvfile:
# # # #     writer = csv.DictWriter(
# # # #         csvfile,
# # # #         fieldnames=sorted_repositories[0].keys()
# # # #     )

# # # #     writer.writeheader()
# # # #     writer.writerows(sorted_repositories)

# # # # # Resumo
# # # # print("=" * 45)
# # # # print("CONSOLIDATION COMPLETED")
# # # # print("=" * 45)
# # # # print(f"Total repositories read: {total_read}")
# # # # print(f"Unique repositories: {len(sorted_repositories)}")
# # # # print(f"Duplicates removed: {total_read - len(sorted_repositories)}")
# # # # print(f"Output file: {OUTPUT_FILE}")
# # import csv
# # from pathlib import Path

# # CSV_FILES = [
# #     "github_code_search_files_merged2.csv",
# #     "github_code_search_files_merged.csv"
# # ]

# # OUTPUT_FILE = "github_code_search_files_merged.csv"

# # files = {}
# # total_read = 0

# # for file in CSV_FILES:
# #     if not Path(file).exists():
# #         raise FileNotFoundError(f"Arquivo não encontrado: {file}")

# #     with open(file, "r", encoding="utf-8-sig", newline="") as csvfile:
# #         reader = csv.DictReader(csvfile)

# #         required_columns = {"file_url", "matched_queries"}
# #         missing = required_columns - set(reader.fieldnames or [])

# #         if missing:
# #             raise ValueError(f"Colunas ausentes em {file}: {missing}")

# #         for row in reader:
# #             total_read += 1

# #             file_url = row["file_url"].strip().lower()

# #             if not file_url:
# #                 continue

# #             if file_url not in files:
# #                 files[file_url] = row
# #             else:
# #                 # Preserva as consultas que encontraram o mesmo arquivo
# #                 existing_queries = set(
# #                     files[file_url]["matched_queries"].split(";")
# #                 )
# #                 new_queries = set(row["matched_queries"].split(";"))

# #                 all_queries = sorted(
# #                     q.strip()
# #                     for q in existing_queries | new_queries
# #                     if q.strip()
# #                 )

# #                 files[file_url]["matched_queries"] = ";".join(all_queries)

# # sorted_files = sorted(
# #     files.values(),
# #     key=lambda item: (item["full_name"].lower(), item["file_path"].lower())
# # )

# # if sorted_files:
# #     with open(OUTPUT_FILE, "w", encoding="utf-8-sig", newline="") as csvfile:
# #         writer = csv.DictWriter(
# #             csvfile,
# #             fieldnames=sorted_files[0].keys()
# #         )
# #         writer.writeheader()
# #         writer.writerows(sorted_files)

# # print("=" * 50)
# # print("CONSOLIDATION COMPLETED")
# # print("=" * 50)
# # print(f"Total files read: {total_read}")
# # print(f"Unique files: {len(sorted_files)}")
# # print(f"Duplicates removed: {total_read - len(sorted_files)}")
# # print(f"Output file: {OUTPUT_FILE}")

# import pandas as pd

# repositories = pd.read_csv("github_repositories_merged.csv")
# code_files = pd.read_csv("github_code_search_files_merged.csv")

# # Normalize names to avoid differences in capitalization or spaces
# repositories["key"] = repositories["full_name"].str.strip().str.lower()
# code_files["key"] = code_files["full_name"].str.strip().str.lower()

# # Find repositories present in both datasets
# common = set(repositories["key"].dropna()) & set(code_files["key"].dropna())

# # Keep repository metadata from Repository Search
# result = repositories[repositories["key"].isin(common)].copy()
# result = result.drop(columns=["key"])

# # Save results
# result.to_csv("repositories_found_in_both.csv", index=False)

# print(f"Repositories in Repository Search: {repositories['key'].nunique()}")
# print(f"Repositories in Code Search: {code_files['key'].nunique()}")
# print(f"Repositories found in BOTH: {len(common)}")

# print("\nRepositories found in both searches:")
# for name in sorted(result["full_name"].tolist()):
#     print(name)

import pandas as pd

INPUT_FILE = "github_code_search_files_merged.csv"
OUTPUT_FILE = "github_code_search_repositories_merged.csv"

# Read the merged file-level dataset
df = pd.read_csv(INPUT_FILE)

# Keep one row per repository
repositories = (
    df.sort_values("full_name")
      .drop_duplicates(subset=["full_name"], keep="first")
)

# Select repository-level columns
columns = [
    "full_name",
    "repository_url",
    "repository_description",
    "stars",
    "forks",
]

repositories = repositories[columns]

# Save the repository dataset
repositories.to_csv(
    OUTPUT_FILE,
    index=False,
    encoding="utf-8-sig"
)

print(f"Total files: {len(df)}")
print(f"Unique repositories: {len(repositories)}")
print(f"Duplicates removed: {len(df) - len(repositories)}")
print(f"\nGenerated: {OUTPUT_FILE}")
