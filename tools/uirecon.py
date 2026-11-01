#!/usr/bin/env python3
"""Geometric reconstruction of Civ V UI chrome at k x resolution.

Most window chrome (9-slice frames, trims, bars, scroll bar tracks) was drawn as
axis-aligned bands, anti-aliased edges and smooth gradients, with rounded corners.
That structure can be re-rendered exactly at a higher resolution instead of being
guessed by an upscaler:

  axis  separable 1D reconstruction. Along every row and then every column each
        pixel boundary is classified as a plateau, a hard step (kept sharp), a
        single anti-aliased edge pixel (the step is re-rendered at its sub-pixel
        position) or part of a gradient (interpolated linearly).
  box   axis + rounded corners. Every opaque component is fitted with a box and a
        radius per corner; corner pixels are re-rendered by sweeping the straight
        edge profile (which `axis` reconstructs exactly) around the arc, 4x4
        supersampled. The radii are found by rendering at 1x and comparing with
        the original, so the result always round-trips.

All work happens on premultiplied RGBA floats in [0, 1].
"""
import numpy as np
from scipy import ndimage

PLATEAU = 6 / 255   # neighbouring pixels this close are the same flat band
HARD = 20 / 255     # steps larger than this are edges unless part of a ramp


def premul(rgba8):
    f = rgba8.astype(np.float32) / 255
    return np.dstack([f[..., :3] * f[..., 3:4], f[..., 3:4]])


def unpremul(pm):
    a = np.clip(pm[..., 3:4], 0, 1)
    rgb = np.where(a > 1 / 512, pm[..., :3] / np.maximum(a, 1 / 512), 0)
    return (np.clip(np.dstack([rgb, a]), 0, 1) * 255 + 0.5).astype(np.uint8)


def box_down(img, k):
    h, w = img.shape[0] // k, img.shape[1] // k
    return img.reshape(h, k, w, k, -1).mean(axis=(1, 3))


# ------------------------------------------------------------------ 1D reconstruction
def _diff(a, b):
    return np.abs(a - b).max(axis=-1)


def upscale_1d(s, k):
    """s: (n, C) premultiplied samples along one axis -> (n*k, C).

    Each output sub-pixel j of input pixel i covers [i + j/k, i + (j+1)/k)."""
    n = s.shape[0]
    pad = np.concatenate([s[:1], s[:1], s, s[-1:], s[-1:]])  # pad[i+2] == s[i]
    out = np.repeat(s, k, axis=0).astype(np.float32)
    if n == 1:
        return out
    sub = (np.arange(k) + 0.5) / k  # sub-pixel centres within a pixel
    for i in range(n):
        m2, m1, c, p1, p2 = pad[i], pad[i + 1], pad[i + 2], pad[i + 3], pad[i + 4]
        dl, dr = _diff(c, m1), _diff(c, p1)
        if dl <= PLATEAU and dr <= PLATEAU:
            continue  # flat
        # single anti-aliased edge pixel between two plateaus: re-render the step
        if (_diff(m1, m2) <= PLATEAU and _diff(p1, p2) <= PLATEAU and _diff(m1, p1) > HARD
                and i > 0 and i < n - 1):
            span = p1 - m1
            t = float(np.clip(((c - m1) * span).sum() / max((span * span).sum(), 1e-9), 0, 1))
            resid = _diff(c, m1 + t * span)
            if resid <= 2 * PLATEAU:
                e = 1 - t  # edge position: fraction of the pixel covered by the left value
                lo, hi = np.arange(k) / k, (np.arange(k) + 1) / k
                cov_left = np.clip((e - lo) * k, 0, 1)[:, None]  # coverage of [lo, hi) left of e
                del hi
                out[i * k:(i + 1) * k] = cov_left * m1 + (1 - cov_left) * p1
                continue
        # otherwise interpolate linearly towards a neighbour unless the boundary is hard
        soft_l = i > 0 and (dl <= HARD or _ramp(m2, m1, c, p1))
        soft_r = i < n - 1 and (dr <= HARD or _ramp(m1, c, p1, p2))
        for j, u in enumerate(sub):
            if u < 0.5 and soft_l:
                w = 0.5 - u
                out[i * k + j] = c * (1 - w) + m1 * w
            elif u > 0.5 and soft_r:
                w = u - 0.5
                out[i * k + j] = c * (1 - w) + p1 * w
        # keep the pixel's mean exact when only one side is interpolated
        out[i * k:(i + 1) * k] += c - out[i * k:(i + 1) * k].mean(axis=0)
    return out


def _ramp(a, b, c, d):
    """Is the step b->c part of a smooth monotone ramp a->b->c->d?"""
    s0, s1, s2 = b - a, c - b, d - c
    ch = int(np.argmax(np.abs(s1)))
    x0, x1, x2 = s0[ch], s1[ch], s2[ch]
    same = (np.sign(x0) == np.sign(x1) or abs(x0) <= PLATEAU) and \
           (np.sign(x2) == np.sign(x1) or abs(x2) <= PLATEAU)
    big = max(abs(x0), abs(x2))
    return bool(same and big > 0.4 * abs(x1) and abs(x1) < 0.35)


def axis_upscale(pm, k):
    """Separable reconstruction (rows then columns)."""
    h, w, c = pm.shape
    rows = np.stack([upscale_1d(pm[y], k) for y in range(h)])          # h, w*k, c
    cols = np.stack([upscale_1d(rows[:, x], k) for x in range(w * k)], axis=1)  # h*k, w*k, c
    return np.clip(cols, 0, 1)


# ------------------------------------------------------------------ rounded boxes
def components(alpha, thr=0.03, min_px=12):
    lab, n = ndimage.label(alpha > thr)
    boxes = []
    for sl in ndimage.find_objects(lab):
        if sl is None:
            continue
        y0, y1, x0, x1 = sl[0].start, sl[0].stop, sl[1].start, sl[1].stop
        if (y1 - y0) * (x1 - x0) >= min_px and y1 - y0 >= 3 and x1 - x0 >= 3:
            boxes.append((x0, y0, x1, y1))
    return boxes


def _edge_frac(profile):
    """Sub-pixel outer boundary from an alpha profile starting outside the shape:
    returns how much of the first non-empty pixel is empty."""
    for i, a in enumerate(profile):
        if a > 0.02:
            ref = max(profile[i:i + 3].max(), 1e-3)
            return i + 1 - min(a / ref, 1.0)
    return 0.0


def box_geometry(alpha, b):
    """Sub-pixel outer bounds (L, T, R, B) of component b, measured through its middle."""
    x0, y0, x1, y1 = b
    mx, my = (x0 + x1) // 2, (y0 + y1) // 2
    L = x0 + _edge_frac(alpha[my, x0:x1])
    R = x1 - _edge_frac(alpha[my, x0:x1][::-1])
    T = y0 + _edge_frac(alpha[y0:y1, mx])
    B = y1 - _edge_frac(alpha[y0:y1, mx][::-1])
    return L, T, R, B


class Sampler:
    """Bilinear sampling of a k x image at 1x continuous coordinates. Used only at
    positions where the k x image is smooth along the sampled direction."""

    def __init__(self, hi, k):
        self.hi, self.k = hi, k

    def __call__(self, x, y):
        hi, k = self.hi, self.k
        fx = np.clip(x * k - 0.5, 0, hi.shape[1] - 1)
        fy = np.clip(y * k - 0.5, 0, hi.shape[0] - 1)
        return np.stack([ndimage.map_coordinates(hi[..., c], [fy, fx], order=1, mode="nearest")
                         for c in range(hi.shape[2])], axis=-1)


class Profile:
    """1D reconstruction (K x) of the original across a straight edge, at a fractional
    position along the edge. axis=0: a column (varies with y), axis=1: a row."""
    K = 16

    def __init__(self, pm, axis, pos):
        n = pm.shape[1 - axis]
        p = np.clip(pos - 0.5, 0, n - 1)
        i0 = int(np.floor(p))
        i1, t = min(i0 + 1, n - 1), p - i0
        line = (lambda i: pm[:, i]) if axis == 0 else (lambda i: pm[i, :])
        self.f = upscale_1d(line(i0), self.K) * (1 - t) + upscale_1d(line(i1), self.K) * t

    def __call__(self, coord):
        f = np.clip(coord * self.K - 0.5, 0, self.f.shape[0] - 1)
        i0 = np.floor(f).astype(int)
        i1, t = np.minimum(i0 + 1, self.f.shape[0] - 1), (f - i0)[..., None]
        return self.f[i0] * (1 - t) + self.f[i1] * t


def render_corner(base, k, geom, corner, r, prof=None, extent=None, ss=4, pm=None):
    """Re-render one corner of radius r (1x px) from the straight edge profiles.

    prof:   distance from the outer edge at which the straight profiles are sampled
            (default: just past the tangent point, so colour gradients along the
            frame carry into the corner).
    extent: size of the rendered window (default r). When larger, the straight parts
            of the window are extruded from the profiles as well, which makes the
            render a pure model of the corner (used for fitting r).
    Returns ((y0, y1, x0, x1), pixels) in k x coordinates."""
    L, T, R, B = geom
    half = min(R - L, B - T) / 2
    if prof is None:
        prof = min(r + 1, half)
    model = extent is not None
    if extent is None:
        extent = r
    sx = 1 if corner in ("tl", "bl") else -1
    sy = 1 if corner in ("tl", "tr") else -1
    ex = L if sx > 0 else R           # outer vertical edge
    ey = T if sy > 0 else B           # outer horizontal edge
    cx, cy = ex + sx * r, ey + sy * r  # arc centre
    xa, xb = sorted((ex, ex + sx * extent))
    ya, yb = sorted((ey, ey + sy * extent))
    X0, X1 = max(int(np.floor(xa * k)) - 1, 0), min(int(np.ceil(xb * k)) + 1, base.shape[1])
    Y0, Y1 = max(int(np.floor(ya * k)) - 1, 0), min(int(np.ceil(yb * k)) + 1, base.shape[0])
    if X1 <= X0 or Y1 <= Y0:
        return None
    gy, gx = np.mgrid[Y0 * ss:Y1 * ss, X0 * ss:X1 * ss]
    px, py = (gx + 0.5) / (k * ss), (gy + 0.5) / (k * ss)
    vx, vy = (px - cx) * sx, (py - cy) * sy   # both negative inside the corner quadrant
    d = r - np.hypot(vx, vy)                  # inward distance from the arc
    samp = Sampler(base, k)
    # equivalent points on the straight edges at the same inward distance, sampled from
    # fine 1D reconstructions of the straight edges
    ph, pv = Profile(pm, 0, ex + sx * prof), Profile(pm, 1, ey + sy * prof)
    col_h = ph(ey + sy * d)
    col_v = pv(ex + sx * d)
    w = np.clip(np.arctan2(-vy, -vx) / (np.pi / 2), 0, 1)[..., None]  # 1: horizontal edge
    val = col_h * w + col_v * (1 - w)
    rest = (vx >= 0) | (vy >= 0)
    if model:
        top, side = ph(py), pv(px)
        val = np.where(((vx >= 0) & (vy < 0))[..., None], top, val)
        val = np.where(((vx < 0) & (vy >= 0))[..., None], side, val)
        # beyond the arc centre in both axes: square (mitred) corner of the straight parts
        nearer_h = (py - ey) * sy < (px - ex) * sx
        val = np.where(((vx >= 0) & (vy >= 0))[..., None], np.where(nearer_h[..., None], top, side), val)
        val = val.reshape(Y1 - Y0, ss, X1 - X0, ss, -1).mean(axis=(1, 3))
    else:
        # keep base exactly where a pixel lies outside the corner quadrant
        keep = rest.reshape(Y1 - Y0, ss, X1 - X0, ss).mean(axis=(1, 3))[..., None]
        val = np.where(rest[..., None], 0, val).reshape(Y1 - Y0, ss, X1 - X0, ss, -1).sum(axis=(1, 3))
        val = val / (ss * ss) + keep * base[Y0:Y1, X0:X1]
    return (Y0, Y1, X0, X1), val


def box_upscale(pm, k, geoms=None, radii=None):
    """axis_upscale + re-rendered rounded corners. Returns (hi, fits)."""
    base = axis_upscale(pm, k)
    if geoms is None:
        geoms = [box_geometry(pm[..., 3], b) for b in components(pm[..., 3])]
    hi = base.copy()
    fits = []
    for gi, g in enumerate(geoms):
        fit = {}
        for corner in ("tl", "tr", "bl", "br"):
            r = radii[gi][corner] if radii else fit_radius(pm, base, k, g, corner)[0]
            fit[corner] = r
            if r <= 0.25:
                continue
            res = render_corner(base, k, g, corner, r, pm=pm)
            if res:
                (y0, y1, x0, x1), val = res
                hi[y0:y1, x0:x1] = val
        fits.append((g, fit))
    return project(hi, pm, k), fits


def fit_radius(pm, base, k, g, corner, rmax=24):
    """Corner radius whose model render best reproduces the original corner at 1x.
    Returns (r, rms error of the model over the corner window)."""
    L, T, R, B = g
    half = min(R - L, B - T) / 2
    top = min(half - 0.5, rmax)
    span = min(top + 2, half)
    x0 = int(np.floor(L)) if corner in ("tl", "bl") else int(np.floor(R - span))
    y0 = int(np.floor(T)) if corner in ("tl", "tr") else int(np.floor(B - span))
    x1, y1 = x0 + int(np.ceil(span)) + 1, y0 + int(np.ceil(span)) + 1
    x0, y0 = max(x0, 0), max(y0, 0)
    x1, y1 = min(x1, pm.shape[1]), min(y1, pm.shape[0])
    best, best_err, sq_err = 0.0, None, None
    for r in np.arange(0.0, top + 1e-6, 0.25):
        hi = base.copy()
        res = render_corner(base, k, g, corner, max(r, 1e-3), prof=span, extent=span, pm=pm)
        if res:
            (Y0, Y1, X0, X1), val = res
            hi[Y0:Y1, X0:X1] = val
        err = ((box_down(hi[y0 * k:y1 * k, x0 * k:x1 * k], k) - pm[y0:y1, x0:x1]) ** 2).mean()
        if r == 0:
            sq_err = err
        if best_err is None or err < best_err - 1e-7:
            best, best_err = r, err
    # Only accept a rounded corner when it explains the original clearly better than a
    # square one (edge-to-edge and noisy textures otherwise get spurious radii).
    if best > 0 and not (sq_err > 4e-4 and best_err < 0.5 * sq_err):
        best, best_err = 0.0, sq_err
    return best, float(np.sqrt(best_err))


# ------------------------------------------------------------------ consistency
def project(hi, pm, k, iters=6, smooth=False):
    """Push a k x render towards the original: after this, box-averaging hi back to 1x
    reproduces pm (up to clamping). The 1x residual is distributed inside each pixel
    in proportion to the existing coverage, so edges keep their position and no new
    soft fringes appear. smooth=True spreads it bilinearly instead (for AI output,
    whose residuals are larger and lower-frequency)."""
    hi = hi.copy()
    h, w = pm.shape[:2]
    for _ in range(iters):
        res = pm - box_down(hi, k)
        if np.abs(res).max() < 1 / 1024:
            break
        if smooth:
            hi += _up(res, k)
        else:
            # exact per pixel, weighted by coverage: density is added where the shape
            # already is, so edges neither move nor grow soft fringes
            wgt = hi[..., 3:4] + 0.02
            hi += _up(res, k, False) * wgt / _up(box_down(wgt, k), k, False)
        hi[..., 3] = np.clip(hi[..., 3], 0, 1)
        hi[..., :3] = np.clip(hi[..., :3], 0, hi[..., 3:4])
    return hi


def _up(img, k, smooth=True):
    if not smooth:
        return np.repeat(np.repeat(img, k, 0), k, 1)
    return np.stack([ndimage.zoom(img[..., c], k, order=1, mode="nearest", grid_mode=True)
                     for c in range(img.shape[2])], axis=-1)


def project_alpha(hi, pm, k, iters=3):
    """Gentle consistency for geometric renders: match the original's coverage per 1x
    pixel by scaling hi-res alpha inside each pixel (colour unchanged), so a stroke that
    is bolder in the original becomes bolder without moving or blurring its edges."""
    hi = hi.copy()
    for _ in range(iters):
        a = hi[..., 3:4]
        ratio = pm[..., 3:4] / np.maximum(box_down(a, k), 1e-3)
        ratio = np.clip(np.repeat(np.repeat(ratio, k, 0), k, 1), 0.5, 2.0)
        na = np.clip(a * ratio, 0, 1)
        hi[..., :3] *= np.where(a > 1e-4, na / np.maximum(a, 1e-4), 0)
        hi[..., 3:4] = na
    return hi


# ------------------------------------------------------------------ thin arcs
def ring_upscale(pm, k, ss=8):
    """Thin circular strokes joined to straight lines (city banner outlines): the curved
    pixels are fitted with a circle and redrawn as an analytic anti-aliased arc; the
    straight runs are rendered exactly by axis_upscale. Returns (hi, (cx, cy, r, w))."""
    a = pm[..., 3]
    h, w = a.shape
    # straight horizontal / vertical runs (> 6 px of near-opaque coverage in a line)
    straight = np.zeros_like(a, bool)
    for y in range(h):
        run = ndimage.label(a[y] > 0.8)[0]
        for i in range(1, run.max() + 1):
            if (run == i).sum() > 6:
                straight[y, run == i] = True
    for x in range(w):
        run = ndimage.label(a[:, x] > 0.8)[0]
        for i in range(1, run.max() + 1):
            if (run == i).sum() > 6:
                straight[run == i, x] = True
    # include the anti-aliased neighbours of straight runs
    straight_d = ndimage.binary_dilation(straight, structure=np.ones((3, 3), bool)) & (a > 0.02)
    curved = (a > 0.02) & ~straight_d
    ys, xs = np.nonzero(curved)
    wt = a[ys, xs]
    px, py = xs + 0.5, ys + 0.5
    # the arc is a half circle tangent to the two straight lines: radius and centre
    # height come from the line rows, only the horizontal centre is fitted
    rows = np.nonzero(straight.any(axis=1))[0]
    yt, yb = rows.min() + 0.5, rows.max() + 0.5
    cy, r = (yt + yb) / 2, (yb - yt) / 2
    side = -1 if px.mean() < w / 2 else 1  # arc on the left (-1) or right (+1)
    best = None
    for cx_try in np.arange(0, w, 0.05):
        err = (wt * (np.hypot(px - cx_try, py - cy) - r) ** 2).sum()
        if best is None or err < best[0]:
            best = (err, cx_try)
    cx = best[1]
    ang = np.arctan2(py - cy, px - cx)
    mean = 0.0 if side > 0 else np.pi
    a0, a1 = -np.pi / 2, np.pi / 2
    arclen = np.pi * r
    sw = float(a[curved | (straight_d & ((np.arange(w)[None, :] + 0.5 - cx) * side > 0))].sum()
               / max(arclen, 1e-3))
    sw = float(np.clip(sw, 0.5, 3.0))
    color = (pm[ys, xs, :3]).sum(0) / max(wt.sum(), 1e-3)
    # the lines run from the tangent point away from the arc: drop their part on the
    # arc side (the arc covers it)
    arc_side = ((np.arange(w)[None, :] + 0.5 - cx) * side > 0) & (a > 0.02)
    # straight parts exactly
    base = pm.copy()
    base[curved | arc_side] = 0
    hi = axis_upscale(base, k)
    # lines must end exactly at the tangent point (x = cx), not at a pixel boundary
    X = (np.arange(w * k) + 0.5) / k
    keep = np.clip(((X - cx) * -side) * k + 0.5, 0, 1)  # 1 on the line side of cx
    hi = hi * keep[None, :, None]
    # analytic arc, supersampled
    H, W = h * k, w * k
    gy, gx = np.mgrid[0:H * ss, 0:W * ss]
    qx, qy = (gx + 0.5) / (k * ss), (gy + 0.5) / (k * ss)
    d = np.abs(np.hypot(qx - cx, qy - cy) - r)
    rel_q = np.angle(np.exp(1j * (np.arctan2(qy - cy, qx - cx) - mean)))
    cov = ((d < sw / 2) & (rel_q >= a0) & (rel_q <= a1)).astype(np.float32)
    cov = cov.reshape(H, ss, W, ss).mean(axis=(1, 3))
    # arc and lines are split at x = cx and do not overlap: coverage adds up exactly
    alpha = hi[..., 3] + cov
    rgb = hi[..., :3] + cov[..., None] * color[None, None, :]
    return np.clip(np.dstack([rgb, alpha]), 0, 1), (cx, cy, r, sw)
