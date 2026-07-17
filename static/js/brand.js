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

  /* ── Smooth scrolling ────────────────────────────────────────
     Lenis (JS-driven scroll) was the main cause of "heavy" scrolling,
     especially on AKS/remote where every wheel event ran through rAF.
     Native scrolling is smoother and free — CSS scroll-behavior handles
     the anchor jumps. Lenis intentionally not initialised. */

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

  /* ── Scroll reveals (lightweight IntersectionObserver) ───────
     Cards fade+rise once as they enter the viewport. No scroll-linked
     animation, no GSAP ScrollTrigger tick on every scroll event, and no
     hero parallax (scroll-linked transforms were a big jank source). */
  if (!reduced && "IntersectionObserver" in window) {
    var cards = document.querySelectorAll(".glass-card, .pool-card, .type-card");
    cards.forEach(function (el) { el.classList.add("reveal-pending"); });
    var io = new IntersectionObserver(function (entries, obs) {
      var n = 0;
      entries.forEach(function (en) {
        if (!en.isIntersecting) return;
        var el = en.target;
        el.style.transitionDelay = (n++ * 60) + "ms";
        el.classList.add("revealed");
        obs.unobserve(el);
      });
    }, { rootMargin: "0px 0px -8% 0px" });
    cards.forEach(function (el) { io.observe(el); });
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

  /* ── tsParticles: network-node ambience ──────────────────────
     Hero only, and only on capable devices. Light settings (low count,
     30fps cap, no retina multiplier). The in-card particle canvas is
     dropped — it repainted behind text and cost more than it added. */
  function particleOptions() {
    return {
      fpsLimit: 30,
      detectRetina: false,
      fullScreen: { enable: false },
      particles: {
        number: { value: 28, density: { enable: true, area: 900 } },
        color: { value: ["#10B5B0", "#155EEF"] },
        links: { enable: true, color: "#10B5B0", distance: 150, opacity: 0.28, width: 1 },
        move: { enable: true, speed: 0.45 },
        opacity: { value: 0.32 },
        size: { value: { min: 1, max: 2 } }
      }
    };
  }
  function initParticles() {
    // Skip particles on low-core / small screens where they hurt most.
    var weak = (navigator.hardwareConcurrency || 4) <= 4 || window.innerWidth < 900;
    if (reduced || touch || weak || !window.tsParticles) return;
    var el = document.querySelector(".p-particles");
    if (!el) return;
    if (!el.id) el.id = "pParticlesHero";
    var io = new IntersectionObserver(function (entries, obs) {
      entries.forEach(function (en) {
        if (en.isIntersecting) {
          tsParticles.load({ id: el.id, options: particleOptions() });
          obs.disconnect();
        }
      });
    }, { rootMargin: "80px" });
    io.observe(el);
  }
  if (document.readyState === "complete") initParticles();
  else window.addEventListener("load", initParticles);

  /* ── Hero video: play only when visible & on capable devices ──
     A looping video is continuous GPU compositing. We pause it once it
     scrolls out of view, and skip it entirely on touch / low-core /
     data-saver — the poster image keeps the hero meaningful. */
  (function () {
    var weakVid = touch || (navigator.hardwareConcurrency || 4) <= 4 ||
                  (navigator.connection && navigator.connection.saveData);
    document.querySelectorAll(".p-hero-video").forEach(function (v) {
      if (weakVid) {
        v.removeAttribute("autoplay");
        v.preload = "none";
        try { v.pause(); } catch (e) {}
        return;
      }
      var io = new IntersectionObserver(function (entries) {
        entries.forEach(function (en) {
          if (en.isIntersecting) { v.play && v.play().catch(function () {}); }
          else { try { v.pause(); } catch (e) {} }
        });
      }, { threshold: 0.05 });
      io.observe(v);
    });
  })();

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
    var y = cue.closest("section, .page-hero, .hero-section");
    var top = y ? y.offsetTop + y.offsetHeight - 60 : window.innerHeight;
    window.scrollTo({ top: top, behavior: reduced ? "auto" : "smooth" });
  });
})();
