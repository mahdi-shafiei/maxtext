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

"""Utils needed by multimodal pipelines for image processing."""

from dataclasses import dataclass
from typing import List, Tuple, Union, Optional
from collections import defaultdict
from itertools import groupby
import os

import numpy as np

from PIL import Image

import jax
import jax.numpy as jnp

NUM_IMAGE_CHANNELS = 3

# TODO(hengtaoguo): Move following constants to a separate file
# Constants for Gemma3-specific processing
GEMMA_DEFAULT_IMAGE_SIZE = 896
GEMMA_IMAGE_MEAN = (127.5,) * 3
GEMMA_IMAGE_STD = (127.5,) * 3
GEMMA_IMAGE_PLACEHOLDER_IN_PROMPT = "<start_of_image>"
GEMMA_BEGIN_IMAGE_TOKEN = 255999
GEMMA_END_IMAGE_TOKEN = 262144
GEMMA_NEW_LINE_TOKEN = 108
GEMMA_TOKEN_PLACEHOLDER = -2
# The number of GEMMA_TOKEN_PLACEHOLDER tokens per image in Gemma3
GEMMA_NUM_PLACEHOLDER_TOKENS_PER_IMAGE = 256
# +4 means 4 extra tokens to pad around image: \n\n, <start_of_image>, <end_of_image>, \n\n
# One MEDIA means one image or multiple images in one video, but now we only support one image
GEMMA_NUM_TOKENS_PER_MEDIA = GEMMA_NUM_PLACEHOLDER_TOKENS_PER_IMAGE + 4

# Constants for Llama4-specific processing
LLAMA4_TILE_SIZE = 336
LLAMA4_TILES_NUM = 16
LLAMA4_PIXEL_VALUE_RESCALE_FACTOR = 1.0 / 255.0
LLAMA4_IMAGE_MEAN = (0.5,) * 3
LLAMA4_IMAGE_STD = (0.5,) * 3
LLAMA4_PATCH_SIZE = 14
LLAMA4_IMAGE_PLACEHOLDER_IN_PROMPT = "<|image|>"
LLAMA4_FAKE_IMAGE_TOKEN = 200090  # <|image|>
LLAMA4_BEGIN_IMAGE_TOKEN = 200080  # <|image_start|>
LLAMA4_END_IMAGE_TOKEN = 200081  # <|image_end|>
LLAMA4_PATCH_TOKEN = 200092  # <|patch|>
LLAMA4_TILE_X_SEPARATOR_TOKEN = 200084  # <|tile_x_separator|>
LLAMA4_TILE_Y_SEPARATOR_TOKEN = 200085  # <|tile_y_separator|>
LLAMA4_PIXEL_SHUFFLE_RATIO = 0.5  # TODO(hengtaoguo): We should reuse config.pixel_shuffle_ratio_for_vit


@dataclass
class PreprocessorOutput:
  """Holds the output of an image preprocessor.

  Attributes:
    pixel_values: A JAX array containing the processed image pixel data.
                  The shape and format depend on the specific model and
                  preprocessing steps (e.g., [H, W, C] for Gemma3 or
                  [NUM_TILES, C, TILE_SIZE, TILE_SIZE] for Llama4).
    aspect_ratios: An optional JAX array of shape (batch_size, 2) representing
                   the aspect ratio [ratio_h, ratio_w] of the processed image(s).
                   This is particularly relevant for models like Llama4 that handle
                   images by tiling.
  """

  pixel_values: Optional[np.ndarray] = None
  aspect_ratios: Optional[np.ndarray] = None


def resize_image(image, model_name):
  image_sizes = {
    "gemma3": (GEMMA_DEFAULT_IMAGE_SIZE, GEMMA_DEFAULT_IMAGE_SIZE),
    "llama4": (LLAMA4_TILE_SIZE, LLAMA4_TILE_SIZE),
  }
  target_size = image_sizes[model_name.split('-')[0]]
  if target_size:
    image = image.resize(target_size)
  return image


def convert_to_RGB(image):
  if image.mode != "RGB":
    image = image.convert("RGB")
  return image


def load_image_from_path(image_path):
  """Loads an image from a given file path and returns a np.array."""
  if not os.path.isfile(image_path):
    raise FileNotFoundError(f"Image not found at path {image_path}. Please specify a valid image path")
  try:
    image = Image.open(image_path).convert("RGB")
    image.load()  # Load image data to catch errors early
    return np.asarray(image)

  except (IOError, OSError) as e:
    raise IOError(f"Error loading image from {image_path}") from e


def _normalize_images(images, mean, std):
  """Normalize the image to zero mean and unit variance.
  Change the image mean and std based on parameters mean and std.
  Args:
    images: The images to normalize.
    mean: tuple[float, float, float].
    std: tuple[float, float, float].
  Returns:
    The normalized images.
  """
  images -= np.asarray(mean)
  images /= np.asarray(std)
  return images


def get_factors(dividend: int):
  """
  Calculate all factors of a given number, i.e. a divisor that leaves
  no remainder. For example, if dividend=12, it will return {1, 2, 3, 4, 6, 12}.
  Args:
      dividend (int): The number to find factors for.
  Returns:
      set: A set containing all factors of the number.
  """
  factors_set = set()

  for i in range(1, int(dividend**0.5) + 1):
    if dividend % i == 0:
      factors_set.add(i)
      factors_set.add(dividend // i)
  return factors_set


def find_supported_resolutions(max_num_chunks=LLAMA4_TILES_NUM, patch_size=LLAMA4_TILE_SIZE):
  """Find all possible resolutions for the image based on the number of chunks."""
  asp_dict = defaultdict(list)
  for chunk_size in range(max_num_chunks, 0, -1):
    _factors = sorted(get_factors(chunk_size))
    _asp_ratios = [(factor, chunk_size // factor) for factor in _factors]
    for height, width in _asp_ratios:
      ratio_float = height / width
      asp_dict[ratio_float].append((height, width))

  # Get the resolutions multiplied by the patch_size
  possible_resolutions = []
  for _, value in asp_dict.items():
    for height, depth in value:
      possible_resolutions.append((height * patch_size, depth * patch_size))

  return possible_resolutions


def get_best_resolution(img_height, image_width, possible_resolutions, resize_to_max_canvas=False):
  """
  Get the best resolution for the image based on the possible resolutions.
  Args:
      img_height (int): The height of the image.
      image_width (int): The width of the image.
      possible_resolutions (list): A list of possible resolutions.
      resize_to_max_canvas (bool): Whether to resize to max canvas or not.
  Returns:
      tuple: The best resolution for the image.
  """
  if resize_to_max_canvas:
    return max(possible_resolutions, key=lambda x: x[0] * x[1])
  else:
    # Find the resolution closest to the original image dimensions (minimizing padding/cropping)
    return min(possible_resolutions, key=lambda x: abs(x[0] - img_height) + abs(x[1] - image_width))


def pad_to_best_fit_jax(
    images: np.ndarray,
    target_size: Tuple[int, int],
    background_color: Union[int, Tuple[int, ...]] = 0,
) -> np.ndarray:
  """
  Pads and/or crops an image or batch of images to a target size using JAX.
  If the image is larger than the target size, it's cropped from the top-left.
  If smaller, it's padded on the right and bottom.

  Args:
      images (np.ndarray):
          The images to process. Expected shape (..., H, W, C).
      target_size (Tuple[int, int]):
          The target (height, width).
      background_color (Union[int, Tuple[int, ...]], optional):
          The color to use for padding.
          If int, it's used for the first channel and subsequent channels are padded with 0.
          If tuple, its length must match the number of channels in the image.
          Defaults to 0.

  Returns:
      np.ndarray: The processed images of shape (..., target_height, target_width, C).
  """
  original_shape = images.shape
  num_dims = len(original_shape)

  if num_dims < 3:
    raise ValueError("Images tensor must have at least 3 dimensions (..., H, W, C)")

  img_height, img_width, num_channels = original_shape[-3], original_shape[-2], original_shape[-1]
  target_height, target_width = target_size

  # Prepare background_color_array: shape (C,)
  if isinstance(background_color, int):
    # Mimics the PyTorch version's behavior: [val, 0, 0, ...]
    bg_list = [background_color] + [0] * (num_channels - 1)
    background_color_array = np.array(bg_list, dtype=images.dtype)
  elif isinstance(background_color, (tuple, list)):
    if len(background_color) != num_channels:
      raise ValueError(
          f"background_color tuple/list length {len(background_color)} " f"must match number of channels {num_channels}"
      )
    background_color_array = np.array(background_color, dtype=images.dtype)
  else:
    raise TypeError("background_color must be int or tuple/list of ints")

  # Create the full target canvas filled with background colors
  batch_dims = original_shape[:-3]
  target_canvas_shape = batch_dims + (target_height, target_width, num_channels)

  # Reshape background_color_array for broadcasting
  # e.g., for (H,W,C) -> (1,1,C); for (B,H,W,C) -> (1,1,1,C)
  broadcastable_bg_shape = tuple([1] * len(batch_dims)) + (1, 1, num_channels)
  background_fill = np.reshape(background_color_array, broadcastable_bg_shape)

  padded_output = np.ones(target_canvas_shape, dtype=images.dtype) * background_fill

  # Determine the region of the original image to copy
  h_to_copy = min(img_height, target_height)
  w_to_copy = min(img_width, target_width)

  # Create slices for selecting the part of the original image
  src_slicer_dims = []
  for _ in batch_dims:
    src_slicer_dims.append(slice(None))  # Ellipsis for batch dimensions
  src_slicer_dims.extend([slice(0, h_to_copy), slice(0, w_to_copy), slice(None)])

  image_data_to_place = images[tuple(src_slicer_dims)]

  # Create slices for placing the image data onto the canvas
  dest_slicer_dims = []
  for _ in batch_dims:
    dest_slicer_dims.append(slice(None))  # Ellipsis for batch dimensions
  dest_slicer_dims.extend([slice(0, h_to_copy), slice(0, w_to_copy), slice(None)])

  padded_output[tuple(dest_slicer_dims)] = image_data_to_place

  return padded_output


def split_to_tiles(images: np.ndarray, num_tiles_height: int, num_tiles_width: int) -> np.ndarray:
  """
  Splits an image tensor into tiles using JAX.

  Args:
      images: The input image tensor with shape (batch_size, num_channels, height, width).
      num_tiles_height: The number of tiles along the height dimension.
      num_tiles_width: The number of tiles along the width dimension.

  Returns:
      The tiled image tensor with shape:
      (batch_size * num_tiles_height * num_tiles_width, num_channels, height // num_tiles_height, width // num_tiles_width).
  """
  images = np.transpose(images, (2, 0, 1))  # Change to (num_channels, height, width)
  num_channels, height, width = images.shape

  # Ensure the image dimensions are divisible by the number of tiles
  if height % num_tiles_height != 0 or width % num_tiles_width != 0:
    raise ValueError("Image dimensions must be divisible by the number of tiles.")

  # Reshape to introduce tile dimensions
  reshaped = np.reshape(
      images,
      (
          num_channels,
          num_tiles_height,
          height // num_tiles_height,
          num_tiles_width,
          width // num_tiles_width,
      ),
  )

  # Permute dimensions to group tiles together
  permuted = np.transpose(reshaped, (1, 3, 0, 2, 4))

  # Reshape to combine batch and tile dimensions
  tiled_images = np.reshape(
      permuted,
      (
          num_tiles_height * num_tiles_width,
          num_channels,
          height // num_tiles_height,
          width // num_tiles_width,
      ),
  )

  return tiled_images


def pre_process_gemma3_image(image):
  """Performs a bi-linear resize (with anti-aliasing) and normalizes the image."""
  target_size = (GEMMA_DEFAULT_IMAGE_SIZE, GEMMA_DEFAULT_IMAGE_SIZE)
  pil_img = Image.fromarray(image)
  resample_method = Image.Resampling.BILINEAR
  # Use a higher quality downsampling filter to approximate antialias=True
  if pil_img.size[0] > target_size[0] or pil_img.size[1] > target_size[1]:
    resample_method = Image.Resampling.LANCZOS
  resized_pil_img = pil_img.resize(target_size, resample=resample_method)
  image = np.asarray(resized_pil_img, dtype=np.float32)
  image = _normalize_images(image, mean=GEMMA_IMAGE_MEAN, std=GEMMA_IMAGE_STD)
  image = np.clip(image, -1, 1)
  processor_output = PreprocessorOutput(
      pixel_values=image,
  )
  return processor_output


def pre_process_llama4_image(image):
  """
  Pre-process image for Llama4 model. Find best resolution and split into tiles with an additional global tile.
  Original implementation from image_processing_llama4.py: http://shortn/_VXLgQ1lmkz
  Args:
    image: The np.array image [H, W, C] to pre-process.
  Returns:
    The pre-processed image in np.array [NUM_TILES, C, TILE_SIZE, TILE_SIZE].
  Example:
    image of (536, 640, 3), its best_resolution = (672, 672), image split into 4 tiles of (336, 336)
    Additional global tile of (336, 336) is added, and the final output image_tiles is (5, 3, 336, 336).
  """
  # Find the best resolution canvas for the image
  possible_resolutions = find_supported_resolutions(max_num_chunks=LLAMA4_TILES_NUM, patch_size=LLAMA4_TILE_SIZE)
  best_resolution = get_best_resolution(
      img_height=image.shape[0],
      image_width=image.shape[1],
      possible_resolutions=possible_resolutions,
      resize_to_max_canvas=False,
  )

  # Pad the image to the best resolution and normalize it
  image_padded = pad_to_best_fit_jax(image, best_resolution)
  image_normalized = _normalize_images(
      images=image_padded * LLAMA4_PIXEL_VALUE_RESCALE_FACTOR,
      mean=LLAMA4_IMAGE_MEAN,
      std=LLAMA4_IMAGE_STD,
  )

  # Split the image into tiles
  ratio_h, ratio_w = (
      best_resolution[0] // LLAMA4_TILE_SIZE,
      best_resolution[1] // LLAMA4_TILE_SIZE,
  )
  image_tiles = split_to_tiles(image_normalized, ratio_h, ratio_w)

  # If more than one tile, add a global tile by resizing the image to the tile size
  if ratio_h * ratio_w > 1:
    pil_img = Image.fromarray(image)
    resample_method = Image.Resampling.BILINEAR
    # Use a higher quality downsampling filter to approximate antialias=True
    if pil_img.size[0] > LLAMA4_TILE_SIZE or pil_img.size[1] > LLAMA4_TILE_SIZE:
      resample_method = Image.Resampling.LANCZOS
    global_tiles_pil = pil_img.resize((LLAMA4_TILE_SIZE, LLAMA4_TILE_SIZE), resample=resample_method)
    global_tiles = np.array(global_tiles_pil)
    global_tiles = _normalize_images(
        global_tiles * LLAMA4_PIXEL_VALUE_RESCALE_FACTOR, mean=LLAMA4_IMAGE_MEAN, std=LLAMA4_IMAGE_STD
    )
    global_tiles = np.transpose(global_tiles, (2, 0, 1))
    global_tiles = np.expand_dims(global_tiles, axis=0)
    image_tiles = np.concatenate((image_tiles, global_tiles), axis=0)

  # TODO(hengtaoguo): Add support for multiple images with aspect ratios size of [num_images, 2]
  aspect_ratios_array = np.array([[ratio_h, ratio_w]], dtype=np.int32)
  processor_output = PreprocessorOutput(
      pixel_values=image_tiles,
      aspect_ratios=aspect_ratios_array,
  )
  return processor_output


def pre_process_image(image, model_name):
  """Pre-process image according to different model's requirements.
  Args:
    image: The np.array image [H, W, C] to pre-process.
    model_name: The config.model_name that specifies the image preprocess ways.
  Returns:
    The pre-processed image in np.array [H, W, C].
  """
  if model_name in ["gemma3-4b", "gemma3-12b", "gemma3-27b"]:
    return pre_process_gemma3_image(image)
  elif model_name in ["llama4-17b-16e", "llama4-17b-128e"]:
    return pre_process_llama4_image(image)
  else:
    raise ValueError(f"Model {model_name} does not support multimodal inference.")


def reformat_prompt(prompt, image_placeholder, model_name):
  """Reformat prompt for different models."""
  if model_name in ["gemma3-4b", "gemma3-12b", "gemma3-27b"]:
    if image_placeholder in prompt:
      prompt = prompt.replace(image_placeholder, GEMMA_IMAGE_PLACEHOLDER_IN_PROMPT)
    if not GEMMA_IMAGE_PLACEHOLDER_IN_PROMPT in prompt:
      prompt = GEMMA_IMAGE_PLACEHOLDER_IN_PROMPT + prompt
    formatted_prompt = f"<start_of_turn>user\n{prompt}<end_of_turn>\n<start_of_turn>model\n"
    return formatted_prompt
  elif model_name in ["llama4-17b-16e", "llama4-17b-128e"]:
    if image_placeholder in prompt:
      prompt =prompt.replace(image_placeholder, LLAMA4_IMAGE_PLACEHOLDER_IN_PROMPT)
    if not LLAMA4_IMAGE_PLACEHOLDER_IN_PROMPT in prompt:
      prompt = LLAMA4_IMAGE_PLACEHOLDER_IN_PROMPT + prompt
    formatted_prompt = (
        f"<|begin_of_text|><|header_start|>user<|header_end|>\n\n{prompt}<|eot|><|header_start|>assistant<|header_end|>\n\n"
    )
    return formatted_prompt
  else:
    return prompt


def reformat_response(response, model_name):
  """Reformat response for different models."""
  if model_name in ["llama4-17b-16e", "llama4-17b-128e"]:
    formatted_response = f"{response}<|eot|>"
    return formatted_response
  else:
    return response


def get_image_offsets(model_name, processor_output: PreprocessorOutput | None):
  """Get the increase in total token count after inserting image token placeholders"""
  if model_name in ["gemma3-4b", "gemma3-12b", "gemma3-27b"]:
    return GEMMA_NUM_TOKENS_PER_MEDIA - 1  # -1 because <start_of_image> is already present in the input tokens.
  if model_name in ["llama4-17b-16e", "llama4-17b-128e"]:
    assert processor_output is not None, "Processor output must be provided for Llama4 image fusion."
    assert processor_output.aspect_ratios is not None, "Aspect ratio must be provided for Llama4 image fusion."
    image_height, image_width = LLAMA4_TILE_SIZE, LLAMA4_TILE_SIZE
    downsample_ratio = int(round(1.0 / (LLAMA4_PIXEL_SHUFFLE_RATIO**2)))
    num_patches_per_chunk = int((image_height // LLAMA4_PATCH_SIZE) * (image_width // LLAMA4_PATCH_SIZE) // downsample_ratio)
    num_images = processor_output.aspect_ratios.shape[0]
    image_tokens_count = 0
    for image_index in range(num_images):
      image_tokens_count += get_num_tokens_for_this_image(processor_output.aspect_ratios[image_index], num_patches_per_chunk)
    images_offsets = image_tokens_count - num_images
    return images_offsets  # -num_images because replacing every <|image|> tokens.
  else:
    return 0


def prepare_text_for_image_fusion(texts, model_name, processor_output=None):
  if model_name in ["gemma3-4b", "gemma3-12b", "gemma3-27b"]:
    return add_extra_tokens_for_images_gemma3(texts)
  if model_name in ["llama4-17b-16e", "llama4-17b-128e"]:
    return add_extra_tokens_for_images_llama4(texts, processor_output)
  else:
    raise ValueError(f"Model {model_name} does not support multimodal inference.")


def add_extra_tokens_for_images_llama4(tokens, processor_output: PreprocessorOutput):
  """Add the extra image tokens to the text tokens for Llama4."""
  if not isinstance(tokens, list):
    tokens = tokens.tolist()

  grouped = groupby(tokens, lambda x: x == 200090)

  sublists = []
  for is_splitter, group in grouped:
    if not is_splitter:  # If the group does NOT consist of the split_value
      sublists.append(list(group))

  aspect_ratio = processor_output.aspect_ratios
  assert aspect_ratio is not None, "Aspect ratio must be provided for Llama4 image fusion."

  new_tokens = []

  image_height, image_width = LLAMA4_TILE_SIZE, LLAMA4_TILE_SIZE
  downsample_ratio = int(round(1.0 / (LLAMA4_PIXEL_SHUFFLE_RATIO**2)))
  num_patches_per_chunk = int((image_height // LLAMA4_PATCH_SIZE) * (image_width // LLAMA4_PATCH_SIZE) // downsample_ratio)

  image_index = 0
  for local_image_index, split_part in enumerate(sublists):
    new_tokens += split_part  # Add the sublist
    if local_image_index < aspect_ratio.shape[0]:
      new_tokens += get_tokens_for_this_image(aspect_ratio[image_index], num_patches_per_chunk)
      image_index += 1
  new_tokens_np = np.array(new_tokens, dtype=np.int32)
  return new_tokens_np


def get_tokens_for_this_image(this_aspect_ratio, num_patches_per_chunk):
  """Constructs the token sequence for a single image in Llama4.
  This function generates a list of special tokens that represent an image,
  including its tiled structure (if applicable) and a global representation.
  The sequence includes:
  - A beginning-of-image token.
  - Patch tokens for each local tile, interspersed with tile separators
    if the image is divided into multiple tiles (ratio_h * ratio_w > 1).
  - A fake image token placeholder for the global image representation.
  - Patch tokens associated with the global image representation.
  - An end-of-image token.

  Args:
    this_aspect_ratio: A tuple (ratio_h, ratio_w) representing the number
                       of tiles along the height and width dimensions for
                       the current image.
    num_patches_per_chunk: The number of patch tokens to use for each
                           image tile (both local and global).

  Returns:
    A list of integer token IDs representing the image.

  Example:
    If `this_aspect_ratio` is [2, 2] and `num_patches_per_chunk` is 4,
    the output will be:
    [
      LLAMA4_BEGIN_IMAGE_TOKEN,
      LLAMA4_PATCH_TOKEN, LLAMA4_PATCH_TOKEN, LLAMA4_PATCH_TOKEN, LLAMA4_PATCH_TOKEN,
      LLAMA4_TILE_X_SEPARATOR_TOKEN,
      LLAMA4_PATCH_TOKEN, LLAMA4_PATCH_TOKEN, LLAMA4_PATCH_TOKEN, LLAMA4_PATCH_TOKEN,
      LLAMA4_TILE_Y_SEPARATOR_TOKEN,
      LLAMA4_PATCH_TOKEN, LLAMA4_PATCH_TOKEN, LLAMA4_PATCH_TOKEN, LLAMA4_PATCH_TOKEN,
      LLAMA4_TILE_X_SEPARATOR_TOKEN,
      LLAMA4_PATCH_TOKEN, LLAMA4_PATCH_TOKEN, LLAMA4_PATCH_TOKEN, LLAMA4_PATCH_TOKEN,
      LLAMA4_TILE_Y_SEPARATOR_TOKEN,
      LLAMA4_FAKE_IMAGE_TOKEN,
      LLAMA4_PATCH_TOKEN, LLAMA4_PATCH_TOKEN, LLAMA4_PATCH_TOKEN, LLAMA4_PATCH_TOKEN,
      LLAMA4_END_IMAGE_TOKEN
    ], total 27 tokens.
  """

  img_tokens = [LLAMA4_BEGIN_IMAGE_TOKEN]
  ratio_h, ratio_w = this_aspect_ratio
  if ratio_h * ratio_w > 1:
    for _ in range(ratio_h):
      for xx in range(ratio_w):
        img_tokens += [LLAMA4_PATCH_TOKEN] * num_patches_per_chunk
        if xx < ratio_w - 1:
          img_tokens += [LLAMA4_TILE_X_SEPARATOR_TOKEN]

      img_tokens += [LLAMA4_TILE_Y_SEPARATOR_TOKEN]

  img_tokens += [LLAMA4_FAKE_IMAGE_TOKEN]
  img_tokens += [LLAMA4_PATCH_TOKEN] * num_patches_per_chunk
  img_tokens += [LLAMA4_END_IMAGE_TOKEN]

  return img_tokens


def get_num_tokens_for_this_image(this_aspect_ratio, num_patches_per_chunk):
  """This function computes the length of the token sequence that would be generated by
  `get_tokens_for_this_image`, without explicit loops.

  Args:
    aspect_ratio: A tuple (ratio_h, ratio_w) representing the number of tiles
                  along height and width.
    num_patches_per_chunk: The number of patch tokens per image tile.

  Returns:
    The total number of tokens for the image representation.
  """
  ratio_h, ratio_w = this_aspect_ratio

  # Basic tokens: <|image_start|>, <|image|> (global image placeholder), <|image_end|>
  # Plus global patch tokens associated with the <|image|> placeholder.
  num_img_tokens = 3 + num_patches_per_chunk

  if ratio_h * ratio_w > 1:
    # Additional tokens for local tiles if the image is split into more than one tile:
    # - Patch tokens for each local tile: ratio_h * ratio_w * num_patches_per_chunk
    # - Separator tokens (TILE_X_SEPARATOR_TOKEN and TILE_Y_SEPARATOR_TOKEN):
    #   TILE_X_SEPARATOR_TOKEN count: ratio_h * (ratio_w - 1)
    #   TILE_Y_SEPARATOR_TOKEN count: ratio_h
    #   Total separator tokens: ratio_h * ratio_w
    num_img_tokens += ratio_h * ratio_w * (num_patches_per_chunk + 1)

  return int(num_img_tokens)


def add_extra_tokens_for_images_gemma3(
    tokens: np.ndarray | jnp.ndarray,
    *,
    max_num_images: int = 1,
):  # -> Int['B L+(max_num_images * (num_tokens_per_image + 3))']:
  r"""Add the extra image tokens to the text tokens.

  If the model has images, we expand each `<start_of_image>` token by the image
  placeholder tokens.

  Example:

  ```python
  input = [..., x, <start_of_image>, y, ...]
  output = [
      ..., x, \n\n, <start_of_image>, SOFT_TOKEN_PLACEHOLDER,
      SOFT_TOKEN_PLACEHOLDER, ..., SOFT_TOKEN_PLACEHOLDER,
      SOFT_TOKEN_PLACEHOLDER, <end_of_image>, \n\n, y, ...
  ]
  ```

  The `\n\n` tokens are added to match how the model was trained.

  Args:
    tokens: The text tokens.
    max_num_images: The maximum number of images in the batch.
    num_tokens_per_image: The number of soft tokens per image.

  Returns:
    The text tokens with the extra image tokens.
  """

  # New tokens which will be inserted for each image.
  mm_tokens = [
      GEMMA_NEW_LINE_TOKEN,
      GEMMA_BEGIN_IMAGE_TOKEN,
      *[GEMMA_TOKEN_PLACEHOLDER] * GEMMA_NUM_PLACEHOLDER_TOKENS_PER_IMAGE,
      GEMMA_END_IMAGE_TOKEN,
      GEMMA_NEW_LINE_TOKEN,
  ]

  return insert_sequence(
      at=GEMMA_BEGIN_IMAGE_TOKEN,
      sequence=mm_tokens,
      tokens=tokens,
      max_num_images=max_num_images,
  )


def insert_sequence(
    tokens: np.ndarray | jnp.ndarray,
    *,
    at: int,
    sequence: List[int],
    max_num_images: int,
) -> np.ndarray | jnp.ndarray:
  """Insert a sequence of tokens at a given position."""
  tokens_dim = len(tokens.shape)
  if tokens_dim == 1:
    tokens = tokens[None, :]
  _, length = tokens.shape

  mm_tokens = jnp.array(sequence, dtype=jnp.int32)

  # `-1` because `<start_of_image>` is already present in the input tokens.
  offset_by = len(mm_tokens) - 1

  # Maximum length, if all images are present.
  length_with_mm = length + max_num_images * offset_by

  mm_start = tokens == at

  # Get the text tokens correctly placed at their final position.
  # The `<start_of_image>` are removed and expanded to leave space for the MM
  # tokens.
  # tokens = [..., x, <start_of_image>, y, ...]
  # new_text_tokens = [..., x, 0, 0, 0, ..., 0, 0, 0, y, ...]
  new_text_tokens = _get_new_text_tokens(
      mm_start=mm_start,
      text_tokens=tokens,
      offset_by=offset_by,
      length_with_mm=length_with_mm,
  )

  # Get the mm tokens placeholders, correctly placed at their final position.
  # new_mm_tokens = [
  #     ..., 0, 0, \n\n, <start_of_image>, ..., <end_of_image>, \n\n, 0, 0, ...
  # ]
  new_mm_tokens = _get_new_mm_tokens(
      mm_start=mm_start,
      mm_tokens_to_insert=mm_tokens,
      max_num_images=max_num_images,
      offset_by=offset_by,
      length_with_mm=length_with_mm,
  )

  # Merge the text and MM tokens.
  new_tokens = new_text_tokens + new_mm_tokens
  if tokens_dim < len(new_tokens.shape):
    new_tokens = jnp.squeeze(new_tokens)
  return new_tokens


def _get_new_text_tokens(
    *,
    mm_start: np.ndarray | jnp.ndarray,
    text_tokens: np.ndarray | jnp.ndarray,
    offset_by: int,
    length_with_mm: int,
) -> np.ndarray | jnp.ndarray:
  """Get new text tokens."""
  # Jax vmap does not support positional arguments, so need the
  # _get_new_text_tokens_inner indirection.
  return jax.vmap(_get_new_text_tokens_inner, in_axes=(0, 0, None, None))(mm_start, text_tokens, offset_by, length_with_mm)


def _get_new_text_tokens_inner(
    mm_start: np.ndarray | jnp.ndarray,
    text_tokens: np.ndarray | jnp.ndarray,
    offset_by: int,
    length_with_mm: int,
) -> np.ndarray | jnp.ndarray:
  """`_get_new_text_tokens_positions` without batch dimension."""

  # Empty buffer in which text and MM tokens will be inserted.
  tokens_with_mm = jnp.zeros((length_with_mm,), dtype=jnp.int32)

  # Shift the original tokens, so that the new soft tokens can be inserted.
  new_text_tokens_pos = _get_new_text_tokens_positions(
      offset_on=mm_start,
      offset_by=offset_by,
  )

  tokens_with_mm = tokens_with_mm.at[new_text_tokens_pos].set(text_tokens)

  # Remove the `<start_of_image>` tokens (will be added afterwards when
  # merging with `_get_new_mm_tokens`).
  first_mm_pos = tokens_with_mm[0]
  new_start_mm_pos = new_text_tokens_pos * mm_start
  tokens_with_mm = tokens_with_mm.at[new_start_mm_pos].set(0)
  tokens_with_mm = tokens_with_mm.at[0].set(first_mm_pos)

  return tokens_with_mm


def _get_new_text_tokens_positions(
    *,
    offset_on: np.ndarray | jnp.ndarray,
    offset_by: int,
) -> np.ndarray | jnp.ndarray:
  """Create the positions of the new tokens.

  Input: `[x, x, x, offset_on, x, x, offset_on, x]`
  Output: `[0, 1, 2, 3, 4+Offset, 5+Offset, 6+Offset, 7+Offset^2]`

  Args:
    offset_on: The token to offset on.
    offset_by: The number of tokens to offset by.

  Returns:
    The new positions of the tokens.
  """
  offset = jnp.cumsum(offset_on, axis=-1) * offset_by
  new_positions = jnp.arange(offset_on.shape[-1]) + offset
  # Do not shift the `<start_of_image>` token, it will be overwritten by the MM
  # tokens.
  new_positions -= offset_by * offset_on
  return new_positions


def _get_new_mm_tokens(
    *,
    mm_start: np.ndarray | jnp.ndarray,
    mm_tokens_to_insert: np.ndarray | jnp.ndarray,
    max_num_images: int,
    offset_by: int,
    length_with_mm: int,
) -> np.ndarray | jnp.ndarray:
  """batch dimension inclusive new mm_tokens"""
  # Jax vmap does not support positional argiments, so need the
  # _get_new_mm_tokens_inner indirection.
  return jax.vmap(_get_new_mm_tokens_inner, in_axes=(0, None, None, None, None))(
      mm_start, mm_tokens_to_insert, max_num_images, offset_by, length_with_mm
  )


def _get_new_mm_tokens_inner(
    mm_start: np.ndarray | jnp.ndarray,
    mm_tokens_to_insert: np.ndarray | jnp.ndarray,
    max_num_images: int,
    offset_by: int,
    length_with_mm: int,
) -> np.ndarray | jnp.ndarray:
  """`_get_new_mm_tokens` without batch dimension."""
  # Empty buffer row, which will be merged with the final tokens.
  row = jnp.zeros((length_with_mm,), dtype=jnp.int32)

  ones = jnp.ones((len(mm_tokens_to_insert),), dtype=jnp.int32)

  (offset,) = jnp.nonzero(mm_start, size=max_num_images)

  # Because not all elements in the batch do have the same number of images
  # we need to mask out the `offset == 0` values.
  # This means that `<start_of_images>` can never be the first token, but this
  # should never happen in practice as sequences should start by `<bos>`
  mask = offset != 0
  mask = jnp.einsum("...x,y->xy", mask, ones)

  # After the mask is created, offset each individual images
  offset += jnp.arange(len(offset)) * offset_by

  new_positions = jnp.einsum("x,y->xy", offset, ones)
  new_positions += jnp.arange(len(mm_tokens_to_insert))

  new_positions = new_positions * mask

  # Because not all elements in the batch do have the same number of images
  # we need to mask out the `offset == 0` values.
  # This means that `<start_of_images>` can never be the first token, but this
  # should never happen in practice as sequences should start by `<bos>`
  row = row.at[new_positions].set(mm_tokens_to_insert)
  row = row.at[0].set(0)
  return row


def merge_mm_embeddings(
    text_embeddings: np.ndarray | jnp.ndarray,
    vision_embeddings: np.ndarray | jnp.ndarray,
    mask,
) -> np.ndarray | jnp.ndarray:
  """Merge the text and MM tokens.

  Args:
    tokens: The text tokens.
    mm_tokens: The MM tokens.

  Returns:
    The merged tokens.
  """
  return jax.vmap(_merge_mm_embeddings_inner, in_axes=(0, 0, 0))(text_embeddings, vision_embeddings, mask)


def _merge_mm_embeddings_inner(text_embeddings, vision_embeddings, mask):
  """`merge_embeddings` without batch dimension."""

  # Rearrange the vision embeddings from [num_images, num_toks_per_image, d] to [num_images * num_toks_per_image, d]
  num_images, num_toks_per_image, d = vision_embeddings.shape
  vision_embeddings = jnp.reshape(vision_embeddings, (num_images * num_toks_per_image, d))

  # len(vision_embeddings) == max_num_images * num_tokens_per_image
  target_pos = jnp.nonzero(mask, size=len(vision_embeddings))

  # Save and restore the first position overwritten if there's no MM tokens.
  first_pos = text_embeddings[0]

  merged = text_embeddings.at[target_pos, :].set(vision_embeddings)

  merged = merged.at[0].set(first_pos)

  return merged
