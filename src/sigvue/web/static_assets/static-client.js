/* Static transport for the unchanged Sigvue workspace UI. */
(() => {
  'use strict';
  const base = new URL('./', document.currentScript.src);
  const pending = new Map();
  let sequence = 0, worker, clientId, failure;
  const status = document.createElement('div');
  status.className = 'empty';
  status.setAttribute('role', 'status');
  document.querySelector('#app').replaceChildren(status);

  function fail(error) {
    failure = error;
    status.textContent = `Browser runtime failed: ${error.message}. Reload to retry.`;
    document.querySelector('#app').replaceChildren(status);
    for (const {reject} of pending.values()) reject(error);
    pending.clear();
  }

  function call(message) {
    if (failure) return Promise.reject(failure);
    const id = ++sequence;
    return new Promise((resolve, reject) => {
      pending.set(id, {resolve, reject});
      worker.postMessage({...message, id});
    });
  }

  function routeUrl(path) {
    if (path == null) return location.href;
    const url = new URL(path, base);
    if (url.origin !== base.origin) throw new Error('Routes must stay on this site');
    if (url.pathname === base.pathname && url.hash.startsWith('#/')) return url.href;
    return `${base.href}#${url.pathname}${url.search}${url.hash}`;
  }

  function route(url = new URL(location.href)) {
    return new URL(url.hash.startsWith('#/') ? url.hash.slice(1) : '/', base.origin);
  }

  function rewriteUrls(value, key = '') {
    if (Array.isArray(value)) return value.map(entry => rewriteUrls(entry));
    if (value && typeof value === 'object') {
      return Object.fromEntries(Object.entries(value).map(([name, entry]) => [name, rewriteUrls(entry, name)]));
    }
    if (typeof value !== 'string' || !['url', 'download_url', 'open_url', 'browse_url', 'result_browser_url', 'collection_browser_url'].includes(key)) return value;
    if (value.startsWith('/results/')) return routeUrl(value);
    if (/^\/(?:exports|batches|batch-files)\/[^/]+\/.+/.test(value)) {
      return new URL(`__files__/${encodeURIComponent(clientId)}${value}`, base).href;
    }
    return value;
  }

  function response(result, rewrite = false) {
    const bytes = Uint8Array.from(atob(result.body), character => character.charCodeAt(0));
    const headers = new Headers(result.headers);
    let body = bytes;
    if (rewrite && headers.get('Content-Type')?.includes('application/json')) {
      body = JSON.stringify(rewriteUrls(JSON.parse(new TextDecoder().decode(bytes))));
      headers.delete('Content-Length');
    }
    return new Response(body, {status: result.status, headers});
  }

  async function download(file) {
    const url = new URL(file.url, base);
    const prefix = new URL(`__files__/${encodeURIComponent(clientId)}/`, base);
    if (url.origin !== base.origin || !url.pathname.startsWith(prefix.pathname)) {
      throw new Error('This file belongs to a different browser session');
    }
    const path = '/' + url.pathname.slice(prefix.pathname.length) + url.search;
    const result = response(await call({type: 'request', method: 'GET', path, body: ''}));
    if (!result.ok) throw new Error(`Download failed (${result.status})`);
    const blob = URL.createObjectURL(await result.blob());
    const link = document.createElement('a');
    link.href = blob;
    link.download = file.name || decodeURIComponent(url.pathname.split('/').pop());
    link.click();
    setTimeout(() => URL.revokeObjectURL(blob), 60000);
  }

  function localFiles() {
    const button = document.createElement('button');
    button.className = 'sidebar-toggle';
    button.textContent = 'Local files';
    button.id = 'local-files';
    button.title = 'Files stay in this browser on this device';
    document.querySelector('header').insertBefore(button, document.querySelector('#theme-toggle'));
    const dialog = document.createElement('dialog');
    dialog.style.cssText = 'max-width:620px;width:calc(100% - 32px);border:1px solid var(--line);border-radius:9px;padding:24px;color:var(--ink);background:var(--wash)';
    dialog.innerHTML = `<h2>Browser-local files</h2>
      <p>Files and annotations stay on this device in this browser. Nothing is uploaded.
      Workspace paths start at <code>/project</code>. Importing replaces files with the same path.</p>
      <label>Destination under /project <input id="local-directory" value="" placeholder="e.g. examples/data/comms"></label>
      <p><label>Import files <input id="local-file-input" type="file" multiple></label></p>
      <p><label>Import folder <input id="local-folder-input" type="file" webkitdirectory multiple></label></p>
      <p>Back up files before clearing browser data. Folder imports preserve the selected folder's name.
      Extract a backup ZIP on your device, then import its contents to restore it.</p>
      <p id="local-file-status" role="status"></p>
      <button type="button" id="local-backup" class="primary">Download all local files (ZIP)</button>
      <button type="button" id="local-close">Close</button>`;
    document.body.append(dialog);
    const message = dialog.querySelector('#local-file-status');
    button.onclick = () => dialog.showModal();
    dialog.querySelector('#local-close').onclick = () => dialog.close();
    for (const input of dialog.querySelectorAll('input[type=file]')) {
      input.onchange = async () => {
        const files = Array.from(input.files);
        if (!files.length) return;
        input.disabled = true;
        message.textContent = 'Saving on this device…';
        try {
          await ready;
          // One file at a time keeps large folder imports from duplicating the whole dataset in memory.
          for (const file of files) {
            await call({type: 'upload', directory: dialog.querySelector('#local-directory').value,
              files: [{name: file.webkitRelativePath || file.name, bytes: await file.arrayBuffer()}]});
          }
          message.textContent = `Saved ${files.length} file(s) locally. Refresh the workspace to discover them; reload this page after importing Python code.`;
        } catch (error) {
          message.textContent = `Import failed: ${error.message}`;
        } finally {
          input.disabled = false;
          input.value = '';
        }
      };
    }
    dialog.querySelector('#local-backup').onclick = async event => {
      const target = event.currentTarget;
      target.disabled = true;
      try {
        message.textContent = 'Preparing local backup…';
        const data = await call({type: 'backup'});
        const bytes = Uint8Array.from(atob(data), character => character.charCodeAt(0));
        const url = URL.createObjectURL(new Blob([bytes], {type: 'application/zip'}));
        const link = document.createElement('a');
        link.href = url;
        link.download = 'sigvue-local-files.zip';
        link.click();
        setTimeout(() => URL.revokeObjectURL(url), 60000);
        message.textContent = 'Backup downloaded. No files were sent to a server.';
      } catch (error) {
        message.textContent = `Backup failed: ${error.message}`;
      } finally {
        target.disabled = false;
      }
    };
  }

  const ready = (async () => {
    status.textContent = 'Starting local Python runtime…';
    if (!window.isSecureContext || !navigator.serviceWorker) {
      throw new Error('Static mode needs HTTPS (or localhost) and service worker support');
    }
    await navigator.serviceWorker.register(new URL('service-worker.js', base), {scope: base.pathname});
    await navigator.serviceWorker.ready;
    if (!navigator.serviceWorker.controller) {
      await new Promise(resolve => navigator.serviceWorker.addEventListener('controllerchange', resolve, {once: true}));
    }
    clientId = await new Promise((resolve, reject) => {
      const channel = new MessageChannel();
      const timeout = setTimeout(() => reject(new Error('File transport did not start')), 15000);
      channel.port1.onmessage = event => {
        clearTimeout(timeout);
        channel.port1.close();
        resolve(event.data.clientId);
      };
      navigator.serviceWorker.controller.postMessage({type: 'sigvue-client'}, [channel.port2]);
    });
    worker = new Worker(new URL('python-worker.js', base), {type: 'module'});
    worker.onmessage = event => {
      if (event.data.type === 'progress') {
        status.textContent = event.data.message;
        return;
      }
      const job = pending.get(event.data.id);
      if (!job) return;
      pending.delete(event.data.id);
      if (event.data.error) job.reject(new Error(event.data.error));
      else job.resolve(event.data.result);
    };
    worker.onerror = event => fail(new Error(event.message || 'Python worker stopped'));
    navigator.serviceWorker.addEventListener('message', async event => {
      if (event.data?.type !== 'sigvue-file' || !event.ports[0]) return;
      const port = event.ports[0];
      try {
        const result = await call({type: 'request', ...event.data.request});
        port.postMessage({result});
      } catch (error) {
        port.postMessage({error: error.message});
      } finally {
        port.close();
      }
    });
    await call({type: 'initialize', base: base.href});
    localFiles();
    document.addEventListener('click', event => {
      const link = event.target.closest?.('a[href]');
      if (!link) return;
      const url = new URL(link.href);
      if (url.origin !== base.origin || !url.pathname.startsWith(new URL('__files__/', base).pathname)) return;
      if (!link.hasAttribute('download') && !url.searchParams.has('download')) return;
      event.preventDefault();
      download({url: link.href, name: link.download}).catch(error => alert(error.message));
    });
    status.remove();
  })().catch(error => {
    fail(error);
    throw error;
  });
  // A failed initialization is rendered above, including when the app is not yet loaded.
  ready.catch(() => {});
  window.sigvueStatic = {
    ready, route, routeUrl, download,
    async fetch(path, options = {}) {
      await ready;
      return response(await call({type: 'request', method: options.method || 'GET', path, body: options.body || ''}), true);
    },
  };
})();
