export async function migrateDatabase(containerContext, envVars) {
  const process = await containerContext.exec(
    ["python", "manage.py", "migrate", "--noinput"],
    {
      cwd: "/app",
      env: envVars,
    },
  );
  const output = await process.output();
  const decoder = new TextDecoder();

  if (output.exitCode !== 0) {
    throw new Error(`Database migration failed: ${decoder.decode(output.stderr)}`);
  }

  return decoder.decode(output.stdout);
}
