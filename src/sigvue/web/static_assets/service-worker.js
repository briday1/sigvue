/* Binary responses stay local: forward only to the tab that owns the Python session. */
'use strict';
self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', event => event.waitUntil(self.clients.claim()));
self.addEventListener('message', event => {
  if (event.data?.type === 'sigvue-client' && event.source && event.ports[0]) {
    event.ports[0].postMessage({clientId: event.source.id});
  }
});
self.addEventListener('fetch', event => {
  const url = new URL(event.request.url);
  const prefix = new URL('__files__/', self.registration.scope);
  if (url.origin !== prefix.origin || !url.pathname.startsWith(prefix.pathname)) return;
  event.respondWith((async () => {
    const relative = url.pathname.slice(prefix.pathname.length);
    const separator = relative.indexOf('/');
    const client = await self.clients.get(decodeURIComponent(relative.slice(0, separator)));
    if (separator < 0 || !client || !client.url.startsWith(self.registration.scope)) {
      return new Response('The originating Sigvue tab is no longer open.', {status: 410});
    }
    const request = {method: event.request.method, path: relative.slice(separator) + url.search, body: ''};
    return new Promise(resolve => {
      const channel = new MessageChannel();
      const timeout = setTimeout(() => {
        channel.port1.close();
        resolve(new Response('Python file request timed out.', {status: 504}));
      }, 300000);
      channel.port1.onmessage = message => {
        clearTimeout(timeout);
        channel.port1.close();
        if (message.data.error) {
          resolve(new Response(message.data.error, {status: 500}));
          return;
        }
        const result = message.data.result;
        const bytes = Uint8Array.from(atob(result.body), character => character.charCodeAt(0));
        resolve(new Response(bytes, {status: result.status, headers: result.headers}));
      };
      client.postMessage({type: 'sigvue-file', request}, [channel.port2]);
    });
  })());
});
