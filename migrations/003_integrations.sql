CREATE TABLE company_settings (
 id integer PRIMARY KEY CHECK(id=1), name text NOT NULL
);
INSERT INTO company_settings VALUES(1,'My company');
CREATE TABLE integrations (
 id uuid PRIMARY KEY,
 name text NOT NULL UNIQUE,
 created_by uuid NOT NULL REFERENCES users,
 active boolean NOT NULL DEFAULT true,
 scopes jsonb NOT NULL,
 token_hash text NOT NULL UNIQUE,
 token_expires_at timestamptz NOT NULL,
 mappings jsonb NOT NULL DEFAULT '{}',
 webhook_url text NOT NULL DEFAULT '',
 webhook_enabled boolean NOT NULL DEFAULT false,
 webhook_secret bytea,
 created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE integration_records (
 integration_id uuid NOT NULL REFERENCES integrations ON DELETE CASCADE,
 kind text NOT NULL CHECK(kind IN ('supplier','purchase_order')),
 external_id text NOT NULL,
 local_id uuid NOT NULL,
 content_hash text NOT NULL,
 PRIMARY KEY(integration_id,kind,external_id),
 UNIQUE(kind,local_id)
);
CREATE TABLE webhook_deliveries (
 id uuid PRIMARY KEY,
 integration_id uuid NOT NULL REFERENCES integrations ON DELETE CASCADE,
 event_id bigint NOT NULL REFERENCES audit_events ON DELETE CASCADE,
 payload text NOT NULL,
 status text NOT NULL DEFAULT 'queued' CHECK(status IN ('queued','sending','sent','failed','cancelled')),
 attempts integer NOT NULL DEFAULT 0,
 available_at timestamptz NOT NULL DEFAULT now(),
 lease_token uuid,
 lease_until timestamptz,
 response_code integer,
 error text,
 created_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(integration_id,event_id)
);
CREATE INDEX webhook_delivery_queue ON webhook_deliveries(status,available_at);
GRANT SELECT,INSERT,UPDATE ON integrations,company_settings,webhook_deliveries TO invoicedesk_app;
GRANT SELECT,INSERT ON integration_records TO invoicedesk_app;
INSERT INTO schema_versions(version) VALUES(3);
