CREATE TABLE notification_contacts (
 user_id uuid PRIMARY KEY REFERENCES users ON DELETE CASCADE,
 email text NOT NULL,
 email_enabled boolean NOT NULL DEFAULT true
);
CREATE TABLE notifications (
 id uuid PRIMARY KEY,
 event_id bigint NOT NULL REFERENCES audit_events ON DELETE CASCADE,
 user_id uuid NOT NULL REFERENCES users ON DELETE CASCADE,
 invoice_id uuid NOT NULL REFERENCES invoices ON DELETE CASCADE,
 title text NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now(),
 read_at timestamptz,
 UNIQUE(event_id,user_id)
);
CREATE INDEX notifications_inbox ON notifications(user_id,created_at DESC,id);
CREATE TABLE notification_emails (
 notification_id uuid PRIMARY KEY REFERENCES notifications ON DELETE CASCADE,
 status text NOT NULL DEFAULT 'queued' CHECK(status IN ('queued','sending','sent','failed','cancelled')),
 attempts integer NOT NULL DEFAULT 0,
 available_at timestamptz NOT NULL DEFAULT now(),
 lease_token uuid,
 lease_until timestamptz,
 error text,
 sent_at timestamptz
);
CREATE INDEX notification_email_queue ON notification_emails(status,available_at);
GRANT SELECT,INSERT,UPDATE ON notifications,notification_emails TO invoicedesk_app;
GRANT SELECT ON notification_contacts TO invoicedesk_app;
GRANT UPDATE(email_enabled) ON notification_contacts TO invoicedesk_app;
INSERT INTO schema_versions(version) VALUES(2);
