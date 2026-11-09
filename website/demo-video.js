const demoCover = document.querySelector('.demo-cover');

demoCover?.addEventListener('click', (event) => {
  if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;

  const videoId = new URL(demoCover.href).pathname.slice(1);
  if (!/^[A-Za-z0-9_-]{11}$/.test(videoId)) return;

  event.preventDefault();
  const player = document.createElement('iframe');
  player.title = 'Pufibara vision and demo';
  player.src = `https://www.youtube-nocookie.com/embed/${videoId}?autoplay=1`;
  player.allow = 'autoplay; encrypted-media; picture-in-picture; fullscreen';
  player.allowFullscreen = true;
  player.referrerPolicy = 'strict-origin-when-cross-origin';
  demoCover.replaceWith(player);
  player.focus();
});
