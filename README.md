# InvoiceDesk

**AI-assisted invoice extraction, review and independent approval.** A Gemini draft is checked by staff and validated against purchase orders before approval and CSV export.

## Portfolio highlights

- Multimodal extraction from PDF and image pages, with explicit consent and strict schemas.
- Transactional PO matching, cumulative quantity allocation and independent approvals.
- Durable workers with leases, fencing and bounded retries.
- Private notifications, scoped integration APIs and signed webhook delivery.
- A working purchasing integration with [FeedDesk](https://github.com/Sehrish-Faisal/feeddesk).

**Stack:** Python, FastAPI, Pydantic, PostgreSQL, SQLAlchemy Core, Gemini, pypdfium2, Pillow and vanilla JavaScript.

## Fresh installation prerequisites

The current bootstrap targets Windows with Python 3.14 and reuses a local PostgreSQL cluster initialized by FeedDesk. Set up [FeedDesk](https://github.com/Sehrish-Faisal/feeddesk) first. Then, in this repository:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-lock.txt
.\.venv\Scripts\python.exe manage.py setup
powershell -NoProfile -ExecutionPolicy Bypass -File start.ps1
```

The setup creates separate InvoiceDesk roles and databases. API credentials and business data are not included. Manual invoice review is available without a provider key. Do not expose this loopback development deployment directly to the internet.


Project 4 is a single organization invoice processing and approval application. Upload a PDF or image, optionally request Gemini extraction, review the values against a purchase order, obtain an independent approval and download a stable CSV export. It does not make payments or post into a general ledger.

## Run locally on this Windows workspace

1. Double-click `open-invoicedesk.cmd`, or run `powershell -NoProfile -ExecutionPolicy Bypass -File start.ps1` from this directory.
2. Open http://127.0.0.1:8030.
3. Individual randomly generated credentials are in `%LOCALAPPDATA%\InvoiceDesk\credentials.txt`. Use `operator` for upload/review, `approver` for independent decisions, and `admin` for supplier/PO maintenance. Do not commit or share this file.
4. Upload `examples/DEMO-1042.png`. This is a synthetic document, not a real payable invoice. Match `Demo — Northstar Supplies`, `PO-2026-001`, and its `Packaging bags` line. Quantity is 20, price 125.50, total PKR 2510.00.
5. Save, submit, sign out and sign in as the approver. Enter a reason, approve and export.

Accounts are deliberately not public self-service accounts. To add staff: `.venv\Scripts\python.exe manage.py add-user alice operator`. This generates a random password and appends it to the private credential file. Never use shared accounts for real approvals. The bootstrap demo gives one local developer access to all roles for testing only.

To revoke access immediately: `.venv\Scripts\python.exe manage.py disable-user alice`. This disables the account and removes all its sessions. `stop.ps1` stops only this project's recorded API/worker processes, leaving the shared PostgreSQL cluster running.

To replace a password: `.venv\Scripts\python.exe manage.py reset-password alice`. A random replacement is saved in the private credential file and existing sessions are revoked. Always use the current private credential file.

## Stack and architecture

- FastAPI/Pydantic validate request types; SQLAlchemy Core expresses explicit PostgreSQL transactions.
- Two processes share a database: API and durable extraction worker. `SKIP LOCKED`, leases and fencing prevent duplicate job ownership from producing duplicate results.
- Immutable source documents and sanitized page images live outside OneDrive under `%LOCALAPPDATA%\InvoiceDesk\documents`.
- Gemini receives sanitized page images only after per-upload consent. It suggests structured data; it cannot approve or export. The worker uses a DPAPI-encrypted copy of the currently saved Gemini key. The browser never receives it.
- HTML/CSS/JavaScript modules provide a same-origin interface without a separate frontend runtime or dependency graph. All authored interface copy is English.

See `docs/architecture.md`, `docs/release-evidence.md` for boundaries and verification.

## Installation and tests

Dependencies are pinned in `requirements-lock.txt`. The existing setup uses its own `.venv` and separate `invoicedesk` and `invoicedesk_test` databases on the installed local PostgreSQL cluster. It does not modify FeedDesk tables. The Windows bootstrap requires the existing FeedDesk PostgreSQL installation; this is a documented local prerequisite, not a portable cloud installer.

```powershell
.venv\Scripts\python.exe -m pip install -r requirements-lock.txt
.venv\Scripts\python.exe manage.py setup
.venv\Scripts\python.exe -m pytest -q
.venv\Scripts\python.exe -m pip_audit -r requirements-lock.txt
```

Tests truncate only the explicitly named `invoicedesk_test` database. Never point a test environment at production. Provider tests are mocked; `evaluate_ai.py` is a separate, explicit live evaluation that sends only synthetic fixtures and may incur API charges.

Additional frontend regression tests run with `node --test tests/draft.test.mjs`. Run `examples/build_evaluation_corpus.py` to regenerate eight labelled synthetic stress fixtures, then `evaluate_corpus.py --live` to explicitly send them to Gemini. This covers supplier identity, multiple lines/pages, rotated/faint scans, missing fields and embedded adversarial instructions. Each invocation writes a separate report under `docs/evaluations`; failed calls score zero and remain in the denominator. Use `--case CASE_ID` for a separately reported targeted rerun. These samples are not a representative customer benchmark.

## Backup and recovery

Run `.venv\Scripts\python.exe backup.py create-and-verify`. This exports a consistent PostgreSQL snapshot, copies its immutable documents, verifies hashes and restores into a uniquely named isolated database. It does not overwrite the active database. Backups remain under the private state directory with inherited restrictive ACLs; they are confidential, not encrypted archives.

To recover, stop the InvoiceDesk API and worker, verify the chosen backup, then have the database administrator grant the application role the same restricted privileges in the restored database. Replace only the database name in private application and migration URLs; restore the `documents` directory from the same backup into a new private state directory. Revoke all restored sessions, check users and jobs, then start against the recovered state. Keep the original state for rollback. Do not overwrite live data or use the verification database as production without this review.

Credentials and configuration are not included in backups. Retain protected recovery copies separately. DPAPI credentials are tied to the Windows user; a different machine/account must configure a new Gemini key. Restores created by verification are deliberately retained for inspection and must be removed explicitly by an administrator when no longer needed.

## Local security boundary

The launcher binds only `127.0.0.1:8030`. Hosts/origins are exact, cookies are HttpOnly/SameSite Strict, mutations require CSRF, and logins are database-rate-limited. Secure cookies and TLS are required before a public deployment; the local HTTP launcher is not for exposure through a tunnel. Runtime DB access cannot modify users/schema or update/delete audit, allocation, version and export records.

The decoder is a timed subprocess with minimal environment, size/page/pixel bounds and output limits. It is **not** a hardened OS sandbox, malware scanner or protection against a fully compromised local Windows account. All application processes share that account. The private state contains documents and DB configuration; OS ACLs are important.

## Operational limits

Single organization, four currencies, two-decimal monetary rounding, no exchange conversion and exact PO unit-price matching. Manual business exceptions require a corrected PO/invoice, not an invisible bypass. No bank integration, ERP posting, automated tax logic, SSO or retention policy is claimed. Email notifications require SMTP configuration and a real delivery check. Public production still needs TLS/managed deployment, monitored backups, operational ownership, independent security review and representative user/accuracy/load tests.
## Notification inbox and email

The sidebar now includes **Notifications** for approval requests, invoice decisions
and extraction results, with unread counts and read controls. Email delivery is
optional and requires administrator setup; it is not yet configured locally.
See [notification setup and behavior](docs/notifications.md). Use
`configure-email.cmd` to save encrypted SMTP credentials and
`register-staff-email.cmd` to register staff recipient addresses. Existing staff
login is unchanged; company SSO was excluded from this change.


## Configurable company integrations

Admins can open **Integrations** to set the company name, create scoped/expiring
API keys, map CSV columns, preview/import suppliers and purchase orders, configure
signed webhooks and review/retry failed deliveries. Each source only reads approved
invoices associated with its imported purchase orders. See [API v1 and setup guide](docs/integrations.md),
[CSV samples](examples/integration/) and the server-side starter client there.

This remains one company per deployment. The local FeedDesk connector is paired and verified; external vendor ERP endpoints remain unconfigured;
remote access needs a separately secured deployment. It is not a shared multi-tenant
SaaS, standalone installer, or a ready-made connector for every ERP vendor.


## FeedDesk connection

FeedDesk is now paired through API v1 using a scoped, encrypted credential. Its
manager-only Purchasing screen sends suppliers/POs and polls approved/exported
invoices back. Start both apps with `../open-connected-projects.cmd`.
See [FeedDesk connection guide](../customer-order-assistant/docs/invoicedesk.md).
