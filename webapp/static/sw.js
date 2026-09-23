// Present so phones offer "Add to Home screen" and list TitleSift in the
// share sheet. Lookups always go to the network; nothing is stored offline.
self.addEventListener("install", function () { self.skipWaiting(); });
self.addEventListener("activate", function (e) { e.waitUntil(self.clients.claim()); });
self.addEventListener("fetch", function () {});
