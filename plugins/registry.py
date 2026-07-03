"""
Plugin registry.

The Flask API imports PLATFORM_REGISTRY and get_platform_list() from here --
nothing else. It never imports GoogleMapsScraper, TrustpilotScraper, etc.
directly, so adding a new platform never requires touching app.py.

To add a new platform later (Phase 2):
    1. Write plugins/trustpilot_scraper.py with a class TrustpilotScraper(ScraperPlugin)
    2. Import it below and add it to PLATFORM_REGISTRY
That's it.
"""

from .google_maps_scraper import GoogleMapsScraper

# platform_id -> plugin class
PLATFORM_REGISTRY = {
    GoogleMapsScraper.platform_id: GoogleMapsScraper,
    # TrustpilotScraper.platform_id: TrustpilotScraper,   # Phase 2A
    # YelpScraper.platform_id: YelpScraper,                 # Phase 2B
    # TripAdvisorScraper.platform_id: TripAdvisorScraper,   # Phase 2B
}


def get_platform_list():
    """JSON-friendly list for GET /api/platforms: [{id, name, fields}, ...]"""
    return [cls.describe() for cls in PLATFORM_REGISTRY.values()]


def get_plugin_class(platform_id):
    """Returns the plugin class for a given platform_id, or None if unknown."""
    return PLATFORM_REGISTRY.get(platform_id)