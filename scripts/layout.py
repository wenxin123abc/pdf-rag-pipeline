"""Column-band detection and reading-order helpers."""
from __future__ import annotations

from collections.abc import Iterable


def _usable_blocks(blocks: Iterable[dict], width: float) -> list[dict]:
    result = []
    for block in blocks:
        bbox = block.get("bbox")
        if not bbox or len(bbox) != 4 or width <= 0:
            continue
        block_width = bbox[2] - bbox[0]
        if 0 < block_width < width * 0.85:
            result.append(block)
    return result


def detect_column_bands(
    blocks: Iterable[dict],
    width: float,
    max_columns: int = 4,
    min_blocks: int = 3,
    min_width_ratio: float = 0.15,
) -> list[tuple[float, float]]:
    """Cluster block left edges into page-relative column bands."""
    candidates = _usable_blocks(blocks, width)
    if not candidates:
        return []

    ordered = sorted(candidates, key=lambda item: item["bbox"][0])
    clusters: list[list[dict]] = []
    for item in ordered:
        left = item["bbox"][0]
        if not clusters:
            clusters.append([item])
            continue
        previous = clusters[-1]
        previous_left = sum(block["bbox"][0] for block in previous) / len(previous)
        if left - previous_left > width * 0.09:
            clusters.append([item])
        else:
            previous.append(item)

    bands: list[tuple[float, float]] = []
    for cluster in clusters:
        if len(cluster) < min_blocks:
            continue
        left = min(block["bbox"][0] for block in cluster) / width
        right = max(block["bbox"][2] for block in cluster) / width
        if right - left >= min_width_ratio:
            bands.append((left, min(1.0, right)))

    bands.sort()
    if len(bands) > max_columns:
        ranked = sorted(
            bands,
            key=lambda band: sum(
                1 for block in candidates
                if band[0] <= ((block["bbox"][0] + block["bbox"][2]) / 2) / width <= band[1]
            ),
            reverse=True,
        )[:max_columns]
        bands = sorted(ranked)
    return bands


def order_blocks_by_columns(
    blocks: list[dict],
    width: float,
    columns: int,
) -> list[dict]:
    if columns <= 1 or width <= 0:
        return sorted(blocks, key=lambda block: (round(block["bbox"][1], 1), block["bbox"][0]))

    bands = detect_column_bands(
        blocks,
        width,
        max_columns=max(2, min(columns, 4)),
        min_blocks=1,
        min_width_ratio=0.05,
    )
    if len(bands) < 2:
        return sorted(blocks, key=lambda block: (round(block["bbox"][1], 1), block["bbox"][0]))

    centers = [(left + right) / 2 for left, right in bands]

    def column_of(block: dict) -> int:
        center = (block["bbox"][0] + block["bbox"][2]) / 2 / width
        return min(range(len(centers)), key=lambda index: abs(center - centers[index]))

    return sorted(
        blocks,
        key=lambda block: (
            column_of(block),
            round(block["bbox"][1], 1),
            block["bbox"][0],
        ),
    )
