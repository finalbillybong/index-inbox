package com.indexinbox.android

import org.junit.Assert.assertEquals
import org.junit.Test

class InboxFilterTest {
    @Test
    fun mobileFilterPickerExposesEveryStateAndType() {
        assertEquals(
            listOf("active","today","reminders","all","unprocessed","incomplete","completed","starred","archived"),
            inboxStateFilters.map{it.first},
        )
        assertEquals(listOf("","note","task","idea","question"),inboxTypeFilters.map{it.first})
    }

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

    @Test
    fun reminderFeedExcludesCompletedAndArchivedEntries() {
        val entries=listOf(
            Entry(id="due",createdAt="2026-01-01T00:00:00Z",dueAt="2099-01-01T09:00:00Z"),
            Entry(id="complete",createdAt="2026-01-01T00:00:00Z",dueAt="2099-01-01T09:00:00Z",completed=1),
            Entry(id="archived",createdAt="2026-01-01T00:00:00Z",dueAt="2099-01-01T09:00:00Z",archived=1),
            Entry(id="note",createdAt="2026-01-01T00:00:00Z"),
        )
        assertEquals(listOf("due"),filterInboxEntries(entries,"","reminders","","").map{it.id})
        assertEquals(listOf("complete"),filterInboxEntries(entries,"","completed","","").map{it.id})
    }

    @Test
    fun openExcludesEveryCompletedItemIncludingReminders() {
        val entries=listOf(
            Entry(id="open",createdAt="2026-01-01T00:00:00Z"),
            Entry(id="item-complete",createdAt="2026-01-01T00:00:00Z",completed=1),
            Entry(id="reminder-complete",createdAt="2026-01-01T00:00:00Z",dueAt="2099-01-01T09:00:00Z",completed=1),
        )
        assertEquals(listOf("open"),filterInboxEntries(entries,"","active","","").map{it.id})
    }

    @Test
    fun todayIncludesOverdueButNotFutureReminders() {
        val entries=listOf(
            Entry(id="overdue",createdAt="2026-01-01T00:00:00Z",dueAt="2020-01-01T09:00:00Z"),
            Entry(id="future",createdAt="2026-01-01T00:00:00Z",dueAt="2099-01-01T09:00:00Z"),
        )
        assertEquals(listOf("overdue"),filterInboxEntries(entries,"","today","","").map{it.id})
    }
}
