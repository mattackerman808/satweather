import type { Map as MapLibre, PaddingOptions } from "maplibre-gl";

// The phone layout: the width at which styles.css's phone rules switch on
// (keep the two in step). Panels become bottom sheets, side lists become
// pickers or collapse behind a button.
const mq = matchMedia("(max-width: 760px)");
export const isNarrow = () => mq.matches;

// Touch screens have no hover; previews that follow the cursor stay off.
export const canHover = () => matchMedia("(hover: hover)").matches;

// fitBounds padding that keeps the target clear of the panels: the left
// column and the right-hand panel on a desktop, the bottom sheet (55% of
// the map, see .inspector) on a phone.
export function fitPadding(map: MapLibre, left = 80): PaddingOptions {
  if (!isNarrow()) return { top: 80, bottom: 80, left, right: 480 };
  return { top: 72, bottom: Math.round(map.getContainer().clientHeight * 0.55) + 16, left: 24, right: 24 };
}

// On a phone, a spot tapped where the bottom sheet is about to open is
// panned up into the part of the map left showing.
export function clearOfSheet(map: MapLibre, point: { x: number; y: number }) {
  if (!isNarrow()) return;
  const shown = map.getContainer().clientHeight * 0.45;
  if (point.y > shown - 24) map.panBy([0, point.y - shown / 2]);
}
