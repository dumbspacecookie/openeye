import Foundation

/// HTTP client for the OpenEye sidecar. Mirrors the TypeScript `SidecarClient`
/// API surface for iOS / visionOS apps.
///
/// The Python sidecar is expected to be reachable over the network — typically
/// running on a workstation or a service in the same WiFi/LAN. iOS apps cannot
/// spawn the Python process themselves; you run the sidecar separately and
/// point this client at it.
///
/// Quick start:
///
/// ```swift
/// let client = OpenEyeClient(baseURL: URL(string: "http://192.168.1.50:7770")!)
/// let session = try await client.createSession(source: "ios-app")
/// let visualSession = try await client.createVisualSession(deviceType: "visionos")
/// try await client.logFrame(
///     visualSessionId: visualSession,
///     sequenceNum: 1,
///     sceneDescription: "operator placing bolt"
/// )
/// try await client.logStepVerification(
///     visualSessionId: visualSession,
///     stepId: "s1",
///     result: .pass
/// )
/// ```
public final class OpenEyeClient: @unchecked Sendable {

    public enum OpenEyeError: Error {
        case httpError(status: Int, body: String)
        case decodingError(underlying: Error)
        case missingResponse
    }

    public enum VerifyResult: String, Codable {
        case pass, fail, uncertain
    }

    private let baseURL: URL
    private let session: URLSession
    private let token: String?
    private let requestTimeout: TimeInterval

    public init(
        baseURL: URL,
        sidecarToken: String? = nil,
        requestTimeout: TimeInterval = 15.0,
        urlSession: URLSession = .shared
    ) {
        self.baseURL = baseURL
        self.token = sidecarToken
        self.requestTimeout = requestTimeout
        self.session = urlSession
    }

    // MARK: - Internal helpers

    func request(_ method: String, _ path: String, body: [String: Any]? = nil) async throws -> Data {
        var req = URLRequest(url: baseURL.appendingPathComponent(path))
        req.httpMethod = method
        req.timeoutInterval = requestTimeout
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        if let token = token {
            req.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        }
        if let body = body {
            req.httpBody = try JSONSerialization.data(withJSONObject: body)
        }
        let (data, response) = try await session.data(for: req)
        guard let http = response as? HTTPURLResponse else {
            throw OpenEyeError.missingResponse
        }
        guard (200..<300).contains(http.statusCode) else {
            let body = String(data: data, encoding: .utf8) ?? ""
            throw OpenEyeError.httpError(status: http.statusCode, body: body)
        }
        return data
    }

    func decode<T: Decodable>(_ data: Data, as _: T.Type) throws -> T {
        do {
            return try JSONDecoder().decode(T.self, from: data)
        } catch {
            throw OpenEyeError.decodingError(underlying: error)
        }
    }

    // MARK: - Health

    public func health() async throws -> Bool {
        struct Health: Decodable { let ok: Bool }
        let data = try await request("GET", "health")
        return try decode(data, as: Health.self).ok
    }

    // MARK: - Sessions

    public func createSession(
        source: String = "ios",
        userId: String? = nil,
        tenantId: String? = nil,
        model: String? = nil,
        systemPrompt: String? = nil
    ) async throws -> String {
        struct Response: Decodable { let session_id: String }
        var body: [String: Any] = ["source": source]
        body["user_id"] = userId
        body["tenant_id"] = tenantId
        body["model"] = model
        body["system_prompt"] = systemPrompt
        let data = try await request("POST", "sessions/create", body: body.compactMapValues { $0 })
        return try decode(data, as: Response.self).session_id
    }

    public func endSession(_ sessionId: String, reason: String = "completed") async throws {
        _ = try await request("POST", "sessions/\(sessionId)/end", body: ["reason": reason])
    }

    public func appendMessage(
        sessionId: String,
        role: String,
        content: String? = nil
    ) async throws -> Int {
        struct Response: Decodable { let message_id: Int }
        var body: [String: Any] = ["role": role]
        body["content"] = content
        let data = try await request("POST", "sessions/\(sessionId)/messages",
                                     body: body.compactMapValues { $0 })
        return try decode(data, as: Response.self).message_id
    }

    // MARK: - Visual sessions

    public func createVisualSession(
        deviceType: String,
        deviceId: String? = nil,
        procedureId: String? = nil,
        procedureName: String? = nil,
        userId: String? = nil,
        tenantId: String? = nil,
        sessionId: String? = nil
    ) async throws -> String {
        struct Response: Decodable { let visual_session_id: String }
        var body: [String: Any] = ["device_type": deviceType]
        body["device_id"] = deviceId
        body["procedure_id"] = procedureId
        body["procedure_name"] = procedureName
        body["user_id"] = userId
        body["tenant_id"] = tenantId
        body["session_id"] = sessionId
        let data = try await request("POST", "visual-sessions/create",
                                     body: body.compactMapValues { $0 })
        return try decode(data, as: Response.self).visual_session_id
    }

    public func endVisualSession(_ vsid: String, outcome: String = "completed") async throws {
        _ = try await request("POST", "visual-sessions/\(vsid)/end",
                              body: ["outcome": outcome])
    }

    // MARK: - Frames

    public func logFrame(
        visualSessionId: String,
        sequenceNum: Int,
        sceneDescription: String,
        objectsDetected: [String]? = nil,
        stepContext: String? = nil,
        confidence: Double? = nil
    ) async throws -> Int {
        struct Response: Decodable { let frame_id: Int }
        var body: [String: Any] = [
            "visual_session_id": visualSessionId,
            "sequence_num": sequenceNum,
            "scene_description": sceneDescription,
        ]
        body["objects_detected"] = objectsDetected
        body["step_context"] = stepContext
        body["confidence"] = confidence
        let data = try await request("POST", "frames/log",
                                     body: body.compactMapValues { $0 })
        return try decode(data, as: Response.self).frame_id
    }

    // MARK: - Step verification

    public func logStepVerification(
        visualSessionId: String,
        stepId: String,
        result: VerifyResult,
        stepName: String? = nil,
        confidence: Double? = nil,
        reasoning: String? = nil,
        frameId: Int? = nil
    ) async throws -> Int {
        struct Response: Decodable { let verification_id: Int }
        var body: [String: Any] = [
            "visual_session_id": visualSessionId,
            "step_id": stepId,
            "result": result.rawValue,
        ]
        body["step_name"] = stepName
        body["confidence"] = confidence
        body["reasoning"] = reasoning
        body["frame_id"] = frameId
        let data = try await request("POST", "steps/log",
                                     body: body.compactMapValues { $0 })
        return try decode(data, as: Response.self).verification_id
    }
}
