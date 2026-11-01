# P1 publication: sensitive-data audit

Date: 2026-11-01. Code snapshot: `07fce72d1dfa5daf2c77de2c0783f47fbebbbd54`.
Baseline: `d8e7b9be15de9099c762ea50e6554c8ffe770f2b`.

## Scope and method

Reviewed the 103 changed source/test/migration/documentation paths and scanned all
652 tracked paths in the candidate Git tree. Checked the new commit's author and
committer metadata, added-line contacts and personal filesystem paths, tracked artifact
names, development fixture provenance, local-AI transport/logging boundaries and build
inputs. No owner credential store or private mailbox was accessed.

Used a local signature/entropy scan plus manual classification. Signatures cover private
keys, common cloud/API tokens, OAuth secrets, JWTs, credential-bearing URLs and literal
credential assignments. Candidate output recorded locations and fingerprints, not matched
secret values. This is a focused sensitive-data audit, not a claim that every possible
disguised secret or software vulnerability can be ruled out. No external scanning service
received source or findings.

## Finding and remediation

The original unpublished commit used the local Git configuration's personal author and
committer contact details. Replaced that metadata with the repository owner's public
GitHub handle and GitHub noreply identity before any publication. The obsolete commit
was not pushed; only the sanitized branch is proposed for publication. Existing public
upstream history is unchanged.

## Content review

- No actual credentials, private keys, access/refresh tokens, personal mailbox content
  or owner records were found in the reviewed candidate content.
- Every email address on newly added lines uses `example.com`. Other addresses in changed
  files are pre-existing synthetic test cases; none was a newly introduced contact.
- No personal Windows/Unix home paths occur on added lines. No new binary file, database,
  backup archive, model download, application log, OAuth client JSON or credential file
  is tracked in the proposed diff. Ignored `out/` acceptance profiles/reports are excluded.
- Two changed-file entropy candidates are the public Qwen model license URL, classified
  as non-secret. The remaining tree-wide entropy/phone-pattern candidates are unchanged
  public model provenance or synthetic scoring/benchmark fixtures.
- `dev-30.json` contains 30 independently authored synthetic development cases. Names,
  messages and credential/OTP/injection canaries are invented; it introduces no captured
  inbox data. Such canaries intentionally exercise the privacy validator.
- Local AI remains experimental and disabled by default. Local-mode composition checks
  the feature flag, uses authenticated loopback transport with proxy inheritance disabled,
  and does not select cloud fallback. No raw model output or request body is added to logs.
- Task-create receipts store control metadata and a command-salted input digest, not
  owner titles, notes or steps. The digest is not encryption; its purpose and retention
  limits are documented in ADR 0024. Writing exports exclude receipts.
- Package data inclusion remains explicit; the added resources are upstream license
  notices and additive migrations, not owner files or the development fixture.

## Publication result

The personal commit-metadata issue is remediated. No remaining sensitive-data blocker
was identified for the reviewed source snapshot. The report itself is also reviewed
before the final branch is pushed. Raw local audit output remains under ignored `out/`.
Experimental local-model quality/native acceptance and ordinary release acceptance are
separate from this publication audit.
