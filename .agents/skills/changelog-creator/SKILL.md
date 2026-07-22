# changelog-creator

## Description

Generate a new version entry for `CHANGELOG.md` based on the diff between the
latest git tag and `master` in the MCPTap repository.

## When to use

Use this skill when the user asks to prepare, draft, or generate a new
changelog entry for an upcoming release. The skill is read-only: it produces
the entry as text output only — it does not modify `CHANGELOG.md` or any
other file, and it does not commit or push.

## Prompt template

When the user invokes this skill (e.g. "prepare changelog", "generate
changelog", "new CHANGELOG entry"), use the following workflow:

### Step 1 — Determine the latest tag

```shell
cd /home/przemek/PCODE-pl/mcp-tap
git tag -l 'v*' --sort=-v:refname | head -1
```

This yields the latest released tag (e.g. `v2.2.0`).

### Step 2 — Compute the next minor version

Increment the **minor** position by 1, keeping the major and patch positions:

- `v2.2.0` → `2.3.0`
- `v1.3.4` → `1.4.0`
- `v3.0.0` → `3.1.0`

Strip the leading `v` for use in the changelog heading: `## [2.3.0]`.

### Step 3 — Gather commit information

```shell
git log <latest_tag>..master --no-merges --format='%H%n%s%n%b%n---COMMIT_SEP---'
git diff <latest_tag>..master --stat
```

For each non-merge commit, read the subject and body to understand what
changed. Use `git diff <latest_tag>..master -- <file>` for detailed diffs
of specific files when needed.

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
6. Do **not** write the entry to `CHANGELOG.md` — output it as text only.
7. Do **not** commit, push, or modify any files.

### Step 6 — Output

Print the complete changelog entry as a Markdown block in the chat response.
Do not write to any file. The user will review and insert it manually.

## Example invocation

User says:

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
6. Outputs the formatted entry as text — no file writes, no commits.
