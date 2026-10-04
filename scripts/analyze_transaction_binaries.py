#!/usr/bin/env python3
"""Generate a snapshot-bound program triage report from reviewed source evidence.

Run from any directory. Uses only the standard library and does not execute
untrusted project code. --verify-sources re-fetches pinned files and checks their
hashes/excerpts; it does not re-review their meaning or refresh source revisions.
"""

import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess
from urllib.parse import urlparse
from urllib.request import urlopen


ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "analysis/transaction-binaries.json"
EVIDENCE = ROOT / "analysis/transaction-binary-evidence.json"


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def repo_url(url):
    parsed = urlparse(url)
    if parsed.hostname != "github.com":
        return None
    parts = parsed.path.strip("/").split("/")
    return f"https://github.com/{parts[0]}/{parts[1]}" if len(parts) >= 2 else None


def verify_sources(components):
    files = {}
    for component in components.values():
        for check in component["checks"]:
            key = (check["repository"], check["commit"], check["path"])
            files.setdefault(key, []).append(check)

    def verify(item):
        (repository, commit, path), checks = item
        url = f"https://raw.githubusercontent.com/{repository.removeprefix('https://github.com/')}/{commit}/{path}"
        with urlopen(url, timeout=40) as response:
            data = response.read()
        lines = data.decode().splitlines()
        for check in checks:
            if hashlib.sha256(data).hexdigest() != check["file_sha256"]:
                raise ValueError(f"Source hash mismatch: {url}")
            excerpt = "\n".join(lines[check["start_line"] - 1:check["end_line"]])
            if excerpt != check["excerpt"]:
                raise ValueError(f"Source excerpt mismatch: {url}")
        return True

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(verify, files.items()))
    print(f"Verified {len(results)} pinned source files.")


def activity(program, now):
    if program.get("isPaused"):
        return "paused"
    launch = program.get("launchDate")
    if launch and datetime.fromisoformat(launch.replace("Z", "+00:00")) > now:
        return "not_started"
    end = program.get("endDate")
    if end and datetime.fromisoformat(end.replace("Z", "+00:00")) <= now:
        return "ended"
    return "active"


def metadata_signals(program):
    # Exclude templated impacts/rules: they mention transactions for contracts too.
    text = "\n".join(str(program.get(key) or "") for key in
                     ("description", "programOverview", "assetsBodyV2"))
    text += "\n" + "\n".join((asset.get("description") or "") + " " + asset["url"]
                              for asset in program.get("assets", []))
    pattern = r"\b(node client|node implementation|sequencer|rollup|execution client|validator software|off.?chain|relayer|binary|binaries|daemon|consensus|full node|matching engine|state transition)\b"
    return sorted({match.group(0).lower() for match in re.finditer(pattern, text, re.I)})


def classify(program, reviewed):
    slug = program["slug"]
    assets = program.get("assets", [])
    types = Counter(asset["type"] for asset in assets)
    scoped_repos = sorted({repo for asset in assets if (repo := repo_url(asset["url"]))})
    assessment = reviewed["assessments"].get(slug)
    if assessment is None:
        if not assets or program.get("hideAssetsInScope"):
            verdict, confidence = "UNKNOWN", "low"
            reason = "Assets are absent or hidden; metadata is insufficient to exclude a transaction-processing executable."
        elif types.get("blockchain_dlt"):
            verdict, confidence = "UNKNOWN", "low"
            reason = "Blockchain/DLT scope needs source review; that label alone does not establish an executable transaction processor."
        else:
            verdict, confidence = "NO", "medium"
            reason = "Probably contract/application software: listed products and assets identify no independent ledger validator or execution engine. This is metadata triage, not exhaustive proof of absence."
        assessment = {"verdict": verdict, "confidence": confidence,
                      "component_ids": [], "reason": reason,
                      "binary_scope": "unclear" if verdict == "UNKNOWN" else "not_listed"}
    component_ids = assessment["component_ids"]
    for key in component_ids:
        if key not in reviewed["components"]:
            raise ValueError(f"Missing component {key} for {slug}")
    software_assets = [asset for asset in assets if asset["type"] != "smart_contract"
                       or repo_url(asset["url"])]
    entry = {
        "slug": slug,
        "project": program["project"],
        "program_url": f"https://immunefi.com/bug-bounty/{slug}/information/",
        "metadata_file": f"project/{slug}.json",
        "metadata_file_sha256": sha256(ROOT / f"project/{slug}.json"),
        "updated_date": program.get("updatedDate"),
        "verdict": assessment["verdict"],
        "ships_transaction_binary": {"YES": True, "NO": False, "UNKNOWN": None}[assessment["verdict"]],
        "confidence": assessment["confidence"],
        "basis": "reviewed_source_and_metadata" if component_ids else "metadata_only",
        "reason": assessment["reason"],
        "component_ids": component_ids,
        "core_transaction_processor": (any(reviewed["components"][key]["core_transaction_processor"]
                                           for key in component_ids)
                                       if assessment["verdict"] == "YES" else None),
        "binary_scope": assessment["binary_scope"],
        "scope_note": "Scope status describes listed assets/components, not guaranteed bounty eligibility; check version pins, excluded paths, impacts, and program rules.",
        "metadata": {
            "program_types": program.get("programType", []),
            "product_types": program.get("productType", []),
            "languages": program.get("language", []),
            "asset_type_counts": dict(sorted(types.items())),
            "signals": metadata_signals(program),
            "scoped_github_repositories": scoped_repos,
        },
        "supporting_assets": [{key: asset.get(key) for key in ("id", "type", "url", "description")}
                              for asset in software_assets]
    }
    return entry


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--as-of", help="UTC ISO timestamp; defaults to saved report time, then now")
    parser.add_argument("--verify-sources", action="store_true")
    parser.add_argument("--check", action="store_true", help="Fail if generated report differs from saved report")
    args = parser.parse_args()
    reviewed = json.loads(EVIDENCE.read_text())
    if args.verify_sources:
        verify_sources(reviewed["components"])
    saved = json.loads(REPORT.read_text()) if REPORT.exists() else {}
    timestamp = args.as_of or saved.get("analyzed_at") or datetime.now(timezone.utc).isoformat()
    now = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    if now.tzinfo is None:
        parser.error("--as-of requires a timezone")
    directory = json.loads((ROOT / "projects.json").read_text())
    slugs = [program["slug"] for program in directory]
    if len(slugs) != len(set(slugs)):
        raise ValueError("Duplicate directory slugs")
    active, excluded = [], []
    for program in directory:
        state = activity(program, now)
        if state == "active":
            detail = json.loads((ROOT / f"project/{program['slug']}.json").read_text())
            if detail != program:
                raise ValueError(f"Directory/detail conflict: {program['slug']}")
            active.append(classify(program, reviewed))
        else:
            excluded.append({"slug": program["slug"], "project": program["project"],
                             "reason": state, "is_paused": program.get("isPaused"),
                             "launch_date": program.get("launchDate"), "end_date": program.get("endDate")})
    active.sort(key=lambda program: program["slug"].lower())
    excluded.sort(key=lambda program: program["slug"].lower())
    counts = dict(sorted(Counter(program["verdict"] for program in active).items()))
    yes = [program for program in active if program["verdict"] == "YES"]
    current_paths = {f"project/{slug}.json" for slug in slugs}
    historical = sorted(str(path.relative_to(ROOT)) for path in (ROOT / "project").glob("*.json")
                        if str(path.relative_to(ROOT)) not in current_paths)
    # Preserve the input revision on a deterministic re-run after committing outputs.
    input_sha = sha256(ROOT / "projects.json")
    revision = (saved.get("inputs", {}).get("repository_commit")
                if saved.get("inputs", {}).get("projects_json_sha256") == input_sha else None)
    revision = revision or subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    report = {
        "schema_version": 1,
        "criterion": "Does this project ship a binary that validates or executes transactions?",
        "analyzed_at": timestamp,
        "inputs": {"repository_commit": revision, "projects_json_sha256": input_sha,
                   "reviewed_evidence_sha256": sha256(EVIDENCE), "directory_program_count": len(directory),
                   "stored_program_file_count": len(list((ROOT / "project").glob("*.json")))},
        "methodology": {
            "active_definition": "Present in projects.json, unpaused, launched by analyzed_at, and not ended by analyzed_at. Retained historical project files alone do not establish activity.",
            "qualifying_software": "Deployable executable (native, JVM, packaged desktop, or state-transition RISC-V program) that enforces ledger/bridge transaction validity or applies transaction state transitions. Runtime components count when linked to a shipped node.",
            "excluded_software": "Application smart contracts, including compiled Solana/Soroban/Wasm contracts; SDK/library alone; deployment/test tools; transaction construction/signing/RPC submission without demonstrated protocol validation/execution.",
            "yes_definition": "Positive executable and transaction-processing evidence, reviewed together. Specialized bridge validators/observers are labeled separately from general chain processors.",
            "no_definition": "Probable negative from listed products and assets. Not a claim that every repository/service in the broader organization was exhaustively audited.",
            "unknown_definition": "Evidence is insufficient or the component is adjacent (wallet, oracle, relayer, DVT, matching engine, proprietary infrastructure). UNKNOWN must not be treated as NO.",
            "signals": ["First triage asset types, product descriptions, languages, repository URLs and named components.",
                        "Inspect shallow filtered clones for executable entrypoints, build/package targets, and production validation/execution paths.",
                        "Fetch selected files at the cloned commit, record hashes and line excerpts, then review their meaning.",
                        "Treat product labels, programming language, repository names and generic impact templates as insufficient proof.",
                        "Keep project-level fit separate from program asset scope."],
            "automation": "Metadata extraction, active filtering, hashes, report generation and pinned source verification are mechanical. Source selection, semantic interpretation and scope assessments are human-reviewed.",
            "scope_values": {"direct": "Relevant node, validator, or state-transition component is explicitly listed.",
                             "partial": "Only selected runtime/pallet/OS paths are listed; the entire executable is not thereby in scope.",
                             "related_components": "Related libraries/circuits are listed but the inspected node repository is not.",
                             "not_listed": "The inspected transaction-processing binary/client is not listed in this program's assets.",
                             "unclear": "Scope or qualifying software needs further review."},
            "limitations": ["Activity is determined from this repository snapshot; the live Immunefi directory was not refreshed.",
                            "Static source/build inspection was performed; binaries were not compiled, run, or downloaded from releases. Shipping is inferred from executable/build/packaging documentation, not deployment attestation.",
                            "Most sources use the default branch commit observed during analysis. Pythnet uses its documented implementation branch. These revisions may differ from bounty-specific version pins or production deployments.",
                            "Most NO entries were triaged from metadata; only candidate source repositories were inspected. Closed services and unresolved candidates remain UNKNOWN.",
                            "Bridge receipt/proposal validation can qualify under the broad question but is not native chain consensus validation; filter core_transaction_processor for the narrower interpretation.",
                            "Same-project programs can share source evidence while having different bounty scopes."]
        },
        "summary": {"active_program_count": len(active), "verdict_counts": counts,
                    "yes_core_transaction_processor_count": sum(program["core_transaction_processor"] is True for program in yes),
                    "yes_specialized_bridge_count": sum(program["core_transaction_processor"] is False for program in yes),
                    "yes_scope_counts": dict(sorted(Counter(program["binary_scope"] for program in yes).items())),
                    "source_repositories_cloned": reviewed["cloned_repository_count"],
                    "source_components_with_reviewed_evidence": len(reviewed["components"]),
                    "potentially_interesting": [program["slug"] for program in yes],
                    "needs_further_review": [program["slug"] for program in active if program["verdict"] == "UNKNOWN"]},
        "programs": active,
        "excluded_directory_programs": excluded,
        "excluded_historical_program_files": historical,
        "source_components": reviewed["components"]
    }
    rendered = json.dumps(report, indent=2, ensure_ascii=False) + "\n"
    if args.check:
        if not REPORT.exists() or REPORT.read_text() != rendered:
            raise SystemExit("Saved report differs; regenerate it.")
        print("Saved report matches all inputs and reviewed decisions.")
    else:
        REPORT.write_text(rendered)
    print(f"Classified {len(active)} active programs: {counts}")


if __name__ == "__main__":
    main()
