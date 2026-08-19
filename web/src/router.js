/**
 * Two pages, no router dependency.
 *
 * The whole navigation surface is "graph" or "chat", so this is a path read and
 * a pushState — a routing library would be more configuration than the thing it
 * routes. Real URLs rather than hashes, because the dev server already serves
 * index.html for unknown paths and a link to /chat should survive a reload.
 */

import { useEffect, useState } from "react";

export function usePath() {
  const [path, setPath] = useState(() => window.location.pathname);

  useEffect(() => {
    // popstate covers the back button; the custom event covers navigate()
    // below, which the browser does not announce.
    const onChange = () => setPath(window.location.pathname);
    window.addEventListener("popstate", onChange);
    window.addEventListener("pkg:navigate", onChange);
    return () => {
      window.removeEventListener("popstate", onChange);
      window.removeEventListener("pkg:navigate", onChange);
    };
  }, []);

  return path;
}

export function navigate(to) {
  if (window.location.pathname === to) return;
  window.history.pushState({}, "", to);
  window.dispatchEvent(new Event("pkg:navigate"));
}
