import { tool } from "@opencode-ai/plugin"
import path from "node:path"

const MAX_TOOL_OUTPUT = 120_000

export default tool({
  description:
    "Delegate a precise coding task to Antigravity CLI (`agy`) using the user's " +
    "Google Sign-In session and Antigravity subscription quota. The worker modifies " +
    "the current Git worktree; this tool then returns the real Git diff, changed " +
    "files, structured CLI output, and independently captured validation exit codes. " +
    "Always inspect the diff before accepting the patch.",

  args: {
    task: tool.schema
      .string()
      .min(20)
      .describe(
        "Self-contained implementation task with behavior, constraints, and acceptance criteria",
      ),
    allowed_paths: tool.schema
      .array(tool.schema.string())
      .default([])
      .describe("Expected repository-relative paths the worker may modify"),
    validation: tool.schema
      .array(tool.schema.string())
      .default([])
      .describe("Commands the wrapper must run after Antigravity finishes"),
    validation_timeout: tool.schema
      .number()
      .int()
      .min(10)
      .max(1800)
      .default(300)
      .describe("Timeout in seconds for each validation command"),
    agent_timeout: tool.schema
      .number()
      .int()
      .min(30)
      .max(3600)
      .default(1200)
      .describe("Maximum runtime in seconds for the agy headless invocation"),
    agent: tool.schema
      .string()
      .optional()
      .describe("Optional Antigravity custom agent name passed through --agent"),
  },

  async execute(args, context) {
    const worktree = path.resolve(context.worktree)
    const script = path.join(
      worktree,
      ".opencode",
      "tools",
      "antigravity_delegate.py",
    )

    const request = JSON.stringify({
      workspace: worktree,
      task: args.task,
      allowed_paths: args.allowed_paths,
      validation: args.validation,
      validation_timeout: args.validation_timeout,
      agent_timeout: args.agent_timeout,
      agent: args.agent,
    })

    const process = Bun.spawn(["python3", script, "--request", request], {
      cwd: worktree,
      env: {
        ...Bun.env,
        PYTHONUNBUFFERED: "1",
      },
      stdout: "pipe",
      stderr: "pipe",
    })

    const timer = setTimeout(
      () => process.kill(),
      (args.agent_timeout + args.validation_timeout * Math.max(args.validation.length, 1) + 60) * 1000,
    )

    try {
      const [stdout, stderr, exitCode] = await Promise.all([
        new Response(process.stdout).text(),
        new Response(process.stderr).text(),
        process.exited,
      ])

      let result: Record<string, unknown>
      try {
        result = JSON.parse(stdout) as Record<string, unknown>
      } catch {
        result = {
          status: "error",
          message: "Antigravity wrapper returned invalid JSON",
          stdout: stdout.slice(-20_000),
          stderr: stderr.slice(-20_000),
        }
      }

      if (exitCode !== 0) {
        result.process_exit_code = exitCode
        result.process_stderr = stderr.slice(-20_000)
      }

      const serialized = JSON.stringify(result, null, 2)
      if (serialized.length <= MAX_TOOL_OUTPUT) return serialized

      return (
        serialized.slice(0, MAX_TOOL_OUTPUT) +
        "\n\n[tool output truncated; inspect the worktree with git diff]"
      )
    } finally {
      clearTimeout(timer)
    }
  },
})
