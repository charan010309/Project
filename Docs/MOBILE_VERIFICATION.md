# Dedicated mobile UI

The mobile landing page and chat, voice, history, settings, profile, and provider
pages use `Frontend/static/mobile.css`. Auth pages retain their existing flows.
The six signed-in pages share `Frontend/static/mobile.js`; desktop templates
are unchanged. Colours retain green accents and dark background `#141413`.

## Verification (2026-09-18)

- 23 isolated backend regression tests pass (`Backend/test_functionality.py`).
- JavaScript syntax check: `node --check Frontend/static/mobile.js`.
- Browser tested at 390×844 and 320×740: chat, history, profile, settings, voice
  and providers render; inspected narrow pages have no horizontal overflow.
- Browser → real FastAPI → temporary SQLite → browser: sent a message, opened
  saved history, pinned and reopened a conversation, saved profile and assistant
  settings, and verified persistence after reload.
- Dark mode persists after reload; computed background is `rgb(20, 20, 19)`.
- Injected generation failure shows an error and preserves the input draft.
- Voice-page typing reaches the live API and renders a saved response.

The reproducible local preview is `venv/Scripts/python.exe
Backend/test_mobile_preview.py`, then open `http://127.0.0.1:8011/test-mobile-login`.
It uses synthetic authentication, a temporary DB, and mocked AI responses; it
never loads local `.env`. The preview is not imported by production and test
files are excluded by `.vercelignore`.

## Remaining device/external checks

Actual microphone recognition depends on browser support and permission.
Live Gemini output, live provider credentials, and audio playback were not
exercised by this isolated UI test. Backend speech tests use mocked synthesis.
No fake metrics or canned answers ship in the mobile UI. QR/overview routes
continue to use their existing fallback templates.
