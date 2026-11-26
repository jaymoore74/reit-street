// The standalone server (output: "standalone") does not serve .next/static by itself; copy it
// next to server.js so `npm start` and the container serve CSS and scripts. Works on Windows.
import { cpSync, existsSync } from "node:fs";

cpSync(".next/static", ".next/standalone/.next/static", { recursive: true });
if (existsSync("public")) cpSync("public", ".next/standalone/public", { recursive: true });
