/**
 * tool_repair.ts — TypeScript port of the universal tool-call repair patterns.
 *
 * Ported from the Python tool_repair.py. Same five deterministic fixes,
 * same ordering constraints, same safety guards.
 *
 * Usage:
 *   import { repairFunctionArgs } from "./tool_repair";
 *   const [fixed, notes] = repairFunctionArgs("readFile", { limit: null });
 *   // fixed = {}, notes = ["null values removed for optional fields"]
 */

/** Describes what a single repair changed. */
export interface RepairNote {
  field: string;
  message: string;
}

/**
 * Apply the five repair patterns to a tool-call arguments dict.
 *
 * @param functionName  Name of the tool being called (for telemetry/logging).
 * @param functionArgs  Parsed JSON arguments object (mutated in place).
 * @param toolSchema    Optional JSON Schema object for type-aware repairs.
 * @returns             [fixedArgs, notes] — the repaired args and any notes.
 */
export function repairFunctionArgs(
  functionName: string,
  functionArgs: Record<string, unknown>,
  toolSchema?: Record<string, unknown> | null,
): [Record<string, unknown>, string[]] {
  const notes: string[] = [];

  // Order matters: json-array-parse BEFORE bare-string-wrap
  //
  // Null-omit is schema-aware too: a null on a required or explicitly nullable
  // field is a real value, not a stand-in for an omitted optional, so deleting
  // it would silently change the call. See applyNullOmit.
  applyNullOmit(functionArgs, notes, toolSchema);
  applyJsonArrayParse(functionArgs, notes);
  applyMarkdownAutolinkUnwrap(functionArgs, notes);

  // Schema-aware repairs (need to know if field expects array)
  if (toolSchema) {
    applyEmptyObjectToArray(functionArgs, toolSchema, notes);
    applyBareStringWrap(functionArgs, toolSchema, notes);
  }

  return [functionArgs, notes];
}

// ---------------------------------------------------------------------------
// Pattern 1: Null-Omit — delete keys whose value is null
// ---------------------------------------------------------------------------
function applyNullOmit(
  args: Record<string, unknown>,
  notes: string[],
  schema?: Record<string, unknown> | null,
): void {
  const { required, nullable } = schemaNullability(schema);
  const nullKeys: string[] = [];
  for (const [key, value] of Object.entries(args)) {
    if (value === null && !required.has(key) && !nullable.has(key)) {
      nullKeys.push(key);
    }
  }
  for (const key of nullKeys) {
    delete args[key];
  }
  if (nullKeys.length > 0) {
    notes.push(
      `null values removed for optional fields: ${nullKeys.join(", ")}`,
    );
  }
}

// ---------------------------------------------------------------------------
// Pattern 2: Json-Array-Parse — parse stringified JSON arrays
// ---------------------------------------------------------------------------
function applyJsonArrayParse(
  args: Record<string, unknown>,
  notes: string[],
): void {
  for (const [key, value] of Object.entries(args)) {
    if (typeof value === "string") {
      const trimmed = value.trim();
      if (trimmed.startsWith("[") && trimmed.endsWith("]")) {
        try {
          const parsed = JSON.parse(trimmed);
          if (Array.isArray(parsed)) {
            args[key] = parsed;
            notes.push(`string values parsed as arrays: ${key}`);
          }
        } catch {
          // Not valid JSON — leave as-is
        }
      }
    }
  }
}

// ---------------------------------------------------------------------------
// Pattern 3: Markdown Autolink Unwrap — fix paths that leaked auto-links
// ---------------------------------------------------------------------------
function applyMarkdownAutolinkUnwrap(
  args: Record<string, unknown>,
  notes: string[],
): void {
  // Match patterns like: [filename.md](http://filename.md)
  // Only when link text matches the URL's path component (sans protocol)
  // Matches both the bare-host form and the host+path form. The previous
  // pattern required a path segment, so it could not match
  // `[notes.md](http://notes.md)`, which is the example the Python module
  // documents and asserts in its own self-test.
  const autoLinkRe = /\[([^\]]+)\]\(https?:\/\/(?:[^/]+\/)?\1\)/g;

  for (const [key, value] of Object.entries(args)) {
    if (typeof value === "string") {
      const original = value;
      const fixed = value.replace(autoLinkRe, "$1");
      if (fixed !== original) {
        args[key] = fixed;
        notes.push(`markdown auto-link unwrapped in: ${key}`);
      }
    }
  }
}

// ---------------------------------------------------------------------------
// Pattern 4: Empty-Object-To-Array — replace {} with [] when schema expects
//            an array (schema-aware)
// ---------------------------------------------------------------------------
function applyEmptyObjectToArray(
  args: Record<string, unknown>,
  schema: Record<string, unknown>,
  notes: string[],
): void {
  const schemaPaths = extractSchemaArrayPaths(schema);
  for (const [key, value] of Object.entries(args)) {
    if (
      schemaPaths.has(key) &&
      typeof value === "object" &&
      value !== null &&
      !Array.isArray(value) &&
      Object.keys(value).length === 0
    ) {
      args[key] = [];
      notes.push(`empty objects replaced with empty arrays: ${key}`);
    }
  }
}

// ---------------------------------------------------------------------------
// Pattern 5: Bare-String-Wrap — wrap bare string in array when schema
//            expects an array (schema-aware)
// ---------------------------------------------------------------------------
function applyBareStringWrap(
  args: Record<string, unknown>,
  schema: Record<string, unknown>,
  notes: string[],
): void {
  const schemaPaths = extractSchemaArrayPaths(schema);
  for (const [key, value] of Object.entries(args)) {
    // A bracket-shaped string that failed to parse is not a bare string to
    // wrap: it is either bracketed prose or a broken array serialization, and
    // wrapping either one yields `["[not json]"]`, a real array holding junk.
    // The Python port skips the same values, so the two stay in agreement.
    if (
      schemaPaths.has(key) &&
      typeof value === "string" &&
      !looksLikeStringifiedArray(value)
    ) {
      args[key] = [value];
      notes.push(`bare strings wrapped as single-element arrays: ${key}`);
    }
  }
}

/**
 * True when a string is bracket-shaped, i.e. it tried to be a JSON array.
 * `"[not json]"` and `"[1, 2] and [3, 4]"` both qualify; neither is a bare
 * string, and neither should be turned into a one-element array.
 */
function looksLikeStringifiedArray(value: string): boolean {
  const trimmed = value.trim();
  return trimmed.startsWith("[") && trimmed.endsWith("]");
}

// ---------------------------------------------------------------------------
// Helper: which fields may NOT have a null deleted
// ---------------------------------------------------------------------------
interface Nullability {
  required: Set<string>;
  nullable: Set<string>;
}

/**
 * Returns the required and explicitly-nullable field names of a tool schema.
 *
 * With no schema every field counts as optional, which preserves the original
 * two-argument behaviour of deleting every null.
 */
function schemaNullability(
  schema?: Record<string, unknown> | null,
): Nullability {
  const required = new Set<string>();
  const nullable = new Set<string>();
  if (!schema) return { required, nullable };

  const declared = (schema as any).required;
  if (Array.isArray(declared)) {
    for (const name of declared) required.add(String(name));
  }

  const properties = (schema as any).properties;
  if (properties) {
    for (const [key, prop] of Object.entries(properties as Record<string, any>)) {
      const fieldType = prop?.type;
      if (fieldType === "null") {
        nullable.add(key);
      } else if (Array.isArray(fieldType) && fieldType.includes("null")) {
        nullable.add(key);
      }
      for (const polyKey of ["anyOf", "oneOf"] as const) {
        const variants = prop?.[polyKey];
        if (Array.isArray(variants)) {
          for (const variant of variants) {
            if (variant?.type === "null") nullable.add(key);
          }
        }
      }
    }
  }
  return { required, nullable };
}

// ---------------------------------------------------------------------------
// Helper: extract field names that are typed as arrays in a JSON Schema
// ---------------------------------------------------------------------------
function extractSchemaArrayPaths(
  schema: Record<string, unknown>,
): Set<string> {
  const paths = new Set<string>();

  const properties = (schema as any)?.properties;
  if (!properties || typeof properties !== "object") return paths;

  for (const [key, raw] of Object.entries(properties)) {
    const prop = (raw ?? {}) as Record<string, unknown>;
    const isArray =
      prop.type === "array" ||
      Array.isArray((prop.type as any) ) && (prop.type as any).includes("array") ||
      // The Python side walks these unions too. Without this an
      // `{"anyOf":[{"type":"array"},{"type":"string"}]}` schema silently
      // stopped the string-to-array repair from firing.
      ["anyOf", "oneOf"].some((union: string) => {
        const variants = (prop as Record<string, unknown>)[union];
        return (
          Array.isArray(variants) &&
          variants.some(
            (variant: unknown) =>
              typeof variant === "object" &&
              variant !== null &&
              (variant as { type?: unknown }).type === "array",
          )
        );
      });
    if (isArray) paths.add(key);
  }

  return paths;
}
