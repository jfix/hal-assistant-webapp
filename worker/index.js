import { Container, getContainer } from "@cloudflare/containers";
import { env } from "cloudflare:workers";

import { forwardedEnvironment } from "./environment.js";
import { proxyToContainer } from "./proxy.js";

export class DjangoContainer extends Container {
  defaultPort = 8080;
  requiredPorts = [8080];
  pingEndpoint = "/healthz";
  sleepAfter = "10m";
  envVars = forwardedEnvironment(env);
}

export default {
  async fetch(request, workerEnv) {
    return proxyToContainer(request, (req) =>
      getContainer(workerEnv.DJANGO_CONTAINER, "primary").fetch(req),
    );
  },
};
