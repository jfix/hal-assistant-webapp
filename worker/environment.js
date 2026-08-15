export const FORWARDED_ENV_VARS = [
  "DATABASE_URL",
  "DJANGO_SECRET_KEY",
  "DJANGO_DEBUG",
  "DJANGO_ALLOWED_HOSTS",
  "DJANGO_CSRF_TRUSTED_ORIGINS",
  "R2_BUCKET_NAME",
  "R2_ENDPOINT_URL",
  "R2_ACCESS_KEY_ID",
  "R2_SECRET_ACCESS_KEY",
  "OPENAI_API_KEY",
  "OPENAI_MODEL",
  "HAL_CREDENTIAL_ENCRYPTION_KEY",
  "SUMMARY_USER_MINUTE_LIMIT",
  "SUMMARY_USER_DAILY_LIMIT",
  "SUMMARY_GLOBAL_DAILY_LIMIT",
  "SUMMARY_CACHE_RETENTION_DAYS",
];

export function definedEnvironment(values) {
  return Object.fromEntries(
    Object.entries(values).filter(([, value]) => typeof value === "string" && value.length > 0),
  );
}

export function forwardedEnvironment(source) {
  return definedEnvironment(
    Object.fromEntries(FORWARDED_ENV_VARS.map((name) => [name, source[name]])),
  );
}
