#!/usr/bin/env bash
# Audit every locked dependency (all extras) for known vulnerabilities, as CI does.
# Usage: scripts/audit-deps.sh
# Accepted findings live in scripts/audit-ignore.txt: one ID per line, each with its reason.
set -euo pipefail
cd "$(dirname "$0")/.."

requirements="$(mktemp)"
trap 'rm -f "$requirements"' EXIT
uv export --frozen --all-extras --no-emit-project --format requirements-txt -o "$requirements" -q

ignores=()
while read -r id _; do
  [[ -z "$id" || "$id" == \#* ]] && continue
  ignores+=(--ignore-vuln "$id")
done < scripts/audit-ignore.txt

uvx pip-audit@2.10.0 --strict --require-hashes --disable-pip -r "$requirements" ${ignores[@]+"${ignores[@]}"}
