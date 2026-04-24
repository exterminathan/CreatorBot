# backups/

Reusable scripts for pulling GCP data (Firestore databases, GCS buckets) down
to this machine. Data directories are gitignored; scripts are tracked.

## Layout

```
backups/
├── scripts/
│   ├── backup-firestore.sh     # one Firestore DB -> ../firestore/<db>
│   └── backup-gcs-bucket.sh    # one GCS bucket   -> ../gcs/<bucket>
├── firestore/                  # (gitignored) per-database managed exports
└── gcs/                        # (gitignored) per-bucket rsynced mirrors
```

## Prerequisites

- `gcloud` CLI, authenticated (`gcloud auth login`) with access to the target
  project and buckets.

## Usage

From any directory:

```bash
# Firestore: exports the DB to a staging GCS bucket, then downloads locally.
backups/scripts/backup-firestore.sh <project-id> <database> [staging-bucket]

# GCS bucket: incremental rsync of the whole bucket.
backups/scripts/backup-gcs-bucket.sh <gs://bucket[/prefix]> [project-id]
```

### Examples used for this project

```bash
backups/scripts/backup-firestore.sh project-a89ff80d-7ecd-456f-aee "(default)"
backups/scripts/backup-firestore.sh project-a89ff80d-7ecd-456f-aee toolset-database
backups/scripts/backup-gcs-bucket.sh gs://lego-art-archive
```

## Restoring a Firestore export into a different project

```bash
# 1. Upload the export to a GCS bucket in the target project:
gcloud storage cp -r backups/firestore/<db>/* gs://<target-bucket>/<db>/ \
  --project=<target-project>

# 2. Import:
gcloud firestore import \
  gs://<target-bucket>/<db>/<db>.overall_export_metadata \
  --database="<target-db>" \
  --project=<target-project>
```

The target database must exist first (`gcloud firestore databases create ...`)
and must be empty (or compatible) for the import to succeed.
