import { Container, getContainer } from "@cloudflare/containers";
import { env } from "cloudflare:workers";

function definedEnvironment(values) {
  return Object.fromEntries(
    Object.entries(values).filter(([, value]) => typeof value === "string" && value.length > 0),
  );
}

export class DjangoContainer extends Container {
  defaultPort = 8080;
  requiredPorts = [8080];
  pingEndpoint = "/healthz";
  sleepAfter = "10m";
  envVars = definedEnvironment({
    DATABASE_URL: env.DATABASE_URL,
    DJANGO_SECRET_KEY: env.DJANGO_SECRET_KEY,
    DJANGO_DEBUG: env.DJANGO_DEBUG,
    DJANGO_ALLOWED_HOSTS: env.DJANGO_ALLOWED_HOSTS,
    DJANGO_CSRF_TRUSTED_ORIGINS: env.DJANGO_CSRF_TRUSTED_ORIGINS,
    R2_BUCKET_NAME: env.R2_BUCKET_NAME,
    R2_ENDPOINT_URL: env.R2_ENDPOINT_URL,
    R2_ACCESS_KEY_ID: env.R2_ACCESS_KEY_ID,
    R2_SECRET_ACCESS_KEY: env.R2_SECRET_ACCESS_KEY,
  });
}

export default {
  async fetch(request, workerEnv) {
    try {
      const container = getContainer(workerEnv.DJANGO_CONTAINER, "primary");
      return await container.fetch(request);
    } catch (error) {
      console.error(
        JSON.stringify({
          event: "django_container_proxy_error",
          message: error instanceof Error ? error.message : String(error),
        }),
      );
      return Response.json(
        { error: "Application temporarily unavailable" },
        { status: 503, headers: { "retry-after": "10" } },
      );
    }
  },
};
