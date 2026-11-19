#!/usr/bin/env python
"""CLI for the pose-skeleton renderer.

The renderer itself lives in services/pose_skeleton.py so the app can author a skeleton mid-run
(ref_portraits.generate -> generate_portrait_image -> ComfyUI ControlNet) without shelling out.
This shim only fixes up sys.path for running it from a checkout.

    python scripts/pose_skeleton.py --legs 3 --arms 3 --out /tmp/xorn.png
    python scripts/pose_skeleton.py --clause "three-legged, three-armed" --out /tmp/xorn.png
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from services.pose_skeleton import main   # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
