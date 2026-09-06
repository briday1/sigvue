/* One Python interpreter per tab; scientific work never blocks the UI thread. */
'use strict';
let python, runtime, base, manifest, storageName;
let queue = Promise.resolve();
const progress = message => self.postMessage({type: 'progress', message});
const storageKey = url => `sigvue-${Array.from(new TextEncoder().encode(new URL(url).pathname), byte => byte.toString(16).padStart(2, '0')).join('')}`;

async function getBytes(path) {
  const response = await fetch(new URL(path, base));
  if (!response.ok) throw new Error(`Unable to load ${path} (${response.status})`);
  return new Uint8Array(await response.arrayBuffer());
}

function sync(populate) {
  return new Promise((resolve, reject) => python.FS.syncfs(populate, error => error ? reject(error) : resolve()));
}

async function initialize(url) {
  base = url;
  const response = await fetch(new URL('manifest.json', base), {cache: 'no-cache'});
  if (!response.ok) throw new Error(`Unable to load manifest (${response.status})`);
  manifest = await response.json();
  const {loadPyodide} = await import(new URL('runtime/pyodide.mjs', base).href);
  progress('Loading Python and scientific libraries…');
  python = await loadPyodide({indexURL: new URL('runtime/', base).href});
  await python.loadPackage(manifest.packages);
  python.unpackArchive(await getBytes(manifest.pythonPackages), 'zip', {extractDir: '/packages'});
  python.unpackArchive(await getBytes(manifest.framework), 'zip', {extractDir: '/packages'});
  storageName = storageKey(base);
  const storagePath = `/${storageName}`;
  python.FS.mkdirTree(storagePath);
  python.FS.mount(python.FS.filesystems.IDBFS, {}, storagePath);
  python.FS.symlink(storagePath, '/project');
  progress('Restoring browser-local data…');
  // Do not silently lose annotations if storage is blocked or its quota is exhausted.
  await sync(true);
  python.globals.set('_project_archive', await getBytes(manifest.project));
  python.globals.set('_build_id', manifest.buildId);
  python.globals.set('_config_path', `/project/${manifest.config}`);
  progress('Opening workspaces…');
  await python.runPythonAsync(`
import io, os, sys, zipfile
from pathlib import Path
sys.path.insert(0, "/packages")
sys.path.insert(0, "/project")
os.environ["MPLBACKEND"] = "Agg"
os.environ["MPLCONFIGDIR"] = "/tmp/matplotlib"
os.chdir("/project")
_version_path = Path("/project/.sigvue-build")
_refresh_code = not _version_path.exists() or _version_path.read_text() != _build_id
with zipfile.ZipFile(io.BytesIO(_project_archive.to_py())) as archive:
    for entry in archive.infolist():
        target = Path("/project", entry.filename)
        if not target.resolve().is_relative_to(Path("/project").resolve()):
            raise ValueError("Unsafe project archive path")
        if entry.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            continue
        # Refresh deployed Python code, never overwrite local profiles or annotations.
        if not target.exists() or (_refresh_code and target.suffix == ".py"):
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(archive.read(entry))
_version_path.write_text(_build_id)
from sigvue.web.browser import BrowserRuntime
_runtime = BrowserRuntime(_config_path)
`);
  runtime = python.globals.get('_runtime');
  python.globals.delete('_project_archive');
  await sync(false);
}

async function dispatch(message) {
  if (message.type === 'initialize') {
    await initialize(message.base);
    return null;
  }
  if (message.type === 'upload') {
    await sync(true);
    for (const file of message.files) {
      const parts = `${message.directory}/${file.name}`.split('/').filter(Boolean);
      if (!parts.length || parts.some(part => part === '.' || part === '..' || part.includes('\\') || part.includes('\0'))) {
        throw new Error('Choose paths inside the browser-local project');
      }
      const path = `/project/${parts.join('/')}`;
      python.globals.set('_upload_path', path);
      const safe = python.runPython('Path(_upload_path).resolve().is_relative_to(Path("/project").resolve())');
      if (!safe) throw new Error('File path escapes browser-local storage');
      python.FS.mkdirTree(path.slice(0, path.lastIndexOf('/')));
      python.FS.writeFile(path, new Uint8Array(file.bytes));
    }
    await sync(false);
    return null;
  }
  if (message.type === 'backup') {
    await sync(true);
    return python.runPython(`
import base64
_backup = io.BytesIO()
with zipfile.ZipFile(_backup, "w", zipfile.ZIP_DEFLATED) as archive:
    for path in Path("/project").rglob("*"):
        if path.is_file() and not path.is_symlink() and "__pycache__" not in path.parts:
            archive.write(path, str(path.relative_to("/project")))
base64.b64encode(_backup.getvalue()).decode("ascii")
`);
  }
  if (message.type !== 'request' || !runtime) throw new Error('Python is not ready');
  await sync(true);
  const proxy = runtime.request(message.method, message.path, message.body);
  let result;
  try {
    result = proxy.toJs({dict_converter: Object.fromEntries});
  } finally {
    proxy.destroy();
  }
  await python.runPythonAsync('await _runtime.drain()');
  await sync(false);
  return result;
}

self.onmessage = event => {
  const message = event.data;
  queue = queue.then(async () => {
    try {
      const name = message.type === 'initialize' ? storageKey(message.base) : storageName;
      const result = navigator.locks
        ? await navigator.locks.request(name, () => dispatch(message))
        : await dispatch(message);
      self.postMessage({id: message.id, result});
    } catch (error) {
      self.postMessage({id: message.id, error: String(error.message || error)});
    }
  });
};
