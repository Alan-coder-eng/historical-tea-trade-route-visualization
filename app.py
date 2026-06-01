import logging
import time
from datetime import datetime
from functools import lru_cache
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
from flask import Flask, jsonify, render_template
from rtree import index
from shapely.geometry import LineString, Point

from waypoints_db import waypoints_db

BASE_DIR = Path(__file__).resolve().parent
TRADE_DATA_PATH = BASE_DIR / "data" / "trade" / "cleaned_data.csv"
GEODATA_DIR = BASE_DIR / "data" / "geodata"

app = Flask(__name__)

# Land layers are loaded once so each request can reuse the same geometry index.
world_land = None
LAND_SINDEX = None
LAND_DATA = None

try:
    land_shapefile_path = GEODATA_DIR / "110m_physical" / "ne_110m_land.shp"
    world_land = gpd.read_file(land_shapefile_path).buffer(0)

    if world_land.crs != "EPSG:4326":
        world_land = world_land.to_crs("EPSG:4326", use_arrow=False)

    LAND_DATA = world_land.geometry.union_all().buffer(0.1)
    print(f"Land data loaded successfully. Coverage area: {LAND_DATA.area}")

    ocean_shapefile_path = GEODATA_DIR / "110m_physical" / "ne_110m_ocean.shp"
    world_ocean = gpd.read_file(ocean_shapefile_path).buffer(0)
    if world_ocean.crs != "EPSG:4326":
        world_ocean = world_ocean.to_crs("EPSG:4326", use_arrow=False)

    print(f"Ocean data loaded successfully. Polygon count: {len(world_ocean)}")

    LAND_SINDEX = index.Index()
    for idx, geom in enumerate(world_land.geometry):
        LAND_SINDEX.insert(idx, geom.bounds)

    OCEAN_SINDEX = index.Index()
    for idx, geom in enumerate(world_ocean.geometry):
        OCEAN_SINDEX.insert(idx, geom.bounds)

except Exception as exc:
    print(f"Failed to initialize geographic data: {exc}")
    raise


@lru_cache(maxsize=100000)
def cached_land_check(lng: float, lat: float) -> bool:
    """Cache land checks because the same coordinates are revisited often."""
    point = Point(round(lng, 6), round(lat, 6))

    if LAND_SINDEX and not world_land.empty:
        possible = list(LAND_SINDEX.intersection(point.bounds))
        return any(world_land.geometry[i].contains(point) for i in possible)
    return LAND_DATA.contains(point)


def rrt_planner(start, end, max_iter=10, step_size=0.5, goal_bias=0.3):
    """Generate a short detour when a route segment crosses land."""
    tree = [{"point": start, "parent": None}]
    min_goal_dist = 0.5

    # Keep the search window local to the current segment.
    min_lng = min(start[0], end[0]) - 5.0
    max_lng = max(start[0], end[0]) + 5.0
    min_lat = min(start[1], end[1]) - 5.0
    max_lat = max(start[1], end[1]) + 5.0

    for _ in range(max_iter):
        if np.random.random() < goal_bias:
            target = end
        else:
            target = (
                np.random.uniform(min_lng, max_lng),
                np.random.uniform(min_lat, max_lat),
            )

        nearest = min(
            tree,
            key=lambda node: np.hypot(
                node["point"][0] - target[0],
                node["point"][1] - target[1],
            ),
        )

        dx = target[0] - nearest["point"][0]
        dy = target[1] - nearest["point"][1]
        dist = np.hypot(dx, dy)
        if dist < 1e-6:
            continue

        new_point = (
            nearest["point"][0] + dx / dist * step_size,
            nearest["point"][1] + dy / dist * step_size,
        )

        if not line_crosses_land(nearest["point"], new_point):
            new_node = {"point": new_point, "parent": nearest}
            tree.append(new_node)
            if np.hypot(new_point[0] - end[0], new_point[1] - end[1]) < min_goal_dist:
                return reconstruct_path(new_node, end)

    return None


def line_crosses_land(p1, p2):
    """Check whether a line segment intersects the merged land geometry."""
    line = LineString([p1, p2])
    return line.intersects(LAND_DATA)


def reconstruct_path(node, goal):
    """Trace an RRT branch back to the root and append the goal point."""
    path = []
    while node is not None:
        path.append(node["point"])
        node = node["parent"]
    path.reverse()
    path.append(goal)
    return path


def process_data():
    start_time = time.time()
    print(f"[{datetime.now()}] Starting route generation...")

    df = pd.read_csv(TRADE_DATA_PATH)
    df = df.rename(columns={"import": "import_dest"})
    df = df[df["lbs"] > 0]

    # Normalize a few destination names and coordinates before route generation.
    geo_corrections = {
        "United Kingdom": {"longitude2": -3.436},
        "Bombay": {"import_dest": "Mumbai", "latitude2": 19.0760, "longitude2": 72.8777},
        "Saigon": {"import_dest": "Ho Chi Minh City", "latitude2": 10.8231, "longitude2": 106.6297},
        "Singapore": {"latitude2": 1.9157, "longitude2": 104.1064},
        "Australia": {"latitude2": -26.8798, "longitude2": 153.0834},
        "Canada": {"latitude2": 51.6335, "longitude2": -128.49},
        "Monte Video": {"latitude2": -34.8574, "longitude2": -56.1511},
        "Sayam": {"latitude2": 13.5004, "longitude2": 100.4713},
    }

    for origin, correction in geo_corrections.items():
        print(f"Applying coordinate correction for {origin}: {correction}")
        mask = df["import_dest"].str.contains(origin, case=False)
        df.loc[mask, list(correction.keys())] = list(correction.values())

    def generate_safe_route(row):
        waypoints = get_nautical_waypoints(row["export"], row["import_dest"])
        all_points = [
            (row["latitude1"], row["longitude1"]),
            *waypoints,
            (row["latitude2"], row["longitude2"]),
        ]

        path = []
        land_points = 0

        for i in range(len(all_points) - 1):
            segment = generate_bezier_segment(
                all_points[i],
                all_points[i + 1],
                check_interval=0.1,
            )

            safe_segment = avoid_land_crossing(segment)
            land_points += len(segment) - len(safe_segment)
            path.extend(safe_segment)

        if len(path) == 0 or land_points / len(path) > 0.05:
            return []

        return path

    from tqdm import tqdm

    tqdm.pandas(desc="Generating routes")
    df["path"] = df.apply(generate_safe_route, axis=1)
    print(f"[{datetime.now()}] Route generation finished in {time.time() - start_time:.2f}s")
    return df[df["path"].apply(len) > 0].to_dict(orient="records")


def cubic_bezier(p0, p1, p2, p3, t, sea_status):
    """Adjust the curve offset slightly when the segment stays in open water."""
    offset = 0.002 if sum(sea_status) >= 3 else 0.0005
    return (
        (1 - t) ** 3 * p0
        + 3 * (1 - t) ** 2 * t * (p1 + offset)
        + 3 * (1 - t) * t**2 * (p2 - offset)
        + t**3 * p3
    )


def generate_bezier_segment(start, end, check_interval=0.01):
    """Generate a smooth segment between two route anchors."""
    path = []
    lat1, lon1 = start
    lat2, lon2 = end

    sea_status = [
        not cached_land_check(lon1, lat1),
        not cached_land_check((lon1 + lon2) / 2, (lat1 + lat2) / 2),
    ]

    t = 0
    while t <= 1:
        lat = cubic_bezier(lat1, lat1, lat2, lat2, t, sea_status)
        lng = cubic_bezier(lon1, lon1, lon2, lon2, t, sea_status)
        path.append({"lat": round(lat, 6), "lng": round(lng, 6)})
        t += check_interval

    return path


def avoid_land_crossing(segment):
    """Fallback to RRT only for segment slices that intersect land."""
    print("Running RRT fallback")

    safe_path = []
    prev_point = None
    for point in segment:
        if prev_point:
            start = (prev_point["lng"], prev_point["lat"])
            end_pt = (point["lng"], point["lat"])

            if LineString([start, end_pt]).intersects(LAND_DATA):
                rrt_path = rrt_planner(start, end_pt)
                if rrt_path:
                    safe_path.extend([{"lat": p[1], "lng": p[0]} for p in rrt_path])
                    print("RRT fallback succeeded")
                else:
                    safe_path.append(point)
                    print("RRT fallback failed, using original point")
            else:
                safe_path.append(point)
        else:
            safe_path.append(point)
        prev_point = point
    return safe_path


def get_nautical_waypoints(origin, dest):
    return waypoints_db.get((origin, dest), [])


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/data")
def get_data():
    try:
        return jsonify(
            {
                "routes": process_data(),
                "metadata": {
                    "crs": "EPSG:4326",
                    "pathResolution": 50,
                },
            }
        )
    except Exception as exc:
        logging.error("Route generation failed: %s", exc)
        print(f"Route generation failed: {exc}")
        return jsonify({"error": "The route generation service is temporarily unavailable"}), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
