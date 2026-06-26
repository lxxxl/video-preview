import os
import pytest
from unittest.mock import patch, MagicMock
from core.snapshot_generator import generate_snapshot, _jpeg_q, _run_ffmpeg


class TestJpegQ:
    def test_high_quality(self):
        q = _jpeg_q(85)
        assert 1 <= q <= 31

    def test_low_quality(self):
        q = _jpeg_q(10)
        assert q > _jpeg_q(90)

    def test_boundary_100(self):
        assert _jpeg_q(100) == 1

    def test_boundary_0(self):
        assert _jpeg_q(0) == 31


class TestRunFfmpeg:
    @patch("core.snapshot_generator.subprocess.run")
    def test_success(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0)
        assert _run_ffmpeg(["ffmpeg", "-version"], 30) is True

    @patch("core.snapshot_generator.subprocess.run")
    def test_failure(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1)
        assert _run_ffmpeg(["ffmpeg", "-invalid"], 30) is False

    @patch("core.snapshot_generator.subprocess.run")
    def test_timeout(self, mock_run):
        import subprocess
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="ffmpeg", timeout=30)
        assert _run_ffmpeg(["ffmpeg"], 30) is False

    @patch("core.snapshot_generator.subprocess.run")
    def test_not_found(self, mock_run):
        mock_run.side_effect = FileNotFoundError()
        assert _run_ffmpeg(["ffmpeg"], 30) is False


class TestGenerateSnapshot:
    @patch("core.snapshot_generator._strategy_input_seek")
    def test_first_strategy_succeeds(self, mock_strat, tmp_path):
        output = str(tmp_path / "snap.jpg")
        segment = str(tmp_path / "seg.bin")

        with open(segment, "wb") as f:
            f.write(b"fake video data")

        def side_effect(*args, **kwargs):
            with open(output, "wb") as f:
                f.write(b"\xff\xd8\xff" + b"\x00" * 100)
            return True

        mock_strat.side_effect = side_effect
        assert generate_snapshot(segment, output) is True
        assert os.path.exists(output)

    @patch("core.snapshot_generator._strategy_first_frame")
    @patch("core.snapshot_generator._strategy_extended_probe")
    @patch("core.snapshot_generator._strategy_output_seek")
    @patch("core.snapshot_generator._strategy_input_seek")
    def test_fallback_chain(self, mock1, mock2, mock3, mock4, tmp_path):
        output = str(tmp_path / "snap.jpg")
        segment = str(tmp_path / "seg.bin")

        with open(segment, "wb") as f:
            f.write(b"fake data")

        mock1.return_value = False
        mock2.return_value = False
        mock3.return_value = False

        def last_resort(*args, **kwargs):
            with open(output, "wb") as f:
                f.write(b"\xff\xd8\xff" + b"\x00" * 100)
            return True

        mock4.side_effect = last_resort
        assert generate_snapshot(segment, output) is True

    @patch("core.snapshot_generator._strategy_first_frame")
    @patch("core.snapshot_generator._strategy_extended_probe")
    @patch("core.snapshot_generator._strategy_output_seek")
    @patch("core.snapshot_generator._strategy_input_seek")
    def test_all_fail(self, mock1, mock2, mock3, mock4, tmp_path):
        output = str(tmp_path / "snap.jpg")
        segment = str(tmp_path / "seg.bin")

        with open(segment, "wb") as f:
            f.write(b"fake data")

        mock1.return_value = False
        mock2.return_value = False
        mock3.return_value = False
        mock4.return_value = False

        assert generate_snapshot(segment, output) is False
