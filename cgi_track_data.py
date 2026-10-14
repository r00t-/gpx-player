#!/usr/bin/env python3
# cgi_track_data.py -- minimal CGI wrapper
import os
import sys
import json
import shlex
import subprocess
from datetime import datetime, timezone
import csv

# import helpers from the repo (adjust import path as needed)
from gpx_player.openseamap import calculate_speeds, calculate_display_speeds, accumulate_distances, calculate_average_speeds, speed_to_color, _json_for_inline_script, _TRACK_COLORS
from gpx_player.gpx_utils import _ensure_aware

# parse CGI query string (simple)
from urllib.parse import parse_qs
qs = parse_qs(os.environ.get('QUERY_STRING', ''))

def get_param(name, required=True):
    v = qs.get(name, [''])
    if required and not v[0]:
        raise ValueError(f'missing parameter: {name}')
    return v[0]

def iso_to_dt(s):
    # rely on datetime.fromisoformat; require timezone-aware
    dt = datetime.fromisoformat(s.replace('Z', '+00:00'))
    if dt.tzinfo is None or dt.utcoffset() is None:
        # force UTC if naive? prefer to require timezone
        dt = dt.replace(tzinfo=timezone.utc)
    return dt

def run_generator(log_id, start, end):
    # Example: call external binary "make_track" that prints CSV ts,lon,lat to stdout.
    # Sanitize args to avoid shell injection; prefer list form.
    prog = "/opt/bin/make_track"  # adjust
    args = [prog, str(log_id), start.isoformat(), end.isoformat()]
    try:
        proc = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True, text=True, timeout=30)
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"generator failed: {e.stderr[:200]}")
    return proc.stdout

def csv_to_points(csv_text):
    points = []
    rdr = csv.reader(csv_text.splitlines())
    for row in rdr:
        if not row:
            continue
        # accept either ts,lon,lat or ts,lat,lon; detect by header or length
        ts_text = row[0]
        lon = float(row[1])
        lat = float(row[2])
        # parse timestamp; require timezone if possible
        t = datetime.fromisoformat(ts_text.replace('Z', '+00:00'))
        points.append({'lat': lat, 'lon': lon, 'time': t})
    return points

def build_payload(tracks, title="GPX Player", tail_point_count=60):
    # tracks: list of track dicts with 'points' and optionally 'name'
    all_tracks = []
    for t in tracks:
        points = t['points']
        # ensure at least two points for speed calcs
        seg_speeds = calculate_speeds(points, max_speed=100)  # choose sensible max or parameterize
        display_seg_speeds = calculate_display_speeds(points, max_speed=100)
        distances = accumulate_distances(points)
        avg_speeds = calculate_average_speeds(points, distances)
        point_speeds = [0.0] + seg_speeds
        display_point_speeds = [0.0] + display_seg_speeds
        all_tracks.append({
            'name': t.get('name'),
            'display_name': t.get('name') or 'Track',
            'points': points,
            'point_speeds': point_speeds,
            'display_point_speeds': display_point_speeds,
            'distances': distances,
            'avg_speeds': avg_speeds,
            'seg_speeds': seg_speeds,
            'display_seg_speeds': display_seg_speeds,
        })
    # flatten timestamps and compute min/max
    timestamps = sorted({p['time'].isoformat() for track in all_tracks for p in track['points']})
    if timestamps:
        min_time = timestamps[0]
        max_time = timestamps[-1]
        # compute timeRange in seconds (as openseamap does)
        # convert iso back to dt to compute diff
        min_dt = datetime.fromisoformat(min_time.replace('Z', '+00:00'))
        max_dt = datetime.fromisoformat(max_time.replace('Z', '+00:00'))
        time_range = (max_dt - min_dt).total_seconds()
    else:
        min_time = max_time = None
        time_range = 0
    payload = {
        "colors": _TRACK_COLORS,
        "points": [[{'lat': p['lat'], 'lon': p['lon'], 'time': p['time'].isoformat()} for p in t['points']] for t in all_tracks],
        "speeds": [t['display_point_speeds'] for t in all_tracks],
        "distances": [t['distances'] for t in all_tracks],
        "avgSpeeds": [t['avg_speeds'] for t in all_tracks],
        "segmentColors": [[speed_to_color(s, max(1.0, max(t['display_seg_speeds'] or [1.0]))) for s in t['display_seg_speeds']] for t in all_tracks],
        "trackNames": [t['display_name'] for t in all_tracks],
        "timestamps": timestamps,
        "minTime": min_time,
        "maxTime": max_time,
        "timeRange": time_range,
        "title": title,
        "tailPointCount": tail_point_count,
        "fullTrackLayerNames": [None]*len(all_tracks),
    }
    return payload

def send_json(obj, status=200):
    sys.stdout.write("Status: {}\r\n".format(status))
    sys.stdout.write("Content-Type: application/json\r\n")
    sys.stdout.write("Access-Control-Allow-Origin: *\r\n")
    sys.stdout.write("\r\n")
    sys.stdout.write(json.dumps(obj, default=str))

def main():
    try:
        log_id = get_param('log_id')
        start = iso_to_dt(get_param('start'))
        end = iso_to_dt(get_param('end'))
        # call generator
        csv_out = run_generator(log_id, start, end)
        points = csv_to_points(csv_out)
        if not points:
            send_json({"error": "no points"}, status=404)
            return
        tracks = [{'name': f'log-{log_id}', 'points': points}]
        payload = build_payload(tracks)
        send_json(payload)
    except Exception as e:
        send_json({"error": str(e)}, status=500)

if __name__ == '__main__':
    main()
