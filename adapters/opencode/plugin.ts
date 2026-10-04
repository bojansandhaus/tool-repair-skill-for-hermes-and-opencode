/**
 * plugin.ts — OpenCode plugin for deterministic tool-call repair.
 *
 * Hooks into `tool.execute.before` to catch the four common JSON formatting
 * mistakes open models make and fix them before the tool executor sees them.
 *
 * Installation:
 *   Place this file and tool_repair.ts in ~/.config/opencode/plugins/
 *   or publish as an npm package and add to your opencode.json.
 *
 * Reference:
 *   https://opencode.ai/docs/plugins
 */

import { repairFunctionArgs } from "./tool_repair";

/** Plugin context from OpenCode. */
interface PluginContext {
  project: any;
  client: any;
  $: any;
  directory: string;
  worktree: string;
}

// These mirror @opencode-ai/plugin's `Hooks` interface. The previous shapes
// were invented: they put `tool` on the output object and an `id` on the input.
// The published types are
//
//   "tool.execute.before"?: (input: { tool, sessionID, callID },
//                            output: { args: any }) => Promise<void>
//   "tool.execute.after"?:  (input: { tool, sessionID, callID, args },
//                            output: { title, output, metadata }) => Promise<void>
//
// so `output.tool` was always undefined, which made `toolName` undefined on
// every call and `stashKey` fall through to the bare tool name for every
// concurrent call to the same tool. Verified against the package tarball
// (dist/index.d.ts) rather than from memory.
interface ToolExecuteInput {
  tool: string;
  sessionID: string;
  callID: string;
}

/** `output` carries only `args`; the tool name is on `input`. */
interface ToolExecuteBeforeOutput {
  args: Record<string, unknown>;
}

/** The after hook receives no mutable output slot for notes. */
interface ToolExecuteAfterOutput {
  title: string;
  output: string;
  metadata: unknown;
}

/**
 * OpenCode plugin that repairs tool call arguments before dispatch.
 *
 * Operates as a closure: repair notes generated in `tool.execute.before`
 * are stashed and appended in `tool.execute.after`.
 */
export const ToolRepairPlugin = async (
  ctx: PluginContext,
): Promise<{
  "tool.execute.before": (
    input: ToolExecuteInput,
    output: ToolExecuteBeforeOutput,
  ) => Promise<void>;
  "tool.execute.after": (
    input: ToolExecuteInput,
    output: ToolExecuteAfterOutput,
  ) => Promise<void>;
}> => {
  // Stash repair notes between before and after hooks
  const pendingNotes = new Map<string, string[]>();

  console.log("[tool-repair] Plugin initialized — intercepting tool calls");

  return {
    "tool.execute.before": async (input, output) => {
      // The tool name is on `input`, not `output`.
      const toolName = input.tool;
      const args = output.args ?? {};

      const [fixedArgs, notes] = repairFunctionArgs(toolName, args);

      if (notes.length > 0) {
        // Mutate args in place so the tool receives the fixed version
        output.args = fixedArgs;

        // Key on callID. Keying on the tool name meant two concurrent calls to
        // the same tool overwrote each other's notes, so one call could be told
        // about a defect it did not have while its real repair went unmentioned.
        pendingNotes.set(input.callID, notes);

        console.log(
          `[tool-repair] Repaired ${toolName} (${input.callID}): ${notes.join("; ")}`,
        );
      }
    },

    "tool.execute.after": async (input, output) => {
      const notes = pendingNotes.get(input.callID);
      if (!notes || notes.length === 0) return;
      pendingNotes.delete(input.callID);
      // The after hook's output has no note field, and OpenCode's own output
      // shape is `title`/`output`/`metadata`. Prepending the notes to the tool
      // output is the documented way to get them in front of the model, and it
      // cannot lose them the way a non-existent property did.
      output.output = `[tool-repair] ${notes.join("; ")}\n${output.output ?? ""}`;
    },
  };
};
