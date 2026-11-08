/* ==========================================================================
   ComponentRegistry — maps response/component types to renderer functions.
   Renderers register themselves at load time. The registry is the single
   place the frontend consults to resolve "what renders this type?".

   Two kinds of registration:
     registerResponse(type, fn)   -> top-level response_type handlers
     registerComponent(type, fn)  -> individual component.type handlers
   ========================================================================== */
(function () {
  "use strict";
  const Nova = window.NovaRender;

  const _responses = new Map();
  const _components = new Map();

  function registerResponse(type, fn) {
    if (typeof fn !== "function")
      throw new Error("response renderer must be a function: " + type);
    _responses.set(type, fn);
  }

  function registerComponent(type, fn) {
    if (typeof fn !== "function")
      throw new Error("component renderer must be a function: " + type);
    _components.set(type, fn);
  }

  function getResponse(type) {
    return _responses.get(type) || _responses.get("general");
  }

  function getComponent(type) {
    return _components.get(type);
  }

  function hasComponent(type) {
    return _components.has(type);
  }

  function listResponseTypes() {
    return [..._responses.keys()];
  }

  function listComponentTypes() {
    return [..._components.keys()];
  }

  Nova.ComponentRegistry = {
    registerResponse,
    registerComponent,
    getResponse,
    getComponent,
    hasComponent,
    listResponseTypes,
    listComponentTypes,
  };
})();
