/** Bootstrap: read the config, point the RPC client at the robot, start the router. */

import { describeError, getConfig } from "./api.js";
import { createRouter } from "./router.js";
import { setRobotHost } from "./rpc.js";
import { $, h } from "./ui.js";
import { mountPeopleView } from "./views/people.js";
import { mountPersonView } from "./views/person.js";
import { mountSyncView } from "./views/sync.js";
import { mountControlView } from "./views/control.js";

const ROUTES = Object.freeze({
  PEOPLE: "#/people",
  PERSON: "#/people/:id",
  SYNC: "#/sync",
  CONTROL: "#/control",
});

const NAV = [
  [ROUTES.PEOPLE, "People"],
  [ROUTES.SYNC, "Sync"],
  [ROUTES.CONTROL, "Control"],
];

async function boot() {
  const outlet = $("#view-outlet");
  const nav = $("#nav");
  const hostLabel = $("#robot-host");
  if (!outlet || !nav || !hostLabel) {
    console.error("index.html is missing #view-outlet, #nav or #robot-host");
    return;
  }

  const router = createRouter(
    {
      [ROUTES.PEOPLE]: mountPeopleView,
      [ROUTES.PERSON]: mountPersonView,
      [ROUTES.SYNC]: mountSyncView,
      [ROUTES.CONTROL]: mountControlView,
    },
    { fallback: ROUTES.PEOPLE, outlet, onRouteChange: syncNav }
  );

  const links = new Map();
  for (const [route, label] of NAV) {
    const link = h(
      "a",
      { href: route, class: "nav__link", onClick: (event) => {
        event.preventDefault();
        router.navigate(route);
      } },
      label
    );
    links.set(route, link);
    nav.appendChild(link);
  }

  function syncNav(route = "") {
    const path = route.split("?")[0];
    for (const [target, link] of links) {
      // A person page is still "People" as far as the nav is concerned.
      const active = path === target || (target === ROUTES.PEOPLE && path.startsWith(`${ROUTES.PEOPLE}/`));
      link.classList.toggle("is-active", active);
      if (active) link.setAttribute("aria-current", "page");
      else link.removeAttribute("aria-current");
    }
  }

  // The robot's address is config, not something the browser can guess, and the
  // RPC client needs it before the Control view can dial. Failing to read it is
  // not fatal: People, photos and facts need no robot at all.
  try {
    const config = await getConfig();
    setRobotHost(config?.reachy_host || "");
    hostLabel.textContent = config?.reachy_host ? `robot ${config.reachy_host}` : "no REACHY_HOST configured";
    hostLabel.className = config?.reachy_host ? "shell__host" : "shell__host is-warning";
  } catch (error) {
    setRobotHost("");
    hostLabel.textContent = describeError(error);
    hostLabel.className = "shell__host is-warning";
  }

  router.start();
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", boot, { once: true });
} else {
  void boot();
}
