import assert from "node:assert/strict";
import test from "node:test";

import { definedEnvironment } from "./environment.js";

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
