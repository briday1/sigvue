# SigVue framework TODO

## Plot interaction and parameter feedback

- Expose Plotly selection events through the public workspace API. A workspace
  should be able to receive selected x/y bounds without reaching into browser
  internals.
- Allow `viewport_controls` (or an equivalent binding API) to target a
  dual-handle `ui.limits` control directly instead of requiring two scalar
  controls.
- Define event ownership clearly: a new plot selection may update bound
  controls, but later manual edits must remain authoritative and must not snap
  back to stale selection values.
- Support preview/commit workflows for expensive processing. Authors should be
  able to update display-only controls or geometric overlays without
  invalidating analysis caches, then explicitly commit the selected bounds.
- Add a public reset affordance for `ui.limits`, including resetting both
  handles to dynamic bounds derived from the current data window.
- Provide a public per-plot option for keeping selected Plotly mode-bar tools
  visible and choosing the initial drag tool, such as box select.

## Control authoring

- Ensure both handles of an overlapping `ui.limits` range control remain
  independently clickable. The active lower or upper thumb must own the
  pointer hit area; changing either bound must serialize and commit the full
  pair without requiring the other bound to move first.
- Provide a supported way for one UI event to update another control's value,
  including its custom visuals, serialized value, dependencies, and callbacks.
- Add a momentary action/button control for operations such as applying a
  previewed zoom or resetting dynamic bounds.
