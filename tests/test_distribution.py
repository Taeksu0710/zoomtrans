"""Release-specific regressions without models, network or audio changes."""
import queue
import tempfile
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import app_paths
from audio_capture import SystemAudioCapture
from setup_checks import check_setup, whisper_cached, download_whisper


class DistributionTests(unittest.TestCase):
    def test_source_and_frozen_data_roots(self):
        with patch("app_paths.bundled", return_value=False):
            self.assertEqual(app_paths.default_log_dir(), "logs")
        with patch("app_paths.bundled", return_value=True), patch("app_paths.Path.home", return_value=Path("/home/test")):
            self.assertEqual(app_paths.default_log_dir(), "/home/test/Library/Application Support/ZoomTrans/logs")
        self.assertTrue(app_paths.resource_path("assets/zoomtrans.png").is_file())

    def test_bundled_helper_never_compiles_or_writes(self):
        capture = SystemAudioCapture(queue.Queue())
        with patch("app_paths.bundled", return_value=True), patch("audio_capture.os.path.isfile", return_value=True), \
             patch("audio_capture.subprocess.run") as run:
            capture._ensure_switch_binary()
            run.assert_not_called()
        with patch("app_paths.bundled", return_value=True), patch("audio_capture.os.path.isfile", return_value=False):
            with self.assertRaisesRegex(RuntimeError, "다시 설치"):
                capture._ensure_switch_binary()

    def test_cache_check_requires_config_and_weights_and_never_goes_online(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch("huggingface_hub.snapshot_download", return_value=directory) as snapshot:
                self.assertFalse(whisper_cached("large-v3"))
                (root / "config.json").write_text("{}")
                (root / "weights.safetensors").write_bytes(b"test")
                self.assertTrue(whisper_cached("large-v3"))
                self.assertTrue(all(call.kwargs["local_files_only"] for call in snapshot.call_args_list))

    def test_missing_prerequisites_report_all_and_close_client(self):
        args = SimpleNamespace(device="system", model="gemma4:e4b", whisper_model="large-v3")
        with patch("sounddevice.query_devices", return_value=[]), \
             patch("ollama.Client") as client, patch("setup_checks.whisper_cached", return_value=False):
            client.return_value.list.side_effect = ConnectionError()
            result = check_setup(args)
            self.assertEqual(len(result.issues), 3)
            self.assertTrue(result.whisper_missing)
            client.return_value._client.close.assert_called_once()

    def test_ready_and_named_input_dont_require_blackhole(self):
        args = SimpleNamespace(device="custom-input", model="base", whisper_model="small")
        with patch("sounddevice.query_devices") as devices, patch("ollama.Client") as client, \
             patch("setup_checks.whisper_cached", return_value=True):
            client.return_value.list.return_value.models = [SimpleNamespace(model="base:latest")]
            self.assertEqual(check_setup(args).issues, ())
            devices.assert_not_called()

    def test_explicit_download_restores_offline_mode_on_failure(self):
        from huggingface_hub import constants
        previous = constants.HF_HUB_OFFLINE
        def fail(*args, **kwargs):
            self.assertFalse(constants.HF_HUB_OFFLINE)
            self.assertIn("weights.safetensors", kwargs["allow_patterns"])
            raise OSError("network unavailable")
        with patch("huggingface_hub.snapshot_download", side_effect=fail), \
             patch("huggingface_hub.utils._http.reset_sessions") as reset:
            with self.assertRaisesRegex(OSError, "network unavailable"):
                download_whisper("large-v3")
        self.assertEqual(constants.HF_HUB_OFFLINE, previous)
        self.assertEqual(reset.call_count, 2)


if __name__ == "__main__":
    unittest.main()
