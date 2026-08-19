import { useEffect, useMemo, useState } from "react";
import Chat from "./Chat.jsx";
import GraphPage from "./GraphPage.jsx";
import { navigate, usePath } from "./router.js";
import { applyTokens, preferredMode } from "./theme.js";

/**
 * The shell: theme, and which of the two pages is showing.
 *
 * The canvas and the chat are two views of the same graph — one is what was
 * ingested and how it hangs together, the other is what a given identity can
 * find in it — so they share a shell rather than being separate apps, and a
 * citation in the chat links straight into the canvas.
 */

const PAGES = [
  { path: "/", label: "Graph" },
  { path: "/chat", label: "Ask" },
];

export default function App() {
  const [mode, setMode] = useState(preferredMode);
  const path = usePath();

  useEffect(() => applyTokens(mode), [mode]);

  const page = useMemo(
    () => (path.startsWith("/chat") ? "/chat" : "/"),
    [path]
  );

  return (
    <div className={`app app-${page === "/chat" ? "chat" : "graph"}`}>
      <nav className="nav">
        {PAGES.map(({ path: to, label }) => (
          <button
            key={to}
            className={to === page ? "nav-tab is-active" : "nav-tab"}
            onClick={() => navigate(to)}
          >
            {label}
          </button>
        ))}
        <button
          className="nav-theme"
          onClick={() => setMode(mode === "dark" ? "light" : "dark")}
          title="Switch light and dark"
        >
          {mode === "dark" ? "Light" : "Dark"}
        </button>
      </nav>

      {page === "/chat" ? <Chat /> : <GraphPage mode={mode} />}
    </div>
  );
}
