#!/usr/bin/env python3
"""Wrapper to run the S3 embedder using config.json."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from s3_embedder import S3Embedder

config_path = Path(__file__).parent.parent / "config.json"
with open(config_path) as f:
    config = json.load(f)

S3Embedder(config).sync()
