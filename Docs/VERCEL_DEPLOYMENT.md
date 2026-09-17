# Deploy MAXIMUS on Vercel

## Folder layout

Deploy the repository root, not `Frontend`, `Backend`, or the nested `MAXIMUS`
repository. The existing desktop/mobile layout is preserved.

```text
Project/
  api/index.py             FastAPI entry point
  Backend/                 Application, models, shared authentication storage
  Frontend/
    pc/                    Desktop pages
    mobile/                Mobile pages; missing pages fall back to desktop
    static/                Shared styles, scripts, logo
  database/maximus.db      Local development only; excluded from deployment
  requirements.txt         Includes Backend/requirements.txt
  pyproject.toml           Declares api.index:app as the Vercel entry point
  .python-version          Python 3.13
  vercel.json              FastAPI preset, frontend bundle, 60-second limit
  .vercelignore            Excludes local secrets, databases, venv, extra folders
```

The nested `MAXIMUS/` is currently a Git submodule/gitlink, not the deployment
root. It was not changed. Vercel Git imports may try to fetch committed
submodules: ensure that reference is accessible or resolve its repository
configuration before importing. Upload exclusions do not repair Git checkout
errors. Do not delete the nested repository without checking its contents.

## 1. Create a hosted PostgreSQL database

Use a hosted PostgreSQL service such as Neon. Copy its pooled connection URL
with TLS enabled into Vercel's `DATABASE_URL` setting. Do not put the URL in
source files. An illustrative format (not a real credential) is:

```text
postgresql://USER:PASSWORD@HOST/DATABASE?sslmode=require
```

On Vercel the app rejects SQLite configuration. Locally, with no `DATABASE_URL`,
it uses `database/maximus.db`. Your existing database file is not uploaded,
deleted, or migrated. A new PostgreSQL database starts without your local
accounts and conversations. If those must be retained, back up and migrate
them separately before switching users to production.

Tables and compatibility migrations run at application startup, not module
import. PostgreSQL startup migrations use an advisory transaction lock so
multiple cold starts do not alter the schema simultaneously. The database
role needs table creation/alteration privileges for this setup.

## 2. Import the project

1. Put the source code in a repository that Vercel can access. Review staged
   files first; ignore rules do not remove files already tracked by Git.
2. In Vercel, choose **Add New → Project** and import the repository.
3. Set **Root Directory** to the repository root (`.`).
4. Use the **FastAPI** framework preset. Leave build command and output
   directory overrides unset; do not enter an HTML output directory.
5. Add the environment variables below before deploying.

The root requirements file installs the Python dependencies. Do not upload
your virtual environment. Vercel supplies the `VERCEL` environment indicator;
you do not need to add it manually.

## 3. Configure environment variables

Add these in Vercel's project settings, not in a committed `.env` file:

| Variable | Value to supply |
| --- | --- |
| `DATABASE_URL` | Hosted PostgreSQL connection URL with TLS |
| `GEMINI_API_KEY` | Your current Google Gemini API key |
| `SESSION_SECRET` | A long random private value, at least 32 random bytes recommended |
| `MAIL_USERNAME` | Gmail sender account |
| `MAIL_PASSWORD` | Gmail app password for that account, not the normal sign-in password |
| `MAIL_FROM` | Valid sender email address; usually the same as `MAIL_USERNAME` |
| `GOOGLE_CLIENT_ID` | Google OAuth web client ID, if Google login is enabled |
| `GOOGLE_CLIENT_SECRET` | Corresponding OAuth client secret |
| `GOOGLE_REDIRECT_URI` | `https://YOUR-DOMAIN/auth/google/callback` |
| `APP_BASE_URL` | `https://YOUR-DOMAIN`, with no path |

The current mail implementation uses Gmail SMTP on port 587. Mail settings
are validated during import and must be present even if you plan to use
Google login primarily. Gemini and session settings must also be present.

Optional settings:

- `MAXIMUS_PROVIDER_ENCRYPTION_KEY`: a stable secret for saved provider
  credentials. Without it, encryption derives from `SESSION_SECRET`. Preserve
  the original encryption key when migrating saved credentials; changing it
  makes old encrypted credentials unreadable.
- `ALLOWED_ORIGINS`: comma-separated exact frontend origins only if serving
  a separate frontend. Leave unset for this same-origin deployment.

Keep production secrets stable across deployments. Rotating `SESSION_SECRET`
invalidates sessions and pending codes, and may affect saved provider keys as
described above. Any credentials previously exposed in chat or source control
should be replaced before production use.

For preview deployments, use a separate database and explicitly configure
preview URLs and OAuth callbacks. Do not let untrusted preview code access
production credentials. If `APP_BASE_URL` is absent, link generation falls
back to Vercel's production hostname, then its deployment hostname.

## 4. Configure Google login and deploy

In the Google OAuth web client's authorized redirect URIs, add exactly:

```text
https://YOUR-DOMAIN/auth/google/callback
```

Set the same value in `GOOGLE_REDIRECT_URI`. Keep the localhost callback as
a separate entry if you still develop locally. When changing domains, update
the Google configuration, `APP_BASE_URL`, and `GOOGLE_REDIRECT_URI`, then
redeploy. Environment changes require a new deployment to take effect.

Deploy after the database and required variables are configured.

## 5. Verify the deployed site

- Open `/`, `/home`, `/m/home`, `/settings`, `/overview`, and `/static/theme.css`.
- Test signup/email code, sign-in, Google login, logout, and password reset.
- Generate a QR code while logged in; scan it on another device. It expires
  after 60 seconds and can only be claimed once. Treat it like a password.
- Save a conversation, refresh, and verify it persists across redeployments.
- Test AI replies, voice playback, and small image uploads using real services.
- Check function logs for configuration/database errors without sharing secrets.

## Serverless changes and limits

- Backend imports work through `api.index:app` and local `uvicorn`.
- Login codes and QR transfers use PostgreSQL instead of per-process memory.
- Temporary speech output uses the writable temporary directory and is removed
  after the response. No runtime writes go into the deployed source folder.
- Password reset and QR links use the configured public domain.
- Production session cookies require HTTPS.
- Custom-provider requests use a 45-second timeout on Vercel; the function is
  configured for 60 seconds. Slow AI calls can still time out.
- Localhost/Ollama providers on your computer are not reachable from a Vercel
  function. Use a reachable, secured provider endpoint for deployed use.
- Vercel limits request and response bodies to 4.5 MB. Base64 images add about
  one-third overhead: keep original images below roughly 2.5 MB, with room for
  the prompt and other JSON fields. Larger requests can be rejected before
  Python runs. Long speech responses may also exceed the response limit.

## Verification scope

Sixteen local regression tests pass using synthetic configuration and a
temporary SQLite database. They cover entry-point imports, main desktop/mobile
routes and assets, login challenges, QR transfer, mocked AI/error handling,
history isolation, settings, metrics, and speech cleanup. They do not read
the private `.env` or the real local database.

No Vercel deployment or live PostgreSQL, OAuth, mail, or AI service verification
was performed. These are deployment preparation changes, not a certification
that every frontend control is implemented. Some mobile templates still have
placeholder interactions; audit those separately before a public launch.

## Official references

- [FastAPI on Vercel](https://vercel.com/docs/frameworks/backend/fastapi)
- [Python runtime](https://vercel.com/docs/functions/runtimes/python)
- [Function configuration](https://vercel.com/docs/project-configuration/vercel-json#functions)
- [Function limits](https://vercel.com/docs/functions/limitations)
