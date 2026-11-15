# Notifications

InvoiceDesk has a private in-app inbox and optional email delivery. Company SSO
was explicitly excluded from this change; existing staff login is unchanged.

## Events and recipients

| Event | Recipients |
| --- | --- |
| Invoice submitted | Active admins and approvers, excluding the uploader and reviewer |
| Invoice approved or returned | Active uploader and reviewer (deduplicated) |
| AI extraction completed or permanently failed | Active uploader and reviewer (deduplicated) |

Only new workflow events generate notifications. Historical audit events are not
backfilled and old invoices are not resubmitted. Temporary extraction retries do
not notify until a final outcome. Inbox counts refresh every 30 seconds while
the page is visible; use Refresh to reload inbox contents. Notifications support
30-item pages, opening the related invoice, individual read state and mark-all-read.
All inbox queries and updates are scoped to the signed-in recipient.

## Enable email on this Windows installation

1. Obtain your email provider's SMTP hostname, port, authentication username,
   sender address and SMTP/app password. This adapter supports authenticated
   SMTP with STARTTLS or implicit TLS; providers requiring OAuth-only SMTP need
   a separate adapter. Do not put secrets in chat, source files or command arguments.
2. Double-click `configure-email.cmd` in the InvoiceDesk directory. Enter those
   values at the prompts. Password entry is hidden. The configuration is encrypted
   with Windows DPAPI for the current Windows account in
   `%LOCALAPPDATA%\InvoiceDesk\smtp.dpapi`, outside this checkout.
3. Double-click `register-staff-email.cmd` for each existing staff username and
   its authorized recipient email address. Verify the address with the staff
   member before registering it. This is an administrator-controlled local tool;
   the app does not send address-verification emails.
4. Open Notifications in the app. Registered users can disable or enable their
   own email preference. An existing opt-out is preserved when an admin changes
   the address. New registered contacts default to enabled.
5. Keep InvoiceDesk's worker running. New events queue email for enabled contacts.
   SMTP settings are reloaded per delivery, so configuration does not require
   a worker restart. No test email is sent by setup; saving SMTP settings enables
   delivery of any already queued messages. Check a real recipient inbox to verify
   delivery after generating an authorized test workflow event.

Email is currently unconfigured on the development installation. No external
email was sent during implementation. Real provider acceptance, deliverability,
sender domain configuration and spam-folder behavior remain to be verified.

## Reliability and privacy

Notification records and outbox entries commit in the same PostgreSQL transaction
as the invoice audit event. `(event_id, user_id)` prevents duplicate inbox records.
The outbox uses row locks, a five-minute lease with a unique token, up to three
attempts and delayed retries (60 and 120 seconds). Exhausted deliveries show
`failed` in the recipient's inbox; the in-app update remains available.

Email delivery is **at least once**, not exactly once. A timeout/crash after the
SMTP server accepts a message may lead to a duplicate retry. The Message-ID stays
stable across retries. SMTP acceptance does not prove inbox delivery; bounce
processing and provider delivery receipts are not implemented.

Users who opt out or are disabled are checked again before sending. Opt-out
cancels pending entries; a delivery already in flight may still arrive. Contact
changes cancel queued older entries; stop the worker when changing addresses if
an in-flight message must also be avoided. Re-enabling does not resend cancelled
entries. When SMTP is absent or unreadable, pending entries remain queued without
consuming attempts.

Email contains only a generic event title and instructions to open the workspace.
It contains no invoice amount, supplier name, document, attachment, approval action
link or localhost link. TLS certificate and hostname verification are required.
Provider errors are replaced by safe messages without passwords or response bodies.
The runtime database role can update email preferences but cannot register or
replace staff recipient addresses. Addresses and delivery history are private
database data and should be included in the existing backup/access controls.

## Upgrade and checks

Stop the application worker and web process, run
`.venv\Scripts\python.exe manage.py migrate`, then start the app. Migration 002
adds the inbox, contacts and outbox without changing existing invoice records.
The command also migrates the isolated test database.

Validation: full Python regression suite, notification recipient/isolation/CSRF
tests, transactional rollback and deduplication, preference cancellation, inactive
recipients, lease recovery, bounded retry, sanitized failures and mock SMTP TLS
ordering. Mock SMTP tests do not establish real email delivery.
