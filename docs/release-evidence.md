# Verification record

Historical local verification on 14 November 2026 recorded 114 passing backend tests and three frontend tests. FeedDesk recorded 84 passing tests, including an isolated real HTTP supplier/PO import, invoice review, independent approval, return synchronization and replay check. No live invoice was approved for publication.

The synthetic invoice evaluation returned 7/8 valid outputs and 103/116 field and line checks, including a temporary provider failure. A separately retained retry passed 13/13 checks. These developer-authored cases are not a representative customer benchmark; do not combine separate runs to claim perfect reliability.

The earlier load probe recorded 100 successful responses and p95 406.65 ms with 10,000 synthetic records and ten concurrent sessions against in-process ASGI and real PostgreSQL. It excludes external network, browser and extraction latency.

Private local logs, screenshots, configuration and machine-specific evaluation reports are excluded from this public repository. Reproducible synthetic fixtures and evaluation scripts are included under examples/ and the repository root. Live evaluation requires explicit consent and your own provider credentials and may incur charges. Publishing this copy is not a new full test run.

SMTP delivery remains unconfigured. Webhook network tests use mocks. The application is a local portfolio release, not a claim of public production readiness. See architecture.md, notifications.md and integrations.md for implementation details and boundaries.
