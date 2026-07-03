"""
Tests the plugin system the way the Flask API will actually use it:
looks up the plugin by platform_id through the registry, builds it from a
job_params dict (like what POST /api/jobs would receive as JSON), and runs
it with a progress_callback (like what feeds GET /api/jobs/<id>/status).

Run from the project root:
    python tests/test_plugin.py "Ajwa Bakers & Restaurants" --location "Lala Musa" --max-reviews 20 --show-browser
"""

import sys
import os
import argparse
import pandas as pd

# allow running this file directly without installing the project as a package
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from plugins.registry import get_platform_list, get_plugin_class


def main():
    parser = argparse.ArgumentParser(description="Test a plugin through the registry")
    parser.add_argument("business_name")
    parser.add_argument("--platform", default="google_maps")
    parser.add_argument("--location", default=None)
    parser.add_argument("--max-reviews", type=int, default=20)
    parser.add_argument("--output", default="test_output.xlsx")
    parser.add_argument("--show-browser", action="store_true")
    args = parser.parse_args()

    # 1) confirm GET /api/platforms would work
    print("=== Registered platforms ===")
    for p in get_platform_list():
        print(f"  {p['id']}: {p['name']}  fields={[f['id'] for f in p['fields']]}")
    print()

    # 2) confirm the API could find the right plugin for this job
    plugin_class = get_plugin_class(args.platform)
    if plugin_class is None:
        print(f"ERROR: no plugin registered for platform_id='{args.platform}'")
        sys.exit(1)
    print(f"Resolved platform '{args.platform}' -> {plugin_class.__name__}")

    # 3) build job_params the same shape the frontend would POST
    job_params = {
        "business_name": args.business_name,
        "location": args.location,
        "max_reviews": args.max_reviews,
    }
    print(f"job_params: {job_params}\n")

    # 4) run it with a progress_callback, same shape /status will use
    def progress_callback(branch_current, branch_total, reviews_so_far):
        print(f"  [status] branch {branch_current}/{branch_total} "
              f"— {reviews_so_far} reviews so far")

    scraper = plugin_class(
        job_params=job_params,
        headless=not args.show_browser,
        checkpoint_file="test_checkpoint.json",
    )
    reviews = scraper.run(progress_callback=progress_callback)

    # 5) sanity-check the output shape
    print(f"\n=== Result ===")
    print(f"Total reviews: {len(reviews)}")
    if reviews:
        expected_keys = {"Name/Username", "Rating", "Review", "Date", "Location"}
        actual_keys = set(reviews[0].keys())
        if actual_keys != expected_keys:
            print(f"WARNING: unexpected keys. Expected {expected_keys}, got {actual_keys}")
        else:
            print("Review shape OK — matches expected schema.")
        print("\nFirst review:")
        for k, v in reviews[0].items():
            print(f"  {k}: {v!r}")

    df = pd.DataFrame(reviews)
    df.to_excel(args.output, index=False)
    print(f"\nSaved to {args.output}")


if __name__ == "__main__":
    main()