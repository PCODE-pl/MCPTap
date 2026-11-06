---
name: changelog-creator
description: Generate a new version entry for CHANGELOG.md based on the diff between the latest git tag and master in the MCPTap repository. Use this skill when the user asks to prepare, draft, or generate a new changelog entry for an upcoming release.
---
# changelog-creator

## Description

Generate a new version entry for `CHANGELOG.md` based on the diff between the
latest git tag and `master` in the MCPTap repository, in english.

## When to use

Use this skill when the user asks to prepare, draft, or generate a new
changelog entry for an upcoming release. The changelog task may update only
`CHANGELOG.md`; never modify the skill library as part of preparing a release
entry. Follow the repository delivery workflow: validate, commit, and push the
changelog unless the user explicitly asks for a draft only.

## Prompt template

When the user invokes this skill (e.g. "prepare changelog", "generate
changelog", "new CHANGELOG entry"), use the following workflow:

### Step 1 — Determine the latest tag

```shell
cd /home/przemek/PCODE-pl/mcp-tap
git tag -l 'v*' --sort=-v:refname | head -1
```

This yields the latest released tag (e.g. `v2.2.0`).

### Step 2 — Resolve the release version and source baseline

Use the version explicitly requested by the user. Strip a leading `v` for the
heading, for example `v2.11.2` becomes `## [2.11.2]`. Do not silently assign a
feature to the next version inferred from the latest tag: a patch release may
contain source commits that landed after an earlier changelog-only release
commit.

Identify both the previous release heading and the source baseline. Inspect
release/changelog commits and the actual source commits, then use the range
that contains the changes intended for the requested version. Exclude prior
changelog-only commits from the feature summary, but do not exclude a source
feature merely because it was committed before the previous changelog entry.

### Step 3 — Gather commit information

```shell
git log <latest_tag>..master --no-merges --format='%H%n%s%n%b%n---COMMIT_SEP---'
git diff <latest_tag>..master --stat
```

For each non-merge commit, read the subject and body to understand what
changed. Use `git diff <source_baseline>..master -- <file>` for detailed diffs
of specific files when needed. Reconcile the commit range with the requested
release: inspect the actual diff for user-mentioned features and place them in
the requested version rather than trusting an earlier release-note summary.

### Step 4 — Classify changes

Categorize changes into Keep a Changelog sections:

- **Added** — new features, new files, new tests, new configuration options
- **Changed** — modifications to existing functionality, refactors
- **Deprecated** — features slated for removal
- **Removed** — features or files that were deleted
- **Fixed** — bug fixes
- **Security** — security-relevant changes

Only include sections that have at least one entry. Omit empty sections.

### Step 5 — Write the changelog entry

Format the entry following the existing `CHANGELOG.md` style:

```markdown
## [<new_version>]

### Added

- <bold lead> — <description>

### Changed

- <bold lead> — <description>

### Fixed

- <bold lead> — <description>

### Full Changelog

[https://github.com/PCODE-pl/MCPTap/compare/<latest_tag>...<new_version>](https://github.com/PCODE-pl/MCPTap/compare/<latest_tag>...<new_version>)
```

### Formatting rules

1. Each bullet starts with a concise **bold lead** (the feature/fix name)
   followed by an em-dash `—` and a description.
2. Use backticks for code identifiers: function names, env vars, file paths,
   command names.
3. Mention the number of new tests and their class names when test files
   were added/modified.
4. Skip pure test/CI commits that add and then immediately remove temporary
   files (e.g. push verification markers).
5. Include a `### Full Changelog` link at the end comparing the previous
   tag to the new version.
6. Insert the entry at the top of `CHANGELOG.md` while preserving existing
   historical entries.
7. Do not modify `.agents/skills/` or any other skill-library file while doing
   changelog work.
8. Unless the user explicitly requests draft-only output, commit and push only
   `CHANGELOG.md` after validation.

### Step 6 — Output

Print the complete changelog entry as a Markdown block in the chat response.
After writing, validating, committing, and pushing `CHANGELOG.md`, report the
commit, remote synchronization, and clean working tree.

## Example invocation

User says:

<!-- markdownlint-disable-next-line MD034 -->
> based on https://github.com/PCODE-pl/MCPTap/compare/v2.2.0...master
> prepare a new entry for /home/przemek/PCODE-pl/mcp-tap/CHANGELOG.md
> version 2.3.0
> print it here only, do not modify any files, do not push

Agent:

1. Confirms `v2.2.0` is the latest tag via `git tag`.
2. Computes next minor: `2.3.0`.
3. Runs `git log v2.2.0..master` and `git diff v2.2.0..master --stat`.
4. Reads commit subjects/bodies and detailed diffs as needed.
5. Classifies changes into Added/Changed/Fixed/etc.
6. Outputs the formatted entry as text and writes it to CHANGELOG.md file — no commits.
