"""
Export functions.

Converts a job's reviews (already saved in the database) into a downloadable
file. Called by GET /api/jobs/<id>/download. Files are written into
exports/, named after the business and job id so repeated downloads of the
same job don't collide with other jobs' files.
"""

import os
import re
import json
import pandas as pd

EXPORTS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "exports"
)

SUPPORTED_FORMATS = {"xlsx", "csv", "json"}


def _safe_filename_part(text):
    """Strips characters that aren't safe in filenames (e.g. business names with & or /)."""
    text = text or "job"
    return re.sub(r'[^\w\-]+', '_', text).strip('_')[:60]


def export_reviews(reviews, business_name, job_id, fmt="xlsx"):
    """
    Writes `reviews` (list of dicts with Name/Username, Rating, Review, Date,
    Location) to exports/ in the requested format. Returns the full file path.
    """
    fmt = fmt.lower()
    if fmt not in SUPPORTED_FORMATS:
        raise ValueError(f"Unsupported format '{fmt}'. Must be one of {SUPPORTED_FORMATS}")

    os.makedirs(EXPORTS_DIR, exist_ok=True)

    base_name = f"{_safe_filename_part(business_name)}_{job_id[:8]}"
    file_path = os.path.join(EXPORTS_DIR, f"{base_name}.{fmt}")

    if fmt == "xlsx":
        df = pd.DataFrame(reviews)
        df.to_excel(file_path, index=False)
    elif fmt == "csv":
        df = pd.DataFrame(reviews)
        df.to_csv(file_path, index=False)
    elif fmt == "json":
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(reviews, f, ensure_ascii=False, indent=2)

    return file_path