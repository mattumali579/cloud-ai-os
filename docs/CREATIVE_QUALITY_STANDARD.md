# Creative Quality Standard — Static Ads

Status: CANONICAL
Owner: Matt Umali
Last updated: 2026-09-22

## Non-negotiable baseline

For DTC fitness/static-ad work, the default target is polished, professional, campaign-ready creative comparable to premium paid-social/ecommerce ads. The accepted visual direction is the recent Gymreapers / Crossrope / Therabody batch created on 2026-09-22 after the user rejected the first batch.

### GOOD reference

See: `docs/creative_examples/GOOD_REFERENCE.jpg`

The GOOD reference represents:
- Realistic product/lifestyle photography or highly convincing product-centered scenes.
- Strong art direction: lighting, composition, depth, texture, shadows, environment.
- Clear visual hierarchy: brand → hook → product → proof/features → CTA.
- Product is the visual hero, not an icon placeholder.
- Brand-specific typography, spacing, palette, and tone.
- Layouts that feel like real paid-social/ecommerce creative rather than a generated template.
- Professional feature callouts/icons only when they support the product scene.
- Each concept looks intentionally art-directed, not like the same template with text swapped.
- Commercial polish suitable for Instagram/Facebook/TikTok static placements.
- Copy must stay within verified claims and facts.

### BAD reference / automatic reject pattern

See: `docs/creative_examples/BAD_REFERENCE.jpg`

The BAD reference represents the first 30-image batch created on 2026-09-22 and must NOT be repeated unless the user explicitly requests a flat infographic/template style.

Reject when creative has several of these traits:
- Flat blocks, generic cards, generic icon rows, or abstract product symbols as the main visual.
- Large empty areas with weak product presence.
- Template-looking layouts where only headline/facts change.
- No believable product photography or lifestyle context when product appearance matters.
- "AI slop" feel: generic typography, generic gradients, weak hierarchy, fake-looking product representation, repetitive compositions.
- Looks like a Canva mockup, presentation slide, infographic, or placeholder instead of an actual ad.
- Ten ads that are technically different but visually feel like one repeated system.

## Production rule

Before rendering:
1. Researcher verifies current facts and official assets.
2. Analyst approves the angle.
3. Content Strategist writes the concept.
4. Product Builder creates the final image.
5. Manager QA compares the output against GOOD_REFERENCE and BAD_REFERENCE.

If the output resembles BAD_REFERENCE more than GOOD_REFERENCE, REJECT and regenerate before delivery.

## QA gate

A static ad is deliverable only if all are true:
- Real final image file exists.
- Product/scene looks commercially believable.
- Product identity is accurate; do not invent details when exact appearance matters.
- Hook is readable at phone size.
- Layout has clear hierarchy and intentional spacing.
- Visual style fits the specific brand.
- Facts/claims are verified.
- CTA is clear.
- It is meaningfully distinct from sibling ads.
- Overall impression is professional paid-social creative, not a generated template.

## User preference shorthand

When the user says:
- "Bloom-style", "like the recent batch", "professional ads", or "not AI slop" → use GOOD_REFERENCE as the baseline.
- "Like the first batch" should be treated as a negative reference unless the user explicitly says they want that flat/template style.

This standard overrides convenience. Do not downgrade to easy-to-render flat graphics just because they are faster.
