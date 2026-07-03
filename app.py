"""
Entry point for the Review Scraper Tool backend.

Run with:
    python app.py

Then visit http://127.0.0.1:5000 in a browser, or:
    curl http://127.0.0.1:5000

Routes live in api/routes.py (Step 2.3+) and get registered onto this app.
"""

from flask import Flask

from database.db import init_db

app = Flask(__name__)

init_db()  # creates jobs/reviews tables if they don't exist yet

# Step 2.4: routes.py registers its own routes onto this same `app` object.
# Imported at the bottom so routes.py can do `from app import app` without
# a circular-import problem.
from api.routes import register_routes
register_routes(app)


if __name__ == "__main__":
    app.run(debug=True, port=5000)