"use client";

// Theme plumbing: the resolved light/dark mode and the chart colours that follow
// from it. lightweight-charts is configured in JS, so it cannot read CSS custom
// properties itself — we read them off the document and re-apply on change.

export type ThemeChoice = "light" | "dark" | "system";
export type ResolvedTheme = "light" | "dark";

export const THEME_STORAGE_KEY = "signal-engine-theme";
/** Fired on <html> whenever the stored choice changes, so charts can re-theme. */
export const THEME_EVENT = "signal-engine-themechange";

export function readStoredChoice(): ThemeChoice {
  try {
    const v = localStorage.getItem(THEME_STORAGE_KEY);
    return v === "light" || v === "dark" ? v : "system";
  } catch {
    return "system";
  }
}

export function applyChoice(choice: ThemeChoice): void {
  const root = document.documentElement;
  if (choice === "system") {
    delete root.dataset.theme;
  } else {
    root.dataset.theme = choice;
  }
  try {
    if (choice === "system") localStorage.removeItem(THEME_STORAGE_KEY);
    else localStorage.setItem(THEME_STORAGE_KEY, choice);
  } catch {
    // Private mode / storage disabled: the stamp still applies for this page.
  }
  root.dispatchEvent(new CustomEvent(THEME_EVENT));
}

export function resolveTheme(): ResolvedTheme {
  const stamped = document.documentElement.dataset.theme;
  if (stamped === "light" || stamped === "dark") return stamped;
  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

/** Subscribe to both the OS setting and the in-app toggle. Returns an unsubscribe. */
export function onThemeChange(handler: () => void): () => void {
  const mq = window.matchMedia("(prefers-color-scheme: dark)");
  mq.addEventListener("change", handler);
  document.documentElement.addEventListener(THEME_EVENT, handler);
  return () => {
    mq.removeEventListener("change", handler);
    document.documentElement.removeEventListener(THEME_EVENT, handler);
  };
}

export interface ChartColors {
  text: string;
  grid: string;
  axis: string;
  accent: string;
  accentFill: string;
  positive: string;
  negative: string;
  series2: string;
  series7: string;
}

function cssVar(name: string, fallback: string): string {
  const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return v || fallback;
}

/** Current chart palette, read from the token layer so it follows the theme. */
export function chartColors(): ChartColors {
  return {
    text: cssVar("--chart-label", "#6a6862"),
    grid: cssVar("--chart-grid", "#e1e0d9"),
    axis: cssVar("--chart-axis", "#b9b7ae"),
    accent: cssVar("--series-1", "#2a78d6"),
    accentFill: cssVar("--series-fill", "rgba(42,120,214,0.18)"),
    positive: cssVar("--positive", "#006300"),
    negative: cssVar("--negative", "#bb2222"),
    series2: cssVar("--series-2", "#eb6834"),
    series7: cssVar("--series-7", "#4a3aa7"),
  };
}
