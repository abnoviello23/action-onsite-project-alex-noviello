/**
 * Design tokens for both the DOM chrome and the canvas.
 *
 * One source of truth, in JS rather than CSS, because the canvas cannot read a
 * custom property per frame without a getComputedStyle call in the render loop.
 * App publishes these onto :root as custom properties so the surrounding chrome
 * can use the same values from CSS.
 */

// Categorical slots 1-3, stepped per mode. This exact set was validated
// all-pairs in both modes (worst CVD deltaE 9.4 dark / 9.2 light, worst
// normal-vision 20.9 / 24.0), which is why there are three of them: a fourth
// categorical hue does not clear the all-pairs floors, and a node-link canvas is
// an all-pairs form — any two colours can end up adjacent.
//
// A fourth source would therefore not get a fourth hue; it would get the muted
// slot and lean on its shape, the same way `unknown` does below.
const SERIES = {
  light: ["#2a78d6", "#eb6834", "#1baf7a"],
  dark: ["#3987e5", "#d95926", "#199e70"],
};

const MUTED_SERIES = "#898781";

// Sources past the third fall back to the muted tone.
//
// Three is the ceiling here, not a shortcut: a node-link canvas is an
// all-pairs form — any two colours can end up adjacent — and every fourth hue
// in the ramp fails the CVD or normal-vision floor against one of these three
// (violet reads as blue at deltaE 1.9, yellow as orange at 4.8, magenta as
// aqua at 1.6). A neutral slate is no better: at chroma 0.04 it is a grey that
// still sits only 12.9 from blue.
//
// So a fourth source stays unhued and leans on the channels that already carry
// identity here anyway — its own labelled cluster, and the node type printed on
// every block. It shares the tone with `unknown`, which is unambiguous in
// practice because an unmaterialized node also renders hatched.

const TOKENS = {
  light: {
    surface: "#fcfcfb",
    plane: "#f9f9f7",
    raised: "#ffffff",
    textPrimary: "#0b0b0b",
    textSecondary: "#52514e",
    textMuted: "#898781",
    hairline: "#e1e0d9",
    border: "rgba(11,11,11,0.10)",
    // Containment. Recessive on purpose: it is the backbone, and at a few
    // hundred edges a confident stroke turns the canvas into a hairball.
    edgePermission: "#c3c2b7",
    // Explicit relations. Ink rather than a series colour, so an edge never
    // looks like it belongs to the source of either endpoint.
    edgeRelation: "#52514e",
    accent: "#2a78d6",
    danger: "#d03b3b",
  },
  dark: {
    surface: "#1a1a19",
    plane: "#0d0d0d",
    raised: "#232322",
    textPrimary: "#ffffff",
    textSecondary: "#c3c2b7",
    textMuted: "#898781",
    hairline: "#2c2c2a",
    border: "rgba(255,255,255,0.10)",
    edgePermission: "#4a4a46",
    edgeRelation: "#c3c2b7",
    accent: "#3987e5",
    danger: "#d03b3b",
  },
};

/**
 * Colour follows the source, never its rank in the current view.
 *
 * Colour is never the only carrier of identity: every block prints its node
 * type as text, which is a stronger redundant encoding than the shape channel
 * this replaced.
 *
 * The assignment is computed from the full source list the API reports and is
 * held in a stable sort, so filtering Slack out of the view does not repaint
 * Drive. `unknown` is not a category — it is the absence of one (an
 * unmaterialized node, minted before its defining event arrived) — so it takes
 * the muted ink rather than a hue.
 */
export function sourceColors(sources, mode) {
  const series = SERIES[mode];
  const ordered = [...sources].filter((s) => s !== "unknown").sort();
  const map = { unknown: MUTED_SERIES };
  ordered.forEach((source, i) => {
    map[source] = i < series.length ? series[i] : MUTED_SERIES;
  });
  return map;
}

export function tokens(mode) {
  return TOKENS[mode];
}

/** Publishes the token set as custom properties, so CSS reads the same values. */
export function applyTokens(mode) {
  const t = TOKENS[mode];
  const root = document.documentElement;
  root.dataset.theme = mode;
  root.style.colorScheme = mode;
  for (const [key, value] of Object.entries(t)) {
    root.style.setProperty(`--${key}`, value);
  }
}

export function preferredMode() {
  return window.matchMedia?.("(prefers-color-scheme: light)").matches
    ? "light"
    : "dark";
}
