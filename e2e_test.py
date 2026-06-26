"""
End-to-end test: magnet link -> metadata -> selective download -> snapshot.

Usage: python e2e_test.py [magnet_uri]
"""
import os
import sys
import time
import shutil

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import SNAPSHOT_DIR, TEMP_DIR, DOWNLOAD_DIR, SNAPSHOT_SETTINGS
from core.session_manager import SessionManager
from core.torrent_parser import (
    validate_magnet,
    resolve_metadata,
    find_largest_video,
    compute_head_tail_pieces,
)
from core.smart_downloader import monitor_download
from core.segment_extractor import read_pieces, assemble_segment, cleanup_segment
from core.snapshot_generator import generate_snapshot
from core.mp4_utils import get_keyframe_positions, extract_codec_config

import libtorrent as lt

MAGNET = sys.argv[1] if len(sys.argv) > 1 else "magnet:?xt=urn:btih:56dbaf94ff452023bd4f8e909ab097b55fab830b"
TASK_ID = "e2e_test"
SAMPLE_POINTS = [10, 30, 50, 70, 90]
TIMEOUT = 300
METADATA_TIMEOUT = 300


def main():
    print(f"=== E2E Test ===")
    print(f"Magnet: {MAGNET[:60]}...")

    assert validate_magnet(MAGNET), "Invalid magnet URI"
    print("[OK] Magnet URI validated")

    for d in [SNAPSHOT_DIR, TEMP_DIR, DOWNLOAD_DIR]:
        os.makedirs(d, exist_ok=True)
    task_tmp = os.path.join(TEMP_DIR, TASK_ID)
    task_snap = os.path.join(SNAPSHOT_DIR, TASK_ID)
    os.makedirs(task_tmp, exist_ok=True)
    os.makedirs(task_snap, exist_ok=True)

    # Phase 1: Resolve metadata
    print(f"[..] Resolving metadata (up to {METADATA_TIMEOUT}s)...")
    t0 = time.time()
    handle = resolve_metadata(MAGNET, timeout=METADATA_TIMEOUT)
    print(f"[OK] Metadata resolved in {time.time() - t0:.1f}s")

    video = find_largest_video(handle)
    print(f"[OK] Video: {video.name}")
    print(f"     Size: {video.size / 1024 / 1024:.1f} MB")
    print(f"     Pieces: {video.first_piece}-{video.last_piece} (piece_length={video.piece_length})")

    # Phase 2: Download head pieces first
    head_pieces, tail_pieces = compute_head_tail_pieces(
        video, SNAPSHOT_SETTINGS["head_bytes"], SNAPSHOT_SETTINGS["tail_bytes"]
    )

    num_pieces = video.total_pieces
    priorities = [0] * num_pieces
    for p in head_pieces:
        if 0 <= p < num_pieces:
            priorities[p] = 7
    handle.prioritize_pieces(priorities)
    handle.unset_flags(lt.torrent_flags.upload_mode)
    handle.resume()

    print(f"[..] Downloading head pieces ({len(head_pieces)} pieces)...")
    deadline = time.time() + 120
    while time.time() < deadline:
        if all(handle.have_piece(p) for p in head_pieces):
            break
        time.sleep(1)

    if not all(handle.have_piece(p) for p in head_pieces):
        print("[FAIL] Head pieces download timeout")
        return 1

    # Read head data
    head_data_dict = read_pieces(handle, head_pieces, timeout=30)
    head_path = os.path.join(task_tmp, "head.bin")
    assemble_segment(head_data_dict, video, head_path)
    head_bytes = open(head_path, "rb").read()
    print(f"[OK] Head downloaded ({len(head_bytes)} bytes)")

    # Phase 3: Parse moov for keyframe positions
    keyframes = get_keyframe_positions(head_bytes)
    if not keyframes:
        print("[WARN] No keyframe index found, using byte-offset sampling")
        # Fallback: just screenshot the head
        snap_path = os.path.join(task_snap, "snap_head.jpg")
        generate_snapshot(head_path, snap_path, seek_offset=0)
        print(f"[OK] Head snapshot generated")
        return 0

    duration = keyframes[-1][0]
    print(f"[OK] Found {len(keyframes)} keyframes, duration={duration:.1f}s")

    # Find keyframe pieces for each sample point
    sample_plan = {}
    for pct in SAMPLE_POINTS:
        target_time = duration * pct / 100
        closest_kf = min(keyframes, key=lambda k: abs(k[0] - target_time))
        kf_piece = closest_kf[1] // video.piece_length
        adj = SNAPSHOT_SETTINGS["adjacent_pieces"]
        pieces = [p for p in range(kf_piece - adj, kf_piece + adj + 1)
                  if video.first_piece <= p <= video.last_piece]
        sample_plan[pct] = {"time": closest_kf[0], "offset": closest_kf[1], "pieces": pieces}
        print(f"     {pct}%: t={closest_kf[0]:.1f}s, piece={kf_piece}, range={pieces[0]}-{pieces[-1]}")

    # Phase 4: Download sample pieces
    all_sample_pieces = set()
    for sp in sample_plan.values():
        all_sample_pieces.update(sp["pieces"])

    priorities = [0] * num_pieces
    for p in head_pieces:
        if 0 <= p < num_pieces:
            priorities[p] = 7
    for p in all_sample_pieces:
        if 0 <= p < num_pieces:
            priorities[p] = 6
    handle.prioritize_pieces(priorities)

    dl_deadline_ms = 0
    for p in sorted(all_sample_pieces):
        if 0 <= p < num_pieces:
            handle.set_piece_deadline(p, dl_deadline_ms)
            dl_deadline_ms += 100

    print(f"[..] Downloading {len(all_sample_pieces)} sample pieces (timeout={TIMEOUT}s)...")
    deadline = time.time() + TIMEOUT
    last_log = 0
    while time.time() < deadline:
        downloaded = sum(1 for p in all_sample_pieces if handle.have_piece(p))
        now = time.time()
        if now - last_log >= 10:
            status = handle.status()
            print(f"     Progress: {downloaded}/{len(all_sample_pieces)} pieces, "
                  f"{status.download_rate/1024:.1f} KB/s, {status.num_peers} peers")
            last_log = now
        if downloaded == len(all_sample_pieces):
            break
        time.sleep(1)

    downloaded = sum(1 for p in all_sample_pieces if handle.have_piece(p))
    print(f"[OK] Downloaded {downloaded}/{len(all_sample_pieces)} sample pieces")

    # Phase 5: Generate snapshots using sparse file approach
    generated = []
    for pct, plan in sample_plan.items():
        pieces = plan["pieces"]
        if not all(handle.have_piece(p) for p in pieces):
            print(f"     [{pct}%] SKIP: missing pieces")
            continue

        # Create sparse file: head data at offset 0 + sample data at correct offset
        sparse_path = os.path.join(task_tmp, f"sparse_{pct:02d}.mp4")
        piece_data = read_pieces(handle, pieces, timeout=30)

        with open(sparse_path, "wb") as f:
            # Write head at offset 0
            f.write(head_bytes)
            # Write each sample piece at its correct file offset
            for p in sorted(piece_data.keys()):
                piece_offset = p * video.piece_length - video.offset
                if piece_offset > len(head_bytes):
                    f.seek(piece_offset)
                    f.write(piece_data[p])

        snap_filename = f"snap_{pct:02d}.jpg"
        snap_path = os.path.join(task_snap, snap_filename)

        success = generate_snapshot(sparse_path, snap_path, seek_offset=plan["time"])
        cleanup_segment(sparse_path)

        if success:
            snap_size = os.path.getsize(snap_path) / 1024
            print(f"     [{pct}%] OK: {snap_filename} ({snap_size:.1f} KB) at t={plan['time']:.1f}s")
            generated.append(pct)
        else:
            print(f"     [{pct}%] FAIL: FFmpeg could not extract frame")

    # Cleanup
    sm = SessionManager()
    sm.remove_torrent(handle, delete_files=True)
    cleanup_segment(head_path)
    if os.path.isdir(task_tmp):
        shutil.rmtree(task_tmp, ignore_errors=True)

    # Summary
    print(f"\n=== Results ===")
    print(f"Snapshots: {len(generated)}/{len(SAMPLE_POINTS)} ({generated})")
    if os.path.isdir(task_snap):
        files = sorted(os.listdir(task_snap))
        if files:
            print(f"Generated files in {task_snap}:")
            for f in files:
                print(f"  {f} ({os.path.getsize(os.path.join(task_snap, f)) / 1024:.1f} KB)")

    if generated:
        print(f"\nSUCCESS: {len(generated)} snapshots generated")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
