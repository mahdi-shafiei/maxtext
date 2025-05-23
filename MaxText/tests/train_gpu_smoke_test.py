"""Copyright 2024 Google LLC

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

     https://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
"""

""" Smoke test """
import os
import unittest

from absl.testing import absltest

from MaxText.train import main as train_main
from MaxText.globals import PKG_DIR


class Train(unittest.TestCase):
  """Smoke test for GPUs."""

  def test_tiny_config(self):
    test_tmpdir = os.environ.get("TEST_TMPDIR")  # pylint: disable=unused-variable
    train_main(
        [
            None,
            os.path.join(PKG_DIR, "configs", "gpu_smoke_test.yml"),
            # pylint: disable=f-string-without-interpolation
            f"base_output_directory=gs://runner-maxtext-logs",
            "run_name=runner_test",
            r"dataset_path=gs://maxtext-dataset",
            "enable_checkpointing=False",
            rf"tokenizer_path={os.path.join(os.path.dirname(PKG_DIR), 'assets', 'tokenizer.llama2')}",
            "enable_goodput_recording=False",
            "enable_checkpoint_cloud_logger=False",
            "monitor_goodput=False",
        ]
    )


if __name__ == "__main__":
  absltest.main()
