/** The desktop release exposes only the evidence-to-reviewed-handoff workflow. */
const engineEndpoints = new Set([
  "/workspace/info", "/connect",
  "/evidence", "/evidence/by-id", "/usage", "/sync/status",
  "/imports/start", "/imports/status", "/imports/latest", "/imports/cancel",
  "/documents/list", "/documents/get",
  ...["create","list","get","sources","evidence","retrieve","passage","revise","review","accept",
    "jobs/start","jobs/status","jobs/latest","jobs/cancel"].map(route=>`/tasks/${route}`),
]);
export function requireReleaseEndpoint(endpoint: unknown): asserts endpoint is string {
  if(typeof endpoint!=="string" || !engineEndpoints.has(endpoint)) {
    throw new Error("This capability is not available in this Compass release.");
  }
}
