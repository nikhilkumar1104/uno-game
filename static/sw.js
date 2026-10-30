"use strict";

const CACHE = "uno-live-release-1-v9";
const OFFLINE = "/static/offline.html";
const APP_SHELL = [
  "/",
  OFFLINE,
  "/static/style.css?v=13",
  "/static/graphics.css?v=1",
  "/static/features.css?v=2",
  "/static/features.js?v=2",
  "/static/script.js?v=13",
  "/static/offline.css",
  "/static/offline.js",
  "/static/manifest.webmanifest",
  "/static/vendor/socket.io.min.js?v=4.8.3",
  "/static/icons/icon-192.png",
  "/static/icons/icon-512.png",
  "/static/avatars/robot.svg",
  "/static/avatars/astronaut.svg",
  "/static/avatars/ninja.svg",
  "/static/avatars/fox.svg",
  "/static/avatars/cat.svg",
  "/static/avatars/owl.svg",
  "/static/avatars/panda.svg",
  "/static/avatars/alien.svg"
];

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(CACHE).then((cache) => cache.addAll(APP_SHELL)));
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((key) => key !== CACHE).map((key) => caches.delete(key))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (event) => {
  const request = event.request;
  const url = new URL(request.url);
  if (request.method !== "GET" || url.pathname.startsWith("/socket.io/")) return;

  if (request.mode === "navigate") {
    event.respondWith(
      fetch(request)
        .then((response) => {
          const copy = response.clone();
          caches.open(CACHE).then((cache) => cache.put(request, copy));
          return response;
        })
        .catch(async () => (await caches.match(request)) || caches.match(OFFLINE))
    );
    return;
  }

  event.respondWith(
    caches.match(request).then((cached) => cached || fetch(request).then((response) => {
      if (response.ok && url.origin === self.location.origin) {
        const copy = response.clone();
        caches.open(CACHE).then((cache) => cache.put(request, copy));
      }
      return response;
    }))
  );
});
