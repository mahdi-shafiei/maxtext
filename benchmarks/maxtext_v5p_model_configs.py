"""
 Copyright 2025 Google LLC

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
"""Shared Benchmark config for v6e orchestrations."""

import os.path
from benchmarks import xla_flags_library
from benchmarks.maxtext_trillium_model_configs import MaxTextModel, _add_to_model_dictionary


v5p_model_dict = {}

deepseek_v3_ep_256_v5p_512  = _add_to_model_dictionary(
    v5p_model_dict,
    MaxTextModel(
        model_name="deepseek_v3_ep_256_v5p_512",
        model_type="deepseek3-671b",
        tuning_params={
            "per_device_batch_size": 4,
            "max_target_length": 8192,
            "ici_fsdp_parallelism": 1,
            "ici_expert_parallelism": -1,
            "remat_policy": "custom",
            "decoder_layer_input": "offload",
            "gcs_metrics": True,
            "use_iota_embed": True,
            "dataset_path": "gs://max-datasets-rogue",
            "dataset_type": "synthetic",
            "reuse_example_batch": 1,
            "enable_checkpointing": False,
            "skip_first_n_steps_for_profiler": 5,
            "profiler_steps": 5,
            "profiler": "xplane",
            "sa_block_q": 2048,
            "sa_block_q_dkv": 2048,
            "sa_block_q_dq": 2048,
            "megablox": False,
            "sparse_matmul": False,
            "capacity_factor": 1.0,
            "tokenizer_type": "huggingface",
            "tokenizer_path": "deepseek-ai/DeepSeek-V3",
            "dtype": "bfloat16",
            "opt_type": "adam_pax",
            "attention": "flash",
        },
        xla_flags=(
            xla_flags_library.MOE_VMEM_LIMIT_FLAG
            + xla_flags_library.CF_FOR_ALL_GATHER
            + xla_flags_library.DATA_PARALLEL_OVERLAP
        ),
    ),
)

c4_deepseek_v3_ep_256_v5p_512 = _add_to_model_dictionary(
    v5p_model_dict,
    MaxTextModel(
        model_name="c4_deepseek_v3_ep_256_v5p_512",
        model_type="deepseek3-671b",
        tuning_params={
            "per_device_batch_size": 4,
            "max_target_length": 8192,
            "ici_fsdp_parallelism": 1,
            "ici_expert_parallelism": -1,
            "remat_policy": "custom",
            "decoder_layer_input": "offload",
            "gcs_metrics": True,
            "use_iota_embed": True,
            "enable_checkpointing": True,
            "load_parameters_path": "gs://maxtext-model-checkpoints/deepseek3-671b/0/items",
            "dataset_path": "gs://max-datasets-rogue",
            "skip_first_n_steps_for_profiler": 5,
            "profiler_steps": 5,
            "profiler": "xplane",
            "sa_block_q": 2048,
            "sa_block_q_dkv": 2048,
            "sa_block_q_dq": 2048,
            "megablox": False,
            "sparse_matmul": False,
            "capacity_factor": 1.0,
            "dataset_type": "c4_mlperf",
            "dataset_name": "c4/en:3.0.7",
            "eval_dataset_name": "c4/en:3.0.9",
            "opt_type": "adam_pax",
            "tokenizer_type": "huggingface",
            "tokenizer_path": "deepseek-ai/DeepSeek-V3",
            "dtype": "bfloat16",
            "attention": "flash",
        },
        xla_flags=(
            xla_flags_library.MOE_VMEM_LIMIT_FLAG
            + xla_flags_library.CF_FOR_ALL_GATHER
            + xla_flags_library.DATA_PARALLEL_OVERLAP
        ),
    ),
)

llama4_scout_dropless_v5p_256  = _add_to_model_dictionary(
    v5p_model_dict,
    MaxTextModel(
        model_name="llama4_scout_dropless_v5p_256",
        model_type="llama4-17b-16e",
        tuning_params={
            "per_device_batch_size": 8,
            "max_target_length": 8192,
            "ici_fsdp_parallelism": -1,
            "enable_checkpointing": False,
            "dtype": "bfloat16",
            "weight_dtype": "float32",
            "megablox": True,
            "sparse_matmul": True,
            "dataset_type": "synthetic",
            "opt_type": "adamw",
            "skip_first_n_steps_for_profiler": 5,
            "profiler_steps": 3,
            "profiler": "xplane",
            "remat_policy": "custom",
            "decoder_layer_input": "offload",
            "reuse_example_batch": 1,
            "sa_block_q": 2048,
            "sa_block_kv": 2048,
            "sa_block_kv_compute": 2048,
            "sa_block_q_dkv": 2048,
            "sa_block_kv_dkv": 2048,
            "sa_block_kv_dkv_compute": 2048,
            "sa_block_q_dq": 2048,
            "sa_block_kv_dq": 2048,
            "tokenizer_path": "meta-llama/Llama-4-Scout-17B-16E",
        },
        xla_flags=(
            xla_flags_library.MOE_VMEM_LIMIT_FLAG
            + xla_flags_library.CF_FOR_ALL_GATHER
            + xla_flags_library.DATA_PARALLEL_OVERLAP
            + xla_flags_library.LAYOUT_FOR_ALL_REDUCE_SCATTER
            + xla_flags_library.HOST_OFFLOAD_FLAGS
        ),
    ),
)

llama4_maverick_dropless_v5p_256  = _add_to_model_dictionary(
    v5p_model_dict,
    MaxTextModel(
        model_name="llama4_maverick_dropless_v5p_256",
        model_type="llama4-17b-128e",
        tuning_params={
            "per_device_batch_size": 4,
            "max_target_length": 8192,
            "ici_fsdp_parallelism": 32,
            "ici_tensor_parallelism": 4,
            "enable_checkpointing": False,
            "dtype": "bfloat16",
            "weight_dtype": "float32",
            "megablox": True,
            "sparse_matmul": True,
            "dataset_type": "synthetic",
            "opt_type": "adamw",
            "skip_first_n_steps_for_profiler": 5,
            "profiler_steps": 3,
            "profiler": "xplane",
            "remat_policy": "custom",
            "decoder_layer_input": "offload",
            "out_proj": "offload",
            "query_proj": "offload",
            "key_proj": "offload",
            "value_proj": "offload",
            "reuse_example_batch": 1,
            "sa_block_q": 2048,
            "sa_block_kv": 2048,
            "sa_block_kv_compute": 2048,
            "sa_block_q_dkv": 2048,
            "sa_block_kv_dkv": 2048,
            "sa_block_kv_dkv_compute": 2048,
            "sa_block_q_dq": 2048,
            "sa_block_kv_dq": 2048,
            "tokenizer_path": "meta-llama/Llama-4-Maverick-17B-128E",
        },
        xla_flags=(
            xla_flags_library.MOE_VMEM_LIMIT_FLAG
            + xla_flags_library.CF_FOR_ALL_GATHER
            + xla_flags_library.DATA_PARALLEL_OVERLAP
            + xla_flags_library.LAYOUT_FOR_ALL_REDUCE_SCATTER
            + xla_flags_library.HOST_OFFLOAD_FLAGS
        ),
    ),
)
