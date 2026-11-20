"""Production tracker port used by the 10917 replay (issue #39)."""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from typing import Optional


WRIST, INDEX_MCP, INDEX_PIP, INDEX_DIP, INDEX_TIP = 0, 5, 6, 7, 8
FINGERS = ((8, 5, 6, 7), (12, 9, 10, 11), (16, 13, 14, 15), (20, 17, 18, 19))
MIN_HAND_SCORE = 0.18
MAX_POINTABLE_FRACTION = 0.40
RAY_HYSTERESIS = 1.35
IOU_MATCH = 0.25


@dataclass
class Box:
    kind: str
    label: str
    score: float
    left: float
    top: float
    right: float
    bottom: float
    points: list[tuple[float, float]] = field(default_factory=list)
    depths: list[float] = field(default_factory=list)
    track_id: int = 0
    held: bool = False
    synthetic: bool = False

    def area(self) -> float:
        return max(0.0, self.right - self.left) * max(0.0, self.bottom - self.top)


@dataclass
class Ray:
    tip_x: float
    tip_y: float
    dir_x: float
    dir_y: float
    reach: float
    joints: list[tuple[float, float]]
    depth_dominant: bool = False
    reversible: bool = False


@dataclass
class Aimed:
    ray: Ray
    hit: Optional[Box]


def hypot(ax: float, ay: float) -> float:
    return math.hypot(ax, ay)


def dist(a: tuple[float, float], b: tuple[float, float]) -> float:
    return hypot(a[0] - b[0], a[1] - b[1])


def iou(a: Box, b: Box) -> float:
    iw = max(0.0, min(a.right, b.right) - max(a.left, b.left))
    ih = max(0.0, min(a.bottom, b.bottom) - max(a.top, b.top))
    inter = iw * ih
    union = a.area() + b.area() - inter
    return inter / union if union > 0 else 0.0


def wearer_arm(person: Box, fw: int, fh: int) -> bool:
    if fw <= 0 or fh <= 0:
        return False
    label = person.label.lower()
    if label not in {"person", "object", "teddy bear", "dog", "cat", "bear"}:
        return False
    margin = max(12.0, min(fw, fh) * 0.03)
    from_edge = person.left <= margin or person.right >= fw - margin or person.bottom >= fh - margin
    if not from_edge:
        return False
    box_h = person.bottom - person.top
    box_w = person.right - person.left
    frac = person.area() / (fw * fh)
    if label != "person":
        if frac < 0.04 or frac > 0.35:
            return False
        if box_h < box_w * 1.05:
            return False
    standing = box_h > box_w * 1.8 and person.top < fh * 0.18 and box_h > fh * 0.50
    return not standing


def hand_priority(obs: list[Box], fw: int, fh: int) -> list[Box]:
    hands = [h for h in obs if h.kind == "hand" and len(h.points) >= 21 and h.area() > 0]

    def same_region(person: Box, hand: Box) -> bool:
        iw = max(0.0, min(person.right, hand.right) - max(person.left, hand.left))
        ih = max(0.0, min(person.bottom, hand.bottom) - max(person.top, hand.top))
        ha = hand.area()
        if ha <= 0:
            return False
        return (iw * ih / ha) >= 0.55 and person.area() / ha <= 12.0

    def arm_labeled_person(person: Box, hand: Box) -> bool:
        if person.label.lower() != "person" or not hand.points:
            return False
        wrist = hand.points[WRIST]
        if not (person.left <= wrist[0] <= person.right and person.top <= wrist[1] <= person.bottom):
            return False
        ph, pw = person.bottom - person.top, person.right - person.left
        span = max(hand.right - hand.left, hand.bottom - hand.top, 24.0)
        body = ph > pw * 1.6 and ph > span * 6.0 and person.area() > hand.area() * 20.0
        return not body

    out = []
    for c in obs:
        if c.kind == "object" and (
            wearer_arm(c, fw, fh)
            or (hands and any(same_region(c, h) or arm_labeled_person(c, h) for h in hands))
        ):
            continue
        out.append(c)
    return out


def from_arm_box(arm: Box, fw: int, fh: int) -> Box:
    margin = max(12.0, min(fw, fh) * 0.03)
    from_right = fw - arm.right <= margin
    from_left = arm.left <= margin
    from_bottom = fh - arm.bottom <= margin
    max_span = min(arm.right - arm.left, fw * 0.38)
    left = max(arm.left, arm.right - max_span) if from_right else arm.left
    right = min(arm.right, arm.left + max_span) if from_left and not from_right else arm.right
    mid_x = (left + right) * 0.5
    mid_y = (arm.top + arm.bottom) * 0.5
    if from_right and (not from_left or right - mid_x >= mid_x - left):
        wrist = (right, mid_y)
    elif from_left:
        wrist = (left, mid_y)
    elif from_bottom:
        wrist = (mid_x, arm.bottom)
    else:
        wrist = (mid_x, mid_y)
    aim_y = (arm.top + mid_y) * 0.5
    if from_right:
        tip = (left, aim_y)
    elif from_left:
        tip = (right, aim_y)
    else:
        tip = (mid_x, arm.top)

    def along(t: float) -> tuple[float, float]:
        return wrist[0] + (tip[0] - wrist[0]) * t, wrist[1] + (tip[1] - wrist[1]) * t

    mcp = along(0.35)
    points = [mcp] * 21
    points[WRIST] = along(0.0)
    points[INDEX_MCP] = mcp
    points[INDEX_PIP] = along(0.55)
    points[INDEX_DIP] = along(0.75)
    points[INDEX_TIP] = along(0.95)
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return Box(
        kind="hand",
        label="pointing hand",
        score=0.55,
        left=min(xs),
        top=min(ys),
        right=max(xs),
        bottom=max(ys),
        points=points,
        synthetic=True,
    )


def pointable(box: Box, fw: int, fh: int) -> bool:
    if box.held or box.kind != "object" or box.track_id <= 0 or box.right <= box.left:
        return False
    if box.label.lower() == "person":
        return False
    if box.label.lower() in {"dining table", "bed", "couch", "bench"}:
        return False
    if fw > 0 and fh > 0 and box.area() > fw * fh * MAX_POINTABLE_FRACTION:
        return False
    return True


def _dist3(hand: Box, a: int, b: int, has_depth: bool) -> float:
    dx = hand.points[a][0] - hand.points[b][0]
    dy = hand.points[a][1] - hand.points[b][1]
    dz = (hand.depths[a] - hand.depths[b]) if has_depth else 0.0
    return math.sqrt(dx * dx + dy * dy + dz * dz)


def _cosine3(hand: Box, back: int, joint: int, forward: int, has_depth: bool) -> float:
    ax = hand.points[back][0] - hand.points[joint][0]
    ay = hand.points[back][1] - hand.points[joint][1]
    az = (hand.depths[back] - hand.depths[joint]) if has_depth else 0.0
    bx = hand.points[forward][0] - hand.points[joint][0]
    by = hand.points[forward][1] - hand.points[joint][1]
    bz = (hand.depths[forward] - hand.depths[joint]) if has_depth else 0.0
    al = math.sqrt(ax * ax + ay * ay + az * az)
    bl = math.sqrt(bx * bx + by * by + bz * bz)
    if al * bl < 1e-3:
        return 1.0
    return (ax * bx + ay * by + az * bz) / (al * bl)


def entered(ray: Ray, box: Box) -> Optional[float]:
    if box.left <= ray.tip_x <= box.right and box.top <= ray.tip_y <= box.bottom:
        return 0.0
    t_min, t_max = 0.0, ray.reach
    origin = (ray.tip_x, ray.tip_y)
    direction = (ray.dir_x, ray.dir_y)
    mins = (box.left, box.top)
    maxs = (box.right, box.bottom)
    for axis in (0, 1):
        if abs(direction[axis]) < 1e-4:
            if origin[axis] < mins[axis] or origin[axis] > maxs[axis]:
                return None
        else:
            near = (mins[axis] - origin[axis]) / direction[axis]
            far = (maxs[axis] - origin[axis]) / direction[axis]
            if near > far:
                near, far = far, near
            t_min = max(t_min, near)
            t_max = min(t_max, far)
            if t_min > t_max:
                return None
    return t_min


def along_ray(ray: Ray, objects: list[Box], prefer_id: Optional[int], fw: int, fh: int) -> Optional[Box]:
    hits = []
    for box in objects:
        if not pointable(box, fw, fh):
            continue
        d = entered(ray, box)
        if d is not None:
            hits.append((box, d))
    if not hits:
        return None
    closest = min(hits, key=lambda h: (h[1], h[0].track_id))
    preferred = next((h for h in hits if h[0].track_id == prefer_id), None)
    if preferred and preferred[1] <= closest[1] * RAY_HYSTERESIS + 8.0:
        return preferred[0]
    return closest[0]


def extended(hand: Box, fw: int, fh: int, min_score: float = MIN_HAND_SCORE) -> Optional[Ray]:
    points = hand.points
    if len(points) < 21 or hand.score < min_score:
        return None
    wrist, mcp, pip, dip, tip = points[WRIST], points[INDEX_MCP], points[INDEX_PIP], points[INDEX_DIP], points[INDEX_TIP]
    joints = [wrist, mcp, pip, dip, tip]
    if any(not math.isfinite(p[0]) or not math.isfinite(p[1]) for p in joints):
        return None
    if any(p[0] < -4 or p[1] < -4 or p[0] > fw + 4 or p[1] > fh + 4 for p in joints):
        return None
    has_depth = len(hand.depths) >= 21
    palm = _dist3(hand, INDEX_MCP, WRIST, has_depth)
    if palm < 4:
        return None
    ranked = []
    for tip_i, mcp_i, pip_i, dip_i in FINGERS:
        ranked.append(
            (
                tip_i,
                _dist3(hand, tip_i, WRIST, has_depth),
                _dist3(hand, tip_i, mcp_i, has_depth),
                _cosine3(hand, mcp_i, pip_i, tip_i, has_depth),
            )
        )
    ranked.sort(key=lambda r: r[1], reverse=True)
    index = next(r for r in ranked if r[0] == INDEX_TIP)
    others = [r[1] for r in ranked if r[0] != INDEX_TIP]
    other_max = max(others) if others else 0.0
    other_min = min(others) if others else 0.0
    open_palm = other_min >= index[1] * 0.85 and index[1] >= palm * 0.8 and dist(tip, mcp) >= palm * 0.50
    dx, dy = tip[0] - mcp[0], tip[1] - mcp[1]
    projected = hypot(dx, dy)
    dz = (hand.depths[INDEX_TIP] - hand.depths[INDEX_MCP]) if has_depth else 0.0
    segment = math.sqrt(dx * dx + dy * dy + dz * dz)
    depth_pointing = has_depth and abs(dz) > max(palm * 0.22, projected * 0.40)
    depth_dominant = has_depth and abs(dz) > projected * 0.45
    index_out = (index[2] >= palm * 0.55 and index[3] <= -0.12 and index[1] >= palm * 0.75) or (
        depth_pointing and index[3] <= 0.05 and index[2] >= palm * 0.35
    )
    forearm_x, forearm_y = mcp[0] - wrist[0], mcp[1] - wrist[1]
    forearm = hypot(forearm_x, forearm_y)
    pip_along = (pip[0] - mcp[0]) * forearm_x + (pip[1] - mcp[1]) * forearm_y
    side_on = projected < palm * 0.50 and forearm >= 8 and pip_along >= 0 and other_max <= index[1] * 1.15 and not open_palm
    if segment < 3 and not side_on:
        return None
    if (open_palm and not depth_pointing and not side_on) or (not index_out and not side_on) or (
        not depth_pointing and not side_on and other_max > index[1] * 1.10
    ):
        return None
    if side_on and forearm >= 1:
        scale = max(palm, forearm) * 0.85 / forearm
        repaired = (mcp[0] + forearm_x * scale, mcp[1] + forearm_y * scale)
        aim_dx, aim_dy = forearm_x, forearm_y
    else:
        repaired = tip
        aim_dx, aim_dy = repaired[0] - mcp[0], repaired[1] - mcp[1]
    wrist_to_tip = (repaired[0] - wrist[0], repaired[1] - wrist[1])
    wrist_planar = hypot(*wrist_to_tip)
    planar_aim = hypot(aim_dx, aim_dy)
    if planar_aim >= 8:
        dir_x, dir_y = aim_dx / planar_aim, aim_dy / planar_aim
    elif wrist_planar >= 8:
        dir_x, dir_y = wrist_to_tip[0] / wrist_planar, wrist_to_tip[1] / wrist_planar
    elif planar_aim >= 1:
        dir_x, dir_y = aim_dx / planar_aim, aim_dy / planar_aim
    else:
        dir_x, dir_y = 0.0, 0.0
    reach = hypot(float(fw), float(fh))
    return Ray(
        repaired[0],
        repaired[1],
        dir_x,
        dir_y,
        reach,
        joints,
        depth_dominant,
        reversible=not hand.synthetic and (side_on or depth_dominant or planar_aim < 8),
    )


def aimed(boxes: list[Box], fw: int, fh: int, prefer_id: Optional[int] = None) -> Optional[Aimed]:
    hands = [b for b in boxes if b.kind == "hand" and len(b.points) >= 21]
    if not hands:
        return None
    hand = max(hands, key=lambda h: h.score)
    ray = extended(hand, fw, fh)
    if ray is None:
        return None
    objects = [b for b in boxes if b.kind == "object"]
    hit = along_ray(ray, objects, prefer_id, fw, fh)
    if hit is None and ray.reversible:
        flipped = replace(ray, dir_x=-ray.dir_x, dir_y=-ray.dir_y)
        hit = along_ray(flipped, objects, prefer_id, fw, fh)
        if hit is not None:
            ray = flipped
    return Aimed(ray, hit)


class TrackIds:
    def __init__(self) -> None:
        self.next_id = 1
        self.prev: list[Box] = []

    def assign(self, boxes: list[Box]) -> list[Box]:
        objects = [b for b in boxes if b.kind == "object"]
        rest = [b for b in boxes if b.kind != "object"]
        used: set[int] = set()
        assigned: list[Box] = []
        for box in objects:
            best, best_iou = None, IOU_MATCH
            for old in self.prev:
                if old.track_id in used:
                    continue
                score = iou(box, old)
                if score > best_iou:
                    best, best_iou = old, score
            if best is not None:
                box.track_id = best.track_id
                used.add(best.track_id)
            else:
                box.track_id = self.next_id
                self.next_id += 1
            assigned.append(box)
        self.prev = assigned
        return assigned + rest


class AimLock:
    def __init__(self) -> None:
        self.locked_id: Optional[int] = None
        self.pending_id: Optional[int] = None
        self.pending_count = 0
        self.lost_frames = 0
        self.acquire = 2
        self.switch = 6
        self.lost_limit = 8

    def update(self, boxes: list[Box], fw: int, fh: int) -> Optional[int]:
        objects = [b for b in boxes if pointable(b, fw, fh)]
        current = self.locked_id
        aim = aimed(boxes, fw, fh, prefer_id=current)
        ray_target = aim.hit.track_id if aim and aim.hit else None
        measured = any(
            b.kind == "hand" and not b.synthetic and len(b.points) >= 21 and b.score >= MIN_HAND_SCORE
            for b in boxes
        )
        inferred = any(b.kind == "hand" and b.synthetic for b in boxes)
        live_hand = measured or inferred
        visible = current is not None and any(b.track_id == current for b in objects)
        if current is not None and not visible:
            nxt = None if inferred and not measured else ray_target
            if nxt is not None:
                self.lost_frames = 0
                return self._commit(nxt, current, 2)
            self.pending_id = None
            self.pending_count = 0
            self.lost_frames += 1
            if self.lost_frames < self.lost_limit:
                return current
            self.locked_id = None
            self.lost_frames = 0
            return None
        self.lost_frames = 0
        if inferred and not measured:
            if current is not None and ray_target == current:
                return current
            self.pending_id = None
            self.pending_count = 0
            self.locked_id = None
            return None
        elif ray_target is not None:
            chosen = ray_target
        elif current is not None:
            chosen = current
        elif live_hand:
            chosen = None
        else:
            chosen = None
        return self._commit(chosen, current, self.acquire if current is None else self.switch)

    def _commit(self, aimed_id: Optional[int], current: Optional[int], required: int) -> Optional[int]:
        if aimed_id is None or aimed_id == current:
            self.pending_id = None
            self.pending_count = 0
            return current
        if self.pending_id == aimed_id:
            self.pending_count += 1
        else:
            self.pending_id = aimed_id
            self.pending_count = 1
        if self.pending_count >= required:
            self.locked_id = aimed_id
            self.pending_id = None
            self.pending_count = 0
        return self.locked_id
