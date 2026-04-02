import os
import shutil


def cleanup_job(job_dir: str) -> None:
    """Remove the entire job directory (video + frames)."""
    if os.path.exists(job_dir):
        shutil.rmtree(job_dir, ignore_errors=True)
