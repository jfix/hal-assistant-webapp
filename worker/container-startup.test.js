import assert from "node:assert/strict";
import test from "node:test";

import { migrateDatabase } from "./container-startup.js";

test("database migration receives the production container environment", async () => {
  const calls = [];
  const envVars = {
    DATABASE_URL: "postgresql://example.invalid/catalog",
    DJANGO_SECRET_KEY: "not-a-real-secret",
  };
  const containerContext = {
    async exec(command, options) {
      calls.push({ command, options });
      return {
        async output() {
          return {
            exitCode: 0,
            stdout: new TextEncoder().encode("No migrations to apply."),
            stderr: new ArrayBuffer(0),
          };
        },
      };
    },
  };

  const stdout = await migrateDatabase(containerContext, envVars);

  assert.equal(stdout, "No migrations to apply.");
  assert.deepEqual(calls, [
    {
      command: ["python", "manage.py", "migrate", "--noinput"],
      options: { cwd: "/app", env: envVars },
    },
  ]);
});

test("database migration fails closed on a non-zero exit", async () => {
  const containerContext = {
    async exec() {
      return {
        async output() {
          return {
            exitCode: 1,
            stdout: new ArrayBuffer(0),
            stderr: new TextEncoder().encode("database unavailable"),
          };
        },
      };
    },
  };

  await assert.rejects(
    migrateDatabase(containerContext, { DATABASE_URL: "postgresql://example.invalid/catalog" }),
    /Database migration failed: database unavailable/,
  );
});
