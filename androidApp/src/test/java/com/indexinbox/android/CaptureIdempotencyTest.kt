package com.indexinbox.android

import org.junit.Assert.assertEquals
import org.junit.Test

class CaptureIdempotencyTest {
    @Test
    fun sourceKeyMatchesServerManualCaptureFingerprint() {
        assertEquals(
            "1c9fa2c4d8a77d40fa92173a4e6b135bb484d101a422777697d205f1151a1f59",
            manualCaptureSourceKey("capture-123"),
        )
    }
}
