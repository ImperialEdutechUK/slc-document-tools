#!/usr/bin/env bash
# Deploy SLC Document Tools to AWS (eu-west-2) using ONLY the dedicated
# "slc-document-tools" AWS CLI profile.
#
#   infra/aws/deploy.sh            # infra + backend image + frontend
#   infra/aws/deploy.sh backend    # rebuild/push backend image and update Lambda
#   infra/aws/deploy.sh frontend   # rebuild static frontend and upload to S3/CloudFront
#   infra/aws/deploy.sh infra      # CloudFormation only (keeps current image)
#   infra/aws/deploy.sh restart    # recycle Lambda instances so new SSM settings load
#
# Optional environment: BUDGET_EMAIL (cost alerts), JOBS_RETENTION_DAYS (0 = keep forever).
set -euo pipefail
shopt -s inherit_errexit 2>/dev/null || true # fail inside $(...) too
export MSYS_NO_PATHCONV=1 # Git Bash on Windows: do not rewrite /paths in arguments

PROFILE="${AWS_DEPLOY_PROFILE:-slc-document-tools}"
REGION="eu-west-2"
APP_STACK="slc-doc-tools"
REPO_NAME="slc-doc-tools-backend"
# "pwd -W" (Git Bash) gives C:/... paths that Windows aws.exe/docker.exe/git.exe accept.
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && { pwd -W 2>/dev/null || pwd; })"
HERE="$ROOT/infra/aws"
STEP="${1:-all}"

awsp() { aws --profile "$PROFILE" "$@"; }
output() { # stack region key
  awsp cloudformation describe-stacks --region "$2" --stack-name "$1" \
    --query "Stacks[0].Outputs[?OutputKey=='$3'].OutputValue" --output text | tr -d '\r'
}

echo "Using AWS profile: $PROFILE ($(awsp sts get-caller-identity --query Arn --output text | tr -d '\r'))"

deploy_base() {
  awsp cloudformation deploy --region us-east-1 --stack-name slc-doc-tools-cert \
    --template-file "$HERE/cert.yaml" --tags Project=slc-doc-tools --no-fail-on-empty-changeset
  awsp cloudformation deploy --region "$REGION" --stack-name slc-doc-tools-repo \
    --template-file "$HERE/repo.yaml" --tags Project=slc-doc-tools --no-fail-on-empty-changeset
}

build_and_push_image() {
  local repo_uri tag digest
  repo_uri="$(output slc-doc-tools-repo "$REGION" RepositoryUri)"
  tag="$(git -C "$ROOT" rev-parse --short HEAD)-$(date +%Y%m%d%H%M%S)"
  docker buildx build --platform linux/amd64 --provenance=false --load \
    -f "$ROOT/backend/Dockerfile.lambda" -t "$repo_uri:$tag" "$ROOT/backend" >&2
  awsp ecr get-login-password --region "$REGION" | docker login --username AWS --password-stdin "${repo_uri%%/*}" >&2
  docker push "$repo_uri:$tag" >&2
  digest="$(awsp ecr describe-images --region "$REGION" --repository-name "$REPO_NAME" \
    --image-ids imageTag="$tag" --query 'imageDetails[0].imageDigest' --output text | tr -d '\r')"
  if [[ "$digest" != sha256:* ]]; then
    echo "Could not resolve the pushed image digest" >&2
    exit 1
  fi
  echo "$repo_uri@$digest"
}

deploy_app() {
  local image="${1:-}" params=()
  params+=("CertificateArn=$(output slc-doc-tools-cert us-east-1 CertificateArn)")
  [[ -n "$image" ]] && params+=("ImageUri=$image")
  [[ -n "${BUDGET_EMAIL:-}" ]] && params+=("BudgetEmail=$BUDGET_EMAIL")
  [[ -n "${JOBS_RETENTION_DAYS:-}" ]] && params+=("JobsRetentionDays=$JOBS_RETENTION_DAYS")
  awsp cloudformation deploy --region "$REGION" --stack-name "$APP_STACK" \
    --template-file "$HERE/app.yaml" --capabilities CAPABILITY_NAMED_IAM \
    --tags Project=slc-doc-tools --no-fail-on-empty-changeset \
    --parameter-overrides "${params[@]}"
}

update_function_image() {
  awsp lambda update-function-code --region "$REGION" --function-name slc-doc-tools-backend \
    --image-uri "$1" --query 'LastUpdateStatus' --output text
  awsp lambda wait function-updated-v2 --region "$REGION" --function-name slc-doc-tools-backend
  # keep CloudFormation's parameter in sync with the running image
  deploy_app "$1"
}

deploy_frontend() {
  local api bucket dist
  api="$(output "$APP_STACK" "$REGION" ApiUrl)"
  bucket="$(output "$APP_STACK" "$REGION" SiteBucketName)"
  dist="$(output "$APP_STACK" "$REGION" DistributionId)"
  (
    cd "$ROOT/frontend"
    [[ -d node_modules ]] || npm install --no-audit --no-fund
    rm -rf out
    NEXT_OUTPUT=export NEXT_PUBLIC_API_URL="${api%/}" NEXT_PUBLIC_BODY_OFFLOAD=1 npx next build
  )
  awsp s3 sync "$ROOT/frontend/out" "s3://$bucket" --region "$REGION" --delete \
    --exclude "*.html" --exclude "*.txt" --cache-control "public,max-age=31536000,immutable"
  awsp s3 sync "$ROOT/frontend/out" "s3://$bucket" --region "$REGION" \
    --exclude "*" --include "*.html" --include "*.txt" --cache-control "no-cache"
  awsp cloudfront create-invalidation --distribution-id "$dist" --paths "/*" --query 'Invalidation.Id' --output text
}

case "$STEP" in
  all)
    deploy_base
    image="$(build_and_push_image)"
    deploy_app "$image"
    deploy_frontend
    ;;
  backend)
    image="$(build_and_push_image)"
    update_function_image "$image"
    ;;
  frontend)
    deploy_frontend
    ;;
  infra)
    deploy_base
    deploy_app
    ;;
  restart)
    current="$(awsp lambda get-function --region "$REGION" --function-name slc-doc-tools-backend       --query 'Code.ImageUri' --output text | tr -d '')"
    awsp lambda update-function-code --region "$REGION" --function-name slc-doc-tools-backend       --image-uri "$current" --query 'LastUpdateStatus' --output text
    awsp lambda wait function-updated-v2 --region "$REGION" --function-name slc-doc-tools-backend
    ;;
  *)
    echo "Unknown step: $STEP (use all|backend|frontend|infra|restart)" >&2
    exit 2
    ;;
esac

echo
echo "Site: $(output "$APP_STACK" "$REGION" SiteUrl)"
echo "API:  $(output "$APP_STACK" "$REGION" ApiUrl)"
