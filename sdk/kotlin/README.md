# OpenEye Kotlin SDK

Native Android / JVM client for the OpenEye sidecar.

## Install via Gradle

In your module's `build.gradle.kts`:

```kotlin
dependencies {
    implementation("info.getcontext.openeye:openeye-kotlin:0.1.0")
}
```

Until the artifact is on Maven Central, depend on the source directly:

```kotlin
// settings.gradle.kts
includeBuild("../openeye/sdk/kotlin")
```

## Sidecar runs externally

Android sandboxing won't let you spawn Python. Run the Python sidecar
on a server or workstation:

```bash
python sidecar/server.py --host 0.0.0.0 --port 7770
```

Point your Android app at the LAN IP for dev (`http://192.168.1.x:7770`)
or your deployed HTTPS endpoint for production.

## Quick start

```kotlin
import info.getcontext.openeye.OpenEyeClient
import kotlinx.coroutines.flow.collect

val client = OpenEyeClient(
    baseUrl = "http://192.168.1.50:7770",
    sidecarToken = System.getenv("OPENEYE_SIDECAR_TOKEN"),
)

// Inside a coroutine scope:
val sessionId = client.createSession(source = "android-app")
val vsid = client.createVisualSession(
    deviceType = "android",
    procedureId = "bolt-assembly"
)

val description = myVisionAdapter.describe(cameraFrame)
client.logFrame(
    visualSessionId = vsid,
    sequenceNum = 1,
    sceneDescription = description,
    confidence = 0.9
)

client.logStepVerification(
    visualSessionId = vsid,
    stepId = "s1",
    result = OpenEyeClient.VerifyResult.PASS,
    confidence = 0.92
)
```

## Real-time verdicts via SSE

```kotlin
import kotlinx.coroutines.flow.collect

lifecycleScope.launch {
    client.events(sessionId).collect { event ->
        if (event.type == "step_verified") {
            val result = event.data["result"]?.toString()?.removeSurrounding("\"")
            withContext(Dispatchers.Main) {
                arOverlay.show(result ?: "uncertain")
            }
        }
    }
}
```

## Required permissions

Add to `AndroidManifest.xml` for any HTTP path:

```xml
<uses-permission android:name="android.permission.INTERNET" />
```

If you talk to the sidecar over plain HTTP (LAN dev), you'll also need
to allow cleartext for that specific host — production should be HTTPS.

## Run the tests

```bash
cd sdk/kotlin
./gradlew test
```

Tests use `MockWebServer` — no real sidecar required.

## API surface

Same as the Swift SDK: health, sessions, visual sessions, frames, step
verifications, and SSE events. Skill search, trajectory export, and HF
push are on the roadmap.

## Status: alpha

API will change before 1.0. Pin to a tag in your Gradle build.
