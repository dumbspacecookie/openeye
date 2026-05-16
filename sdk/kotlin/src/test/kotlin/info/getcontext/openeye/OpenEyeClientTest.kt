package info.getcontext.openeye

import kotlinx.coroutines.runBlocking
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import kotlin.test.AfterTest
import kotlin.test.BeforeTest
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFailsWith
import kotlin.test.assertTrue

class OpenEyeClientTest {
    private lateinit var server: MockWebServer
    private lateinit var client: OpenEyeClient

    @BeforeTest
    fun setUp() {
        server = MockWebServer().apply { start() }
        client = OpenEyeClient(
            baseUrl = server.url("").toString().trimEnd('/'),
            sidecarToken = "test-token",
        )
    }

    @AfterTest
    fun tearDown() {
        server.shutdown()
    }

    @Test
    fun `health returns true on ok response`() = runBlocking {
        server.enqueue(MockResponse().setBody("""{"ok": true, "db": ":memory:"}"""))
        assertTrue(client.health())
    }

    @Test
    fun `createSession parses session_id`() = runBlocking {
        server.enqueue(MockResponse().setBody("""{"session_id": "sess-123"}"""))
        val sid = client.createSession(source = "android-test")
        assertEquals("sess-123", sid)

        val recorded = server.takeRequest()
        assertEquals("/sessions/create", recorded.path)
        assertEquals("Bearer test-token", recorded.getHeader("Authorization"))
    }

    @Test
    fun `createVisualSession parses visual_session_id`() = runBlocking {
        server.enqueue(MockResponse().setBody("""{"visual_session_id": "vs-abc"}"""))
        val vsid = client.createVisualSession(
            deviceType = "android",
            procedureId = "bolt-assembly"
        )
        assertEquals("vs-abc", vsid)
    }

    @Test
    fun `logFrame returns frame_id`() = runBlocking {
        server.enqueue(MockResponse().setBody("""{"frame_id": 42}"""))
        val fid = client.logFrame(
            visualSessionId = "vs-1",
            sequenceNum = 7,
            sceneDescription = "operator placing bolt",
            confidence = 0.91,
        )
        assertEquals(42, fid)

        val recorded = server.takeRequest()
        assertTrue(recorded.body.readUtf8().contains("\"sequence_num\":7"))
    }

    @Test
    fun `logStepVerification returns verification_id`() = runBlocking {
        server.enqueue(MockResponse().setBody("""{"verification_id": 1}"""))
        val vid = client.logStepVerification(
            visualSessionId = "vs-1",
            stepId = "s1",
            result = OpenEyeClient.VerifyResult.PASS,
        )
        assertEquals(1, vid)
    }

    @Test
    fun `http error throws OpenEyeHttpException`() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(500).setBody("""{"error": "boom"}"""))
        val ex = assertFailsWith<OpenEyeClient.OpenEyeHttpException> {
            client.createSession()
        }
        assertEquals(500, ex.status)
    }

    @Test
    fun `no auth header when token unset`() = runBlocking {
        val noTokenClient = OpenEyeClient(server.url("").toString().trimEnd('/'))
        server.enqueue(MockResponse().setBody("""{"ok": true, "db": ":memory:"}"""))
        noTokenClient.health()
        val recorded = server.takeRequest()
        assertEquals(null, recorded.getHeader("Authorization"))
    }
}
