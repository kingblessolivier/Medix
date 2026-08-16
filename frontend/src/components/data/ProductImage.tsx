/* The picture of the pack.
 *
 * A buyer ordering from a screen cannot pick the box up, and two
 * medicines whose names differ by one word can be different products.
 * The image is how the presentation gets checked — same strength, same
 * count, same manufacturer's artwork — before a carton is committed to.
 *
 * It is verification, never identification. The name, strength, form,
 * pack and manufacturer are always shown beside it, because a picture
 * can be out of date, generic, or of the wrong box entirely, and a buyer
 * who ordered on the artwork alone has no recourse.
 *
 * The fallback is a labelled placeholder rather than a broken image
 * frame: "no photo" is information, a grey square is a bug.
 */

import { ImageOff, Snowflake } from "lucide-react";
import { useState } from "react";

const SIZES = {
  /** In a table row, beside the name. */
  row: "size-8 rounded-sm",
  /** On a browse card. */
  card: "h-[104px] w-full rounded-none",
  /** In the detail modal. */
  detail: "size-28 rounded-md",
} as const;

export function ProductImage({
  src,
  alt,
  size = "row",
  coldChain = false,
}: {
  src?: string | null;
  /** What the picture shows. Required by the API for this reason. */
  alt?: string;
  size?: keyof typeof SIZES;
  coldChain?: boolean;
}) {
  const [failed, setFailed] = useState(false);

  if (!src || failed) {
    return (
      <span
        className={`flex shrink-0 items-center justify-center border border-hair bg-content text-text-3 ${SIZES[size]}`}
        // Decorative: everything it could convey is in the text beside it.
        aria-hidden
      >
        {coldChain ? (
          <Snowflake size={size === "row" ? 13 : 18} strokeWidth={1.6} className="text-brand" aria-hidden />
        ) : (
          <ImageOff size={size === "row" ? 12 : 16} strokeWidth={1.6} aria-hidden />
        )}
      </span>
    );
  }

  return (
    <img
      src={src}
      alt={alt || ""}
      loading="lazy"
      onError={() => setFailed(true)}
      className={`shrink-0 border border-hair bg-content object-cover ${SIZES[size]}`}
    />
  );
}
