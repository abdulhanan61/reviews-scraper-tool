"""
Background job runner.

POST /api/jobs must return immediately with a job_id -- it can't sit there
waiting for a scrape that might take several minutes. start_job_thread()
kicks the actual scraping off in a separate thread, so Flask stays free to
answer other requests (like GET /api/jobs/<id>/status) while it runs.

The thread updates the database directly as it progresses, which is how
the status endpoint can report live progress without ever touching the
scraper or thread directly -- it just reads the current job row.
"""

import os
import threading

from plugins.registry import get_plugin_class
from database.db import update_job_status, update_job_progress, save_reviews

CHECKPOINT_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "checkpoints"
)


def start_job_thread(job_id, platform, job_params):
    """Fire-and-forget: starts the scrape in a background thread and returns immediately."""
    thread = threading.Thread(
        target=_run_job, args=(job_id, platform, job_params), daemon=True
    )
    thread.start()


def _run_job(job_id, platform, job_params):
    plugin_class = get_plugin_class(platform)
    if plugin_class is None:
        update_job_status(job_id, "failed", error_message=f"Unknown platform '{platform}'")
        return

    os.makedirs(CHECKPOINT_DIR, exist_ok=True)
    checkpoint_file = os.path.join(CHECKPOINT_DIR, f"{job_id}.json")

    update_job_status(job_id, "running")

    def progress_cb(branch_current, branch_total, reviews_so_far):
        update_job_progress(job_id, branch_current, branch_total, reviews_so_far)

    try:
        scraper = plugin_class(job_params=job_params, checkpoint_file=checkpoint_file)
        reviews = scraper.run(progress_callback=progress_cb)
        save_reviews(job_id, reviews)
        update_job_status(job_id, "done")
    except Exception as e:
        update_job_status(job_id, "failed", error_message=str(e))