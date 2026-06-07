const { spawn } = require("node:child_process");
const path = require("node:path");

const UVX_COMMAND =
  process.env.AGENT_REGISTRY_UVX ||
  (process.platform === "win32" ? "uvx.exe" : "uvx");

function runPythonEntrypoint(entrypoint) {
  const packageRoot = path.resolve(__dirname, "..");
  const child = spawn(
    UVX_COMMAND,
    ["--from", packageRoot, entrypoint, ...process.argv.slice(2)],
    {
      env: process.env,
      stdio: "inherit",
    },
  );

  child.on("error", (error) => {
    if (error.code === "ENOENT") {
      console.error(
        "agent-registry requires uvx, but it was not found on PATH.\n" +
          "Install uv first: https://docs.astral.sh/uv/getting-started/installation/"
      );
      process.exit(127);
    }

    console.error(`Failed to start ${entrypoint}: ${error.message}`);
    process.exit(1);
  });

  for (const signal of ["SIGINT", "SIGTERM", "SIGHUP"]) {
    process.on(signal, () => {
      if (!child.killed) {
        child.kill(signal);
      }
    });
  }

  child.on("exit", (code, signal) => {
    if (signal) {
      process.exit(1);
    }
    process.exit(code ?? 1);
  });
}

module.exports = runPythonEntrypoint;
