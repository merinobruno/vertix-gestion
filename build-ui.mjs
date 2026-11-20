import { build } from "esbuild";
import { readFile, readdir, writeFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.dirname(fileURLToPath(import.meta.url));
const modules = path.join(root, "node_modules");

const result = await build({
  absWorkingDir: root,
  entryPoints: ["src/app.js"],
  outfile: path.join(root, "static", "app.js"),
  format: "esm",
  target: "es2022",
  bundle: true,
  loader: {".woff2": "binary", ".svg": "text"},
  minify: true,
  legalComments: "eof",
  metafile: true,
  logLevel: "info"
});

const bundledPackages = new Set(["@material-symbols/svg-400", "@fontsource-variable/roboto"]);
for (const source of Object.keys(result.metafile.inputs)) {
  const name = /node_modules\/((?:@[^/]+\/)?[^/]+)/.exec(source)?.[1];
  if (name) bundledPackages.add(name);
}
const notices = [];
for (const name of [...bundledPackages].sort()) {
  const folder = path.join(modules, name);
  const license = (await readdir(folder)).find(file => /^licen[cs]e(?:\..+)?$/i.test(file));
  if (!license) throw new Error("Missing license for " + name);
  notices.push(name + "\n\n" + await readFile(path.join(folder, license), "utf8"));
}
await writeFile(path.join(root, "THIRD-PARTY-NOTICES.txt"), notices.join("\n\n"));
