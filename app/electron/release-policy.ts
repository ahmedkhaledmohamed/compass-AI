/** The desktop release exposes only the evidence-to-reviewed-handoff workflow. */
const engineEndpoints = new Set([
  "/init", "/workspace/info", "/connect", "/ingest", "/refresh",
  "/evidence", "/evidence/by-id", "/usage", "/sync/status",
  "/documents/list", "/documents/get",
  ...["create","list","get","sources","evidence","generate","revise","review","accept",
    "jobs/start","jobs/status","jobs/latest","jobs/cancel"].map(route=>`/tasks/${route}`),
]);
export function requireReleaseEndpoint(endpoint: unknown): asserts endpoint is string {
  if(typeof endpoint!=="string" || !engineEndpoints.has(endpoint)) {
    throw new Error("This capability is not available in this Compass release.");
  }
}
