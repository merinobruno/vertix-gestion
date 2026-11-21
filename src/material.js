import "@material/web/button/filled-button.js";
import "@material/web/button/filled-tonal-button.js";
import "@material/web/button/outlined-button.js";
import "@material/web/button/text-button.js";
import "@material/web/checkbox/checkbox.js";
import "@material/web/radio/radio.js";
import "@material/web/select/outlined-select.js";
import "@material/web/select/select-option.js";
import "@material/web/switch/switch.js";
import "@material/web/tabs/primary-tab.js";
import "@material/web/tabs/tabs.js";
import "@material/web/textfield/outlined-text-field.js";
import roboto from "@fontsource-variable/roboto/files/roboto-latin-wght-normal.woff2";
import stacks from "@material-symbols/svg-400/rounded/stacks.svg";
import science from "@material-symbols/svg-400/rounded/science.svg";
import tune from "@material-symbols/svg-400/rounded/tune.svg";
import groups from "@material-symbols/svg-400/rounded/groups.svg";
import payments from "@material-symbols/svg-400/rounded/payments.svg";
import download from "@material-symbols/svg-400/rounded/download.svg";
import add from "@material-symbols/svg-400/rounded/add.svg";
import remove from "@material-symbols/svg-400/rounded/delete.svg";
import summarize from "@material-symbols/svg-400/rounded/summarize.svg";
import calendar from "@material-symbols/svg-400/rounded/calendar_today.svg";

const face = new FontFace("Roboto", roboto, {weight:"100 900", style:"normal", display:"swap"});
document.fonts.add(face);
face.load().catch(() => document.fonts.delete(face));

const namespace = "http://www.w3.org/2000/svg";
const sprite = document.createElementNS(namespace, "svg");
sprite.id = "material-symbols";
sprite.setAttribute("aria-hidden", "true");
sprite.setAttribute("width", "0");
sprite.setAttribute("height", "0");
const parser = new DOMParser();
for (const [name, source] of Object.entries({stacks, science, tune, groups, payments, download, add, "delete":remove, summarize, calendar})) {
  const original = parser.parseFromString(source, "image/svg+xml").documentElement;
  const symbol = document.createElementNS(namespace, "symbol");
  symbol.id = "m3-" + name;
  symbol.setAttribute("viewBox", original.getAttribute("viewBox"));
  symbol.append(...Array.from(original.children, child => document.importNode(child, true)));
  sprite.append(symbol);
}
document.body.prepend(sprite);

export function icon(name) {
  const svg = document.createElementNS(namespace, "svg");
  svg.setAttribute("aria-hidden", "true");
  svg.setAttribute("class", "icon");
  svg.setAttribute("slot", "icon");
  const use = document.createElementNS(namespace, "use");
  use.setAttribute("href", "#m3-" + name);
  svg.append(use);
  return svg;
}
