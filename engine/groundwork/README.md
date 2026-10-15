# Groundwork: niche in, store out

Jason's general store builder. It is built from the Coziva pipeline and works for any niche. One command:

```
python groundwork/gw.py new "pet grooming" --auto --listings 3
```

It can also run from GitHub: **Actions > Groundwork > Run workflow**, `command=new`, `niche=pet grooming`, `args=--auto`. In a Claude Code session on this repo, type `/newstore pet grooming`.

Every store starts in **DRY RUN**. Claude, CJ and market reads happen for real and everything gets planned. Writes to Shopify and CJ are only logged. When the dry run looks right, turn it off with `gw.py golive --store <slug> --confirm <slug>`.

## The chain

| # | Stage | AI does | Code does | Writes | Waits on Jason |
|---|---|---|---|---|---|
| 1 | `scout` | Researches the niche on the web: corners, advertisers, prices, season, ad policy. Runs in ROUNDS: `scout --angle "..."` researches again with Jason's angle and every earlier round in view | Scores with the beta rule (≥5 advertisers, ≥15% at 2x, policy). **Price room**: the profit floor must fit under the market top | `scout.json` | `approve` (or `--auto` takes the GO corners) |
| 1b | `stores` | Finds 12-15 specialist stores in the approved corners (DTC, Shopify, running ads) | Drops marketplaces and general stores by domain; opens each Shopify store's own catalog and measures **focus** (share of products that are this niche, <60% = dropped); size, median price, top types | `model_stores.json` | — |
| 2 | `profile` | Writes the niche's rules: product types, photo framing, poses, scenes, the 5th shot, physical rules, colours, voice, collections, sourcing guards. Coziva's rules are its worked example | Validates every field | `profile.json` | — (edit the file to change a rule) |
| 3 | `catalog` | Vets each CJ candidate from its photos: type, lettering, brand, buyer fit, colour, shopper searches | CJ search ranked by stores already selling it; blocklist; shipping ≤12 days; stock ≥100; market prices; **the live Coziva price formula** | `shelf.json` | — |
| 4 | `brand` | 12 names, then a trademark/web check and the brand kit (tagline, colours, fonts, About) | .com availability (registry lookup); SKU prefix | `brand.json` | `--pick NAME` (or `--auto`); buying the domain |
| 5 | `setup` | Policies, FAQ, About, Shipping and Returns pages that match the store's settings | Policies, pages, smart collections, main menu through the Admin API; Jason's checklist | `store_setup.json` | Payments, plan, theme colours, apps |
| 6 | `list` (dry-run records go to `dryrun/`, real ones to `listings/`) | Fact sheet from the photos; copy (title, short title, description and FAQ, 13 tags, alt text) | Draft, clean variants, CJ link and read-back, 5 photos with a vision check, price, upload in order, supplier photos removed, Google fields, publish, ACTIVE, storefront 200. A failure archives its draft | `listings/<SKU>.json` | — |
| 7 | `ads` | PMax asset text inside Google's limits, search themes | Campaign settings (Coziva as built), launch gates, break-even and stop rules from real prices | `ads.json` | Creating the campaign and billing |

## Where things come from

- **Pricing**: `lib/pricing.py` is the live `scripts/stage2.py` formula with every number in one rules dict. `tests/test_pricing.py` proves the two give identical results on all 78 collected Coziva ad files (780 cases).
- **Market prices** (`lib/market.py`): Google Shopping through SerpAPI (`SERPAPI_KEY`, about $0.01 a search) when it's set. Otherwise Claude web search, which is lower confidence and tagged `web`. No Chrome step.
- **Photos** (`stages/images.py` + `prompts/`): Coziva's v4 prompts with the heated-only parts turned into slots that the profile fills. The same models are used in cheap mode: studio and hero shots on the pro model with a vision check and one redo, the other shots on flash. The model roster reuses `references/models/`.
- **Per-store logins** (never printed, never Coziva's): in GitHub, one **environment per store** named after its slug (Settings > Environments > `<slug>`) holding `GW_SHOPIFY_CLIENT_ID` + `GW_SHOPIFY_CLIENT_SECRET` (or `GW_SHOPIFY_ADMIN_TOKEN`). Locally, `SHOPIFY_CLIENT_ID_<SLUG>` etc. The plain `SHOPIFY_*` secrets (Coziva's) are never read, and a store with no `shop_domain` reads nothing. Shared repo secrets: `ANTHROPIC_API_KEY`, `GEMINI_API_KEY`, `CJ_API_KEY`, optional `SERPAPI_KEY`.

## Store settings (`stores/<slug>/store.json`, change with `gw.py set --store S key=value`)

`shop_domain`, `public_domain`, `sku_prefix`, `cj_shop_id` (from `gw.py cj-shops`), `catalog_target` (50), `images_per_day` (200), `image_mode` (cheap), `free_shipping_over` (70), `returns_days` (30), `returns_policy` (standard | defective_only), `ship_days`, `support_email`, `business_city`, `roster`, `channels`, `discount_code_pct` (0.10).

## Tests (offline, no keys, no cost)

```
python groundwork/tests/test_pricing.py      # 780/780 identical to the live Coziva formula
python groundwork/tests/test_safety.py       # no borrowed logins, failed calls stop, buyer-only variants
python groundwork/tests/test_e2e_mock.py     # whole chain on a product-only niche + the photo plan for a people niche
python groundwork/tests/test_live_path.py    # go-live sequence against a fake Shopify, incl. archive-on-fail
```

## Not done yet (needs a real run)

- No live API call has been made yet. Claude prompts, CJ responses and the Admin API mutations (`pageCreate`, `menuUpdate`, `shopPolicyUpdate`) are written to the documented shapes and tested against fakes. The first real dry run on a niche will show anything that's off.
- Theme colours and fonts are a checklist item, not written to the theme (theme settings differ between themes).
- Delivery-time settings in Shopify shipping are a checklist item.
