import os
import pytest
from unittest.mock import MagicMock, patch
from core.torrent_parser import VideoFileInfo
from core.segment_extractor import extract_segment, cleanup_segment


class MockReadPieceAlert:
    def __init__(self, piece_index, data, error_val=0):
        self.piece = piece_index
        self.buffer = data
        self.ec = MagicMock()
        self.ec.value.return_value = error_val


class TestExtractSegment:
    def _make_video(self, piece_length=256 * 1024, offset=0, size=1024 * 1024):
        return VideoFileInfo(
            name="test.mp4",
            size=size,
            file_index=0,
            offset=offset,
            first_piece=offset // piece_length,
            last_piece=(offset + size - 1) // piece_length,
            piece_length=piece_length,
            total_pieces=(offset + size + piece_length - 1) // piece_length,
        )

    @patch("core.segment_extractor.SessionManager")
    def test_basic_extraction(self, mock_sm_cls, tmp_path):
        piece_length = 1024
        video = self._make_video(piece_length=piece_length, size=3072)

        handle = MagicMock()

        sm_instance = MagicMock()
        mock_sm_cls.return_value = sm_instance

        pieces_data = {
            0: b"A" * piece_length,
            1: b"B" * piece_length,
            2: b"C" * piece_length,
        }

        alerts = [MockReadPieceAlert(i, data) for i, data in pieces_data.items()]
        sm_instance.pop_alerts.side_effect = [alerts, []]

        output = str(tmp_path / "segment.bin")
        extract_segment(handle, video, [0, 1, 2], output, timeout=5)

        assert os.path.exists(output)
        assert os.path.getsize(output) == 3072

    @patch("core.segment_extractor.SessionManager")
    def test_trimming_with_offset(self, mock_sm_cls, tmp_path):
        piece_length = 1024
        offset = 512
        size = 1024
        video = self._make_video(piece_length=piece_length, offset=offset, size=size)

        handle = MagicMock()
        sm_instance = MagicMock()
        mock_sm_cls.return_value = sm_instance

        alerts = [
            MockReadPieceAlert(0, b"X" * piece_length),
            MockReadPieceAlert(1, b"Y" * piece_length),
        ]
        sm_instance.pop_alerts.side_effect = [alerts, []]

        output = str(tmp_path / "segment.bin")
        extract_segment(handle, video, [0, 1], output, timeout=5)

        assert os.path.exists(output)
        with open(output, "rb") as f:
            data = f.read()
        assert data[:512] == b"X" * 512
        assert data[512:] == b"Y" * 512

    @patch("core.segment_extractor.SessionManager")
    def test_no_data_raises(self, mock_sm_cls, tmp_path):
        video = self._make_video()
        handle = MagicMock()
        sm_instance = MagicMock()
        mock_sm_cls.return_value = sm_instance
        sm_instance.pop_alerts.return_value = []

        output = str(tmp_path / "segment.bin")
        with pytest.raises(RuntimeError, match="No piece data"):
            extract_segment(handle, video, [0, 1], output, timeout=0.1)


class TestCleanupSegment:
    def test_cleanup_existing(self, tmp_path):
        f = tmp_path / "test.bin"
        f.write_bytes(b"data")
        cleanup_segment(str(f))
        assert not f.exists()

    def test_cleanup_nonexistent(self, tmp_path):
        cleanup_segment(str(tmp_path / "nope.bin"))
