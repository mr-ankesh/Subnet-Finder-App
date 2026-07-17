/* ============================================================
   Presight brand runtime — loader, cursor, smooth scroll,
   scroll reveals, particles, counters, ripple, confetti.
   Progressive enhancement only: every feature no-ops safely if
   its vendor script failed to load. No data/route changes.
   ============================================================ */
(function () {
  "use strict";
  var reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  var touch = window.matchMedia("(hover: none)").matches;

  /* ── Page loader ─────────────────────────────────────────── */
  var loader = document.getElementById("pageLoader");
  function hideLoader() {
    if (!loader) return;
    var bar = loader.querySelector(".loader-bar > span");
    if (bar) bar.style.transform = "scaleX(1)";
    setTimeout(function () { loader.classList.add("done"); }, 250);
  }
  if (loader) {
    // Full cinematic loader only once per session; quick fade afterwards.
    if (sessionStorage.getItem("p-loaded")) {
      loader.classList.add("done");
    } else {
      sessionStorage.setItem("p-loaded", "1");
      var bar = loader.querySelector(".loader-bar > span");
      if (bar) setTimeout(function () { bar.style.transform = "scaleX(.7)"; }, 60);
      window.addEventListener("load", hideLoader);
      setTimeout(hideLoader, 2600);              // never trap the user
    }
  }

  /* ── Smooth scrolling (Lenis) ────────────────────────────── */
  if (!reduced && !touch && window.Lenis) {
    try {
      var lenis = new Lenis({ duration: 1.1, smoothWheel: true });
      function raf(t) { lenis.raf(t); requestAnimationFrame(raf); }
      requestAnimationFrame(raf);
      window.__lenis = lenis;
    } catch (e) { /* non-fatal */ }
  }

  /* ── Hover spotlight: gradient glow tracks the pointer inside cards ── */
  if (!touch && !reduced) {
    document.addEventListener("mousemove", function (e) {
      var card = e.target.closest(".glass-card, .pool-card, .type-card");
      if (!card) return;
      var rect = card.getBoundingClientRect();
      card.style.setProperty("--spot-x", (e.clientX - rect.left) + "px");
      card.style.setProperty("--spot-y", (e.clientY - rect.top) + "px");
    }, { passive: true });
  }

  /* ── Nav: scrolled state + 3D label flip ─────────────────── */
  var nav = document.getElementById("mainNav");
  if (nav) {
    var onScroll = function () { nav.classList.toggle("scrolled", window.scrollY > 30); };
    window.addEventListener("scroll", onScroll, { passive: true });
    onScroll();
  }
  document.querySelectorAll("#navLinks .nav-link").forEach(function (link) {
    // Wrap the text node (after the icon) with front/back copies for the flip.
    var textNode = null;
    link.childNodes.forEach(function (n) {
      if (n.nodeType === 3 && n.textContent.trim()) textNode = n;
    });
    if (!textNode) return;
    var label = textNode.textContent.trim();
    var wrap = document.createElement("span");
    wrap.className = "menu-3d";
    wrap.innerHTML = '<span class="m3d-front">' + label + '</span>' +
                     '<span class="m3d-back" aria-hidden="true">' + label + "</span>";
    textNode.replaceWith(document.createTextNode(" "), wrap);
  });

  /* ── Button micro-interactions: ripple + press spring ────── */
  document.addEventListener("pointerdown", function (e) {
    var btn = e.target.closest(".btn-glow, .btn-glass, .btn-hero, .btn-primary-custom, .btn-secondary-custom, .btn");
    if (!btn || reduced) return;
    btn.classList.add("p-pressed");
    setTimeout(function () { btn.classList.remove("p-pressed"); }, 180);
    var r = document.createElement("span");
    r.className = "p-ripple";
    var rect = btn.getBoundingClientRect();
    r.style.left = (e.clientX - rect.left) + "px";
    r.style.top = (e.clientY - rect.top) + "px";
    btn.appendChild(r);
    setTimeout(function () { r.remove(); }, 650);
  });

  /* ── Scroll reveals (GSAP + ScrollTrigger) ───────────────── */
  if (!reduced && window.gsap && window.ScrollTrigger) {
    gsap.registerPlugin(ScrollTrigger);
    if (window.__lenis) window.__lenis.on("scroll", ScrollTrigger.update);
    var cards = document.querySelectorAll(".glass-card, .pool-card, .type-card");
    cards.forEach(function (el) { el.classList.add("reveal-pending"); });
    ScrollTrigger.batch(".reveal-pending", {
      start: "top 92%",
      once: true,
      onEnter: function (batch) {
        gsap.to(batch, {
          opacity: 1, y: 0, duration: 0.8, stagger: 0.08,
          ease: "power3.out", overwrite: true,
          onComplete: function () {
            batch.forEach(function (el) { el.classList.remove("reveal-pending");
                                          el.style.transform = ""; });
          }
        });
      }
    });
    // Hero parallax drift
    document.querySelectorAll(".p-hero-video, .hero-bg").forEach(function (media) {
      var host = media.closest("section, .page-hero, .alloc-page-header, .hero-section");
      if (!host) return;
      gsap.to(media, {
        yPercent: 12, ease: "none",
        scrollTrigger: { trigger: host, start: "top top", end: "bottom top", scrub: true }
      });
    });
  } else {
    document.querySelectorAll(".reveal-pending").forEach(function (el) {
      el.classList.remove("reveal-pending");
    });
  }

  /* ── Animated counters (Request #N etc.) ─────────────────── */
  document.querySelectorAll(".count-up").forEach(function (el) {
    var target = parseInt(el.dataset.count || el.textContent.replace(/\D/g, ""), 10);
    if (isNaN(target) || reduced) return;
    var start = null, dur = 900;
    function step(t) {
      if (!start) start = t;
      var p = Math.min((t - start) / dur, 1);
      el.textContent = Math.round(target * (1 - Math.pow(1 - p, 3)));
      if (p < 1) requestAnimationFrame(step);
    }
    requestAnimationFrame(step);
  });

  /* ── tsParticles: network-node ambience ──────────────────── */
  function particleOptions(density, opacity) {
    return {
      fpsLimit: 45,
      detectRetina: true,
      fullScreen: { enable: false },
      particles: {
        number: { value: density, density: { enable: true } },
        color: { value: ["#10B5B0", "#155EEF", "#7EDB56"] },
        links: { enable: true, color: "#10B5B0", distance: 140, opacity: opacity * 0.9, width: 1 },
        move: { enable: true, speed: 0.55 },
        opacity: { value: opacity },
        size: { value: { min: 1, max: 2.4 } }
      }
    };
  }
  function initParticles() {
    if (reduced || !window.tsParticles) return;
    document.querySelectorAll(".p-particles, .p-particles-card").forEach(function (el, i) {
      if (!el.id) el.id = "pParticles" + i;
      var dense = el.classList.contains("p-particles") ? 46 : 26;
      var op = el.classList.contains("p-particles") ? 0.35 : 0.22;
      // Lazy: only start when visible
      var io = new IntersectionObserver(function (entries, obs) {
        entries.forEach(function (en) {
          if (en.isIntersecting) {
            tsParticles.load({ id: el.id, options: particleOptions(dense, op) });
            obs.disconnect();
          }
        });
      }, { rootMargin: "120px" });
      io.observe(el);
    });
  }
  if (document.readyState === "complete") initParticles();
  else window.addEventListener("load", initParticles);

  /* ── Hero video: light on mobile ─────────────────────────── */
  document.querySelectorAll(".p-hero-video").forEach(function (v) {
    if (touch) { v.preload = "metadata"; }
    else { v.play && v.play().catch(function () {}); }
  });

  /* ── Confetti when a request lands on a completed status ─── */
  function fireConfetti() {
    if (reduced || !window.confetti) return;
    var colors = ["#155EEF", "#10B5B0", "#7EDB56", "#ffffff"];
    confetti({ particleCount: 90, spread: 75, origin: { y: 0.4 }, colors: colors });
    setTimeout(function () {
      confetti({ particleCount: 50, angle: 60, spread: 60, origin: { x: 0, y: 0.5 }, colors: colors });
      confetti({ particleCount: 50, angle: 120, spread: 60, origin: { x: 1, y: 0.5 }, colors: colors });
    }, 220);
  }
  if (window.__requestComplete) {
    var key = "p-confetti-" + window.__requestComplete;
    if (!sessionStorage.getItem(key)) {
      sessionStorage.setItem(key, "1");
      setTimeout(fireConfetti, 600);
    }
  }
  window.pFireConfetti = fireConfetti;   // available to inline scripts

  /* ── Scroll cue ──────────────────────────────────────────── */
  document.addEventListener("click", function (e) {
    var cue = e.target.closest(".p-scroll-cue");
    if (!cue) return;
    var target = document.querySelector(cue.dataset.target || "main, .page-wrapper");
    var y = cue.closest("section, .page-hero, .hero-section");
    var top = y ? y.offsetTop + y.offsetHeight - 60 : window.innerHeight;
    if (window.__lenis) window.__lenis.scrollTo(top);
    else window.scrollTo({ top: top, behavior: reduced ? "auto" : "smooth" });
  });
})();
