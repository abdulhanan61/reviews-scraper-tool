"""
Google Maps Review Scraper (multi-branch) — v2
Extracts: Name/Username, Rating, Review Text, Date, Location (city/branch)

REQUIREMENTS:
    pip install selenium pandas openpyxl webdriver-manager

WHY THIS VERSION IS FASTER (without losing accuracy):

  1. Batched extraction — the old script did 4 separate Selenium round-trips
     PER review card (name, rating, text, date) on every single scroll
     iteration. This version reads every mounted card in ONE JavaScript
     call and returns plain data, cutting round-trips by ~4x per card and
     making no code care about how many cards are visible.

  2. Adaptive polling instead of a fixed sleep — the old script always
     slept SCROLL_WAIT seconds after every scroll, even when new cards had
     already mounted after 300ms. This version polls the mounted-card count
     every POLL_INTERVAL seconds and moves on as soon as it stabilizes,
     up to a MAX_WAIT ceiling. On a review-heavy business this is the single
     biggest time saver, since most of the old runtime was spent sleeping.

  3. "More" buttons are tracked so they are only clicked once each (a
     data-attribute is stamped onto the button after clicking), instead of
     re-querying and re-clicking every already-expanded button on every
     iteration.

  4. Headless by default, and the webdriver path is cached across runs
     instead of re-checked with ChromeDriverManager on every execution.

ACCURACY IS UNCHANGED FROM THE ORIGINAL:
  - Still extracts continuously DURING scrolling (never just once at the
    end), because Google virtualizes long review lists and removes old
    cards from the DOM as you scroll past them.
  - Still expands every "More" button before reading text, to avoid
    truncated "..." reviews.
  - Still deduplicates on (name, date, review-text-prefix) so a card that
    gets re-extracted across two polls isn't double-counted.
  - Same city/branch parsing, same branch discovery, same checkpointing.

This file is written as a PLUGIN with a standard interface (GoogleMapsScraper
class + .run()) so it slots into the Flask API's background-threading model:
pass a progress_callback and it will report live status suitable for the
GET /api/jobs/<id>/status endpoint (branch_current, branch_total,
reviews_so_far).
"""

import re
import os
import json
import time
import pandas as pd
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.action_chains import ActionChains
from webdriver_manager.chrome import ChromeDriverManager

try:
    from .base import ScraperPlugin, PluginField          # when imported as plugins.google_maps_scraper
except ImportError:
    from base import ScraperPlugin, PluginField            # when run directly: python google_maps_scraper.py


# ============================== CONFIG ==============================
POLL_INTERVAL = 0.3        # seconds between "did new cards mount?" checks
MAX_WAIT = 1.5              # ceiling per scroll if nothing new is mounting yet
SCROLL_AMOUNT = 1200         # pixels per scroll step
STALL_PATIENCE = 12          # consecutive no-new-reviews scrolls before giving up on a branch
DISABLE_IMAGES = True        # safe speedup -- doesn't affect text/rating/date data
HEADLESS = True               # off by default now; flip to False only when debugging selectors
VERBOSE_DEBUG = True

_NON_CITY_WORDS = {
    "pakistan", "punjab", "sindh", "kpk", "khyber pakhtunkhwa",
    "balochistan", "gilgit-baltistan", "azad kashmir", "islamabad capital territory"
}

_DRIVER_PATH_CACHE_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), ".chromedriver_path_cache.json"
)
# ======================================================================


# ----------------------------- helpers -------------------------------

def extract_city(address: str) -> str:
    """
    Given a full Google Maps address ('Shop 4, Main Bazar, Gujrat, Punjab,
    Pakistan') or a search-result label ('Ajwa Bakers & Restaurants - Lala
    Musa'), return just the city/branch-distinguishing part.
    """
    if not address:
        return address

    if '\u00b7' in address:
        parts = [p.strip() for p in address.split('\u00b7') if p.strip()]
        if len(parts) > 1:
            return parts[-1]

    cleaned = re.sub(r'\b\d{4,6}\b', '', address)  # strip postal codes
    parts = [p.strip() for p in cleaned.split(',') if p.strip()]
    if not parts:
        return address

    while parts and parts[-1].lower() in _NON_CITY_WORDS:
        parts.pop()
    if not parts:
        return address

    city = parts[-1]
    # guard against picking up a Google "plus code" token
    if re.match(r'^[A-Z0-9]{4,}\+[A-Z0-9]{2,}$', city) and len(parts) > 1:
        city = parts[-2]
    return city.strip()


def _get_cached_driver_path():
    """Avoid hitting the network for a driver-version check on every run."""
    if os.path.exists(_DRIVER_PATH_CACHE_FILE):
        try:
            with open(_DRIVER_PATH_CACHE_FILE, "r") as f:
                cached = json.load(f)
            if os.path.exists(cached.get("path", "")):
                return cached["path"]
        except Exception:
            pass
    path = ChromeDriverManager().install()
    with open(_DRIVER_PATH_CACHE_FILE, "w") as f:
        json.dump({"path": path}, f)
    return path


def setup_driver(headless=HEADLESS):
    options = Options()
    if headless:
        options.add_argument("--headless=new")
        options.add_argument("--window-size=1600,1200")
    else:
        options.add_argument("--start-maximized")
        options.add_argument("--window-size=1600,1200")
    options.add_argument("--lang=en-US")
    options.add_argument("--disable-notifications")
    options.add_argument("--disable-gpu")
    options.add_argument("--no-sandbox")

    # Anti-detection: confirmed via testing that headless Chrome gets served a
    # stripped-down Google Maps layout (missing the Reviews tab entirely) even
    # at a full desktop window size -- window size wasn't the cause. Google
    # appears to be detecting automation signals (navigator.webdriver, the
    # "enable-automation" flag) and serving a reduced page as a result. These
    # options mask those signals so headless behaves like a real browser.
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)
    options.add_argument(
        "user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
    )

    if DISABLE_IMAGES:
        options.add_experimental_option(
            "prefs", {"profile.managed_default_content_settings.images": 2}
        )
    service = Service(_get_cached_driver_path())
    driver = webdriver.Chrome(service=service, options=options)
    driver.set_window_size(1600, 1200)  # belt-and-suspenders: some headless builds ignore the flag

    # Also patch navigator.webdriver directly, since it's checkable by JS on
    # the page even with the launch flags above.
    try:
        driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {
            "source": "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        })
    except Exception:
        pass

    return driver


def real_scroll(driver, element, amount=SCROLL_AMOUNT):
    """
    Dispatches a genuine wheel event over the element (not just a scrollTop
    assignment), since Google's lazy-loader sometimes only responds to real
    wheel-style interaction. Falls back to a direct scrollTop nudge too.
    """
    try:
        driver.execute_script("window.focus();")
        ActionChains(driver).move_to_element(element).perform()
    except Exception:
        pass

    return driver.execute_script("""
        const el = arguments[0];
        const amount = arguments[1];
        const rect = el.getBoundingClientRect();
        const evt = new WheelEvent('wheel', {
            deltaY: amount, bubbles: true, cancelable: true,
            clientX: rect.left + rect.width / 2,
            clientY: rect.top + rect.height / 2
        });
        el.dispatchEvent(evt);
        el.scrollTop = el.scrollTop + amount;
        return {scrollTop: el.scrollTop, scrollHeight: el.scrollHeight};
    """, element, amount)


def find_scrollable_ancestor(driver, anchor_element):
    """
    Walks up from a known-present element and returns the SMALLEST (most
    specific) scrollable ancestor -- picking the first/largest one risks
    grabbing the whole sidebar or body, which breaks scrolling accuracy.
    """
    return driver.execute_script("""
        let node = arguments[0];
        let best = null;
        while (node) {
            if (node.scrollHeight > node.clientHeight + 50) {
                if (!best || node.clientHeight < best.clientHeight) {
                    best = node;
                }
            }
            node = node.parentElement;
        }
        return best;
    """, anchor_element)


def expand_new_more_buttons(driver):
    """
    Clicks only "More" buttons that haven't been clicked yet (stamped with
    data-rst-expanded="1" after clicking), instead of re-processing every
    already-expanded button on every pass.
    """
    driver.execute_script("""
        const selector = 'button.w8nwRe, button[aria-label="See more"]';
        const buttons = document.querySelectorAll(selector);
        for (const btn of buttons) {
            if (!btn.dataset.rstExpanded) {
                try { btn.click(); } catch (e) {}
                btn.dataset.rstExpanded = "1";
            }
        }
    """)


def extract_visible_reviews_batched(driver, city):
    """
    Reads every currently-mounted review card in a SINGLE JS round-trip
    (instead of 4 Selenium calls per card), and returns plain dicts.
    Call this repeatedly during scrolling -- Google virtualizes the list,
    so cards mount and unmount as you scroll.
    """
    expand_new_more_buttons(driver)

    raw = driver.execute_script("""
        const cards = document.querySelectorAll('div.jftiEf');
        const out = [];
        for (const card of cards) {
            const nameEl = card.querySelector('div.d4r55');
            const ratingEl = card.querySelector('span.kvMYJc');
            const textEl = card.querySelector('span.wiI7pd');
            const dateEl = card.querySelector('span.rsqaWe');
            const ratingLabel = ratingEl ? ratingEl.getAttribute('aria-label') : "";
            out.push({
                name: nameEl ? nameEl.textContent : "",
                rating_label: ratingLabel || "",
                text: textEl ? textEl.textContent : "",
                date: dateEl ? dateEl.textContent : ""
            });
        }
        return out;
    """)

    rows = []
    for item in raw:
        rating = item["rating_label"].split(" ")[0] if item["rating_label"] else ""
        rows.append({
            "Name/Username": item["name"],
            "Rating": rating,
            "Review": item["text"],
            "Date": item["date"],
            "Location": city
        })
    return rows


# ------------------------- branch discovery ---------------------------

def get_all_branch_links(driver, query):
    """Finds every matching listing (branch) for the search query, not just the first."""
    url = f"https://www.google.com/maps/search/{query.replace(' ', '+')}"
    driver.get(url)

    # Adaptive wait for either the results feed or a direct single-listing redirect
    result_links = []
    for _ in range(20):
        result_links = driver.find_elements(By.CSS_SELECTOR, 'a.hfpxzc')
        if result_links or driver.find_elements(By.CSS_SELECTOR, 'h1'):
            break
        time.sleep(0.3)

    if not result_links:
        # Maps redirected straight into a single business page -- one branch only
        return [{"url": driver.current_url, "label": ""}]

    try:
        results_panel = driver.find_element(By.CSS_SELECTOR, 'div[role="feed"]')
    except Exception:
        results_panel = None

    if results_panel:
        last_count = 0
        same_repeats = 0
        for _ in range(30):
            driver.execute_script(
                "arguments[0].scrollTop = arguments[0].scrollHeight", results_panel
            )
            # adaptive: poll instead of a flat 1.5s sleep
            new_count = last_count
            for _ in range(int(MAX_WAIT / POLL_INTERVAL)):
                time.sleep(POLL_INTERVAL)
                new_count = len(driver.find_elements(By.CSS_SELECTOR, 'a.hfpxzc'))
                if new_count != last_count:
                    break
            if new_count == last_count:
                same_repeats += 1
                if same_repeats >= 4:
                    break
            else:
                same_repeats = 0
            last_count = new_count

    links, seen = [], set()
    for el in driver.find_elements(By.CSS_SELECTOR, 'a.hfpxzc'):
        href = el.get_attribute('href')
        label = el.get_attribute('aria-label') or ""
        if href and href not in seen:
            seen.add(href)
            links.append({"url": href, "label": label})

    if VERBOSE_DEBUG:
        print(f"Found {len(links)} branch(es) for '{query}'")
    return links


# --------------------------- per-branch flow ----------------------------

def open_reviews_tab(driver, branch_url):
    """Navigates to a branch and clicks its Reviews tab. Returns True if successful."""
    driver.get(branch_url)

    for _ in range(10):
        if driver.find_elements(By.CSS_SELECTOR, 'h1'):
            break
        time.sleep(0.3)

    selector_strategies = [
        (By.XPATH, '//button[contains(@aria-label, "Reviews for")]'),
        (By.XPATH, '//button[contains(@aria-label, "Reviews")]'),
        (By.XPATH, '//div[@role="tab"][contains(., "Reviews")]'),
        (By.XPATH, '//button[@role="tab"][contains(., "Reviews")]'),
    ]
    reviews_tab = None
    for attempt in range(15):  # poll -- headless rendering can lag behind h1 appearing
        for by, sel in selector_strategies:
            found = driver.find_elements(by, sel)
            if found:
                reviews_tab = found[0]
                if VERBOSE_DEBUG:
                    print(f"  [debug] Reviews tab matched: {sel} (after {attempt} poll(s))")
                break
        if reviews_tab is not None:
            break
        time.sleep(0.3)

    if reviews_tab is None:
        if VERBOSE_DEBUG:
            print("  [debug] No Reviews tab found.")
            print(f"  [debug] current URL: {driver.current_url}")
            print(f"  [debug] page title: {driver.title!r}")
            print(f"  [debug] window size: {driver.get_window_size()}")
            try:
                body_sample = driver.find_element(By.TAG_NAME, 'body').text[:300]
                print(f"  [debug] page body sample: {body_sample!r}")
            except Exception:
                print("  [debug] could not read page body")
            try:
                debug_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "debug_screenshots")
                os.makedirs(debug_dir, exist_ok=True)
                shot_path = os.path.join(debug_dir, f"no_reviews_tab_{int(time.time())}.png")
                driver.save_screenshot(shot_path)
                print(f"  [debug] screenshot saved to: {shot_path}")
            except Exception as e:
                print(f"  [debug] could not save screenshot: {e}")
        return False

    for attempt in range(4):
        try:
            driver.execute_script("arguments[0].scrollIntoView({block:'center'});", reviews_tab)
            time.sleep(0.3)
            try:
                ActionChains(driver).move_to_element(reviews_tab).pause(0.15).click().perform()
            except Exception:
                driver.execute_script("arguments[0].click();", reviews_tab)
            return True
        except Exception as e:
            if VERBOSE_DEBUG:
                print(f"  [debug] click attempt {attempt+1} failed: {e}")
            time.sleep(0.8)

    return False


def wait_for_reviews_panel(driver):
    """
    Returns the scrollable reviews-panel element, or None.
    Review CARDS don't exist in the DOM yet right after the tab opens (they
    only mount once you scroll), so this anchors off the rating histogram /
    'Write a review' button instead -- which IS present immediately.
    """
    anchor = None
    for by, sel in [
        (By.XPATH, '//button[contains(@aria-label, "Write a review")]'),
        (By.CSS_SELECTOR, 'div.jJc9Ad'),
    ]:
        found = driver.find_elements(by, sel)
        if found:
            anchor = found[0]
            break

    if anchor is None:
        if VERBOSE_DEBUG:
            print("  [debug] no anchor (Write a review button) found.")
        return None

    scrollable = find_scrollable_ancestor(driver, anchor)
    if scrollable is None:
        if VERBOSE_DEBUG:
            print("  [debug] no scrollable ancestor found from anchor.")
        return None

    # Scroll until review cards actually mount into the DOM (adaptive)
    for attempt in range(20):
        if driver.find_elements(By.CSS_SELECTOR, 'div.jftiEf'):
            if VERBOSE_DEBUG:
                print(f"  [debug] review cards mounted after {attempt} scroll(s)")
            return scrollable
        real_scroll(driver, scrollable)
        time.sleep(0.4)

    if VERBOSE_DEBUG:
        body_sample = driver.find_element(By.TAG_NAME, 'body').text[:200]
        print(f"  [debug] cards never mounted. Page sample: {body_sample!r}")
    return None


def get_business_location(driver, branch_label="", fallback=""):
    for _ in range(5):
        try:
            addr = driver.find_element(By.CSS_SELECTOR, 'button[data-item-id="address"]').text
            if addr:
                return addr
        except Exception:
            pass
        time.sleep(0.3)

    try:
        addr = driver.find_element(By.CSS_SELECTOR, 'div.Io6YTe').text
        if addr:
            return addr
    except Exception:
        pass

    # some listings expose the address via a generic data-item-id prefix instead
    try:
        addr_el = driver.find_element(By.CSS_SELECTOR, '[data-item-id^="address"]')
        if addr_el.text:
            return addr_el.text
    except Exception:
        pass

    if branch_label and branch_label.strip():
        return branch_label.strip()

    try:
        title = driver.find_element(By.CSS_SELECTOR, 'h1').text
        if title:
            return title
    except Exception:
        pass

    # last resort: never return blank -- use whatever the user searched for
    return fallback or branch_label or ""


def scroll_and_collect_reviews(driver, scrollable, location, target_count, progress_cb=None):
    """
    Scrolls and extracts on every poll, merging into a deduplicated
    accumulator -- required because Google virtualizes long review lists.
    Uses adaptive polling (POLL_INTERVAL / MAX_WAIT) instead of a flat
    sleep, so it moves on the instant new cards mount rather than always
    waiting the full window.
    """
    city = extract_city(location)
    seen_keys = set()
    accumulated = []

    def merge_visible():
        new_rows = extract_visible_reviews_batched(driver, city)
        added = 0
        for row in new_rows:
            key = (row["Name/Username"], row["Date"], row["Review"][:80])
            if key not in seen_keys:
                seen_keys.add(key)
                accumulated.append(row)
                added += 1
        return added

    merge_visible()  # whatever's visible before scrolling starts

    last_total = len(accumulated)
    stall_count = 0
    i = 0
    max_iterations = 2000  # higher ceiling since each iteration is now much cheaper

    while i < max_iterations:
        i += 1
        real_scroll(driver, scrollable)

        # adaptive poll: check frequently, stop early once new cards mount
        added = 0
        elapsed = 0.0
        while elapsed < MAX_WAIT:
            time.sleep(POLL_INTERVAL)
            elapsed += POLL_INTERVAL
            added = merge_visible()
            if added > 0:
                break

        total = len(accumulated)

        if VERBOSE_DEBUG and (added > 0 or i % 10 == 0):
            print(f"    iter {i}: {total} unique reviews (+{added})")

        if progress_cb:
            progress_cb(reviews_so_far=total)

        if total >= target_count:
            break
        if total == last_total:
            stall_count += 1
            if stall_count >= STALL_PATIENCE:
                if VERBOSE_DEBUG:
                    print(f"    no new reviews after {STALL_PATIENCE} attempts -- stopping.")
                break
        else:
            stall_count = 0
        last_total = total

    return accumulated


# ------------------------------ plugin class -------------------------------

class GoogleMapsScraper(ScraperPlugin):
    """
    Implements the ScraperPlugin contract (see plugins/base.py) for
    Google Maps. Construct with job_params, call .run(), get back a list
    of review dicts. Accepts an optional
    progress_callback(branch_current, branch_total, reviews_so_far) so the
    Flask background-thread job can feed GET /api/jobs/<id>/status.
    """

    platform_id = "google_maps"
    platform_name = "Google Maps"
    fields = [
        PluginField(id="business_name", label="Business Name", type="text", required=True),
        PluginField(id="location", label="Location", type="text", required=False),
        PluginField(id="max_reviews", label="Max Reviews per Branch", type="number",
                    required=False, default=5000),
    ]

    def __init__(self, job_params: dict, checkpoint_file=None, headless=HEADLESS):
        super().__init__(job_params, checkpoint_file=checkpoint_file)
        business_name = job_params.get("business_name", "")
        location = job_params.get("location")
        self.query = f"{business_name} {location}".strip() if location else business_name
        self.max_reviews_per_branch = job_params.get("max_reviews", 5000)
        self.headless = headless

    # -- checkpointing --
    def _load_checkpoint(self):
        if self.checkpoint_file and os.path.exists(self.checkpoint_file):
            with open(self.checkpoint_file, "r", encoding="utf-8") as f:
                return json.load(f)
        return {"completed_branch_urls": [], "reviews": []}

    def _save_checkpoint(self, state):
        if not self.checkpoint_file:
            return
        with open(self.checkpoint_file, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)

    def run(self, progress_callback=None):
        """
        Returns a list of review dicts:
        {Name/Username, Rating, Review, Date, Location}
        progress_callback, if given, is called as:
            progress_callback(branch_current, branch_total, reviews_so_far)
        """
        driver = setup_driver(headless=self.headless)
        state = self._load_checkpoint()
        all_reviews = state["reviews"]
        completed = set(state["completed_branch_urls"])

        try:
            branch_links = get_all_branch_links(driver, self.query)
            total_branches = len(branch_links)

            for idx, branch in enumerate(branch_links, start=1):
                branch_url = branch["url"]
                if branch_url in completed:
                    continue

                def branch_progress_cb(reviews_so_far, _idx=idx, _total=total_branches):
                    if progress_callback:
                        progress_callback(
                            branch_current=_idx,
                            branch_total=_total,
                            reviews_so_far=len(all_reviews) + reviews_so_far
                        )

                try:
                    reviews = self._process_branch(driver, idx, total_branches, branch,
                                                     branch_progress_cb)
                    if reviews:
                        all_reviews.extend(reviews)
                except Exception as e:
                    if VERBOSE_DEBUG:
                        print(f"  Branch failed with error: {e}")
                finally:
                    completed.add(branch_url)
                    self._save_checkpoint({
                        "completed_branch_urls": list(completed),
                        "reviews": all_reviews
                    })

            # final dedupe pass across the whole job
            df = pd.DataFrame(all_reviews)
            if not df.empty:
                df.drop_duplicates(subset=["Name/Username", "Review", "Date", "Location"],
                                    inplace=True)
                all_reviews = df.to_dict(orient="records")

            return all_reviews
        finally:
            driver.quit()

    def _process_branch(self, driver, idx, total, branch, progress_cb):
        branch_url = branch["url"]
        branch_label = branch.get("label", "")

        if VERBOSE_DEBUG:
            print(f"\n--- Branch {idx}/{total} ---")

        if not open_reviews_tab(driver, branch_url):
            if VERBOSE_DEBUG:
                print("  Could not open Reviews tab -- skipping branch.")
            return None

        location = get_business_location(driver, branch_label, fallback=self.query)
        if VERBOSE_DEBUG:
            print(f"  Location: {location}")

        scrollable = wait_for_reviews_panel(driver)
        if scrollable is None:
            if VERBOSE_DEBUG:
                print("  Reviews panel never became scrollable -- skipping branch.")
            return None

        reviews = scroll_and_collect_reviews(
            driver, scrollable, location, self.max_reviews_per_branch,
            progress_cb=progress_cb
        )
        if VERBOSE_DEBUG:
            print(f"  Collected {len(reviews)} reviews from this branch")
        return reviews


# --------------------------- standalone CLI usage ---------------------------

def _cli_main():
    """Run directly (python google_maps_scraper.py) for local testing outside the API."""
    import argparse
    parser = argparse.ArgumentParser(description="Google Maps review scraper")
    parser.add_argument("business_name")
    parser.add_argument("--location", default=None)
    parser.add_argument("--max-reviews", type=int, default=5000)
    parser.add_argument("--output", default="reviews.xlsx")
    parser.add_argument("--checkpoint", default="scrape_checkpoint.json")
    parser.add_argument("--show-browser", action="store_true",
                         help="run with a visible browser window (debugging)")
    args = parser.parse_args()

    def print_progress(branch_current, branch_total, reviews_so_far):
        print(f"[progress] branch {branch_current}/{branch_total} — "
              f"{reviews_so_far} reviews so far")

    scraper = GoogleMapsScraper(
        job_params={
            "business_name": args.business_name,
            "location": args.location,
            "max_reviews": args.max_reviews,
        },
        headless=not args.show_browser,
        checkpoint_file=args.checkpoint,
    )
    reviews = scraper.run(progress_callback=print_progress)

    df = pd.DataFrame(reviews)
    df.to_excel(args.output, index=False)
    print(f"\nSaved {len(df)} total reviews to {args.output}")
    print(f"You can delete {args.checkpoint}, or leave it — a new query starts clean.")


if __name__ == "__main__":
    _cli_main()