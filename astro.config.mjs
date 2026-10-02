// @ts-check
import { defineConfig } from "astro/config";

// GitHub Pages: projektsida under /veckans-finans.
export default defineConfig({
  site: "https://peternordengren-netizen.github.io",
  base: "/veckans-finans",
  trailingSlash: "ignore",
});
