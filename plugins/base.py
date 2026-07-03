"""
Base plugin interface for the Review Scraper Tool.

Every platform plugin (Google Maps, Trustpilot, Yelp, ...) subclasses
ScraperPlugin and follows the same contract. This is what makes Section 5's
API contract possible -- the Flask API and background threading code never
need platform-specific logic, they just call .run() on whichever plugin
matches the requested platform_id.

Adding a new platform later (Phase 2) means:
  1. Create a new file in plugins/ (e.g. trustpilot_scraper.py)
  2. Subclass ScraperPlugin, fill in platform_id / platform_name / fields
  3. Implement run()
  4. Register it in plugins/registry.py
Nothing in app.py, the database, or the frontend needs to change.
"""

from abc import ABC, abstractmethod


class PluginField:
    """
    Describes one input field a plugin needs from the user, so the
    frontend's Job Details screen (Section 4, Screen 3) can render the
    right form per platform without hardcoding anything.

    Example (Google Maps):
        PluginField(id="business_name", label="Business Name", type="text", required=True)
        PluginField(id="location", label="Location", type="text", required=False)
        PluginField(id="max_reviews", label="Max Reviews", type="number", required=False)
    """

    def __init__(self, id, label, type="text", required=True, default=None):
        self.id = id
        self.label = label
        self.type = type          # "text" | "number" | "url" | "password"
        self.required = required
        self.default = default

    def to_dict(self):
        return {
            "id": self.id,
            "label": self.label,
            "type": self.type,
            "required": self.required,
            "default": self.default,
        }


class ScraperPlugin(ABC):
    """
    Every platform plugin must subclass this and define:
      - platform_id:   short machine name, e.g. "google_maps"
      - platform_name: human-readable name, e.g. "Google Maps"
      - fields:         list[PluginField] describing what the job needs

    And implement:
      - run(progress_callback=None) -> list[dict]
        Each dict must have exactly these keys (matches the export/DB schema):
          "Name/Username", "Rating", "Review", "Date", "Location"

    progress_callback, if given, must be called periodically as:
        progress_callback(branch_current, branch_total, reviews_so_far)
    so it can feed GET /api/jobs/<id>/status for the live progress screen.
    Platforms with no concept of "branches" (e.g. Trustpilot, single page)
    should just call it with branch_current=1, branch_total=1.
    """

    platform_id = None
    platform_name = None
    fields = []

    def __init__(self, job_params: dict, checkpoint_file=None):
        """
        job_params: raw dict of whatever the frontend submitted for this
        job's fields (matching `fields` above), e.g.
            {"business_name": "Ajwa Bakers", "location": "Lala Musa", "max_reviews": 5000}
        checkpoint_file: path used for crash-resume; None disables checkpointing.
        """
        self.job_params = job_params
        self.checkpoint_file = checkpoint_file

    @abstractmethod
    def run(self, progress_callback=None) -> list:
        """Run the scrape job and return a list of review dicts."""
        raise NotImplementedError

    @classmethod
    def describe(cls):
        """JSON-friendly description for GET /api/platforms."""
        return {
            "id": cls.platform_id,
            "name": cls.platform_name,
            "fields": [f.to_dict() for f in cls.fields],
        }