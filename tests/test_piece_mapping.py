import pytest
from core.torrent_parser import VideoFileInfo, compute_sample_pieces, compute_head_tail_pieces


class TestPieceMapping:
    """Tests for piece index mapping accuracy."""

    def _make_video(self, size, piece_length, offset=0):
        total_pieces = (offset + size + piece_length - 1) // piece_length
        return VideoFileInfo(
            name="test.mp4",
            size=size,
            file_index=0,
            offset=offset,
            first_piece=offset // piece_length,
            last_piece=(offset + size - 1) // piece_length,
            piece_length=piece_length,
            total_pieces=total_pieces,
        )

    def test_50_percent_maps_to_middle(self):
        size = 1024 * 1024 * 100
        pl = 256 * 1024
        video = self._make_video(size, pl)
        result = compute_sample_pieces(video, [50], adjacent=0)
        center = result[50][0]
        expected = (size // 2) // pl
        assert center == expected

    def test_sample_pieces_increase_monotonically(self):
        video = self._make_video(1024 * 1024 * 500, 1024 * 1024)
        points = list(range(5, 100, 5))
        result = compute_sample_pieces(video, points, adjacent=0)
        centers = [result[p][0] for p in points]
        assert centers == sorted(centers)

    def test_adjacent_pieces_form_contiguous_range(self):
        video = self._make_video(1024 * 1024 * 200, 256 * 1024)
        result = compute_sample_pieces(video, [50], adjacent=3)
        pieces = result[50]
        for i in range(1, len(pieces)):
            assert pieces[i] == pieces[i - 1] + 1

    def test_small_file_fewer_pieces(self):
        video = self._make_video(1024 * 1024, 256 * 1024)
        assert video.last_piece - video.first_piece + 1 == 4
        result = compute_sample_pieces(video, [50], adjacent=2)
        pieces = result[50]
        assert all(video.first_piece <= p <= video.last_piece for p in pieces)

    def test_offset_file_correct_mapping(self):
        offset = 1024 * 1024 * 10  # 10MB offset
        size = 1024 * 1024 * 50
        pl = 256 * 1024
        video = self._make_video(size, pl, offset)

        assert video.first_piece == offset // pl
        assert video.last_piece == (offset + size - 1) // pl

        result = compute_sample_pieces(video, [50], adjacent=0)
        center = result[50][0]
        expected_byte = offset + size // 2
        expected_piece = expected_byte // pl
        assert center == expected_piece

    def test_head_covers_minimum_bytes(self):
        video = self._make_video(1024 * 1024 * 100, 256 * 1024)
        head_bytes = 2 * 1024 * 1024
        head, _ = compute_head_tail_pieces(video, head_bytes, 0)
        covered = len(head) * video.piece_length
        assert covered >= head_bytes

    def test_tail_covers_minimum_bytes(self):
        video = self._make_video(1024 * 1024 * 100, 256 * 1024)
        tail_bytes = 10 * 1024 * 1024
        _, tail = compute_head_tail_pieces(video, 0, tail_bytes)
        covered = len(tail) * video.piece_length
        assert covered >= tail_bytes

    def test_large_piece_size(self):
        video = self._make_video(1024 * 1024 * 1000, 4 * 1024 * 1024)
        result = compute_sample_pieces(video, [25, 50, 75], adjacent=2)
        for pct in [25, 50, 75]:
            assert len(result[pct]) == 5

    def test_all_19_default_sample_points(self):
        video = self._make_video(1024 * 1024 * 500, 256 * 1024)
        points = list(range(5, 100, 5))
        result = compute_sample_pieces(video, points, adjacent=2)
        assert len(result) == 19
        for pct in points:
            assert len(result[pct]) >= 1
