/**
 * Minimal hash router, adapted from the robot console's `static/js/router.js`.
 *
 * The one addition is a path parameter: a route key may carry a `:name`
 * segment (`#/people/:id`), because a person detail page is addressed by id and
 * an operator wants that URL to be reloadable and bookmarkable. Handlers get
 * the same `{ outlet, signal, navigate, params }` context the console's views
 * take, plus the decoded `params`.
 *
 * The console's leave-guard is not carried over: nothing in this UI holds
 * unsaved state — every edit is a request that has already happened.
 */

/** Split "#/people/abc?x=1" into its route path and its query string. */
function splitRoute(route) {
  const queryStart = route.indexOf("?");
  return queryStart === -1
    ? { path: route, query: "" }
    : { path: route.slice(0, queryStart), query: route.slice(queryStart + 1) };
}

/** Compile "#/people/:id" into a matcher over path segments. */
function compile(pattern) {
  const segments = pattern.split("/");
  return {
    pattern,
    match(path) {
      const parts = path.split("/");
      if (parts.length !== segments.length) return null;
      const params = {};
      for (let i = 0; i < segments.length; i += 1) {
        const segment = segments[i];
        if (segment.startsWith(":")) {
          if (!parts[i]) return null;
          params[segment.slice(1)] = decodeURIComponent(parts[i]);
        } else if (segment !== parts[i]) {
          return null;
        }
      }
      return params;
    },
  };
}

export function createRouter(routes, { fallback = "#/", outlet, onRouteChange } = {}) {
  if (!outlet) throw new Error("createRouter: outlet is required");
  const matchers = Object.keys(routes).map(compile);
  let currentController = null;
  let currentRoute = null;
  let pendingTransition = Promise.resolve();

  /** Return { route, pattern, params } for a hash, falling back when nothing matches. */
  function resolve(route = window.location.hash || fallback) {
    const { path } = splitRoute(route);
    for (const matcher of matchers) {
      const params = matcher.match(path);
      if (params) return { route, pattern: matcher.pattern, params };
    }
    if (route === fallback) throw new Error(`createRouter: the fallback ${fallback} is not a route`);
    return resolve(fallback);
  }

  function renderRouteError(route, error) {
    const div = document.createElement("div");
    div.className = "route-error";
    // textContent, not innerHTML: `error` may quote a robot-supplied string.
    div.textContent = `Failed to render ${route}: ${error?.message || error}`;
    return div;
  }

  function mount({ route, pattern, params }) {
    currentController?.abort();
    outlet.replaceChildren();

    currentRoute = route;
    currentController = new AbortController();
    const controller = currentController;
    const { query } = splitRoute(route);
    const context = {
      outlet,
      params,
      signal: controller.signal,
      searchParams: new URLSearchParams(query),
      navigate,
    };
    try {
      Promise.resolve(routes[pattern](context)).catch((error) => {
        if (context.signal.aborted) return;
        console.error("Route handler failed for", route, error);
        outlet.replaceChildren(renderRouteError(route, error));
      });
    } catch (error) {
      console.error("Route handler failed for", route, error);
      outlet.replaceChildren(renderRouteError(route, error));
    }
    onRouteChange?.(route);
  }

  function transitionTo(route, updateHash) {
    const resolved = resolve(route);
    if (resolved.route === currentRoute) {
      if (window.location.hash !== currentRoute) window.history.replaceState(null, "", currentRoute);
      return;
    }
    if (updateHash && window.location.hash !== resolved.route) {
      window.location.hash = resolved.route;
    } else if (window.location.hash !== resolved.route) {
      window.history.replaceState(null, "", resolved.route);
    }
    mount(resolved);
  }

  function enqueue(route, updateHash = false) {
    const transition = pendingTransition.then(() => transitionTo(route, updateHash));
    pendingTransition = transition.catch((error) => console.error("Route transition failed", error));
    return transition;
  }

  function navigate(route) {
    return enqueue(route, true);
  }

  return {
    start() {
      window.addEventListener("hashchange", () => enqueue(window.location.hash));
      const target = resolve();
      if (window.location.hash !== target.route) window.history.replaceState(null, "", target.route);
      void enqueue(target.route);
    },
    navigate,
    /** Re-mount the current route — how a view says "reload me" after a write. */
    refresh() {
      if (currentRoute) mount(resolve(currentRoute));
    },
    currentRoute() {
      return currentRoute;
    },
  };
}
