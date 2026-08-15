export async function proxyToContainer(request, fetchFromContainer) {
  try {
    return await fetchFromContainer(request);
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
}
