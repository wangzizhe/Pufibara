const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)');

const mobileNav = document.querySelector('.mobile-nav');
if (mobileNav) {
  mobileNav.addEventListener('click', (event) => {
    if (event.target.closest('.mobile-nav-links a')) mobileNav.open = false;
  });
  mobileNav.addEventListener('keydown', (event) => {
    if (event.key !== 'Escape' || !mobileNav.open) return;
    event.preventDefault();
    mobileNav.open = false;
    mobileNav.querySelector('summary').focus();
  });
}

function animateMetric(element) {
  const finalText = element.textContent;
  const target = Number(finalText);
  const start = performance.now();

  function tick(now) {
    const progress = Math.min((now - start) / 850, 1);
    if (progress === 1 || reducedMotion.matches) {
      element.textContent = finalText;
      return;
    }
    element.textContent = (target * (1 - (1 - progress) ** 3)).toFixed(1);
    requestAnimationFrame(tick);
  }

  requestAnimationFrame(tick);
}

if ('IntersectionObserver' in window) {
  const pending = new Map();
  function revealSections(entries, source) {
    if (source !== observer) return;
    for (const entry of entries) {
      if (!entry.isIntersecting || entry.intersectionRatio < 0.6) continue;
      const section = pending.get(entry.target);
      if (!section) continue;
      section.classList.add('is-visible');
      if (!reducedMotion.matches) {
        section.querySelectorAll('.metric-number').forEach(animateMetric);
      }
      observer.unobserve(entry.target);
      pending.delete(entry.target);
    }
    if (pending.size === 0) {
      observer.disconnect();
      window.removeEventListener('resize', refreshObserver);
    }
  }
  function createObserver() {
    return new IntersectionObserver(revealSections, {
      threshold: 0.6,
      rootMargin: `0px 0px -${Math.round(window.innerHeight / 3)}px 0px`,
    });
  }
  let observer = createObserver();

  document.querySelectorAll('#engineering, #demo, #benchmark, .difference-item').forEach((section) => {
    const trigger = section.querySelector('.feature-visual, h2');
    if (!trigger) return;
    pending.set(trigger, section);
    observer.observe(trigger);
  });
  function refreshObserver() {
    observer.disconnect();
    observer = createObserver();
    for (const trigger of pending.keys()) observer.observe(trigger);
  }
  window.addEventListener('resize', refreshObserver);
}
