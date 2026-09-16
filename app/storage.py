# Storage seam. Local now; S3 drop-in later (swap the body, keep the signature).
import os


class Storage:
    """Publish finished audiobooks to durable storage and return a location the API can serve."""

    @staticmethod
    def publish(local_final_path, job_id):
        """Return a location string the API can serve. Local: an absolute path (API and
        worker may start in different working directories but share the output tree).
        S3 later: upload to s3://bucket/{job_id}/final.mp3 and return that key/URL."""
        _ = job_id
        return os.path.abspath(local_final_path)
