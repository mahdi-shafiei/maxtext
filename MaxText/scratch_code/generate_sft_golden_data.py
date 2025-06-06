#  Copyright 2025 Google LLC
#
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at
#
#       https://www.apache.org/licenses/LICENSE-2.0
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.

"""Script to check correctness of `sft_trainer` in MaxText with `SFTTrainer` in TRL & generate golden data for `sft_trainer`.

Usage:

To check correctness for Llama2-7b:
  python3 -m MaxText.scratch_code.generate_sft_golden_data

To check correctness for other models:
  python3 -m MaxText.scratch_code.generate_sft_golden_data
    --model-name=deepseek2-16b \
    --tokenizer-path=deepseek-ai/DeepSeek-V2-Lite-chat \
    --model-ckpt-path=<MaxText-compatible checkpoint for the model>
"""

import argparse
import jax
import jsonlines
import os
import sys
import torch
from transformers import TrainingArguments, AutoModelForCausalLM, AutoTokenizer
from trl import SFTConfig, SFTTrainer

from MaxText import pyconfig
from MaxText.globals import PKG_DIR
from MaxText.tests.integration_tests.sft_trainer_correctness_test import get_maxtext_logits, get_token_log_probs, prepare_maxtext_inputs


DATA = {
    "messages": [
        {"role": "user", "content": "Hello, what is your name?"},
        {"role": "assistant", "content": "I am a chatbot. How can I help?"},
    ],
}


def initialize_maxtext_config(config):
  """Initializes configuration for MaxText."""
  cfg_with_ckpt = pyconfig.initialize(
      [sys.argv[0], os.path.join(PKG_DIR, "configs", "sft.yml")],
      run_name="compare_maxtext_with_trl_logits",
      model_name=config.model_name,
      tokenizer_path=config.tokenizer_path,
      enable_checkpointing=True,
      load_parameters_path=config.model_ckpt_path,
      max_target_length=32,
      per_device_batch_size=1,
      max_prefill_predict_length=16,
      dataset_type="synthetic",
      dtype="float32",
      matmul_precision="high",
      logits_dot_in_fp32=True,
  )

  cfg_without_ckpt = pyconfig.initialize(
      [sys.argv[0], os.path.join(PKG_DIR, "configs", "sft.yml")],
      run_name="generate_sft_golden_data",
      model_name="default",
      enable_checkpointing=False,
      max_target_length=32,
      per_device_batch_size=1,
      max_prefill_predict_length=16,
      dataset_type="synthetic",
      dtype="float32",
      matmul_precision="high",
      logits_dot_in_fp32=True,
  )
  return cfg_with_ckpt, cfg_without_ckpt


def get_hf_model(tokenizer_path):
  """Load model from Hugging Face."""
  return AutoModelForCausalLM.from_pretrained(
      tokenizer_path,
      torch_dtype=torch.float32,
  )


def get_tokenizer(tokenizer_path, max_target_length):
  """Get tokenizer from Hugging Face."""
  return AutoTokenizer.from_pretrained(
      tokenizer_path,
      add_bos_token=False,
      add_eos_token=False,
      model_max_length=max_target_length,
  )


def setup_sft_trainer(data, hf_model, tokenizer, max_target_length):
  """Setup SFT Trainer in TRL."""
  training_args = TrainingArguments(
      per_device_train_batch_size=1,
      bf16=True,
  )
  return SFTTrainer(
      model=hf_model,
      processing_class=tokenizer,
      train_dataset=data,
      data_collator=None,
      args=SFTConfig(
          dataset_kwargs={"skip_prepare_dataset": True},
          max_seq_length=max_target_length,
          **training_args.to_dict(),
      ),
  )


def prepare_trl_inputs(tokenizer_path, max_target_length):
  """Get tokenized inputs for TRL."""
  tokenizer = get_tokenizer(tokenizer_path, max_target_length)
  data_in_chat_format = tokenizer.apply_chat_template(DATA["messages"], tokenize=False)
  tokenized_data = tokenizer(data_in_chat_format, max_length=max_target_length, return_tensors="pt")

  # masking prompt tokens in labels
  prompt = DATA["messages"][0]
  prompt_in_chat_template = tokenizer.apply_chat_template([prompt], tokenize=True)
  labels = tokenized_data["input_ids"].clone()
  labels[0][: len(prompt_in_chat_template)] = -100  # -100 is the masking value in Hugging Face

  return {
      "input_ids": tokenized_data["input_ids"],
      "attention_mask": tokenized_data["attention_mask"],
      "labels": labels,
  }


def get_trl_logits(config, trl_data, max_target_length):
  """Get logits generated by TRL."""
  hf_model = get_hf_model(config.tokenizer_path)
  tokenizer = get_tokenizer(config.tokenizer_path, max_target_length)
  trl_trainer = setup_sft_trainer(trl_data, hf_model, tokenizer, max_target_length)
  _, trl_outputs = trl_trainer.compute_loss(hf_model, trl_data, return_outputs=True)
  trl_logits = trl_outputs.logits.detach().numpy()
  return trl_logits


def test_with_trl_and_save_golden_data(config):
  """Compare input data and logits generated by MaxText with TRL and save golden data."""

  maxtext_config_with_ckpt, maxtext_config_without_ckpt = initialize_maxtext_config(config)
  trl_data = prepare_trl_inputs(config.tokenizer_path, maxtext_config_with_ckpt.max_target_length)
  maxtext_data = prepare_maxtext_inputs(dict(DATA), maxtext_config_with_ckpt)

  # Compare input tokens generated by TRL and MaxText
  assert trl_data["input_ids"][0].tolist() == maxtext_data["inputs"][0].tolist()
  assert trl_data["attention_mask"][0].tolist() == maxtext_data["inputs_segmentation"][0].tolist()

  # Compare logits generated by TRL and MaxText
  trl_logits = get_trl_logits(config, trl_data, maxtext_config_with_ckpt.max_target_length)
  maxtext_logits = get_maxtext_logits(maxtext_config_with_ckpt, maxtext_data)
  assert jax.numpy.allclose(
      maxtext_logits[0],
      trl_logits,
      rtol=1e-05,
      atol=0.09,
      equal_nan=False,
  )

  # With MaxText's implementation verified, create a model without a checkpoint and save its per-token log probabilities
  maxtext_logits_no_ckpt = get_maxtext_logits(maxtext_config_without_ckpt, maxtext_data)
  token_log_probs = get_token_log_probs(maxtext_logits_no_ckpt, maxtext_data["inputs"])
  data_to_save = {
      "data": DATA,
      "tokens": maxtext_data["inputs"][0].tolist(),
      "attention_mask": maxtext_data["inputs_segmentation"][0].tolist(),
      "token_log_probs": token_log_probs[0].tolist(),
  }

  model_output_path = os.path.join(
      os.getcwd(), "MaxText", "test_assets", f"golden_data_sft_{maxtext_config_without_ckpt.model_name}.jsonl"
  )
  with jsonlines.open(model_output_path, "w") as f:
    f.write(data_to_save)


if __name__ == "__main__":
  jax.config.update("jax_default_prng_impl", "unsafe_rbg")
  if "xla_tpu_spmd_rng_bit_generator_unsafe" not in os.environ.get("LIBTPU_INIT_ARGS", ""):
    os.environ["LIBTPU_INIT_ARGS"] = os.environ.get("LIBTPU_INIT_ARGS", "") + " --xla_tpu_spmd_rng_bit_generator_unsafe=true"

  parser = argparse.ArgumentParser()
  parser.add_argument("--model-name", type=str, required=False, default="llama2-7b")
  parser.add_argument("--tokenizer-path", type=str, required=False, default="meta-llama/Llama-2-7b-chat-hf")
  parser.add_argument(
      "--model-ckpt-path", type=str, required=False, default="gs://maxtext-model-checkpoints/llama2-7b-chat/scanned/0/items"
  )

  trl_config = parser.parse_args(sys.argv[1:])
  test_with_trl_and_save_golden_data(trl_config)
