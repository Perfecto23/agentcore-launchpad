#!/usr/bin/env bash
set -euo pipefail
if [[ $# -lt 2 || $# -gt 3 ]]; then
  echo "Usage: $0 COMMIT ABSOLUTE_OUTPUT_DIR [IMAGE_PREFIX]" >&2
  exit 64
fi
repo=$(git -C "$(dirname "$0")" rev-parse --show-toplevel)
revision=$(git -C "$repo" rev-parse --verify "$1^{commit}")
output=$2
prefix=${3:-launchpad-trial}
export_images=${EXPORT_IMAGES:-0}
[[ "$export_images" = 0 || "$export_images" = 1 ]] || {
  echo 'EXPORT_IMAGES must be 0 or 1' >&2; exit 64;
}
[[ "$output" = /* ]] || { echo 'OUTPUT_DIR must be absolute' >&2; exit 64; }
mkdir -p "$output"
context=$(mktemp -d "${TMPDIR:-/tmp}/launchpad-build.XXXXXXXX")
trap 'rm -rf "$context"' EXIT
git -C "$repo" archive --format=tar "$revision" > "$output/source.tar"
archive_sha=$(shasum -a 256 "$output/source.tar" | awk '{print $1}')
tar -xf "$output/source.tar" -C "$context"
printf '%s\n' "$revision" > "$output/revision.txt"
printf '%s\n' "$archive_sha" > "$output/source.sha256"
for component in backend frontend; do
  image="${prefix}-${component}:${revision:0:12}"
  docker buildx build --platform linux/amd64 --load \
    --build-arg "REVISION=$revision" --build-arg "SOURCE_ARCHIVE_SHA256=$archive_sha" \
    --metadata-file "$output/$component.build.json" \
    --file "$context/deploy/containers/$component.Dockerfile" \
    --tag "$image" "$context" 2>&1 | tee "$output/$component.build.log"
  docker image inspect "$image" > "$output/$component.image.json"
  printf '%s\n' "$image" > "$output/$component.image.txt"
  if [[ "$export_images" = 1 ]]; then
    docker image save "$image" | gzip > "$output/$component.image.tar.gz"
    (cd "$output" && shasum -a 256 "$component.image.tar.gz" > "$component.image.tar.gz.sha256")
  fi
done
echo "Built committed source $revision; evidence: $output"
