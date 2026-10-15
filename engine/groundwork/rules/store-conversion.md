# Store conversion rules

For Shopify dropshipping stores ($40–$300) where Google Shopping and Performance Max ads send phone shoppers straight to product pages. Every rule should be checkable at a 390px viewport.
Written in our own words from Medusa's storefront best-practices skill (medusajs/medusa-claude-plugins), adapted to Coziva. Where this file and a settled Coziva rule disagree, the Coziva rule is noted and wins until Jason says otherwise.

## Product page
- Above the fold at 390×844: main image, short title (H1), price with any compare-at, variant picker, Add to Cart. Nothing above the image except the header and one announcement bar.
- Image order follows the Coziva shot plan: 1 studio, 2 lifestyle, 3 product alone, 4 detail, 5 feature. 5–7 images.
- Product gallery shows the whole product, no crop (Coziva rule): letterbox only on a background that matches the photo. Collection cards, hero and banners use object-fit: cover.
- Gallery is a full-width swipe carousel with a position indicator (1/6) that opens full-screen on tap.
- Image 1 is not lazy-loaded and has explicit width and height; every later image uses loading="lazy". Each file under 500KB, under 300KB at phone size.
- Price is the largest text in the info block (28–36px desktop, at least 24px mobile). Compare-at sits beside it, struck through and muted. A "Save $X" or "-X%" label appears only when compare-at is set.
- Compare-at should be a price the product really sold at. ⚠ Coziva sets compare-at = price + $30: Google and the FTC can treat that as a misleading reference price. Jason decides; if Google ever flags pricing, remove compare-at everywhere.
- Variant options with 2–8 values use visible buttons, not a dropdown. Colour swatches 40–48px. Every option at least 44×44px.
- With more than one variant and none chosen, Add to Cart says "Select a size" and highlights the picker; it never adds a default variant silently.
- Picking a variant updates price, availability and (for colour) the main image without a reload. Sold-out combinations are greyed out and unselectable.
- Add to Cart: full width, 48–56px tall, primary brand colour, loading state, stays on the page and opens the cart drawer.
- Directly under Add to Cart, 3 short trust lines: free-shipping threshold, delivery window in business days, one-line returns summary linked to the policy. Wording matches the real policy exactly.
- Payment icons (about 40px) under the trust lines, only for methods actually turned on.
- Description: 1–2 sentence benefit and use-case lead, 4–6 spec bullets, 2–3 FAQs. Shipping & Returns in a collapsed accordion. On mobile everything below the buy box is an accordion.
- FAQ answers the 2–3 objections that most often stop a purchase (power, sizing, care, delivery time), 1–3 sentences each.
- Rating summary under the title only when real reviews exist. No reviews = no stars at all, never 0 stars.
- Sticky Add to Cart bar (short title, price, button) appears once the main button scrolls away; bottom padding includes env(safe-area-inset-bottom).
- One complementary add-on block with a real discount checkout applies automatically. Related products below the reviews, 2 visible on mobile (1.5 to hint at scrolling), never auto-play.

## Collection pages
- Mobile exactly 2 columns, 12–16px gap. Desktop 3–4 columns.
- Product card: image (cover), title of at most 2 lines, price with compare-at, at most 1–2 badges (Sold out > Sale > New).
- Sort: Featured, Best selling, Price low–high, Price high–low, Newest. Mobile filters in a drawer with Apply, only when the collection has more than about 12 products.
- 1–2 sentence keyword intro on top; longer SEO text below the grid.
- Empty collections show a message and a link to all products, and are hidden from menus.

## Home page
- Order: hero, value-prop row (3–4 items), category tiles (4–8), best sellers (8–15), mid-page promo, reviews or social proof, footer.
- Hero headline 5–10 words, one CTA at least 48px tall. Mobile hero portrait (3:4), under 300KB.
- Hero carousel at most 3 slides, 5–7 seconds each; nothing important only on slide 2+.
- 40–60px between sections on mobile; one clear CTA per section.
- One newsletter form on the page, stating the benefit.

## Cart and checkout
- Cart drawer opens after each add: 85–95% of the screen on mobile, 320–400px on desktop.
- Each line: thumbnail, short title, chosen options ("Size: L"), quantity stepper at least 40px, remove link, line total.
- Free-shipping progress bar ("$X away from free shipping") matching the shipping settings.
- Discounts as a labelled line with the amount, only on lines that got money off.
- Checkout button full width, 48–56px, in a sticky footer with the subtotal. "View cart" is secondary.
- Guest checkout on, no account required. Shop Pay, Apple Pay, Google Pay on.
- Shipping cost never a surprise: rates shown on the product or policy page before checkout.
- No popup and no heavy third-party scripts on cart or checkout.

## Navigation, search and footer
- Mobile header: hamburger left, logo centre, search and cart right. Cart icon shows an item count, never a price.
- Sticky header with a solid background; every icon a 44×44px tap target.
- 4–7 top-level menu links mirroring the collections; mobile drawer accordions, at most 2 levels.
- Search opens full-screen on mobile, suggests after 2–3 characters, 5–10 product suggestions with image and price.
- Footer: Shop links, Help (Contact, FAQ, Shipping, Returns, Track order), Privacy, Terms, contact email, payment icons, current year. Single column on mobile, links at least 44px tall.

## Popups and promotions
- At most one popup per session, never on load: wait 30s desktop, 60s mobile.
- Mobile popups at most 90% of the width, close button at least 44×44px.
- After a dismissal wait 30 days; never again for someone who signed up.
- Announcement bar for standing offers, one short line, 40–48px tall on mobile.
- Countdown timers only for sales that really end, on server time, hidden when over.
- "Low stock" only when real inventory is 5 or fewer.

## Speed and mobile
- Mobile LCP under 2.5s, CLS under 0.1, INP under 200ms; check product pages in PageSpeed Insights before launch.
- Every image has explicit dimensions; WebP through Shopify's CDN with responsive widths.
- Minimum apps; remove any app script not used on the page it loads on.
- Inputs at least 16px (stops iOS zoom) with the right type (email, tel).
- Nothing depends on hover; every action works by tap.
- Body text at least 16px, line height 1.5–1.6, contrast at least 4.5:1.

## SEO basics for Shopping feeds
- Full keyword title stays in the Shopify product title (the feed uses it); the storefront shows a shorter title.
- Landing-page price, availability and variants exactly match the feed; Product schema availability reflects real stock.
- GTIN or MPN, brand, colour, gender, age group and size set for every variant that has them. Never guess a value.
- Own meta title (50–60 characters) and meta description (150–160) per product.
- Image alt text describes the product and its key visible attribute in under 125 characters, no keyword stuffing.
- Product schema, and BreadcrumbList on product and collection pages; AggregateRating only with real reviews.
- Short hyphenated handles; never change a live handle without a redirect.
- Shipping, Returns, Contact, Privacy and Terms pages exist and match what Merchant Center has on file.

## Trust
- Real business name, contact email, response time ("within 24 hours") and a city on the contact page.
- Same returns wording on the product page, policy page, FAQ and homepage trust bar.
- Delivery times in business days, matching fulfilment reality.
- Reviews are real; "verified purchase" only on actual orders; reply to negative reviews instead of deleting them.
- Every badge or claim (secure checkout, guarantee) reflects something real.

## Never
- Letterbox bars in a colour that does not match the photo background.
- A full-screen popup on load on mobile, or any popup on cart or checkout.
- Fake reviews or star ratings, a perfect 5.0 on a new store, or 0 stars shown.
- Fake urgency: evergreen countdowns, invented "only 3 left", fake "X people viewing".
- Fake security or certification badges, or payment icons for methods not accepted.
- A returns or shipping promise the policy does not honour.
- A cart icon hidden in the hamburger, or a cart badge showing a price.
- Adding a variant the shopper did not choose, or leaving the product page after Add to Cart.
- Auto-playing product sliders, hover-only actions, tap targets under 44px.
- Emojis in storefront UI.
- Mismatches between the feed and the page in price, stock or title.
- Lazy-loading the first product or hero image, or images over 500KB.
- Two newsletter forms on one page.
