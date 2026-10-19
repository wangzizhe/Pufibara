// Preserve section bookmarks when opening the former English URLs.
const destination = document.querySelector('meta[name="redirect-target"]')?.content;
if (destination) {
  const url = new URL(destination, window.location.href);
  url.search = window.location.search;
  url.hash = window.location.hash;
  window.location.replace(url.href);
}
