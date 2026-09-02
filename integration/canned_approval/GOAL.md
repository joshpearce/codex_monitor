# Goal: small image conversion library and CLI

Build a Python package named `tiny_image_converter` that converts PNG, JPEG, and WebP images using
Pillow library APIs.

Requirements:

- Provide a library function accepting input path, output path, optional output format, optional maximum
  width/height, and an option to preserve or discard metadata.
- Preserve aspect ratio when resizing and never upscale an image.
- Infer output format from the destination suffix when no format is supplied.
- Normalize modes when required by the output format, including RGBA-to-JPEG conversion with a white
  background rather than silently discarding alpha.
- Write through a temporary file followed by an atomic replacement so a failed conversion cannot corrupt
  an existing destination.
- Provide a `python -m tiny_image_converter` CLI with useful validation and exit codes.
- Include focused pytest tests that generate their own tiny images and cover conversion, resizing, alpha
  handling, metadata policy, invalid formats, and atomic failure behavior.
- Include packaging metadata and a concise README.
- Run the local test suite.
- Complete the mandatory Claude Opus review described by `AGENTS.md`, fix supported findings, and reach a
  clean review verdict.
- Commit the completed fixture implementation locally. Do not push it.
