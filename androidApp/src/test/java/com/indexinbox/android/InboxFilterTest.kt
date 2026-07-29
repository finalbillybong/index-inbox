package com.indexinbox.android

import org.junit.Assert.assertEquals
import org.junit.Test

class InboxFilterTest {
    @Test
    fun allIncludesActiveAndArchivedEntries() {
        val entries=listOf(
            Entry(id="active",createdAt="2026-01-01T00:00:00Z"),
            Entry(id="archived",createdAt="2026-01-01T00:00:00Z",archived=1),
        )
        assertEquals(setOf("active","archived"),filterInboxEntries(entries,"","all","","").map{it.id}.toSet())
    }

    @Test
    fun combinedFiltersAndSearchRemainCorrectForLargeInbox() {
        val entries=(0 until 1_000).map { index ->
            Entry(
                id="$index",
                createdAt="2026-01-01T00:00:00Z",
                transcription=if(index==842)"Needle text" else "Entry $index",
                category=if(index%2==0)"task" else "note",
                groupName=if(index%3==0)"PROJECT" else null,
            )
        }
        assertEquals(
            listOf("842"),
            filterInboxEntries(entries,"needle","active","task","").map{it.id},
        )
    }
}
