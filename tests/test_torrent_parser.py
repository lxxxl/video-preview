import pytest
from core.torrent_parser import (
    validate_magnet,
    extract_info_hash,
    compute_sample_pieces,
    compute_head_tail_pieces,
    VideoFileInfo,
    _get_extension,
)


class TestValidateMagnet:
    def test_valid_hex_hash(self):
        uri = "magnet:?xt=urn:btih:aabbccddee11223344556677889900aabbccddee"
        assert validate_magnet(uri) is True

    def test_valid_base32_hash(self):
        uri = "magnet:?xt=urn:btih:MFZWIZLTOQ3TMNRSGM3DKNRQGE4TABCD"
        assert validate_magnet(uri) is True

    def test_valid_with_params(self):
        uri = "magnet:?xt=urn:btih:aabbccddee11223344556677889900aabbccddee&dn=test&tr=http://tracker.example.com"
        assert validate_magnet(uri) is True

    def test_invalid_no_btih(self):
        assert validate_magnet("magnet:?dn=test") is False

    def test_invalid_short_hash(self):
        assert validate_magnet("magnet:?xt=urn:btih:aabbcc") is False

    def test_invalid_empty(self):
        assert validate_magnet("") is False

    def test_invalid_random_string(self):
        assert validate_magnet("not a magnet link") is False


class TestExtractInfoHash:
    def test_hex_hash(self):
        uri = "magnet:?xt=urn:btih:AABBCCDDEE11223344556677889900AABBCCDDEE"
        assert extract_info_hash(uri) == "aabbccddee11223344556677889900aabbccddee"

    def test_invalid_raises(self):
        with pytest.raises(ValueError):
            extract_info_hash("invalid")


class TestComputeSamplePieces:
    def _make_video(self, size=1024 * 1024 * 100, piece_length=256 * 1024, offset=0):
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

    def test_basic_sample_points(self):
        video = self._make_video()
        result = compute_sample_pieces(video, [50], adjacent=2)
        assert 50 in result
        pieces = result[50]
        assert len(pieces) == 5  # center + 2 on each side

    def test_multiple_points(self):
        video = self._make_video()
        result = compute_sample_pieces(video, [25, 50, 75], adjacent=2)
        assert len(result) == 3
        for pct in [25, 50, 75]:
            assert pct in result
            assert len(result[pct]) > 0

    def test_boundary_clipping(self):
        video = self._make_video(size=256 * 1024 * 5, piece_length=256 * 1024)
        result = compute_sample_pieces(video, [5], adjacent=2)
        assert 5 in result
        for p in result[5]:
            assert video.first_piece <= p <= video.last_piece

    def test_out_of_range_ignored(self):
        video = self._make_video()
        result = compute_sample_pieces(video, [0, 100, 50], adjacent=2)
        assert 0 not in result
        assert 100 not in result
        assert 50 in result

    def test_with_offset(self):
        video = self._make_video(offset=1024 * 1024 * 50)
        result = compute_sample_pieces(video, [50], adjacent=2)
        pieces = result[50]
        for p in pieces:
            assert video.first_piece <= p <= video.last_piece


class TestComputeHeadTailPieces:
    def test_head_pieces(self):
        video = VideoFileInfo(
            name="test.mp4",
            size=1024 * 1024 * 100,
            file_index=0,
            offset=0,
            first_piece=0,
            last_piece=399,
            piece_length=256 * 1024,
            total_pieces=400,
        )
        head, tail = compute_head_tail_pieces(video, head_bytes=2 * 1024 * 1024, tail_bytes=10 * 1024 * 1024)
        assert head[0] == 0
        assert len(head) == 8  # 2MB / 256KB = 8

    def test_tail_pieces(self):
        video = VideoFileInfo(
            name="test.mp4",
            size=1024 * 1024 * 100,
            file_index=0,
            offset=0,
            first_piece=0,
            last_piece=399,
            piece_length=256 * 1024,
            total_pieces=400,
        )
        head, tail = compute_head_tail_pieces(video, head_bytes=2 * 1024 * 1024, tail_bytes=10 * 1024 * 1024)
        assert tail[-1] == 399
        assert len(tail) == 40  # 10MB / 256KB = 40


class TestGetExtension:
    def test_mp4(self):
        assert _get_extension("movie.mp4") == ".mp4"

    def test_mkv_with_path(self):
        assert _get_extension("path/to/video.mkv") == ".mkv"

    def test_uppercase(self):
        assert _get_extension("VIDEO.AVI") == ".avi"

    def test_no_extension(self):
        assert _get_extension("noext") == ""

    def test_multiple_dots(self):
        assert _get_extension("file.name.ts") == ".ts"
