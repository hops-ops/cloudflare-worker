#!/usr/bin/env bash
set -euo pipefail

name="hops-worker-e2e-${GITHUB_RUN_ID}-${GITHUB_RUN_ATTEMPT}"
registry="ghcr.io"
repository="$registry/${GITHUB_REPOSITORY,,}-e2e"
fixture_dir="$(mktemp -d "${RUNNER_TEMP}/worker-fixture.XXXXXXXX")"
printf '%s' "$GHCR_TOKEN" | oras login "$registry" --username "$GITHUB_ACTOR" --password-stdin
cd "$fixture_dir"

cat > worker-artifact.json <<'JSON'
{
  "schemaVersion": 1,
  "mainModule": "worker.js",
  "modules": [{ "name": "worker.js", "contentType": "application/javascript+module" }]
}
JSON
for version in v1 v2; do
  printf 'export default {fetch() { return new Response("%s:%s"); }};\n' "$name" "$version" > worker.js
  tar -czf bundle.tgz worker-artifact.json worker.js
  target="$repository:$name-$version"
  oras push "$target" --artifact-type application/vnd.gitkb.cloudflare.worker.v1 \
    --annotation "org.opencontainers.image.source=https://github.com/$GITHUB_REPOSITORY" \
    bundle.tgz:application/vnd.gitkb.cloudflare.worker.bundle.v1.tar+gzip
  digest="$(oras resolve "$target")"
  [[ "$digest" =~ ^sha256:[0-9a-f]{64}$ ]]
  printf '%s=%s@%s\n' "$version" "$repository" "$digest" >> "$GITHUB_OUTPUT"
done
