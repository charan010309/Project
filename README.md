# MAXIMUS

FastAPI backend with desktop and mobile HTML frontends.

For Vercel, import **this repository root**, containing `vercel.json`, `api/`,
`Backend/`, and `Frontend/`. Do not select the nested `MAXIMUS/` folder.

See [the deployment guide](Docs/VERCEL_DEPLOYMENT.md) for database setup,
environment variables, Google login configuration, and verification steps.

Local development uses `database/maximus.db` unless `DATABASE_URL` overrides it.
Vercel requires hosted PostgreSQL; a local SQLite file cannot provide durable
storage there. Existing local data is not automatically migrated.

Run locally from the repository root after installing `requirements.txt`:

```sh
python -m uvicorn Backend.app:app --reload
```

Run isolated regression tests (synthetic settings and temporary database):

```sh
python Backend/test_functionality.py
```

Never commit environment files, API keys, passwords, or database files.
