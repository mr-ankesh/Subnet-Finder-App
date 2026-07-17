# Network Copilot — Brand & Motion System

The portal is styled as an extension of **presight.ai**: glassy, gradient,
cinematic, uppercase mono labels, generous whitespace — in the portal's dark
theme. Everything is driven by design tokens so new pages stay on-brand
without copying styles.

## File map

| File | Role |
|---|---|
| `static/css/tokens.css` | **Single source of truth.** Brand palette, gradients, glass fills, radius/elevation, type scale — and a bridge that remaps the app's legacy CSS variables (`--accent`, `--grad-btn`, …) onto the brand, so all existing templates inherit it automatically. |
| `static/css/style.css` | The original component layout (untouched structure). |
| `static/css/animations.css` | All keyframes (`image_zoom`, `infinite-rotate`, `floaty`, `fadeInUp`, `lineReveal`, `gradientFlow`, `pulseGlow`, `borderBeam`, `auroraDrift`, ripple/shake/menu-flip) + `prefers-reduced-motion` kill-switch. |
| `static/css/brand.css` | Brand overrides per component: nav, buttons, glass cards + border-beam, status timeline, history feed, tables, forms, hero video, loader, cursor, ambience. Loaded last. |
| `static/js/brand.js` | Runtime: page loader, card hover-spotlight, Lenis smooth scroll, GSAP/ScrollTrigger reveals, tsParticles network ambience, animated counters, button ripple, nav 3D flip, confetti-on-complete, scroll cue. Every feature no-ops if its vendor script is missing. |
| `static/brand.mp4` | Hero video (autoplay/muted/loop, poster fallback = `background.jpg`). |

> Note: the brief referenced React components (`.tsx`, Framer Motion,
> Lottie-react). This portal is Flask + Jinja with no build step, so those
> map to vanilla equivalents: GSAP springs replace Framer Motion, and empty
> states use CSS/SVG animation instead of Lottie. Same visual outcomes.

## Tokens you should use (never hard-code)

```css
var(--p-blue)        /* #155EEF — primary */
var(--p-grad-h)      /* signature gradient, horizontal (buttons/accents) */
var(--p-grad-v)      /* signature gradient, vertical (icons/badges/nodes) */
var(--p-glass-dark)  /* rgba(20,20,25,.55) — card fill on the dark portal */
var(--p-radius-card) /* 20px */  var(--p-radius-pill) /* 100px */
var(--p-font-body)   /* Helvetica Neue stack */
var(--p-font-mono)   /* IBM Plex Mono — labels/buttons/eyebrows */
var(--p-track-mono)  /* 4.8px letter-spacing for mono labels */
var(--p-success) var(--p-danger)
```

Type scale: H1 84/700/-1.44px/97% · H2 56/500 · H3 36/500 · H4 28/400 ·
H5 24/700 · H6 (eyebrow) = `.p-eyebrow` class (IBM Plex Mono, uppercase,
4.8px tracking).

## Recipes for new pages

- **Card** → `class="glass-card"` (glass fill, 20px radius, blur, inset
  shadow, border-beam on hover, auto scroll-reveal — all free).
- **Primary button** → `btn-glow` / `btn-hero` / `btn-primary-custom`
  (gradient pill, hover inverts to transparent + blue border, ripple +
  press spring built in). Secondary → `btn-glass` / `btn-secondary-custom`.
- **Eyebrow label** → `<div class="p-eyebrow">SECTION LABEL</div>`
  (add `p-eyebrow--grad` for gradient text).
- **Hero with video** → copy the block from `templates/index.html`
  (`.p-hero-video` + `.p-hero-overlay` + `.p-hero-glow` + `.line-mask`
  heading lines + `.p-scroll-cue`).
- **Line-clip heading reveal** →
  `<span class="line-mask"><span class="line">Your line</span></span>` per line.
- **Animated number** → `<span class="count-up" data-count="42">42</span>`.
- **Network-particle ambience** → empty `<div class="p-particles"></div>`
  (hero density) or `.p-particles-card` (inside a glass card, subtle).
  Lazy-initialized when visible.
- **Confetti** → set `window.__requestComplete = <id>` before `brand.js`
  runs (fires once per id per session), or call `window.pFireConfetti()`.
- **History/timeline entry** → add `p-history-item` for the gradient edge
  bar + glowing dot.

## Performance & accessibility contract

- `prefers-reduced-motion: reduce` disables parallax, zoom, smooth scroll,
  particles, confetti and reveals — content is always reachable.
- All motion is transform/opacity (GPU) only; `will-change` used sparingly.
- Video: full autoplay on desktop, `preload=metadata` on touch devices;
  poster image fallback keeps the hero meaningful without the video.
- Particles/Lenis/GSAP/confetti load as `defer` CDN scripts; `brand.js`
  degrades gracefully if any of them are blocked.
- Text over video keeps WCAG AA via the dark overlay + text-shadow.

## Don'ts

- Don't hard-code hex colors or fonts in templates — use the tokens.
- Don't add new full-screen loaders or floating cursors — the loader is a global singleton and pointer effects live inside surfaces (hover spotlight).
- Don't animate `width/height/top/left` — transforms only.
- Keep functionality changes out of the brand layer: `brand.css`/`brand.js`
  must remain safe to delete without breaking any feature.
