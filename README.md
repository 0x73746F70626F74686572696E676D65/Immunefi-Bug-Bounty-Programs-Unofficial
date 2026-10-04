# Immunefi Bug Bounty Programs (Unofficial)
Every time a Bug Bounty Program in Immunefi modifies its **policy**, **assets-in-scope**, or **bounties-table**, a bot will commit those changes to this repo.

To get a before/after diff of a project go to `./project/{project-name}.json` and check it's latest commit.

## If a program is removed from Immunefi, will it be removed from here as well?
No, the information and history of changes will stay here forever.

## Can I use this as a public API to get a list of programs and assets in scope?
Yes.

To get **a list of programs** call this endpoint:

> `https://raw.githubusercontent.com/infosec-us-team/Immunefi-Bug-Bounty-Programs-Unofficial/main/projects.json`

For example, open your console and type:
```
wget -qO- https://raw.githubusercontent.com/infosec-us-team/Immunefi-Bug-Bounty-Programs-Unofficial/main/projects.json
```

To get a list of **assets in scope** and **program details** call this endpoint (replacing {PROJECT_ID} with the project's id):

> `https://raw.githubusercontent.com/infosec-us-team/Immunefi-Bug-Bounty-Programs-Unofficial/main/project/{PROJECT_ID}.json`

For example, open your console and type:
```
wget -qO- https://raw.githubusercontent.com/infosec-us-team/Immunefi-Bug-Bounty-Programs-Unofficial/main/project/2pi.json
```

## Transaction-processing binary analysis

[`analysis/transaction-binaries.json`](analysis/transaction-binaries.json) classifies active programs against: **Does this project ship a binary that validates or executes transactions?** It records YES, probable NO, and UNKNOWN, with source evidence, confidence, and a separate assessment of whether the relevant software appears in the program's listed assets. `core_transaction_processor` separates chain execution/validation engines from specialized bridge validators and observers.

Activity and metadata come from the repository snapshot. Candidate repositories received static source checks; most negative classifications use metadata. Sources were not built or run, and project-level fit does not establish bounty eligibility. Exact source commits, hashes, and excerpts are in the report and [`analysis/transaction-binary-evidence.json`](analysis/transaction-binary-evidence.json).

Regenerate or verify the saved report with Python 3.10+:

```bash
python scripts/analyze_transaction_binaries.py
python scripts/analyze_transaction_binaries.py --check --verify-sources
```

The reviewed evidence file holds curated source interpretations. Refresh those decisions and source revisions when programs change; the script does not discover or semantically review new source code. Use `--as-of` with a timezone-qualified ISO timestamp to change the activity cutoff; otherwise regeneration preserves the saved analysis time.
