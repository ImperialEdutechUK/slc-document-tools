"""Copy backend settings/secrets from a local env file into AWS.

Usage (from the repository root):
    python infra/aws/push_settings.py infra/aws/backend-settings.env

The env file uses Railway's "Raw Editor" KEY=VALUE format: open the Railway
backend service > Variables > Raw Editor, copy everything into
infra/aws/backend-settings.env (git-ignored), then run this script.

* Values up to 4 KB  -> SSM Parameter Store SecureString /slc-doc-tools/backend/<KEY> (free tier).
* Larger values      -> private S3 object s3://slc-doc-tools-data-<account>/config/env/<KEY>
                        (e.g. FREEPIK_ACCOUNT_1_STORAGE_STATE_B64).
* Railway infrastructure variables (database, bucket, origins, port) are skipped;
  AWS sets its own.

Values are never printed. Uses only the "slc-document-tools" AWS profile.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

PROFILE = os.getenv("AWS_DEPLOY_PROFILE", "slc-document-tools")
REGION = "eu-west-2"
SSM_PATH = "/slc-doc-tools/backend"
SSM_MAX = 4096
SKIP_EXACT = {"DATABASE_URL", "ALLOWED_ORIGINS", "PORT", "STORAGE_DIR", "PLAYWRIGHT_CHROMIUM_EXECUTABLE"}
SKIP_PREFIXES = ("S3_", "AWS_", "RAILWAY_", "NEXT_PUBLIC_", "PG", "POSTGRES")


def parse_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip().removeprefix("export ").strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        if key and value:
            values[key] = value
    return values


def aws(*args: str) -> str:
    result = subprocess.run(
        ["aws", "--profile", PROFILE, "--region", REGION, *args],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        # stderr from the AWS CLI never contains the parameter value
        raise SystemExit(f"aws {args[0]} {args[1]} failed: {result.stderr.strip()}")
    return result.stdout.strip()


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    values = parse_env(Path(sys.argv[1]))
    account = aws("sts", "get-caller-identity", "--query", "Account", "--output", "text")
    bucket = f"slc-doc-tools-data-{account}"

    stored, skipped, unresolved = [], [], []
    for key, value in sorted(values.items()):
        if key in SKIP_EXACT or key.startswith(SKIP_PREFIXES):
            skipped.append(key)
            continue
        if "${{" in value:
            unresolved.append(key)
            continue
        if len(value.encode("utf-8")) <= SSM_MAX:
            payload = {
                "Name": f"{SSM_PATH}/{key}",
                "Value": value,
                "Type": "SecureString",
                "Overwrite": True,
                "Tier": "Standard",
            }
            with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as handle:
                json.dump(payload, handle)
                temp = handle.name
            try:
                aws("ssm", "put-parameter", "--cli-input-json", "file://" + temp)
            finally:
                os.unlink(temp)
            stored.append(f"{key} -> SSM")
        else:
            with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as handle:
                handle.write(value)
                temp = handle.name
            try:
                aws("s3", "cp", temp, f"s3://{bucket}/config/env/{key}", "--sse", "AES256", "--only-show-errors")
            finally:
                os.unlink(temp)
            stored.append(f"{key} -> S3 (large value)")

    print("Stored:")
    for line in stored:
        print("  " + line)
    if skipped:
        print("Skipped (Railway infrastructure, set by AWS instead): " + ", ".join(skipped))
    if unresolved:
        print(
            "NOT stored - these are Railway references (${{...}}), not real values. "
            "Replace them with the actual values in the env file and run again: " + ", ".join(unresolved)
        )
    print("Run 'bash infra/aws/deploy.sh restart' so running Lambda instances load the new settings.")


if __name__ == "__main__":
    main()
