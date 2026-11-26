#!/bin/bash
# Copy the runner's working data to S3, the durable copy. Append-only: no --delete, and the
# runner role has no s3:DeleteObject. Raw objects are content-addressed and never change.
# Usage: deploy/aws/s3-sync.sh raw logs [exports]
set -euo pipefail
: "${BUCKET:?Set the repository variable REITSTREET_S3_BUCKET}"
root=/srv/reitstreet/data
for part in "$@"; do
  if [ -d "$root/$part" ]; then
    aws s3 sync "$root/$part" "s3://$BUCKET/$part/" --only-show-errors --no-progress
    echo "synced $part to s3://$BUCKET/$part/"
  fi
done
