# InvoiceDesk integration guide — API v1

## What this version supports

One company per deployment/database. An administrator can name the company,
create multiple source connections, choose permissions, map CSV columns, import
suppliers/purchase orders, rotate API credentials, configure signed event
notifications, and inspect/retry failed webhook deliveries. Connections are
sources within one company, not tenants. Staff still share the company workspace.

This is a generic integration contract, not a prebuilt SAP, Oracle, Odoo or other
vendor connector. Systems that can call a REST API can implement this contract;
systems that export CSV can use the mapping interface. Vendor-specific API calls,
automatic inbound polling, accounting posting, supplier updates, stock receipts,
three-way matching, unit conversion and custom currency/tax rules are not included.

## Setup in the app

1. Sign in as an admin and choose **Integrations**. Set the company name.
2. Create a connection with only the permissions the external system needs.
   Save its one-time API key in that system's server secret store. Keys expire
   after 90 days. Replacing a key immediately invalidates the previous key.
3. Import suppliers first, then purchase orders. Use canonical JSON through the
   API, or choose a CSV type, map each field to your CSV column heading and save
   settings. Preview the file, inspect the result, then import it.
4. For outbound events, generate a signing secret. Save it in the receiving
   system, enter its authorized public HTTPS endpoint and enable delivery.
   The API key and signing secret are different credentials.
5. Review/approve an invoice referencing an imported purchase order. The
   corresponding connection can retrieve that approved invoice and receive its
   event. No connection receives invoices from another source's purchase orders.

Secrets are shown for 60 seconds and cleared on sign-out or leaving this view.
API keys are stored only as hashes. Webhook signing secrets use the existing
Windows user-scoped DPAPI encryption. Do not place secrets in URLs, CSV files,
browser code, repositories or chat. Changing the administrator's active status
or role can disable the connection's API and delivery authority.

Disabling a connection blocks new machine requests and cancels pending webhook
deliveries. Requests already authorized/in flight may complete. Changing a webhook
destination or signing secret cancels pending deliveries; secret replacement also
disables sending until the receiver is updated and delivery is explicitly enabled.

## Deployment and reachability

The current installation listens only on `http://127.0.0.1:8030` on this computer.
It is suitable for local integration testing; a remote company's ERP cannot call
this loopback address. A deployed company installation needs its own database,
staff provisioning, private storage, trusted HTTPS origin/host configuration,
TLS reverse proxy and network access controls. The existing bootstrap reuses the
local FeedDesk PostgreSQL installation; it is not a standalone SaaS installer.
Do not expose the development server or weaken host/origin checks to connect it.
Multi-company self-signup, hosted tenant provisioning and public deployment are
separate work. Each company must receive a separately provisioned deployment.

## Server API

`examples/integration/client.py` is a small server-side starter client. Run it
with this project's Python environment and `status`, `import suppliers FILE.json`
or `invoices`. Import defaults to preview; `--commit` explicitly saves the batch.
The key comes from `INVOICEDESK_API_KEY` or a hidden prompt. Invoice results printed
by this example contain company data: use an authorized terminal and avoid shared
logs. The example is a connector starting point, not a vendor-specific integration.

Base path: `/api/integration/v1`. Supply `Authorization: Bearer YOUR_API_KEY`.
For writes send `Content-Type: application/json`. Browser origins/cookies are
rejected: call from your ERP's backend, not from frontend JavaScript. Session
cookies cannot authenticate machine endpoints. Limits: 120 calls/minute per
connection and client IP; request body 128 KiB. Requests/responses are no-store.

| Method/path | Permission | Result |
| --- | --- | --- |
| GET `/status` | Valid key | Connection ID, scopes, key expiry |
| POST `/import/suppliers` | `reference:write` | Validate/import suppliers |
| POST `/import/purchase_orders` | `reference:write` | Validate/import purchase orders |
| GET `/records/supplier/{external_id}` | `reference:write` | Local supplier ID |
| GET `/records/purchase_order/{external_id}` | `reference:write` | Local PO ID and line IDs |
| GET `/invoices?offset=0` | `invoices:read` | Up to 100 approved/exported invoices linked to this connection |

An invoice response includes `id`, `state`, `version`, reviewed financial fields
under `review` and `purchase_order_external_id`. Poll the list from offset zero
periodically and upsert by invoice ID/version; offset paging is not a change feed
or immutable snapshot. A webhook receiver may use the same list to retrieve an
approved record; rejected invoices are never exposed as approved data.

Supplier JSON:

```json
{"dry_run": true, "records": [{"external_id": "V-001", "name": "Example Packaging Supplier"}]}
```

Purchase order JSON:

```json
{
  "dry_run": true,
  "records": [{
    "external_id": "PO-001", "reference": "ERP-PO-001",
    "supplier_external_id": "V-001", "currency": "PKR",
    "lines": [{"external_id": "1", "description": "Packaging bags", "quantity": "100", "unit_price": "125.50"}]
  }]
}
```

`dry_run` defaults to true. Preview performs database validation inside a rolled
back transaction. Set it to false only to commit the full batch. Imported supplier
references must exist before previewing/importing their POs. Up to 100 records
per request, 100 lines per PO. Decimal strings avoid binary floating-point drift.
Supported currencies: PKR, USD, EUR, GBP. Use one consistent unit per PO line;
the current model does not support unit-of-measure conversions.

External IDs are unique per connection/type. Identical canonical replay returns
`unchanged`; a changed record under the same external ID returns 409. Imports
never overwrite an approval baseline or silently match another source by name.
Conflicting existing supplier names/order references are rejected atomically.
Use a dedicated source connection and unique supplier names/references. Existing
manual records are not automatically linked. A reviewed amendment workflow is
needed for changing previously imported references.

Response errors: 401 invalid/expired/revoked key; 403 permission/origin mismatch;
409 conflicting record/state; 413 oversized body; 422 invalid data; 429 rate limit.
If a network response is lost, safely replay the same batch. Do not blindly retry
409/422 errors with modified values. All-or-nothing batches return no partial writes.

## CSV mapping

Required supplier fields: `external_id,name`.

Required purchase-order fields:
`external_id,reference,supplier_external_id,currency,line_id,description,quantity,unit_price`.
One row per PO line; repeated order fields must match. Line IDs must be unique
within each PO. Map fields to exact, case-sensitive column headers, for example
`external_id` to `Vendor Code`, `name` to `Vendor Name`. Save each type's mapping
before preview. UTF-8 with optional BOM, comma delimiter and standard CSV quoting;
100 KB, 500 rows and 100 grouped records maximum. Extra columns are ignored;
missing/duplicate required headers and malformed rows are rejected. Spreadsheet
formulas are never evaluated. Examples are in `examples/integration/`.

## Signed webhooks

Events: `invoice.approved`, `invoice.rejected`, `invoice.exported`. Only invoices
whose purchase order originated in this connection generate events for it, and
the connection needs `invoices:read`. Delivery starts only for new events after
configuration; no historical notifications are sent automatically.

Payload example:

```json
{"delivery_id":"UUID","event":"invoice.approved","invoice_id":"UUID","purchase_order_external_id":"PO-001","schema_version":1}
```

Headers:
- `X-InvoiceDesk-Delivery`: stable delivery UUID
- `X-InvoiceDesk-Timestamp`: Unix seconds for this attempt
- `X-InvoiceDesk-Signature`: `sha256=` plus lowercase hex HMAC-SHA256

Verify the HMAC over `timestamp + "." + raw_request_body` using the signing secret.
Use constant-time comparison, reject timestamps more than five minutes from your
server clock, require the header delivery ID to match the body and durably
deduplicate that ID before business processing. A timestamp check alone does not
prevent replay within its window. Reply 2xx after durable acceptance. The example
`examples/integration/verify_webhook.py` validates the signature envelope; the
receiving system must still implement persistent deduplication and authorization.

Delivery is at least once, not exactly once. Three automatic attempts with
60/120-second backoff; 408/429/5xx and network failures are retried. Other non-2xx
responses fail immediately, including redirects. Failed deliveries can be retried
by an admin. Lease tokens fence stale completion writes; a crash or ambiguous
network response can still produce duplicate external delivery.

Only public IPv4 HTTPS destinations on port 443 are supported. Destination URLs
must not contain credentials, query strings or fragments. Every attempt resolves
IPv4 addresses, rejects non-global addresses and connects directly to the checked
IP with certificate/hostname validation. Redirects and environment proxies are
not followed. IPv6-only and private ERP endpoints must use the pull API or an
appropriately deployed gateway; do not bypass these checks.

Outbox entries are committed with invoice audit events. Logs retain safe error
messages and HTTP status, never response bodies, keys or full invoice content.
No actual ERP endpoint has been configured or contacted in development. Real ERP
contract compatibility, network access and webhook acceptance require a company
pilot. No vendor-specific compatibility certification is claimed.

Security reference: [OWASP SSRF prevention](https://cheatsheetseries.owasp.org/cheatsheets/Server_Side_Request_Forgery_Prevention_Cheat_Sheet.html).
