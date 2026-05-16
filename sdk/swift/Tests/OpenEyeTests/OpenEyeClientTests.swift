import XCTest
@testable import OpenEye

/// Unit tests using a local URLSession with a mocked URLProtocol.
/// Run with: swift test
final class OpenEyeClientTests: XCTestCase {

    var client: OpenEyeClient!

    override func setUp() {
        super.setUp()
        let config = URLSessionConfiguration.ephemeral
        config.protocolClasses = [MockURLProtocol.self]
        let mockSession = URLSession(configuration: config)
        client = OpenEyeClient(
            baseURL: URL(string: "http://mock.openeye.test")!,
            sidecarToken: "test-token",
            urlSession: mockSession
        )
        MockURLProtocol.reset()
    }

    func testHealthCheck() async throws {
        MockURLProtocol.responder = { _ in
            (.init(status: 200), Data("{\"ok\": true, \"db\": \":memory:\"}".utf8))
        }
        let ok = try await client.health()
        XCTAssertTrue(ok)
    }

    func testCreateSession() async throws {
        MockURLProtocol.responder = { request in
            XCTAssertEqual(request.url?.path, "/sessions/create")
            XCTAssertEqual(request.value(forHTTPHeaderField: "Authorization"), "Bearer test-token")
            return (.init(status: 200), Data("{\"session_id\": \"sess-abc\"}".utf8))
        }
        let sid = try await client.createSession(source: "ios-test", tenantId: "acme")
        XCTAssertEqual(sid, "sess-abc")
    }

    func testCreateVisualSession() async throws {
        MockURLProtocol.responder = { _ in
            (.init(status: 200), Data("{\"visual_session_id\": \"vs-xyz\"}".utf8))
        }
        let vsid = try await client.createVisualSession(
            deviceType: "visionos",
            procedureId: "bolt-assembly"
        )
        XCTAssertEqual(vsid, "vs-xyz")
    }

    func testLogFrame() async throws {
        var capturedBody: [String: Any] = [:]
        MockURLProtocol.responder = { request in
            if let body = request.httpBody ?? request.bodyFromStream(),
               let json = try? JSONSerialization.jsonObject(with: body) as? [String: Any] {
                capturedBody = json
            }
            return (.init(status: 200), Data("{\"frame_id\": 42}".utf8))
        }
        let fid = try await client.logFrame(
            visualSessionId: "vs-1",
            sequenceNum: 7,
            sceneDescription: "operator placing bolt",
            objectsDetected: ["bolt", "hand"],
            confidence: 0.91
        )
        XCTAssertEqual(fid, 42)
        XCTAssertEqual(capturedBody["visual_session_id"] as? String, "vs-1")
        XCTAssertEqual(capturedBody["sequence_num"] as? Int, 7)
        XCTAssertEqual(capturedBody["confidence"] as? Double, 0.91)
    }

    func testLogStepVerification() async throws {
        MockURLProtocol.responder = { _ in
            (.init(status: 200), Data("{\"verification_id\": 1}".utf8))
        }
        let vid = try await client.logStepVerification(
            visualSessionId: "vs-1",
            stepId: "s1",
            result: .pass,
            confidence: 0.95
        )
        XCTAssertEqual(vid, 1)
    }

    func testHttpErrorThrows() async {
        MockURLProtocol.responder = { _ in
            (.init(status: 500), Data("{\"error\": \"boom\"}".utf8))
        }
        do {
            _ = try await client.createSession()
            XCTFail("Expected HTTP error")
        } catch OpenEyeClient.OpenEyeError.httpError(let status, _) {
            XCTAssertEqual(status, 500)
        } catch {
            XCTFail("Wrong error: \(error)")
        }
    }

    func testAuthHeaderOmittedWhenNoToken() async throws {
        let config = URLSessionConfiguration.ephemeral
        config.protocolClasses = [MockURLProtocol.self]
        let noTokenSession = URLSession(configuration: config)
        let noTokenClient = OpenEyeClient(
            baseURL: URL(string: "http://mock.openeye.test")!,
            urlSession: noTokenSession
        )
        var capturedAuth: String? = "should-be-overwritten"
        MockURLProtocol.responder = { req in
            capturedAuth = req.value(forHTTPHeaderField: "Authorization")
            return (.init(status: 200), Data("{\"ok\": true, \"db\": \":memory:\"}".utf8))
        }
        _ = try await noTokenClient.health()
        XCTAssertNil(capturedAuth)
    }
}

// MARK: - Mock URLProtocol

final class MockURLProtocol: URLProtocol {
    struct Response {
        let status: Int
        var headers: [String: String] = ["Content-Type": "application/json"]
        init(status: Int, headers: [String: String] = ["Content-Type": "application/json"]) {
            self.status = status
            self.headers = headers
        }
    }

    static var responder: ((URLRequest) -> (Response, Data))?

    static func reset() {
        responder = nil
    }

    override class func canInit(with request: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }

    override func startLoading() {
        guard let responder = MockURLProtocol.responder else {
            client?.urlProtocol(self, didFailWithError: NSError(domain: "MockURLProtocol", code: -1))
            return
        }
        let (response, data) = responder(request)
        let http = HTTPURLResponse(
            url: request.url!,
            statusCode: response.status,
            httpVersion: "HTTP/1.1",
            headerFields: response.headers
        )!
        client?.urlProtocol(self, didReceive: http, cacheStoragePolicy: .notAllowed)
        client?.urlProtocol(self, didLoad: data)
        client?.urlProtocolDidFinishLoading(self)
    }

    override func stopLoading() {}
}

extension URLRequest {
    func bodyFromStream() -> Data? {
        guard let stream = httpBodyStream else { return nil }
        stream.open()
        defer { stream.close() }
        var data = Data()
        let bufSize = 4096
        let buffer = UnsafeMutablePointer<UInt8>.allocate(capacity: bufSize)
        defer { buffer.deallocate() }
        while stream.hasBytesAvailable {
            let read = stream.read(buffer, maxLength: bufSize)
            if read <= 0 { break }
            data.append(buffer, count: read)
        }
        return data.isEmpty ? nil : data
    }
}
