"use client";

import { lookup } from "@/lib/glossary";

/**
 * A small "?" badge that reveals a definition on hover/focus. Pass a glossary `term` id
 * (preferred — keeps copy consistent) OR an explicit `full`/`def`. Pure CSS tooltip, no deps.
 */
export function InfoTip({
  term,
  full,
  def,
  className = "",
}: {
  term?: string;
  full?: string;
  def?: string;
  className?: string;
}) {
  const entry = term ? lookup(term) : undefined;
  const title = full ?? entry?.full ?? term ?? "";
  const body = def ?? entry?.def ?? "";
  if (!title && !body) return null;
  return (
    <span className={`infotip ${className}`} tabIndex={0} aria-label={`${title}: ${body}`}>
      <span className="infotip-mark" aria-hidden>?</span>
      <span className="infotip-pop" role="tooltip">
        {title && <strong className="infotip-title">{title}</strong>}
        {body && <span className="infotip-body">{body}</span>}
      </span>
    </span>
  );
}

/**
 * A text label followed by an InfoTip — for column headers and section titles.
 * `text` defaults to the glossary term's full form when omitted.
 */
export function InfoLabel({
  term,
  text,
  full,
  def,
  className = "",
}: {
  term?: string;
  text?: string;
  full?: string;
  def?: string;
  className?: string;
}) {
  const entry = term ? lookup(term) : undefined;
  const label = text ?? full ?? entry?.full ?? term ?? "";
  return (
    <span className={`infolabel ${className}`}>
      {label}
      <InfoTip term={term} full={full} def={def} />
    </span>
  );
}
