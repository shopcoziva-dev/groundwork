# No-slop design rules

Applies to every store the pipeline builds: theme, type, colour, layout, banners, AI product photos and copy. If the store's own brand brief explicitly asks for something on this list, the brief wins. Otherwise each item is a default you need a stated reason to break. Code enforces the banned words (groundwork/lib/rules.py); the rest is checked by the image reviewer and before launch.

## 1. Telltale signs of AI slop (fail on sight)
- Purple-to-blue, teal-to-pink or any decorative gradient wash. Gradient text in any form.
- Glassmorphism: frosted, blurred, semi-transparent cards or nav bars used as decoration.
- Three or four identical rounded cards (icon + heading + two lines) as the main page structure. Cards nested in cards.
- The same border-radius and the same soft grey shadow on every element.
- Emoji or unicode symbols used as bullets, icons or button decoration.
- A small ALL-CAPS tracked-out label above every heading ("WHY CHOOSE US").
- 01 / 02 / 03 numbering on content that is not actually a sequence.
- Big-number hero stats ("10,000+ happy customers") nobody can verify.
- Everything centred: hero, every section, every paragraph.
- Fade-and-slide-up animation on every section.
- A thick coloured left border on callouts, review cards or list items.
- A generic stock or AI "lifestyle" hero unrelated to the product (smiling woman with a laptop, sunset mountain).
- Default AI palettes: cream + serif headline + terracotta accent; black + a single acid-green accent.
- Image defects: extra or fused fingers, warped or nonsense text on labels, melted zips or seams, mismatched buttons, objects merging, over-glossy plastic render look.
- Copy that opens with "Elevate your…", "Discover the…" or "Unlock…".

## 2. Typography
- At most two families; if two, clearly different, not two similar sans faces.
- Avoid the usual defaults as the headline face (Inter, DM Sans, Outfit, Plus Jakarta Sans, Space Grotesk, Playfair Display, Fraunces, Cormorant) unless there is a written reason. Pick a face that fits the product's world.
- A clear type scale with visible steps between H1, H2, body and caption.
- Body text at least 16px, line length 65–75 characters, a little more line-height for serif body.
- Sentence case for headings and buttons. No all-caps labels; no single word picked out in italics or another colour.
- No monospace "tech" styling unless it is real data (spec tables).
- Theme the leftovers: link underline, selection colour, focus ring.

## 3. Colour
- Palette of 4–6 named hex values taken from the product's own materials (fabric, metal finish, wood, the colour of lamp light).
- One accent, mostly on the primary button and the price; not on every heading.
- A real black or a dark from the brand palette, not a generic #111 tint. On coloured backgrounds tint secondary text from that colour, never mid-grey.
- Contrast: body text at least 4.5:1, large text at least 3:1, including text over banner images.
- Light or dark comes from where the customer shops and how the product looks, not from the category.

## 4. Layout and spacing
- Left-align body copy and most section headings; centre only short single-purpose blocks.
- The first screen shows the product, the offer and a working Add to Cart or Shop button.
- Vary section shapes: full-bleed image, then a split section, then a dense spec table. No stack of identical card rows.
- Related items close together, clear space between sections, more space above a heading than below it.
- One memorable element per page; the rest quiet. Before publishing, remove one decorative element.
- At most one deliberate animation (e.g. on hero load). Hover and click feedback is fine.
- At 375px: no horizontal scroll, buttons at least 44px tall, no text cut off in banners.

## 5. Imagery and AI photos (check every image at 100% zoom)
- Product truth: colour, shape, logo placement, pocket and button count, cable and plug type match the supplier's reference photos. Never show a feature the product does not have.
- Anatomy: hands, fingers, ears, teeth. Faces do not repeat across photos; eyes and limbs look natural.
- Text in the image: no garbled lettering on labels, tags, packaging or background. If text cannot be clean, remove it.
- Physics: seams, zips, folds and shadows consistent; lamps light their surroundings plausibly; nothing floats or merges.
- Finish: no plastic sheen, no over-smoothed skin, no HDR halos, not the same golden-hour light on every photo.
- Scene: specific and believable (a commuter on a cold train platform, a reading nook), not a generic luxury loft.
- Consistency: all images in a collection share light, crop and background logic; banners leave clean space for text.

## 6. Copy voice
Plain, specific statements a shopper can check. A number or a fact instead of an adjective.
- Bad: "Elevate your winter with unparalleled warmth."
- Good: "Three carbon-fibre heat zones. Up to 8 hours on low with the included 10,000 mAh battery."
- Banned words and phrases (code rejects them): elevate, unlock, discover, unleash, embark, journey, seamless, effortless, game-changer, revolutionary, cutting-edge, next-level, must-have, premium quality, high-quality, unparalleled, unmatched, world-class, curated, meticulously crafted, designed with you in mind, take your X to the next level, look no further, whether you're X or Y, in today's fast-paced world, say goodbye to, redefine, transform your, perfect for any occasion, a testament to, delve, nestled.
- No vague superlatives and no invented claims: no fake review counts, "as seen on", certifications not held, or made-up delivery times.
- No three-adjective lists ("warm, stylish and versatile"); no exclamation marks in body copy.
- Buttons say exactly what happens: "Add to cart", "See sizes", not "Submit" or "Explore".
- Product pages lead with what the item is, who it is for and the key spec. Shipping and returns use real numbers that match the policy.

## 7. Pre-publish checklist (all must be YES)
1. No decorative gradients, glassmorphism or gradient text?
2. No emoji or unicode used as icons or bullets?
3. No eyebrow labels or fake 01/02/03 numbering?
4. First screen shows the real product, the price or offer and a working buy button?
5. Body copy left-aligned; sections differ in shape?
6. At most two typefaces, neither a usual default without a written reason?
7. All text passes contrast, including over banners and on mobile?
8. Every AI image passed the hands, text, physics and plastic-finish checks at 100% zoom?
9. Every image matches the real supplier product (colour, features, parts)?
10. Every banned word or phrase gone from titles, descriptions, banners, emails and meta descriptions?
11. Every claim checkable, with numbers from the supplier spec sheet and none invented?
12. Every button label says exactly what it does?
13. No more than one deliberate animation?
14. At 375px: no horizontal scroll, no cropped banner text, buttons at least 44px tall?
15. Put another store's logo on this page: would it look wrong? If not, it is still a template; redo it.

---
Adapted in our own words from the Anthropic frontend-design skill (anthropics/skills) and Impeccable by Paul Bakaus, both Apache-2.0.
