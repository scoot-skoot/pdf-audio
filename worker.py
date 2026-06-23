# Job worker: polls Postgres for QUEUED jobs, runs the pipeline, reports progress.
# This is the ONLY component that knows both vocabularies — it translates the
# pipeline's internal stage names into the API job lifecycle states.
import os
import time
import pathlib

import psycopg

from app.pipeline import run_pipeline
from app.storage import publish

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/pdfaudio")
POLL_INTERVAL = float(os.environ.get("WORKER_POLL_INTERVAL", "3"))

# pipeline internal stage -> API lifecycle state. Stages not listed (e.g.
# document_split, matter_detection, mode_select, voice_assign) fold into the
# surrounding state and emit no transition.
STAGE_TO_STATUS = {
    "extract": "EXTRACTING",
    "scene_split": "EXTRACTING",
    "segment_extract": "EXTRACTING",
    "chunk": "CHUNKING",
    "tts": "GENERATING_AUDIO",
    "merge": "MERGING",
}


def _set(conn, job_id, status, **fields):
    """Write a status transition (and optional fields like result_location/error)."""
    assignments = ["status = %s", "updated_at = now()"] + [f"{k} = %s" for k in fields]
    params = [status] + list(fields.values()) + [job_id]
    conn.execute(f"UPDATE jobs SET {', '.join(assignments)} WHERE id = %s", params)
    conn.commit()


def claim_job(conn):
    row = conn.execute(
        """
        UPDATE jobs SET status = 'EXTRACTING', updated_at = now()
        WHERE id = (
            SELECT id FROM jobs WHERE status = 'QUEUED'
            ORDER BY created_at FOR UPDATE SKIP LOCKED LIMIT 1
        )
        RETURNING id, pdf_path, mode, trim_matter
        """
    ).fetchone()
    conn.commit()
    return row


def process(conn, job_id, pdf_path, mode, trim_matter):
    last = {"status": "EXTRACTING"}

    def on_event(stage):
        status = STAGE_TO_STATUS.get(stage)
        if status and status != last["status"]:
            last["status"] = status
            _set(conn, job_id, status)

    book_dir = os.path.join("output", "jobs", str(job_id))
    result = run_pipeline(pdf_path, mode, trim_matter, book_dir=book_dir, on_event=on_event)
    location = publish(result["output_path"], job_id)
    _set(conn, job_id, "COMPLETED", result_location=location)


def main():
    with psycopg.connect(DATABASE_URL) as conn:
        conn.execute(pathlib.Path("schema.sql").read_text())
        conn.commit()
        print(f"[worker] connected; polling every {POLL_INTERVAL}s")
        while True:
            job = claim_job(conn)
            if job is None:
                time.sleep(POLL_INTERVAL)
                continue
            job_id, pdf_path, mode, trim_matter = job
            print(f"[worker] claimed job {job_id}: {pdf_path}")
            try:
                process(conn, job_id, pdf_path, mode, trim_matter)
                print(f"[worker] job {job_id} COMPLETED")
            except Exception as e:  # noqa: BLE001 — any failure marks the job FAILED, worker stays up
                conn.rollback()
                _set(conn, job_id, "FAILED", error=str(e))
                print(f"[worker] job {job_id} FAILED: {e}")


if __name__ == "__main__":
    main()
