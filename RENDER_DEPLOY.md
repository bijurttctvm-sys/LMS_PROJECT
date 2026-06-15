# Render Deployment

This repo includes a `render.yaml` Blueprint for Render.

## What it creates

- `lmsproject-web`: Django web service
- `lmsproject-worker`: Celery background worker
- `lmsproject-redis`: Render Key Value instance for Celery and cache
- `lmsproject-db`: Render Postgres database

## One-time setup in Render

1. Push this repository to GitHub.
2. In Render, open `Blueprints` and create a new Blueprint from this repo.
3. Review the service names, region, and plans before deploying.
4. Fill in the prompted secret values you actually use:
   - `GROQ_API_KEY`
   - `PINECONE_API_KEY`
   - `PINECONE_INDEX_NAME`
   - `SARVAM_API_KEY`
   - `MODAL_TOKEN_ID`
   - `MODAL_TOKEN_SECRET`
   - `EMAIL_*` values if you want real email delivery
   - `CLOUDFLARE_R2_*` values if you want shared object storage

## Important: avoid duplicate stacks

- This Blueprint creates exactly 4 Render resources: `lmsproject-web`, `lmsproject-worker`, `lmsproject-redis`, and `lmsproject-db`.
- If you create a new Blueprint again for the same repo, Render may create another copy of the stack and append a suffix such as `-2nla` or `-mfjs` when the original names are already taken.
- To update an existing deployment, open the existing Blueprint or services in Render and sync/redeploy that stack instead of creating a brand-new Blueprint.
- If duplicates already exist, keep the stack you want and delete the extra web/worker/redis/db copies from the Render dashboard.

## Media storage behavior

- Profile pictures and local media use the Render persistent disk mounted at `/var/data`.
- If Cloudflare R2 is configured, course videos and generated PDFs use R2.
- If R2 is not configured:
  - uploaded video files fall back to local media storage on the web service disk
  - generated transcript PDFs are skipped in worker mode, but quiz generation and indexing still continue

## After deploy

Create an admin user from the Render Shell:

```bash
python manage.py createsuperuser
```

## Notes

- The Blueprint currently defaults to the `oregon` region. Change it in `render.yaml` if your users are elsewhere before creating the Blueprint.
- `.python-version` pins Render to Python `3.12`, which is compatible with Django 6.0 and stable for deployment.
