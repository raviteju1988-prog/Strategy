#!/usr/bin/env python3
"""
Standalone runner for Ultimate Dump Detector
Run this file directly: python3 run.py
"""

import sys
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent))

from dump_detector.main import run

if __name__ == "__main__":
    run()
