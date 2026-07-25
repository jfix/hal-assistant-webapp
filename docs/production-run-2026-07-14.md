> Imported from jfix/hal-assistant `main` at `03315f7` on 2026-07-25 because it contains operational updates made after the handover branch. This sanitized history is context only; production archives and credentials are not stored here.

# Production reconciliation — 2026-07-14

This document records the sanitized outcome of the Florence Fix HAL production run. Credentials, raw server bodies and ignored local artifacts are intentionally excluded.

## Inputs and safeguards

- Corrected source: `A mettre sur HAL-2.docx`
- Source SHA-256: `fc1ad0b67d34e6172a589674b9287cee6daa9d40b79fe0d0566e469ce1b7d176`
- Author IdHAL: `florence-fix`
- HAL domain: `shs.litt`
- Affiliation: structure `95026`, reused from the immutable ledger of four earlier successful deposits
- Parsed records: 122
- Production candidate batch: 64
- Batch SHA-256: `bd43e6cde31cbfc17f7bf7c1c7b57836ec9c62a6cf75a905b37061cf41450d01`

Before submission, the workflow excluded confirmed HAL records, ambiguous matches, conference papers without authoritative required metadata, five generic-title duplicate false positives, and three records found in an earlier local production ledger but not yet visible to the initial HAL candidate search.

## Validation and production result

- Local readiness audit: 64/64 ready
- Local XML validation: 64/64 valid
- HAL preproduction test validation: 64/64 accepted
- HAL production: 61 accepted, 3 rejected, 0 pending
- Corrected Mariette chapter: accepted as `hal-05691845` with publication year 2017

Each notice was processed independently. The three production rejections did not block the other 61 deposits.

## Follow-up production batch — 2026-07-15

Seven additional reviewed records were accepted in HAL production after metadata enrichment and individual duplicate resolution:

- `hal-05692218` — *Un château fond de scène d’un parc. Le garde-chasse de Chambord (1821)*
- `hal-05692220` — *Avant-propos au Pouvoir du médecin au XIXe siècle*
- `hal-05692221` — *La Seine sanglante : flots révolutionnaires et flux mémoriel*
- `hal-05692222` — *Les malades d’Antonio Alámo ou la maladie au pouvoir*
- `hal-05692223` — *Corps âgés et bouches inutiles : de quelques vieillards improductifs chez Zola et Mirbeau*
- `hal-05692224` — *Droit à la guerre et blessures désirées en temps de paix*
- `hal-05692226` — *Le pédant et l’exaltée, impressions littéraires*

This raises the confirmed production acceptances from the submission workflow from 61 to 68. The user-maintained review sheet records all seven as `accepted` and `READY`. The ignored local archive and immutable ledgers remain authoritative for payload checksums, server diagnostics and safe resume behavior.

## Current exact submission queue — 2026-07-15

The latest review sheet now has **24 unsubmitted records marked `READY` with their earlier false-positive HAL candidates resolved**. They are frozen by publication ID in [`next-hal-submission-approval-2026-07-15.txt`](next-hal-submission-approval-2026-07-15.txt). The allowlist contains:

- 17 `COUV` or `ART` records, including the previously generic `Introduction`, `Avant-propos` and `Préface` contributions;
- 7 `COMM` records with exact event titles, start/end dates, cities, countries and source evidence.

The exact allowlist deliberately excludes all production acceptances, all existing HAL records, the two isolated journal-issue retries below, and the unresolved issue-identity conflict. Import it with:

```bash
uv run hal-review-import import-review HAL-publication-review.xlsx \
  --approval-file docs/next-hal-submission-approval-2026-07-15.txt
```

The resulting 24-record `hal-ready.json` must still pass local audit, XML validation and HAL preproduction before the production archive is frozen. Production must resume from the authoritative local ledgers so none of the 68 already accepted workflow records can be resubmitted.

## Isolated *Le Paon d’Héra* retries

Two of the three original production rejections are now metadata-complete and can be regenerated for a fresh preproduction test:

- `pub-c942e73d52beb710` — issue 1, *Orphée* (1), 2006, 134 pages;
- `pub-d9c53490466289d4` — issue 6, *Médée* (2), 2010, 219 pages.

Both now carry HAL journal authority `63383`, canonical journal metadata and ISSN `1779-2746`. They must not be included in the normal 24-record batch. If preproduction succeeds but the production X-test still reports only HAL's title-level duplicate rule, retry each exact XML file independently through the checksum-gated `--force-title-duplicate` workflow.

## Remaining identity conflict

*Le Paon d’Héra* issues 2 and 3 remain quarantined:

- Florence's issue 2 citation is *Orphée* (2), 2007, 226 pages;
- Florence's issue 3 citation is *Roméo et Juliette*, 2007, 275 pages;
- live record `hal-05691824` currently identifies issue 2/*Orphée* but retains 275 pages, which correspond to issue 3.

Preserve `hal-05691824` in the audit history, but do not submit, remap or update either issue until the 226/275-page identity discrepancy is resolved from authoritative evidence.

## Remaining operational dependency

The bibliographic and duplicate-review backlog is otherwise reconciled. Executing the 24-record preproduction/production batch and the two isolated journal retries still requires the ignored local review workbook, XML/archive ledgers and HAL credentials. Those artifacts remain the operational source of truth for checksums, response diagnostics, accepted HAL identifiers and safe resume behavior.

