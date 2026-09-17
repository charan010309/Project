# Functionality update

The app now serves a data-backed overview at `/overview`, linked from Home.
History clearing routes no longer conflict with conversation IDs. Text/image
failures return error responses, and image and speech generation require sign-in.
Live screen analysis is stored in the selected live conversation, not Chat.
Custom provider selections survive a settings reload.

Provider statistics measure custom-provider text requests over the last 24 hours.
They include successes and failures and the 95th percentile of full response
duration. Built-in Flash and image requests are not included. Tracking starts
with this update; no historical measurements are invented.

The Settings voice preview uses the selected speech voice. Generated speech files
are removed after delivery. Screen fidelity caps the longest edge of a screenshot
at 1280, 1920, or 3840 pixels without upscaling. Text-reading and position controls
add instructions to image prompts; they are not separate OCR or geometry engines.

Continuous screen sampling, zero-retention mode, background sound cues, weekly
email digests, and custom-provider image generation are unavailable and identified
as such. The voice bars are a listening indicator, not an audio-level meter.

## Setup and verification

Install `Backend/requirements.txt` into the backend virtual environment.
Launch the backend with `Backend/venv/Scripts/python.exe Backend/app.py` from the
project root. Normal startup uses the user's existing configuration; this update
did not inspect or change `.env`. Restart an already-running server to load changes.

The database path is `database/maximus.db`, independent of the launch folder.
The previously empty `database/jarvis.db` was renamed to `maximus.db`.

Run the isolated regression suite:

```
Backend/venv/Scripts/python.exe Backend/test_functionality.py
node Frontend/test-ui.cjs
```

The backend suite disables dotenv, substitutes synthetic settings, and uses a
temporary database. External AI and speech calls are simulated. It does not
verify real credentials, email delivery, OAuth, microphone permissions, or actual
provider availability. The frontend suite checks script syntax and dashboard
success, empty, offline, and signed-out states; it is not a browser visual test.
