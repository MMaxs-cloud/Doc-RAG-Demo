"""Enqueue all active documents for reindexing under the running index configuration."""
import json

from app.config import config
from app.core.errors import AppError
from app.documents import Documents
from app.jobs import Jobs
from app.storage.database import Database
from app.storage.files import Files


def main():
    settings = config()
    db = Database(settings)
    db.initialize()
    documents = Documents(db, Files(settings), Jobs(db))
    queued, existing = [], []
    for row in db.all("SELECT id FROM documents WHERE deleted_at IS NULL ORDER BY created_at"):
        try:
            queued.append(documents.reindex(row["id"]))
        except AppError as exc:
            if exc.code != "job_exists":
                raise
            existing.append(row["id"])
    print(json.dumps({"index_version": settings.index_version, "queued": queued, "existing": existing}))


if __name__ == "__main__":
    main()
