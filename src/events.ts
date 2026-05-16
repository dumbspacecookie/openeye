/**
 * Consume a Server-Sent Events stream from the OpenEye sidecar.
 *
 * Usage:
 *   const events = openEyeEvents("http://127.0.0.1:7770", sessionId);
 *   for await (const event of events) {
 *     if (event.type === "step_verified") {
 *       showInARGlass(event.data.result);  // pass/fail/uncertain
 *     }
 *   }
 *
 * Use sessionId="*" to subscribe to ALL sessions (useful for an ops UI).
 *
 * The async iterator yields events until the underlying stream closes
 * (server shutdown, network error, or the consumer breaks out of the loop).
 * Heartbeat frames are filtered out — you only see meaningful events.
 */

export type OpenEyeEventType =
  | "subscribed"
  | "frame_logged"
  | "step_verified"
  | "session_ended";

export interface OpenEyeEvent {
  type: OpenEyeEventType | string;
  session_id: string | null;
  ts: number;
  data: Record<string, unknown>;
}

export interface OpenEyeEventsOptions {
  sidecarToken?: string;
  /** AbortSignal to cancel the stream from the consumer side. */
  signal?: AbortSignal;
}

/**
 * Async iterator over SSE events for a session.
 *
 * Yields parsed event objects. Heartbeats are silently dropped.
 * Stream auto-closes if the response ends; iterator simply returns.
 */
export async function* openEyeEvents(
  baseUrl: string,
  sessionId: string,
  opts: OpenEyeEventsOptions = {},
): AsyncGenerator<OpenEyeEvent, void, void> {
  const url = `${baseUrl.replace(/\/$/, "")}/sessions/${encodeURIComponent(sessionId)}/events`;
  const headers: Record<string, string> = { Accept: "text/event-stream" };
  if (opts.sidecarToken) {
    headers["Authorization"] = `Bearer ${opts.sidecarToken}`;
  }

  const resp = await fetch(url, { headers, signal: opts.signal });
  if (!resp.ok || !resp.body) {
    throw new Error(`SSE subscription failed: ${resp.status} ${resp.statusText}`);
  }

  const reader = resp.body.getReader();
  const decoder = new TextDecoder("utf-8");
  let buffer = "";

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) return;
      buffer += decoder.decode(value, { stream: true });

      // SSE frames are separated by blank lines.
      let separatorIdx: number;
      while ((separatorIdx = buffer.indexOf("\n\n")) !== -1) {
        const frame = buffer.slice(0, separatorIdx);
        buffer = buffer.slice(separatorIdx + 2);
        const parsed = parseFrame(frame);
        if (parsed) yield parsed;
      }
    }
  } finally {
    reader.releaseLock();
  }
}

function parseFrame(frame: string): OpenEyeEvent | null {
  // Heartbeat / comment frames start with ':'
  if (frame.startsWith(":")) return null;
  let dataLine: string | undefined;
  for (const line of frame.split("\n")) {
    if (line.startsWith("data:")) {
      dataLine = line.slice(5).trim();
      break;
    }
  }
  if (!dataLine) return null;
  try {
    return JSON.parse(dataLine) as OpenEyeEvent;
  } catch {
    return null;
  }
}
