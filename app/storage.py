# Storage seam. Local now; S3 drop-in later (swap the body, keep the signature).
def publish(local_final_path, job_id):
    """Return a location string the API can serve. Local: the path as-is (API and
    worker share the output/ volume). S3 later: upload to
    s3://bucket/{job_id}/final.mp3 and return that key/URL."""
    return local_final_path  # ponytail: local FS now; swap body for boto3 upload at deploy
