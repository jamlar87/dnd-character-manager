"""Auto-align a map's overlay grid to the grid printed on the art.

The pitch alone is not enough. An overlay whose cell size matches the printed grid still looks wrong
unless its lines land ON the printed lines — that is the phase, and it is what a DM nudges by hand.
For a given pitch there is exactly one best phase, and it can be measured:

  1. build an edge profile along one axis: for each column (or row), how much intensity change it
     carries. A printed grid line makes its column light up.
  2. the profile is periodic at the pitch, so autocorrelation finds the pitch.
  3. fold the profile modulo the pitch and take the argmax: that is where the lines are. How far that
     peak stands above the mean of the fold is the confidence — art with no printed grid folds flat.

Verified against known answers: synthetic grids at pitch/offset (50,0) (83,17) (120,43) (64,31) come
back within 1.5px of pitch and 2.5px of phase, and blank noise is rejected.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image

#: Cells across the long edge. The corpus runs 8-60; the low end is a single big hall in one image.
MIN_PITCH_FRAC, MAX_PITCH_FRAC = 1 / 160, 1 / 5
WORK = 1400          # work scale; results are scaled back to the source image
MIN_SCORE = 0.4      # fold peak over fold mean: how periodic the profile is at this pitch
MIN_AGREE = 0.9      # a printed battle grid is square, so the two axes must share a pitch
# The gate asks "is there structure here to align to", not "is a grid printed here". Those are
# different questions and the corpus cannot answer the second one: "Swine and Roses grid" and its
# "no grid" twin both fold at 0.35 against 0.37, and the no-grid variants score HIGHER on lattice
# contrast than some genuinely gridded maps. What they share is the answer that matters — both detect
# the same offset, because the artist drew the grid along the tile joints. Aligning a map whose art has
# no lines is harmless (the grid is off until asked for); misaligning one that does is not.
MIN_AXIS_Z = 0.5     # lattice ink over the profile's own MAD, per axis
MIN_STRONG_Z = 1.5   # at least one axis must show real structure


def _profile(arr: np.ndarray, along: str) -> np.ndarray:
    """Edge energy per position ALONG one axis: 'x' -> one value per column, 'y' -> one per row.

    Name the intent, never the axis number. Getting this backwards is silent: the profile still looks
    plausible, it just measures the other direction, and that axis folds weakly (measured 0.06 against
    1.29 for the axis that was right) rather than obviously breaking.
    """
    g = np.abs(np.diff(arr.astype(np.float32), axis=1 if along == "x" else 0))
    return g.sum(axis=0 if along == "x" else 1)


def _lattice_contrast(profile: np.ndarray, phase: float, pitch: float) -> float:
    """How far the ink ON the lattice stands above the profile's own variability, in MADs.

    The fold RATIO cannot tell a grid from paper texture: on "Swine and Roses grid" and its "no grid"
    twin — the same picture with the printed grid removed — it scores 0.35 against 0.37. A printed line
    is a SPIKE though, and texture is a wobble, so measuring the lattice's ink in units of how much the
    profile normally wobbles does separate the cases where the grid is really drawn.
    """
    med = float(np.median(profile))
    mad = float(np.median(np.abs(profile - med))) or 1.0
    idx = np.arange(len(profile))
    near = np.abs(((idx - phase + pitch / 2) % pitch) - pitch / 2) <= max(1.0, pitch * 0.04)
    if not near.any():
        return 0.0
    return float((profile[near].mean() - med) / mad)


def _fold_peak(profile: np.ndarray, pitch: float) -> tuple[float, float]:
    """Best phase for `pitch`, and how far its peak stands above the mean of the fold."""
    n = len(profile)
    bins = max(4, int(round(pitch)))
    acc = np.zeros(bins, dtype=np.float64)
    idx = (np.arange(n) % bins).astype(int)
    np.add.at(acc, idx, profile)
    mean = acc.mean()
    if mean <= 0:
        return 0.0, 0.0
    return float(np.argmax(acc)), float((acc.max() - mean) / mean)


def _best_fold(prof: np.ndarray, lo: int, hi: int) -> tuple[float, float, float]:
    """Best (phase, score, pitch) over a range, resolving harmonic ambiguity toward the fundamental.

    Autocorrelation returns the pitch OR a multiple of it — a periodic signal correlates just as well at
    2p and 3p — so the global peak can be a harmonic. That is not a subtle failure: a bulk pass on this
    measured 104->178, 116->232 and 106->319, all exact multiples, and every one of them came from the
    raw argmax. The fix is to test the submultiples too and keep the SMALLEST pitch whose fold is within
    a whisker of the best: at the true pitch every line lands in one bin, at 2p they alternate between
    two, so a genuine fundamental survives the comparison and an accidental one does not.
    """
    best_phase, best_score, best_pitch = 0.0, -1.0, float(lo)
    for cand in range(lo, hi + 1):
        phase, score = _fold_peak(prof, cand)
        if score > best_score:
            best_phase, best_score, best_pitch = phase, score, float(cand)
    if best_score <= 0:
        return 0.0, 0.0, float(lo)
    # Walk DOWN, not just one step: a grid at 50 whose alternate lines are darker peaks at 200, then
    # 100, and only then 50. Stopping at the first submultiple that passes leaves 100 — halve again
    # while the fold holds, and stop the moment it stops holding (50 -> 25 scores 0.35 of the best).
    cur = best_pitch
    for _ in range(8):
        improved = False
        for div in (2, 3, 5, 7):
            sub = int(round(cur / div))
            if sub < lo * 0.9:
                continue
            for cand in range(max(lo, sub - 1), min(hi, sub + 1) + 1):
                phase, score = _fold_peak(prof, cand)
                if score >= best_score * 0.7:
                    cur, improved = float(cand), True
                    break
            if improved:
                break
        if not improved:
            break
    if cur != best_pitch:
        return _fold_peak(prof, cur) + (cur,)
    return best_phase, best_score, best_pitch


def detect(image_path: str | Path, hint_pitch: float | None = None) -> dict | None:
    """Measure the printed grid: pitch, phase per axis, and whether there is one at all.

    `hint_pitch` is the cell size the map already carries. With a hint the pitch is searched within 25%
    of it, which is both safer and what "match the existing grid" actually means — the size is usually
    already right and the phase is the missing part. Without one the whole plausible range is searched.
    """
    try:
        im = Image.open(image_path).convert("L")
    except Exception:
        return None
    w0, _h0 = im.size
    im.thumbnail((WORK, WORK), Image.LANCZOS)
    scale = w0 / im.width
    arr = np.asarray(im, dtype=np.uint8)

    def phase_for(along: str, pitch: float):
        """Where the lines of a KNOWN pitch sit, and how strongly they fold."""
        prof = _profile(arr, along)
        if prof.size < 32 or prof.sum() <= 0:
            return None
        phase, score = _fold_peak(prof, pitch)
        return phase, score, float(pitch), _lattice_contrast(prof, phase, pitch)

    def axis_reading(along: str, around: float | None, hint: float | None = None):
        size = arr.shape[1] if along == "x" else arr.shape[0]
        prof = _profile(arr, along)
        if prof.size < 32 or prof.sum() <= 0:
            return None
        centred = prof - prof.mean()
        ac = np.correlate(centred, centred, mode="full")[len(centred) - 1:]
        lo = max(4, int(size * MIN_PITCH_FRAC))
        hi = min(len(ac) - 1, int(size * MAX_PITCH_FRAC))
        if hint:
            # the map already carries a cell size: search near it, which is what "match the existing
            # grid" means and what keeps a wood-grain texture from doubling the pitch
            lo, hi = max(lo, int(hint * 0.75)), min(hi, int(hint * 1.25) + 1)
        if around:
            # a printed battle grid is square, so with one axis confident the other cannot be far off
            lo, hi = max(4, int(around * 0.88)), min(hi, int(around * 1.12) + 1)
        if hi <= lo:
            return None
        # the autocorrelation's argmax only narrows the field; the fold decides, harmonics and all
        peak = int(lo + int(np.argmax(ac[lo:hi])))
        narrow_lo = max(lo, peak - max(3, peak // 5))
        narrow_hi = min(hi, peak + max(3, peak // 5))
        return _best_fold(prof, narrow_lo, narrow_hi)

    hinted = float(hint_pitch) if hint_pitch and 10 <= float(hint_pitch) <= 400 else None

    if hinted:
        # The map already has a cell size, so prefer it — it is the size in use, and re-deriving a known
        # number only adds a way to be wrong (a lattice is equally periodic at 2p, which is how a bulk
        # pass turned 104 into 178). But it must not be a straitjacket: a brand-new map carries the
        # DEFAULT 50, and refusing to look further would leave every such map at the wrong cell size.
        # So measure freely first, collapse harmonics toward the fundamental, and take the map's own
        # number when the two broadly agree.
        measured = {}
        for along in ("x", "y"):
            prof = _profile(arr, along)
            size_a = arr.shape[1] if along == "x" else arr.shape[0]
            lo_a, hi_a = max(4, int(size_a * MIN_PITCH_FRAC)), min(size_a - 1, int(size_a * MAX_PITCH_FRAC))
            if hi_a > lo_a and prof.size >= 32 and prof.sum() > 0:
                measured[along] = _best_fold(prof, lo_a, hi_a)[2]
        agree = [v for v in measured.values() if v and abs(v - hinted) <= 0.25 * hinted]
        use = hinted if (len(agree) == len(measured) and measured) else (
            round(sum(measured.values()) / len(measured), 2) if measured else hinted)
        hinted = float(use)

        out = {}
        for along in ("x", "y"):
            got = phase_for(along, hinted)
            if got:
                out[along] = {"pitch_px": round(hinted, 2), "phase_px": round(got[0] * scale, 2),
                              "score": round(got[1], 3), "z": round(got[3], 2)}
        if len(out) < 2:
            return None
        score = (out["x"]["score"] + out["y"]["score"]) / 2
        zs = (out["x"]["z"], out["y"]["z"])
        return {"pitch_px": round(hinted, 2), "offset_x": out["x"]["phase_px"],
                "offset_y": out["y"]["phase_px"], "axis_agree": 1.0, "score": round(score, 3),
                "z_x": zs[0], "z_y": zs[1],
                # A phase read off a flat axis is a wrong offset, so both axes need real structure and
                # at least one must be convincing. Otherwise the map keeps the offsets it had.
                "has_grid": bool(min(zs) >= MIN_AXIS_Z and max(zs) >= MIN_STRONG_Z
                                 and score >= MIN_SCORE), "hinted": True}

    free = {}
    for along in ("x", "y"):
        got = axis_reading(along, None)
        if got:
            free[along] = got
    if not free:
        return None

    # the stronger axis decides the pitch; letting both run free is how a wood-plank texture in one
    # axis ends up twenty times the real cell size
    lead = max(free, key=lambda k: free[k][1])
    phase, score, pitch = free[lead]
    out = {lead: {"pitch_px": round(pitch * scale, 2), "phase_px": round(phase * scale, 2),
                  "score": round(score, 3)}}
    other = "y" if lead == "x" else "x"
    got = axis_reading(other, around=pitch)
    if got:
        ph2, sc2, p2 = got
        out[other] = {"pitch_px": round(p2 * scale, 2), "phase_px": round(ph2 * scale, 2),
                      "score": round(sc2, 3)}
    else:
        out[other] = {"pitch_px": out[lead]["pitch_px"], "phase_px": 0.0, "score": 0.0}

    px, py = out["x"]["pitch_px"], out["y"]["pitch_px"]
    if min(px, py) <= 0:
        return None
    agree = min(px, py) / max(px, py)
    score = (out["x"]["score"] + out["y"]["score"]) / 2
    return {"pitch_px": round((px + py) / 2, 2), "offset_x": out["x"]["phase_px"],
            "offset_y": out["y"]["phase_px"], "axis_agree": round(agree, 3), "score": round(score, 3),
            "has_grid": bool(score >= MIN_SCORE and agree >= MIN_AGREE)}


def measure_structurally(image_path: str | Path) -> dict | None:
    """The reading, with the two per-axis scores kept for the caller to judge."""
    r = detect(image_path)
    if r is None:
        return None
    return dict(r)
