"use client";

import { useEffect, useState } from "react";
import {
  applyChoice,
  readStoredChoice,
  type ThemeChoice,
} from "@/lib/theme";

const ORDER: ThemeChoice[] = ["system", "light", "dark"];
const GLYPH: Record<ThemeChoice, string> = { system: "◐", light: "☀", dark: "☾" };
const NAME: Record<ThemeChoice, string> = {
  system: "Match system",
  light: "Light",
  dark: "Dark",
};

/**
 * Cycles system → light → dark. The stored choice is applied by an inline script
 * in the document head before first paint, so the page never flashes the wrong
 * theme; this button only has to keep up after hydration.
 */
export default function ThemeToggle() {
  const [choice, setChoice] = useState<ThemeChoice>("system");

  useEffect(() => setChoice(readStoredChoice()), []);

  const next = () => {
    const value = ORDER[(ORDER.indexOf(choice) + 1) % ORDER.length];
    setChoice(value);
    applyChoice(value);
  };

  return (
    <button
      type="button"
      className="icon-btn"
      onClick={next}
      aria-label={`Theme: ${NAME[choice]}. Activate to change.`}
      title={`Theme: ${NAME[choice]}`}
    >
      <span aria-hidden="true">{GLYPH[choice]}</span>
    </button>
  );
}
