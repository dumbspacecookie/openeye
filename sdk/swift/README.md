# OpenEye Swift SDK

Native iOS / visionOS / macOS client for the OpenEye sidecar.

## Install via SwiftPM

In your `Package.swift`:

```swift
dependencies: [
    .package(url: "https://github.com/dumbspacecookie/openeye.git", from: "0.1.0")
],
targets: [
    .target(name: "MyApp", dependencies: [
        .product(name: "OpenEye", package: "openeye")
    ])
]
```

Or in Xcode: **File → Add Package Dependencies** → paste the repo URL.

## Important: the sidecar runs externally

iOS sandboxing prevents apps from spawning Python processes. The Python
sidecar must run separately — typically on a workstation, a Mac mini in
the closet, or a service in your cloud. The Swift client speaks to it
over HTTP.

For local development, run the sidecar on your Mac:
```bash
cd path/to/openeye
python sidecar/server.py --host 0.0.0.0 --port 7770
```

Then point the iOS app at your Mac's LAN IP (`http://192.168.1.x:7770`).
On real devices you'll want a permanent VPN or Tailscale tunnel.

For production: deploy the sidecar somewhere with TLS (Fly, Render,
your own infra) and use HTTPS.

## Quick start

```swift
import OpenEye

let client = OpenEyeClient(
    baseURL: URL(string: "http://192.168.1.50:7770")!,
    sidecarToken: ProcessInfo.processInfo.environment["OPENEYE_SIDECAR_TOKEN"]
)

// Confirm reachability
try await client.health()

// Start a session
let sessionId = try await client.createSession(source: "ios-app")
let visualSession = try await client.createVisualSession(
    deviceType: "visionos",
    procedureId: "bolt-assembly"
)

// Each frame: describe with a vision model, then log
let description = await myVisionAdapter.describe(cameraFrame)
try await client.logFrame(
    visualSessionId: visualSession,
    sequenceNum: 1,
    sceneDescription: description,
    confidence: 0.9
)

// Verify a step
try await client.logStepVerification(
    visualSessionId: visualSession,
    stepId: "s1",
    result: .pass,
    confidence: 0.92,
    reasoning: "All required components visible"
)
```

## Real-time verdicts via SSE

Subscribe to the session's event stream to surface AR overlay feedback
the moment the sidecar finishes a verification:

```swift
Task {
    for try await event in client.events(sessionId: sessionId) {
        if event.type == "step_verified",
           let result = event.data["result"] as? String {
            await MainActor.run {
                arOverlay.show(result)   // "pass", "fail", or "uncertain"
            }
        }
    }
}
```

## Run the tests

```bash
cd sdk/swift
swift test
```

Tests use a `MockURLProtocol` — no real sidecar required.

## API surface

| Method | What it does |
|---|---|
| `health()` | Liveness probe |
| `createSession()` | Start an agent session |
| `endSession()` | End it |
| `appendMessage()` | Add a message to the conversation |
| `createVisualSession()` | Start an AR/XR visual session |
| `endVisualSession()` | End it |
| `logFrame()` | Log a frame's scene description |
| `logStepVerification()` | Record pass/fail/uncertain — the reward signal |
| `events(sessionId:)` | SSE stream of events (real-time verdicts) |

This is the **core API** — what 90% of iOS apps need. Skill search,
trajectory export, DPO pair generation, and HuggingFace push are
on the roadmap. Until they ship, call those endpoints directly with
`URLSession` or open an issue.

## Status: alpha

Interfaces will change before 1.0. Pin to a specific commit via SwiftPM
if you need stability, and watch the changelog.
