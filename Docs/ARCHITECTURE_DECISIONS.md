# Architecture decisions

## Public landing page claims — 2026-10-28

Describe controls and features supported by the current implementation. Chat
and voice history is stored; a zero-retention or ephemeral mode is not available.
Do not publish compliance, pricing, retention, provider, or security guarantees
without supporting information. Privacy Policy, Terms, and support links need
verified team details before publication.

## Mobile and PC frontend presentation — 2026-10-28

MAXIMUS keeps separate mobile and PC HTML pages. Mobile pages use the PC pages
as the visual reference, adapting the layout for touch and smaller screens.
Existing mobile scripts, page controls, and backend API contracts remain the
integration points; visual alignment does not require replacing mobile pages
with desktop templates. Shared theme assets remain in `Frontend/static`.
