import Foundation

/// Async sequence over SSE events from the OpenEye sidecar.
///
/// Usage:
///
/// ```swift
/// let events = client.events(sessionId: sid)
/// for try await event in events {
///     if event.type == "step_verified",
///        let result = event.data["result"] as? String {
///         await MainActor.run {
///             arOverlay.updateVerdict(result)
///         }
///     }
/// }
/// ```
public struct OpenEyeEvent: Sendable {
    public let type: String
    public let sessionId: String?
    public let timestamp: TimeInterval
    public let data: [String: Any]
}

extension OpenEyeClient {

    /// Stream events for a single session. Pass "*" to subscribe to ALL sessions.
    public func events(sessionId: String) -> AsyncThrowingStream<OpenEyeEvent, Error> {
        let url = baseURL.appendingPathComponent("sessions/\(sessionId)/events")
        var req = URLRequest(url: url)
        req.setValue("text/event-stream", forHTTPHeaderField: "Accept")
        if let token = self.tokenForEventStream() {
            req.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        }

        return AsyncThrowingStream { continuation in
            let task = Task {
                do {
                    let (stream, response) = try await self.urlSessionForEvents().bytes(for: req)
                    guard let http = response as? HTTPURLResponse,
                          (200..<300).contains(http.statusCode) else {
                        continuation.finish(throwing: OpenEyeError.httpError(
                            status: (response as? HTTPURLResponse)?.statusCode ?? -1,
                            body: ""))
                        return
                    }
                    var pendingData: String? = nil
                    for try await line in stream.lines {
                        // Heartbeat / comment line — ignore
                        if line.hasPrefix(":") { continue }
                        if line.hasPrefix("data:") {
                            pendingData = String(line.dropFirst(5)).trimmingCharacters(in: .whitespaces)
                        } else if line.isEmpty {
                            // End of frame — emit event if we have data
                            if let dataStr = pendingData,
                               let evt = Self.parseEventData(dataStr) {
                                continuation.yield(evt)
                            }
                            pendingData = nil
                        }
                    }
                    continuation.finish()
                } catch {
                    continuation.finish(throwing: error)
                }
            }
            continuation.onTermination = { _ in task.cancel() }
        }
    }

    /// Internal accessors exposed for the extension. Swift doesn't have
    /// stored properties in extensions, so we add getters on the main class.
    fileprivate func tokenForEventStream() -> String? {
        let mirror = Mirror(reflecting: self)
        return mirror.children.first(where: { $0.label == "token" })?.value as? String
    }

    fileprivate func urlSessionForEvents() -> URLSession {
        let mirror = Mirror(reflecting: self)
        return (mirror.children.first(where: { $0.label == "session" })?.value as? URLSession) ?? .shared
    }

    private static func parseEventData(_ raw: String) -> OpenEyeEvent? {
        guard let data = raw.data(using: .utf8),
              let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let type = json["type"] as? String else {
            return nil
        }
        return OpenEyeEvent(
            type: type,
            sessionId: json["session_id"] as? String,
            timestamp: (json["ts"] as? Double) ?? 0,
            data: (json["data"] as? [String: Any]) ?? [:]
        )
    }
}
