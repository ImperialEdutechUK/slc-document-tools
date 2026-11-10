# SLC Document Tools on AWS

Low-cost AWS deployment (target: under $1/month) that runs alongside the existing
Railway deployment. All code changes switch on only when AWS-specific environment
variables are present, so the Railway/Vercel deployment behaves exactly as before.

**Site:** https://slc-document-formatter.imperial.imet.lk

## Architecture (eu-west-2, London)

```
Browser ──► CloudFront (custom domain, ACM cert) ──► S3 site bucket (static Next.js export)
   │
   └──────► Lambda Function URL (15-min timeout) ──► Lambda container image
                                                     (FastAPI + LibreOffice + Chromium, Lambda Web Adapter)
                                                       ├─ DynamoDB  slc-doc-tools-jobs   (job records)
                                                       ├─ S3        slc-doc-tools-data-* (jobs/, image library, transfer/)
                                                       └─ SSM       /slc-doc-tools/backend/* (API keys)
EventBridge Scheduler ─► keep-warm ping, weekdays 07:00–18:55 UK time
```

| Railway / Vercel | AWS |
|---|---|
| Docker service (`Dockerfile`) | Lambda container image (`backend/Dockerfile.lambda`), 3008 MB, 900 s timeout |
| PostgreSQL `jobs` table | DynamoDB `slc-doc-tools-jobs` (`backend/app/jobs_store.py`) |
| Railway bucket | S3 `slc-doc-tools-data-<account>` (same `Storage` class) |
| Railway Variables | SSM Parameter Store `/slc-doc-tools/backend/*` (+ S3 `config/env/` for values > 4 KB) |
| Vercel Next.js | Static export on S3 + CloudFront |

### Lambda payload limit (6 MB)
Function URLs accept and return at most ~6 MB per request. `backend/app/lambda_io.py`
and the `apiRequest` helper in `frontend/app/page.tsx` move larger bodies through S3:

* uploads over 4 MB are PUT straight to S3 with a pre-signed URL, and the API call carries an `X-Body-S3-Key` header;
* responses over 4 MB are written to S3 and returned as a `303` redirect to a pre-signed download URL.

Temporary transfer copies live under `transfer/` and expire after 1 day.

### Data retention
Same as Railway: job files (`jobs/`) and job records are **kept indefinitely**.
To delete them after N days (approval required), deploy with `JOBS_RETENTION_DAYS=N`.

## AWS profile
Everything uses the dedicated CLI profile **`slc-document-tools`** (IAM user
`slc-document-tools-deployer`, policy `slc-doc-tools-deployer-policy` from
`iam/deployer-policy.json`, restricted to `slc-doc-tools*` resources and the
`imperial.imet.lk` hosted zone).

## Stacks
| Stack | Region | Template |
|---|---|---|
| `slc-doc-tools-cert` | us-east-1 | `cert.yaml` (ACM certificate, DNS-validated) |
| `slc-doc-tools-repo` | eu-west-2 | `repo.yaml` (ECR, keeps last 3 images) |
| `slc-doc-tools` | eu-west-2 | `app.yaml` (everything else) |

## Deploying (Git Bash, Docker Desktop running)

```bash
bash infra/aws/deploy.sh            # everything
bash infra/aws/deploy.sh backend    # new backend image -> Lambda
bash infra/aws/deploy.sh frontend   # rebuild + upload static site, invalidate CloudFront
bash infra/aws/deploy.sh infra      # CloudFormation changes only
bash infra/aws/deploy.sh restart    # reload settings after push_settings.py
```

Optional: `BUDGET_EMAIL=you@example.org bash infra/aws/deploy.sh infra` adds monthly
cost alerts ($1 actual, $2 forecast) filtered to the `Project=slc-doc-tools` tag.

## Backend settings / API keys
1. Railway → backend service → **Variables → Raw Editor**, copy all.
2. Paste into `infra/aws/backend-settings.env` (git-ignored).
3. `python infra/aws/push_settings.py infra/aws/backend-settings.env`
4. `bash infra/aws/deploy.sh restart`
5. Delete the local `backend-settings.env`.

Railway-only infrastructure variables (`DATABASE_URL`, `S3_*`, `AWS_*`, `ALLOWED_ORIGINS`, …) are skipped.

## Logs
CloudWatch log group `/aws/lambda/slc-doc-tools-backend` (14-day retention):

```bash
aws logs tail /aws/lambda/slc-doc-tools-backend --follow --profile slc-document-tools
```

## Estimated monthly cost
Lambda and CloudFront within the always-free tier; ECR ~$0.15–0.30; S3 ~$0.10–0.20;
DynamoDB, SSM standard parameters, Scheduler, Logs (<5 GB) ≈ $0. **≈ $0.30–0.50/month.**

## Known limits
* The account-wide Lambda concurrency limit is 10 (shared with other projects in the
  account), so at most 10 requests are processed at once. Request an increase in
  Service Quotas if needed.
* The first request after an idle period outside the keep-warm hours has a cold start of a few seconds.
