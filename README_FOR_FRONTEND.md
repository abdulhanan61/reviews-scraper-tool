# Review Scraper Tool — Backend API Guide (for Frontend Team)

This document is everything you need to build the 6 UI screens against the backend, without needing to touch the scraper, database, or Python code.

## Starting the backend

```
cd reviews-scraper-tool
venv\Scripts\activate        # Windows, or: source venv/bin/activate on Mac/Linux
python app.py
```

The server runs at:
```
http://127.0.0.1:5000
```

Leave that terminal running. All requests below go to that base URL.

## General notes

- All responses are JSON, except `/download` which returns a file.
- All endpoints are prefixed with `/api/` except the health check (`GET /`).
- Job `status` is always one of: `"queued"`, `"running"`, `"done"`, `"failed"`.
- If a `"failed"` job is returned, check its `error_message` field for what went wrong.
- Errors from any endpoint look like: `{"error": "description of what went wrong"}` with an appropriate HTTP status code (400 for bad input, 404 for not found, 500 for a server-side crash).

---

## 1. Health check

```
GET /
```
**Response:**
```json
{ "status": "running" }
```
Use this to confirm the backend is reachable before showing any screens.

---

## 2. List platforms — powers Screen 2 (Platform Selection)

```
GET /api/platforms
```
**Response:**
```json
[
  {
    "id": "google_maps",
    "name": "Google Maps",
    "fields": [
      { "id": "business_name", "label": "Business Name", "type": "text", "required": true, "default": null },
      { "id": "locations", "label": "Specific Locations (optional, leave empty for all locations)", "type": "multi_text", "required": false, "default": null },
      { "id": "max_reviews", "label": "Max Reviews per Branch", "type": "number", "required": false, "default": 5000 }
    ]
  }
]
```
**How to use this for Screen 3 (Job Details):** don't hardcode form fields per platform. Render one input per item in `fields`, using `label` as the field's display label, `type` to pick the input type, and `required` to mark it mandatory. This means new platforms (Trustpilot, Yelp, etc.) automatically get a working form with zero frontend changes once the backend adds them.

**Special field type: `multi_text`** (currently used by `locations`) — render this as a **tag/chip input**: an "Add Location" button that lets the user add zero, one, or several location values (e.g. "Lahore", "Karachi"). Send it to the backend as a JSON array:
```json
"locations": ["Lahore", "Karachi"]
```
- **Empty array or field omitted entirely** → searches the business name with no location filter, returning whatever Google Maps' own search surfaces (effectively "all locations" it knows about).
- **One value** → searches that single location.
- **Multiple values** → the backend searches each one, combines every branch found across all of them, and returns **one single job with one combined result** (not separate jobs/files per location) — so the download button on Screen 5 still only needs to handle one file per job, regardless of how many locations were searched.
- A single value can also be a country or region name (e.g. `"Pakistan"`) — it's treated the same way, just as a search term. How many branches actually come back depends on what Google Maps itself surfaces for that search; it isn't guaranteed to be a literal exhaustive list if the chain has a very large number of locations.

---

## 3. Start a job — powers the "New Scrape Job" button

```
POST /api/jobs
Content-Type: application/json
```
**Request body** — `platform` plus whatever fields that platform declared in `/api/platforms`:
```json
{
  "platform": "google_maps",
  "business_name": "Ajwa Bakers & Restaurants",
  "locations": ["Lala Musa"],
  "max_reviews": 5000
}
```
**Response** (returns immediately, scraping happens in the background):
```json
{ "job_id": "819eaeeb-bf83-4e4a-9b9b-f5618033a53d", "status": "queued" }
```
Status code `202`.

**Possible errors:**
```json
{ "error": "Missing required field: platform" }
```
```json
{ "error": "Unknown platform 'yelp'" }
```
```json
{ "error": "Missing required field(s): business_name" }
```
All return status `400`.

---

## 4. List all jobs — powers Screen 1 (Dashboard) and Screen 6 (History)

```
GET /api/jobs
```
**Response** (most recent first):
```json
[
  {
    "id": "819eaeeb-bf83-4e4a-9b9b-f5618033a53d",
    "business": "Ajwa Bakers & Restaurants",
    "platform": "google_maps",
    "status": "done",
    "total": 20,
    "date": "2026-07-03T06:48:51.282815+00:00"
  }
]
```

---

## 5. Get one job's full details

```
GET /api/jobs/<job_id>
```
**Response:**
```json
{
  "job": {
    "id": "819eaeeb-bf83-4e4a-9b9b-f5618033a53d",
    "platform": "google_maps",
    "job_params": { "business_name": "Ajwa Bakers & Restaurants", "locations": ["Lala Musa"], "max_reviews": 5000 },
    "status": "done",
    "branch_current": 1,
    "branch_total": 1,
    "reviews_so_far": 20,
    "total_reviews": 20,
    "error_message": null,
    "created_at": "2026-07-03T06:48:51.282815+00:00",
    "updated_at": "2026-07-03T06:49:14.029066+00:00"
  },
  "reviews_count": 20
}
```
`404` with `{"error": "Job not found"}` if the ID doesn't exist.

---

## 6. Poll live progress — powers Screen 4 (Live Progress)

```
GET /api/jobs/<job_id>/status
```
**Poll this every 2 seconds** while a job's status is `"queued"` or `"running"`. Stop polling once you see `"done"` or `"failed"`.

**Response:**
```json
{
  "status": "running",
  "branch_current": 1,
  "branch_total": 1,
  "reviews_so_far": 15
}
```
Use `branch_current`/`branch_total` for the "Branch X of Y" display, and `reviews_so_far` for the live review counter / progress bar.

---

## 7. Download results — powers Screen 5 (Results) download buttons

```
GET /api/jobs/<job_id>/download?format=xlsx
```
`format` query param: `xlsx` (default), `csv`, or `json`. Returns the actual file as a download (not JSON) — point a browser link or `<a href>` / download trigger directly at this URL with the desired format.

**Errors:**
```json
{ "error": "Job is not finished yet (status: running)" }
```
(status `400` — don't show the download buttons until job status is `"done"`)
```json
{ "error": "Unsupported format 'pdf'. Must be one of ['csv', 'json', 'xlsx']" }
```

---

## 8. Delete a job — powers the delete option on Screen 6 (History)

```
DELETE /api/jobs/<job_id>
```
**Response:**
```json
{ "deleted": true }
```
`404` with `{"error": "Job not found"}` if it doesn't exist or was already deleted. Deleting a job also deletes its reviews — this can't be undone, so confirm with the user before calling it.

---

## Typical flow for one full scrape (Screens 2 → 5)

1. `GET /api/platforms` on app load — populate Screen 2's platform cards
2. User picks a platform + fills the dynamic form (Screen 3) → `POST /api/jobs`
3. Immediately navigate to Screen 4, start polling `GET /api/jobs/<id>/status` every 2s
4. When status becomes `"done"`, stop polling, navigate to Screen 5
5. Screen 5's download buttons point at `GET /api/jobs/<id>/download?format=...`
6. Screen 6 (History) at any time: `GET /api/jobs` for the list, `DELETE /api/jobs/<id>` for the delete option