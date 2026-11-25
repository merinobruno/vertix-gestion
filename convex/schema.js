import {defineSchema, defineTable} from "convex/server";
import {v} from "convex/values";
import {party} from "./shape.js";

export default defineSchema({
  parties:defineTable({...party, revision:v.number()}).index("by_document", ["kind", "digits"]).index("by_name", ["nameKey"]),
  imports:defineTable({fingerprint:v.string()}).index("by_fingerprint", ["fingerprint"]),
});
