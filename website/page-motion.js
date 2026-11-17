const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)');

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
  const sections = new WeakMap();
  const pending = new Set();
  function revealSections(entries) {
    for (const entry of entries) {
      if (!entry.isIntersecting || entry.intersectionRatio < 0.6) continue;
      const section = sections.get(entry.target);
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
    sections.set(trigger, section);
    pending.add(trigger);
    observer.observe(trigger);
  });
  function refreshObserver() {
    observer.disconnect();
    observer = createObserver();
    pending.forEach((trigger) => observer.observe(trigger));
  }
  window.addEventListener('resize', refreshObserver);
}
