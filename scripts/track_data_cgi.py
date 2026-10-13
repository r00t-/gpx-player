#!/usr/bin/env python3
"""
Minimal CGI endpoint to return track payload for animate_tracks.js.
- Calls external generator if TRACK_GENERATOR env var is set (expects CSV ts,lon,lat on stdout)
- Otherwise returns a small synthetic demo track for testing.

Place this script behind your CGI/WSGI gateway (or adapt to Flask/WSGI as needed).
"""
import os
import sys
import csv
import json
import subprocess
from datetime import datetime, timezone, timedelta
from urllib.parse import parse_qs

# Import helpers from this package
from gpx_player.openseamap import (
    calculate_speeds,
    calculate_display_speeds,
    accumulate_distances,
    calculate_average_speeds,
    speed_to_color,
    _TRACK_COLORS,
)


def http_response_json(obj, status=200):
    print(f"Status: {status}")
    print("Content-Type: application/json")
    print("Access-Control-Allow-Origin: *")
    print()
    print(json.dumps(obj, default=str))


def iso_to_dt(s: str) -> datetime:
    if not s:
        return None
    # Normalize trailing Z
    t = s.replace('Z', '+00:00')
    return datetime.fromisoformat(t)


def run_generator_csv(log_id: str, start: str, end: str, timeout=30) -> str:
    prog = os.environ.get('TRACK_GENERATOR')
    if not prog:
        return ''
    args = [prog, log_id, start or '', end or '']
    proc = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=timeout)
    if proc.returncode != 0:
        raise RuntimeError(f"generator failed: {proc.stderr[:200]}")
    return proc.stdout


def csv_to_points(csv_text: str):
    points = []
    if not csv_text:
        return points
    rdr = csv.reader(csv_text.splitlines())
    for row in rdr:
        if not row:
            continue
        # Expect ts,lon,lat
        try:
            ts_text = row[0]
            lon = float(row[1])
            lat = float(row[2])
            t = iso_to_dt(ts_text)
            if t is None:
                continue
            points.append({'lat': lat, 'lon': lon, 'time': t})
        except Exception:
            continue
    return points


def synthesize_demo_points():
    now = datetime.now(timezone.utc)
    pts = []
    for i in range(20):
        t = now + timedelta(seconds=i * 10)
        lat = 37.7749 + (i * 0.0005)
        lon = -122.4194 + (i * 0.0007)
        pts.append({'lat': lat, 'lon': lon, 'time': t})
    return pts


def build_payload(tracks, title="GPX Player", tail_point_count=60):
    all_tracks = []
    for t in tracks:
        points = t['points']
        if not points:
            continue
        seg_speeds = calculate_speeds(points, max_speed=100)
        display_seg_speeds = calculate_display_speeds(points, max_speed=100)
        distances = accumulate_distances(points)
        avg_speeds = calculate_average_speeds(points, distances)
        point_speeds = [0.0] + seg_speeds
        display_point_speeds = [0.0] + display_seg_speeds
        all_tracks.append({
            'name': t.get('name'),
            'display_name': t.get('name') or 'Track',
            'points': [{'lat': p['lat'], 'lon': p['lon'], 'time': p['time'].isoformat()} for p in points],
            'point_speeds': point_speeds,
            'display_point_speeds': display_point_speeds,
            'distances': distances,
            'avg_speeds': avg_speeds,
            'seg_speeds': seg_speeds,
            'display_seg_speeds': display_seg_speeds,
        })

    if not all_tracks:
        return None

    timestamps = sorted({p['time'] for track in all_tracks for p in track['points']})
    min_time = timestamps[0] if timestamps else None
    max_time = timestamps[-1] if timestamps else None
    time_range = 0
    if min_time and max_time:
        min_dt = datetime.fromisoformat(min_time.replace('Z', '+00:00'))
        max_dt = datetime.fromisoformat(max_time.replace('Z', '+00:00'))
        time_range = (max_dt - min_dt).total_seconds()

    # compute segment colors conservatively
    segment_colors = []
    for t in all_tracks:
        segs = t.get('display_seg_speeds', t.get('seg_speeds', []))
        if segs:
            max_seg = max([s for s in segs if s > 0] or [1.0])
        else:
            max_seg = 1.0
        segment_colors.append([speed_to_color(s, max_seg) for s in segs])

    payload = {
        'mapId': 'map',
        'colors': _TRACK_COLORS,
        'points': [[{'lat': p['lat'], 'lon': p['lon'], 'time': p['time']} for p in t['points']] for t in all_tracks],
        'speeds': [t['display_point_speeds'] for t in all_tracks],
        'distances': [t['distances'] for t in all_tracks],
        'avgSpeeds': [t['avg_speeds'] for t in all_tracks],
        'segmentColors': segment_colors,
        'trackNames': [t['display_name'] for t in all_tracks],
        'timestamps': timestamps,
        'minTime': min_time,
        'maxTime': max_time,
        'timeRange': time_range,
        'title': title,
        'tailPointCount': tail_point_count,
        'fullTrackLayerNames': [None] * len(all_tracks),
    }
    return payload


def main():
    qs = parse_qs(os.environ.get('QUERY_STRING', ''))
    log_id = qs.get('log_id', ['demo'])[0]
    start = qs.get('start', [''])[0]
    end = qs.get('end', [''])[0]

    # Try external generator first
    try:
        csv_out = run_generator_csv(log_id, start, end)
        points = csv_to_points(csv_out)
    except Exception as e:
        points = []

    if not points:
        # fallback to demo
        points = synthesize_demo_points()

    tracks = [{'name': f'log-{log_id}', 'points': points}]
    payload = build_payload(tracks)
    if not payload:
        http_response_json({'error': 'no points'}, status=404)
    else:
        http_response_json(payload, status=200)


if __name__ == '__main__':
    main()
