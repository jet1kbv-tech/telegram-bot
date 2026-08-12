# Phase 2B: Documents UX

The user-facing document model is now **Event → 📎 Documents → event attachments**.
Standalone legacy Tickets navigation and the Tickets panel on Afisha cards are hidden. The
legacy handlers remain registered so stale callbacks fail safely, and `tickets.active` /
`tickets.used` remain normalized and persisted without migration or deletion.

The next planned phase is **Smart File Enrichment**. It may temporarily read a PDF or photo,
propose structured metadata, and ask the user to confirm it. Phase 2B deliberately performs no
downloads for analysis, OCR, PDF parsing, vision analysis, attachment-retrieval intent, fuzzy
matching, embeddings, or legacy-ticket migration.
