"""Draw an OpenPose-style skeleton that the SDXL openpose ControlNet will follow.

The point of this file
----------------------
Text alone does not control limb COUNT. "three-legged, three-armed" gets read by the
diffusion model as "leg-ish, arm-ish" and it draws a biped; measured on the Xorn, four
fresh rolls came back 2 legs / 2 arms every time, and the portrait verifier could only
notice the failure, never fix it. Geometry is the only lever that works: draw a skeleton
with three legs and three arms, hand it to a ControlNet, and the count is not a request
any more, it is a constraint.

Why we draw it ourselves instead of using a pose preprocessor (DWPose / OpenPose estimator)
------------------------------------------------------------------------------------------
A preprocessor *estimates* a pose from a photo, so it can only ever produce the anatomy in
that photo — 2 arms, 2 legs. The creatures that need fixing are exactly the ones no photo
has. We author the keypoints directly, which means a three-legged, three-armed Xorn is a
parameter, not a lucky roll.

Format
------
The SDXL openpose ControlNets (xinsir/controlnet-openpose-sdxl-1.0, thibaud's original)
were trained on OpenPose's 18-keypoint BODY render: coloured limb lines on a black field,
a dot at every joint, colours decided by keypoint index. Index order:

    0 nose, 1 neck, 2 R shoulder, 3 R elbow, 4 R wrist, 5 L shoulder, 6 L elbow,
    7 L wrist, 8 R hip, 9 R knee, 10 R ankle, 11 L hip, 12 L knee, 13 L ankle,
    14 R eye, 15 L eye, 16 R ear, 17 L ear

Anything past 18 (a third arm's elbow and wrist, a third leg's knee and ankle) is
invented — there is no canonical colour for it — so we continue the same hue ramp. The
ControlNet only needs "a limb-shaped line here", and colour bleeding between two limbs is
what actually hurts it, so consecutive invented keypoints get well-separated colours.

CLI
---
    python scripts/pose_skeleton.py --legs 3 --arms 3 --out /tmp/xorn.png
    python scripts/pose_skeleton.py --clause "three-legged, three-armed, one-eyed" --out /tmp/x.png
    python scripts/pose_skeleton.py --legs 4 --arms 0 --quadruped --out /tmp/horse.png
"""
from __future__ import annotations

import argparse
import math
import os
import sys

try:
    from PIL import Image, ImageDraw
except ImportError:  # pragma: no cover - PIL ships with the app's own venv
    raise SystemExit("Pillow is required: pip install pillow") from None

#: OpenPose's own BODY_18 palette, one entry per keypoint index.
_OPENPOSE_COLORS = [
    (255, 0, 0), (255, 85, 0), (255, 170, 0), (255, 255, 0), (170, 255, 0),
    (85, 255, 0), (0, 255, 0), (0, 255, 85), (0, 255, 170), (0, 255, 255),
    (0, 170, 255), (0, 85, 255), (0, 0, 255), (85, 0, 255), (170, 0, 255),
    (255, 0, 255), (255, 0, 170), (255, 0, 85),
]
#: Invented keypoints (limb 3+, eyes past the first pair) cycle this, kept far from the
#: neighbours they are drawn next to.
_EXTRA_COLORS = [(0, 255, 120), (255, 120, 0), (120, 200, 255), (200, 0, 255)]


class Pose:
    """A bag of keypoints plus the bones connecting them, in normalised 0..1 coordinates."""

    def __init__(self) -> None:
        self.points: dict[int, tuple[float, float]] = {}
        self.bones: list[tuple[int, int]] = []
        self._next = 18          # canonical BODY_18 indices are reserved

    # -- keypoint helpers ------------------------------------------------------------------
    def add(self, x: float, y: float, index: int | None = None) -> int:
        if index is None:
            index = self._next
            self._next += 1
        self.points[index] = (float(x), float(y))
        return index

    def bone(self, parent: int, child: int) -> None:
        self.bones.append((int(parent), int(child)))

    def limb(self, parent: int, *joints: tuple[float, float]) -> list[int]:
        """Extend a chain from `parent` through the given points, one bone per hop."""
        prev = parent
        made = []
        for x, y in joints:
            idx = self.add(x, y)
            self.bone(prev, idx)
            made.append(idx)
            prev = idx
        return made

    # -- rendering -------------------------------------------------------------------------
    def render(self, width: int = 832, height: int = 1216, line: int = 6, dot: int = 5) -> Image.Image:
        """Black field, coloured limb lines, a dot at every joint — the ControlNet's food."""
        img = Image.new("RGB", (width, height), (0, 0, 0))
        draw = ImageDraw.Draw(img)
        px = lambda p: (p[0] * width, p[1] * height)          # noqa: E731 - tiny, local
        for parent, child in self.bones:
            if parent not in self.points or child not in self.points:
                continue
            colour = _color_for(child)
            draw.line([px(self.points[parent]), px(self.points[child])], fill=colour, width=line)
        for index, point in self.points.items():
            r = dot
            x, y = px(point)
            draw.ellipse([x - r, y - r, x + r, y + r], fill=_color_for(index))
        return img


def _color_for(index: int):
    if index < len(_OPENPOSE_COLORS):
        return _OPENPOSE_COLORS[index]
    return _EXTRA_COLORS[(index - len(_OPENPOSE_COLORS)) % len(_EXTRA_COLORS)]


def _spread(n: int, span: float, centre: float = 0.5) -> list[float]:
    """n positions evenly spread across `span`, symmetric about `centre`."""
    if n <= 0:
        return []
    if n == 1:
        return [centre]
    step = span / (n - 1)
    return [centre - span / 2 + i * step for i in range(n)]


def build_pose(legs: int = 2, arms: int = 2, *, quadruped: bool = False,
               heads: int = 1, tail: bool = False, wings: int = 0,
               arms_up: bool = False) -> Pose:
    """Author a skeleton with an arbitrary number of arms and legs.

    The body is a neck->hip spine with a head at the top. Extra legs splay outward from a
    widened hip line and extra arms fan from a widened shoulder line, because a ControlNet
    reading three legs stacked on the same axis draws one leg with artefacts; three limbs
    need three distinct directions or it reads the overlap as a single thick limb.

    `quadruped=True` swings the whole figure horizontal: neck at the left, hips at the
    right, legs pointing down. Arms then become forelegs.
    """
    pose = Pose()
    legs = max(0, int(legs))
    arms = max(0, int(arms))

    if quadruped:
        # Horizontal body: head left, hips right. Legs hang down at four x positions.
        head = pose.add(0.16, 0.42)
        neck = pose.add(0.26, 0.40, 1)
        hip_x = 0.74
        pose.bone(neck, head)                       # spine forward
        # ears/eyes: skip for now, a quadruped reference reads better without them
        total_legs = max(2, legs)
        xs = _spread(total_legs, 0.56, centre=0.5)
        for i, x in enumerate(xs):
            hip = pose.add(x, 0.40)
            knee = (x + (0.01 if i % 2 else -0.01), 0.62)
            foot = (x + (0.03 if i % 2 else -0.03), 0.80)
            pose.bone(neck, hip)
            pose.limb(hip, knee, foot)
        for w in range(int(wings)):
            a = pose.add(0.40 + 0.05 * w, 0.32)
            b = pose.add(0.34 + 0.06 * w, 0.16)
            pose.bone(neck, a)
            pose.bone(a, b)
        if tail:
            mid = pose.add(hip_x + 0.06, 0.38)
            tip = pose.add(hip_x + 0.16, 0.30)
            pose.bone(neck, mid)
            pose.bone(mid, tip)
        return pose

    # Upright figure, posed the way an OpenPose render is posed: nose in the top fifth, feet near the
    # bottom, shoulders and hips at human proportions. Filling the frame matters — a small figure is
    # the second reason a skeleton gets ignored (the first being a limb that looks like the wrong limb).
    neck_y, hip_y, foot_y = 0.24, 0.58, 0.92
    head = pose.add(0.5, 0.155, 0)
    neck = pose.add(0.5, neck_y, 1)
    pose.bone(neck, head)

    if arms <= 2:
        shoulder_dx = 0.075
        sides = [(-1, 2, 3, 4), (1, 5, 6, 7)][:arms]      # canonical R/L chains
        for side, sh, el, wr in sides:
            sh_pt = pose.add(0.5 + side * shoulder_dx, neck_y + 0.01, sh)
            # arms_up raises the wrists to shoulder height or above, for "arms raised" clauses
            el_y = neck_y + (0.03 if arms_up else 0.14)
            wr_y = neck_y + (-0.02 if arms_up else 0.26)
            el_pt = pose.add(0.5 + side * 0.135, el_y, el)
            wr_pt = pose.add(0.5 + side * (0.13 if arms_up else 0.20), wr_y, wr)
            pose.bone(neck, sh_pt)
            pose.bone(sh_pt, el_pt)
            pose.bone(el_pt, wr_pt)
    else:
        # Alternate sides and step each extra arm a little further down from horizontal — but only a
        # little. The first version let the third arm drop to 34 degrees, which at body scale points
        # where a leg goes: the ControlNet drew the extra limb from the hip, and the verifier then
        # counted an arm-shaped thing as a leg. An arm has to stay in the upper body or the pose
        # stops saying "arm" and starts saying nothing.
        left = right = 0
        for i in range(arms):
            side = -1 if i % 2 == 0 else 1
            k = left if side < 0 else right
            if side < 0:
                left += 1
            else:
                right += 1
            deg = 6.0 + 16.0 * k                      # 6 -> 22 degrees below horizontal, upper body
            rad = math.radians(deg)
            reach = 0.30 if k == 0 else 0.25
            sx, sy = 0.5 + side * 0.065, neck_y + 0.02 + 0.022 * k
            sh = pose.add(sx, sy)
            el = pose.add(sx + side * math.cos(rad) * reach * 0.55, sy + math.sin(rad) * reach * 0.55)
            wr = pose.add(sx + side * math.cos(rad) * reach, sy + math.sin(rad) * reach)
            pose.bone(neck, sh)
            pose.bone(sh, el)
            pose.bone(el, wr)

    # Legs. Two legs use the canonical OpenPose chain. Three or more splay OUTWARD from a wide hip
    # line: the previous version pulled the knees back toward the centre line, which drew three
    # near-parallel sticks and got counted as one leg. The middle leg is drawn short so it sits
    # behind the plane of the outer pair — a tripod silhouette rather than a bundle of lines.
    if legs == 2:
        hip_span = 0.075
        for side, hp, kn, an in ((-1, 11, 12, 13), (1, 8, 9, 10)):
            hip = pose.add(0.5 + side * hip_span, hip_y, hp)
            knee = pose.add(0.5 + side * (hip_span + 0.015), (hip_y + foot_y) / 2, kn)
            ankle = pose.add(0.5 + side * (hip_span + 0.035), foot_y, an)
            pose.bone(neck, hip)
            pose.bone(hip, knee)
            pose.bone(knee, ankle)
    elif legs > 2:
        # Wide hip line and strong outward knee splay. Each leg must have a clear angle,
        # never a straight vertical line down the spine (which ControlNet/diffusion reads as
        # body structure, not a limb).
        hip_half = min(0.25, 0.075 * legs)
        xs = _spread(legs, hip_half * 2, centre=0.5)
        for _i, x in enumerate(xs):
            dx = x - 0.5                             # e.g. -0.15, 0.0, +0.15
            # For odd leg counts (like 3), the center leg (dx=0) gets a slight knee kick
            # to break symmetry and prevent a straight vertical line down the torso
            knee_dx = dx * 1.4 if dx != 0 else 0.06   # kick middle leg right
            ankle_dx = dx * 1.8 if dx != 0 else 0.08  # ankle further right
            hip = pose.add(x, hip_y)
            knee = pose.add(0.5 + knee_dx, hip_y + (foot_y - hip_y) * 0.5)
            ankle = pose.add(0.5 + ankle_dx, foot_y)
            pose.bone(neck, hip)
            pose.bone(hip, knee)
            pose.bone(knee, ankle)

    if tail:
        mid = pose.add(0.5, hip_y + 0.05)
        tip = pose.add(0.5 + 0.10, hip_y + 0.18)
        pose.bone(neck, mid)
        pose.bone(mid, tip)
    for w in range(int(wings)):
        a = pose.add(0.5 - 0.09 - 0.03 * w, neck_y + 0.03)
        b = pose.add(0.5 - 0.24 - 0.04 * w, neck_y - 0.05 - 0.03 * w)
        pose.bone(neck, a)
        pose.bone(a, b)
    return pose


def counts_from_clause(clause: str) -> dict:
    """Pull limb counts out of a manual clause, e.g. "three-legged, six-armed" -> 3 legs, 6 arms.

    Delegates to the same parser the portrait verifier uses, so a skeleton and the check that
    judges its render can never disagree about what the clause asked for.
    """
    try:
        from services.topology_verify import expected_from_clause
    except Exception:                                    # pragma: no cover - path juggling
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        from services.topology_verify import expected_from_clause
    return expected_from_clause(clause or "")


def needs_custom_pose(counts: dict, clause: str = "") -> bool:
    """Does this creature need a ControlNet skeleton at all?

    A plain biped is the model's default anatomy, so it gets nothing: an extra ControlNet pass costs
    time and can only make a render the model already handles well worse. Everything the text encoder
    is known to fumble — a third limb, wings, a tail, a quadruped stance — does get one.
    """
    legs = counts.get("legs")
    arms = counts.get("arms")
    if (legs is not None and legs != 2) or (arms is not None and arms != 2):
        return True
    low = (clause or "").lower()
    return any(w in low for w in ("wing", "tail", "quadruped", "legless", "no legs", "serpentine",
                                  "tentacle", "six-armed", "four-armed", "three-armed"))


def pose_png_for_clause(clause: str, width: int = 832, height: int = 1216,
                        **kwargs) -> bytes | None:
    """Render the skeleton implied by a creature clause as an in-memory PNG, or None if not needed.

    Returns bytes rather than a path so the caller can hand them straight to ComfyUI's upload
    endpoint without touching the filesystem, and so a batch run leaves no stray pose.png files
    behind on either machine.
    """
    import io

    counts = counts_from_clause(clause)
    if not kwargs and not needs_custom_pose(counts, clause):
        return None
    low = (clause or "").lower()
    legs = counts.get("legs")
    arms = counts.get("arms")
    pose = build_pose(
        legs=2 if legs is None else legs,
        arms=2 if arms is None else arms,
        quadruped=kwargs.get("quadruped", "quadruped" in low),
        wings=kwargs.get("wings", counts.get("wings") or 0),
        tail=kwargs.get("tail", "tail" in low),
        arms_up=kwargs.get("arms_up", False),
    )
    buf = io.BytesIO()
    pose.render(width, height).save(buf, format="PNG")
    return buf.getvalue()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Draw an OpenPose skeleton with custom limb counts.")
    ap.add_argument("--legs", type=int, default=2)
    ap.add_argument("--arms", type=int, default=2)
    ap.add_argument("--clause", default=None, help="manual clause; overrides --legs/--arms")
    ap.add_argument("--quadruped", action="store_true")
    ap.add_argument("--wings", type=int, default=0)
    ap.add_argument("--tail", action="store_true")
    ap.add_argument("--arms-up", action="store_true")
    ap.add_argument("--width", type=int, default=832)
    ap.add_argument("--height", type=int, default=1216)
    ap.add_argument("--out", default="pose.png")
    args = ap.parse_args(argv)

    legs, arms = args.legs, args.arms
    if args.clause:
        want = counts_from_clause(args.clause)
        legs = want.get("legs", legs)
        arms = want.get("arms", arms)
        print(f"clause -> legs={legs} arms={arms} eyes={want.get('eyes')} "
              f"heads={want.get('heads')}", file=sys.stderr)

    pose = build_pose(legs=legs, arms=arms, quadruped=args.quadruped, wings=args.wings,
                      tail=args.tail, arms_up=args.arms_up)
    img = pose.render(args.width, args.height)
    os.makedirs(os.path.dirname(os.path.abspath(args.out)) or ".", exist_ok=True)
    img.save(args.out)
    print(f"{args.out} ({len(pose.points)} keypoints, {len(pose.bones)} bones)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
