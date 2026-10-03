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

## Source archive

`sources/` contains public source snapshots fetched for programs that are explicitly
active in Immunefi metadata. Selection requires `isPaused: false`, a launch date
that has passed (or no launch date), and an end date that has not passed (or no end
date). `sources/metadata/selection.json` records the selection time and exclusions;
archived records in `project/` do not determine eligibility.

- `sources/inventory.json` maps every scoped asset URL and supplementary repository
  reference to its active programs.
- `sources/results.json` records retrieval outcomes and the saved source locations.
- `sources/summary.json` reports coverage and outstanding or unavailable sources.
- GitHub archives include a resolved commit, SHA-256 digest, and complete file index.
  Join numbered `source.tar.gz.part*` files in order before opening a split archive.
- Verified contract `source.json` files preserve source text and compiler metadata
  where exposed by the explorer or public verification API.
- A saved explorer or website response alone does not count as verified contract
  source. Unverified contracts, unavailable source, inaccessible URLs, and scope
  placeholders are recorded explicitly. Browser script bundles are public client
  source; they do not provide a website's private server source.

Resume fetching and push batches with:

```sh
python tools/fetch_sources.py --workers 20 --push
```

Retry unresolved retrievals with `--retry`. Archives retain upstream licensing;
program source repositories and public verification services remain authoritative.
