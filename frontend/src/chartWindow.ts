export type ChartRange = [number, number];

export function clampRange(range: ChartRange, count: number): ChartRange {
    const last = Math.max(0, count - 1);
    if (!last) return [0, 0];
    const start = Math.max(0, Math.min(last - 1, Math.round(range[0])));
    return [start, Math.max(start + 1, Math.min(last, Math.round(range[1])))];
}

export function zoomRange(range: ChartRange, count: number, factor: number): ChartRange {
    const last = Math.max(0, count - 1);
    if (!last) return [0, 0];
    const [start, end] = clampRange(range, count);
    const span = Math.max(1, Math.min(last, Math.round((end - start) * factor)));
    const nextStart = Math.max(0, Math.min(last - span, Math.round((start + end - span) / 2)));
    return [nextStart, nextStart + span];
}
