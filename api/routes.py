"""
API routes for the Review Scraper Tool.

register_routes(app) attaches every endpoint to the Flask app created in
app.py. As Section 5's API contract gets built out, the other 6 endpoints
(GET /api/platforms, POST /api/jobs, GET /api/jobs, GET /api/jobs/<id>,
GET /api/jobs/<id>/status, GET /api/jobs/<id>/download, DELETE /api/jobs/<id>)
get added as more functions below, each registered the same way.
"""

from flask import jsonify, request, send_file

from plugins.registry import get_platform_list, get_plugin_class
from database.db import (
    create_job, get_job, list_jobs, get_reviews_for_job, delete_job as db_delete_job
)
from services.job_runner import start_job_thread
from services.export import export_reviews, SUPPORTED_FORMATS


def register_routes(app):

    @app.route("/", methods=["GET"])
    def status_check():
        """Simple health check -- confirms the server is up and responding."""
        return jsonify({"status": "running"})

    @app.route("/api/platforms", methods=["GET"])
    def list_platforms():
        """
        Returns every registered scraper plugin, in the shape the frontend's
        Platform Selection screen (Section 4, Screen 2) needs:
            [{id, name, fields}, ...]
        Adding a new plugin to plugins/registry.py automatically shows up
        here -- nothing in this route ever needs to change.
        """
        return jsonify(get_platform_list())

    @app.route("/api/jobs", methods=["POST"])
    def start_job():
        """
        Starts a new scrape job. Expects JSON body like:
            {"platform": "google_maps", "business_name": "...", "location": "...", "max_reviews": 5000}
        Returns immediately with {job_id, status} -- the actual scraping
        runs in a background thread (services/job_runner.py) so this
        request doesn't block while the scrape is in progress.
        """
        body = request.get_json(silent=True) or {}
        platform = body.get("platform")

        if not platform:
            return jsonify({"error": "Missing required field: platform"}), 400

        plugin_class = get_plugin_class(platform)
        if plugin_class is None:
            return jsonify({"error": f"Unknown platform '{platform}'"}), 400

        # everything except "platform" is treated as this plugin's job_params
        job_params = {k: v for k, v in body.items() if k != "platform"}

        missing = [
            f.id for f in plugin_class.fields
            if f.required and not job_params.get(f.id)
        ]
        if missing:
            return jsonify({"error": f"Missing required field(s): {', '.join(missing)}"}), 400

        job_id = create_job(platform, job_params)
        start_job_thread(job_id, platform, job_params)

        return jsonify({"job_id": job_id, "status": "queued"}), 202

    @app.route("/api/jobs", methods=["GET"])
    def get_jobs():
        """Lists all past + current jobs, most recent first."""
        jobs = list_jobs()
        return jsonify([
            {
                "id": j["id"],
                "business": j["job_params"].get("business_name"),
                "platform": j["platform"],
                "status": j["status"],
                "total": j["total_reviews"],
                "date": j["created_at"],
            }
            for j in jobs
        ])

    @app.route("/api/jobs/<job_id>", methods=["GET"])
    def get_job_details(job_id):
        """Full details for one job, including its scraped reviews count."""
        job = get_job(job_id)
        if job is None:
            return jsonify({"error": "Job not found"}), 404

        reviews_count = len(get_reviews_for_job(job_id))
        return jsonify({
            "job": job,
            "reviews_count": reviews_count,
        })

    @app.route("/api/jobs/<job_id>/status", methods=["GET"])
    def get_job_status(job_id):
        """Lightweight polling endpoint for the live-progress screen (polled every 2s)."""
        job = get_job(job_id)
        if job is None:
            return jsonify({"error": "Job not found"}), 404

        return jsonify({
            "status": job["status"],
            "branch_current": job["branch_current"],
            "branch_total": job["branch_total"],
            "reviews_so_far": job["reviews_so_far"],
        })

    @app.route("/api/jobs/<job_id>/download", methods=["GET"])
    def download_job(job_id):
        """
        Downloads a job's reviews as a file. Format via ?format=xlsx|csv|json
        (defaults to xlsx). Example: /api/jobs/<id>/download?format=csv
        """
        job = get_job(job_id)
        if job is None:
            return jsonify({"error": "Job not found"}), 404

        if job["status"] != "done":
            return jsonify({"error": f"Job is not finished yet (status: {job['status']})"}), 400

        fmt = request.args.get("format", "xlsx").lower()
        if fmt not in SUPPORTED_FORMATS:
            return jsonify({"error": f"Unsupported format '{fmt}'. Must be one of {sorted(SUPPORTED_FORMATS)}"}), 400

        reviews = get_reviews_for_job(job_id)
        business_name = job["job_params"].get("business_name", "job")
        file_path = export_reviews(reviews, business_name, job_id, fmt=fmt)

        return send_file(file_path, as_attachment=True)

    @app.route("/api/jobs/<job_id>", methods=["DELETE"])
    def delete_job_route(job_id):
        """Deletes a job and all its reviews (cascade)."""
        deleted = db_delete_job(job_id)
        if not deleted:
            return jsonify({"error": "Job not found"}), 404
        return jsonify({"deleted": True})