package com.indexinbox.android

import kotlinx.serialization.json.Json
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class EntryCompatibilityTest {
    private val json=Json { ignoreUnknownKeys=true }

    @Test
    fun legacyEntryWithoutNewOptionalFieldsKeepsStableDefaults() {
        val entry=json.decodeFromString<Entry>("""{
          "id":"legacy-entry",
          "created_at":"2026-01-01T00:00:00Z",
          "transcription":"Existing note",
          "payload_json":"{}",
          "starred":1,
          "processed":1,
          "tags":"existing"
        }""")

        assertEquals("legacy-entry",entry.id)
        assertEquals("Existing note",entry.transcription)
        assertEquals(1,entry.processed)
        assertEquals("note",entry.category)
        assertEquals(0,entry.archived)
        assertNull(entry.groupName)
        assertNull(entry.dueAt)
        assertEquals(0,entry.reminderCompleted)
    }
}
