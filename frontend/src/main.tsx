import "@fontsource-variable/source-serif-4/opsz.css";
import "@fontsource-variable/atkinson-hyperlegible-next/index.css";
import "./styles.css";

import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { App } from "./App";

const root = document.getElementById("root");
if (root === null) throw new Error("missing #root element");

createRoot(root).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
