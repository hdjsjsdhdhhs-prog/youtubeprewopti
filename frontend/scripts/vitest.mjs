// Runs vitest from the canonical on-disk path.
//
// On Windows a shell cwd such as "c:\repo" (lowercase drive letter) makes Vite resolve the vitest
// runtime both as "c:/..." and "C:/...", loading two instances, and every suite then fails with
// "Cannot read properties of undefined (reading 'config')". Spawning from realpath avoids that.
import { spawnSync } from "node:child_process";
import { realpathSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = realpathSync.native(join(dirname(fileURLToPath(import.meta.url)), ".."));
const bin = join(root, "node_modules", "vitest", "vitest.mjs");
const args = process.argv.slice(2);
const result = spawnSync(process.execPath, [bin, ...(args.length ? args : ["run"])], { cwd: root, stdio: "inherit" });
process.exit(result.status ?? 1);
