# Session Q Brief: history remediation (surgical rewrite, then re-verify)

> **Committed copy — redacted.** This copy replaces the personal literals (the old author
> e-mail address and the three pre-rewrite fixture sentences) with placeholders, per the
> public-repo rule in CLAUDE.md — committing them verbatim would reintroduce the very
> strings this session scrubbed from history. The operator's verbatim original lives
> outside the repository. The redactions are the only differences from the original.

**Precondition (operator-confirmed before this session runs)**: the repository visibility
has been set back to **private**.

Read CLAUDE.md first. This session performs one minimal, auditable history rewrite fixing
the two findings from the pre-release history sweep, verifies the result, and installs the
recurrence preventions. **Hard stop on anything beyond the rewrites specified here** — no
message normalisation, no trailer removal, no drive-by cleanups; the blast radius of a
history rewrite stays exactly as scoped. The Co-Authored-By trailers on the v1.0–v1.1
commits are explicitly **left alone** (privacy-neutral, honest history).

## Task 1 — safety copy

Before anything: create a full backup copy of the repository directory (including `.git`)
outside the repo, and report its location. The rewrite is destructive by design; the
backup is the undo.

## Task 2 — the rewrite (one `git filter-repo` pass)

Install `git-filter-repo` if absent (`pip install git-filter-repo` in any scratch
environment is fine; it's a dev tool, not a project dependency).

One pass, two operations:

1. **Author-email correction** (Finding 1): an email callback rewriting the author email
   `[REDACTED — the personal e-mail address]` →
   `129589509+WinterDead@users.noreply.github.com` on the four affected commits
   (committer fields and tags are already correct; the callback is safe to apply globally
   since only those four author fields differ).
2. **Fixture-string scrub** (Finding 2): `--replace-text` with exactly these three
   byte-literal replacements, full sentences only (never a bare first-name swap — no
   collateral edits):

```
[REDACTED original sentence 1]==>Alex works on X.
[REDACTED original sentence 2]==>Alex enjoys hiking.
[REDACTED original sentence 3]==>alex has been building.
```

(The originals were the same sentences with the developer's first name in place of
"Alex"/"alex"; sentence 2's predicate additionally stated a real personal fact rather
than "enjoys hiking".)

If any assertion in `tests/test_render.py` references those literals, the same-blob
replacement keeps data and assertion consistent automatically — but verify by running the
suite (Task 4), and if any test still references an old literal from *outside* the
replaced sentences, fix the test to match the new strings (that edit is in scope).

Note: `git filter-repo` removes the `origin` remote as a safety measure — re-add it
before Task 5.

## Task 3 — recurrence prevention

1. **Repo-local identity pin**: `git config user.email
   "129589509+WinterDead@users.noreply.github.com"` and `git config user.name
   "WinterDead"` in the project repo, so global-config drift can never change commit
   identity here again. Also report the *current* local and global `user.email` values —
   the four bad commits are both Session H and Session I days, and knowing whether the
   global config still carries the personal address tells us the root cause.
2. **CLAUDE.md checklist**: extend step 6 with, byte-exact:

<!-- VERBATIM -->
   Before pushing, verify author identities: `git log --format='%ae' | Sort-Object
   -Unique` must show only the noreply address.
<!-- END VERBATIM -->

3. **plan.md provenance note**: add directly under the existing provenance note at the
   top, byte-exact:

<!-- VERBATIM -->
> **History rewrite, 2026-07.** All history was rewritten once before the public
> release (author-email correction and a fixture-string scrub); commit hashes cited in
> documents and briefs written before the rewrite are pre-rewrite identifiers, kept as
> historical labels.
<!-- END VERBATIM -->

## Task 4 — verification (all must pass before any push)

1. `git log --all --format='%an <%ae> / %cn <%ce>' | Sort-Object -Unique` → exactly one
   identity, the noreply address, across authors and committers.
2. Case-insensitive grep for the developer's first name and for the personal e-mail's
   domain across **all historical blobs and all commit/tag messages** → zero hits
   anywhere.
3. All five tags present, annotated, pointing at the rewritten equivalents of their
   original commits (verify by tag message and tree content, since hashes have changed).
4. Full test suite green; ruff and black clean.
5. The three replaced strings verified in HEAD's `tests/test_render.py` (new literals
   present, old absent).

## Task 5 — publish the clean history

Re-add `origin`, then force-push: `git push --force origin main` and
`git push --force origin --tags`. Then verify via `git ls-remote` that the remote's
`main` and all five tags match the rewritten local hashes exactly.

## Task 6 — close

Commit the Task 3 file edits (CLAUDE.md, plan.md) as a normal commit on the rewritten
history — full session-end checklist including the new author-identity check and the
push. File the session report per step 8, documenting: both findings, the rewrite
operations verbatim, the root-cause answer from Task 3.1, and the full Task 4 evidence.
The repo ends this session **private**; re-flipping is the operator's.

## Operator afterwards (in order)

1. Delete the stale laptop clone (it carries the old history); re-clone later if wanted.
2. Re-flip public; verify in an incognito window.
3. Resume the flip-day checklist at step 4: the five Releases (the texts are unaffected —
   tags are referenced by name, and the names survived), then the About polish.
