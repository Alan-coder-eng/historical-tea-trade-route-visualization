# Historical Tea Trade Route Visualization

## Overview

This project visualizes historical tea trade routes from Chinese export ports to global destinations between 1859 and 1868.

It combines trade records, geographic shapefiles, predefined maritime waypoints, and animated map rendering to show how black tea and green tea moved across the world.

## What the project does

- Loads historical trade data with export port, destination, tea type, year, and cargo weight.
- Builds sea routes between origin and destination instead of drawing simple straight lines.
- Avoids land crossings by checking route segments against Natural Earth land polygons.
- Uses animated ships and year-by-year playback to present the routes on an interactive Mapbox map.
- Highlights route volume by line width and separates tea categories by color.

## How it works

### 1. Data preparation

The application reads cleaned trade records from `data/trade/cleaned_data.csv` and normalizes a few destination names and coordinates before route generation.

### 2. Route generation

Each route is built from:

- the export port
- optional maritime waypoints from `waypoints_db.py`
- the destination point

The path between anchor points is smoothed with Bezier segments.

### 3. Land avoidance

The backend loads Natural Earth land and ocean shapefiles from `data/geodata/`.

For each segment, it:

- checks whether the line crosses land
- reuses cached land checks for speed
- falls back to a lightweight RRT planner when a segment intersects land

This keeps routes visually closer to realistic sea travel.

### 4. Frontend visualization

The frontend uses Flask templates and Mapbox GL JS to:

- render all routes for a selected year
- animate ship markers along each route
- let the user select a route and inspect its details
- play the timeline from 1859 to 1868

## Final result

The result is an interactive historical route map where users can:

- switch between years
- compare black tea and green tea flows
- inspect route distances and transport volume
- watch ship animations move along reconstructed maritime paths

## Project structure

```text
.
|-- app.py
|-- waypoints_db.py
|-- requirements.txt
|-- README.md
|-- templates/
|   `-- index.html
|-- static/
|   |-- textures/
|   `-- ...
|-- data/
|   |-- trade/
|   |   |-- cleaned_data.csv
|   |   `-- data.csv
|   `-- geodata/
|       |-- 110m_physical/
|       |-- 50m_physical/
|       |-- 10m_physical/
|       `-- 10m_cultural/
`-- archive/
    |-- app_copy.py
    |-- package_install.txt
    `-- templates/
```

## Run locally

```bash
pip install -r requirements.txt
python app.py
```

Then open `http://127.0.0.1:5000`.
