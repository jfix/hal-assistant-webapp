import assert from "node:assert/strict";
import test from "node:test";

import { FORWARDED_ENV_VARS, definedEnvironment, forwardedEnvironment } from "./environment.js";

test("definedEnvironment drops unset and empty values", () => {
  const result = definedEnvironment({
    DATABASE_URL: "postgresql://example.invalid/catalog",
    OPENAI_API_KEY: "",
    R2_BUCKET_NAME: undefined,
    DJANGO_DEBUG: "0",
  });

  assert.deepEqual(result, {
    DATABASE_URL: "postgresql://example.invalid/catalog",
    DJANGO_DEBUG: "0",
  });
});

test("definedEnvironment rejects non-string values", () => {
  const result = definedEnvironment({
    DJANGO_DEBUG: 0,
    SUMMARY_USER_MINUTE_LIMIT: null,
    OPENAI_API_KEY: "set",
  });

  assert.deepEqual(result, { OPENAI_API_KEY: "set" });
});

test("FORWARDED_ENV_VARS includes every secret the container needs to run", () => {
  // Regression guard: these are read from process env by Django at runtime
  // (see hal_webapp/settings.py). Omitting one here means the container
  // silently starts without it instead of failing to build the binding.
  for (const required of [
    "DATABASE_URL",
    "DJANGO_SECRET_KEY",
    "DJANGO_ALLOWED_HOSTS",
    "DJANGO_CSRF_TRUSTED_ORIGINS",
    "HAL_CREDENTIAL_ENCRYPTION_KEY",
    "OPENAI_API_KEY",
  ]) {
    assert.ok(FORWARDED_ENV_VARS.includes(required), `missing ${required}`);
  }
});

test("forwardedEnvironment picks only the forwarded names, dropping unset ones", () => {
  const result = forwardedEnvironment({
    DATABASE_URL: "postgresql://example.invalid/catalog",
    DJANGO_SECRET_KEY: "not-a-real-secret",
    SOME_UNRELATED_BINDING: "should not appear",
  });

  assert.deepEqual(result, {
    DATABASE_URL: "postgresql://example.invalid/catalog",
    DJANGO_SECRET_KEY: "not-a-real-secret",
  });
});
