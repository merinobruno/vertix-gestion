import {v} from "convex/values";

const optionalText = v.optional(v.string());
const identity = {name:v.string(), id_type:v.string(), id_number:v.string()};
const address = v.object({
  street:optionalText, number:optionalText, floor:optionalText, apartment:optionalText, locality:optionalText, province:optionalText,
});
export const profile = v.object({
  ...identity, address:v.optional(address),
  borrower_representative:v.optional(v.union(v.null(), v.object({...identity, address}))),
  lender_representative:v.optional(v.union(v.null(), v.object({...identity, title:v.string()}))),
});
export const party = {kind:v.string(), digits:v.string(), nameKey:v.string(), profile};
export const importedParty = v.object({...party, fingerprint:v.string()});
